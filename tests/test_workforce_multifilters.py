import os
import unittest
from urllib.parse import urlencode

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class WorkforceMultiFiltersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_multi_values_intersect_fields_preserve_scope_totals_and_cache(self):
        f = self.f
        f.app.config['TESTING'] = False
        employers, categories = [], []
        for index, worker in enumerate(f.ids):
            employers.append(str(f.db.native('INSERT INTO workforce_organizations(name) VALUES (%s) RETURNING id',
                (f.suffix + str(index),)).fetchone()[0]))
            categories.append(f.db.native('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                VALUES (%s,%s,'test',%s,'now') RETURNING id''',
                (f.suffix + str(index), f.suffix + str(index), f.users['admin']['id'])).fetchone()[0])
            f.db.native('UPDATE workforce_profiles SET employer_id=%s WHERE worker_id=%s', (employers[-1], worker))
            f.db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (%s,%s,'test',%s,'now')''', (worker, categories[-1], f.users['admin']['id']))
            f.add_source_record(worker, 'urp:К15', role='recruitment', sheet='ПВП')
        filters = [('department', 'TEST-SMU'), ('department', 'OTHER-SMU'),
                   *[('employer', value) for value in employers], *[('category', value) for value in categories]]

        def get(values, role='admin', extra=''):
            response = f.request('get', 'people?date=2026-09-16&section=recruitment&' + urlencode(values) + extra, role=role)
            self.assertEqual(response.status_code, 200, response.data)
            return response.json

        both = get(filters)
        self.assertEqual({row['id'] for row in both['rows']}, set(f.ids))
        self.assertEqual(both['totals']['total'], 2)
        self.assertEqual(get(list(reversed(filters))), both)
        self.assertEqual(get(filters, extra='&limit=1&offset=1')['totals']['total'], 2)
        self.assertEqual(len(get(filters, extra='&limit=1&offset=1')['rows']), 1)
        scoped = get(filters, role='recruitment')
        self.assertEqual([row['id'] for row in scoped['rows']], [f.worker])
        self.assertEqual(scoped['totals']['total'], 1)
        for key, first in [('department', 'TEST-SMU'), ('employer', employers[0]), ('category', categories[0])]:
            narrowed = [(k, v) for k, v in filters if k != key] + [(key, first)]
            self.assertEqual([row['id'] for row in get(narrowed)['rows']], [f.worker])
        contradictory = [('department', 'TEST-SMU'), ('employer', employers[1])]
        self.assertEqual(get(contradictory)['totals']['total'], 0)
        self.assertEqual(get([('department', 'TEST-SMU'), ('employer', ''), ('category', '')])['totals']['total'], 1)

    def test_employment_statuses_include_unspecified_and_combine_with_stage(self):
        f = self.f
        for worker in f.ids:
            f.add_source_record(worker, 'urp:К15', role='recruitment', sheet='ПВП')
        f.db.native('UPDATE workforce_profiles SET employment_code=NULL WHERE worker_id=%s', (f.ids[1],))
        def get(values, role='admin'):
            query = [('department', 'TEST-SMU'), ('department', 'OTHER-SMU'),
                     ('section', 'recruitment'), ('date', '2026-09-17'), *values]
            response = f.request('get', 'people?' + urlencode(query), role=role)
            self.assertEqual(response.status_code, 200, response.data)
            return response.json
        staff = [('employment', 'employment.staff')]
        unknown = [('employment', '__none__')]
        self.assertEqual([r['id'] for r in get(staff)['rows']], [f.worker])
        self.assertEqual([r['id'] for r in get(unknown)['rows']], [f.ids[1]])
        self.assertEqual(get(staff + unknown)['totals']['total'], 2)
        self.assertEqual(get(staff + unknown + [('limit', '1')])['totals']['total'], 2)
        self.assertEqual(get(staff + unknown, role='recruitment')['totals']['total'], 1)
        self.assertEqual(get(unknown, role='recruitment')['rows'], [])
        self.assertEqual(get(staff + [('stage', 'unconfirmed')])['totals']['total'], 1)
        self.assertEqual(get(staff + [('stage', 'stage.onsite')])['totals']['total'], 0)

    def test_all_selected_values_are_validated(self):
        for query in ['category=1&category=bad', 'category=1&category=-1',
                      'category=1&category=999999999999999999999',
                      'employer=00000000-0000-0000-0000-000000000001&employer=bad',
                      urlencode([('department', str(i)) for i in range(101)])]:
            with self.subTest(query=query):
                self.assertEqual(self.f.request('get', 'people?' + query).status_code, 400)
