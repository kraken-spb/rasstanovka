import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class RentalRsoEmploymentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        for worker in self.f.ids:
            self.f.add_source_record(worker, 'urp:П15')
            self.f.add_source_record(worker, 'urp:К15', role='recruitment', sheet='ПВП')

    def test_reference_profile_and_both_registry_exports_use_new_types(self):
        f = self.f
        catalog = {r['code']: r for r in f.request('get', 'reference').json['catalog']}
        for worker, code, label in zip(f.ids, ('employment.rental', 'employment.rso'), ('Аренда', 'РСО')):
            self.assertEqual(catalog[code]['label'], label)
            self.assertTrue(catalog[code]['system_value'])
            current = f.request('get', f'people/{worker}').json['profile']
            body = {'employment_code': code, 'token': current['token'], 'reason': 'Проверка категории занятости', 'request_key': str(uuid4())}
            saved = f.request('patch', f'people/{worker}/profile', body)
            self.assertEqual(saved.status_code, 200, saved.json)
            self.assertEqual(saved.json['employment_code'], code)
            for section in ('rotation', 'recruitment'):
                query = f'?date=2026-09-19&section={section}&employment={code}&department=TEST-SMU&department=OTHER-SMU'
                result = f.request('get', 'people' + query)
                self.assertEqual([r['id'] for r in result.json['rows']], [worker])
                self.assertEqual(result.json['rows'][0]['employment'], label)
                export = f.request('get', 'people/export' + query)
                self.assertEqual(export.status_code, 200)
                self.assertEqual(export.headers['X-Export-Row-Count'], '1')

    def test_new_types_do_not_bypass_scope_or_allow_archived_assignment(self):
        f = self.f
        f.db.native("UPDATE workforce_profiles SET employment_code='employment.rso' WHERE worker_id=%s", (f.ids[1],))
        response = f.request('get', 'people?date=2026-09-19&employment=employment.rso', role='rotation')
        self.assertEqual(response.json['rows'], [])
        f.db.native("UPDATE workforce_catalog SET active=FALSE WHERE code='employment.rental'")
        current = f.request('get', f'people/{f.worker}').json['profile']
        body = {'employment_code': 'employment.rental', 'token': current['token'], 'reason': 'Проверка', 'request_key': str(uuid4())}
        self.assertEqual(f.request('patch', f'people/{f.worker}/profile', body).status_code, 400)
