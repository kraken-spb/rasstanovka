import os
import unittest
from urllib.parse import urlencode

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class WorkforcePpsFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.pps, self.smu = [], []
        for index, worker in enumerate(self.f.ids):
            name = 'ППС-' + self.f.suffix + str(index)
            self.pps.append(self.f.db.native('INSERT INTO pps_catalog(name,name_key) VALUES (%s,%s) RETURNING id', (name, name)).fetchone()[0])
            self.smu.append(self.f.db.native('INSERT INTO smu_catalog(name,pps_id) VALUES (%s,%s) RETURNING id', (name, self.pps[-1])).fetchone()[0])
            self.f.db.native('INSERT INTO employee_smu(worker_id,smu_id,source_department) VALUES (%s,%s,%s)', (worker, self.smu[-1], name))
            self.f.add_source_record(worker, 'urp:П15', role='rotation')
            self.f.add_source_record(worker, 'urp:К15', role='recruitment', sheet='ПВП')

    def get(self, pps, section='rotation', role='admin', endpoint='people', extra=()):
        return self.f.request('get', endpoint + '?' + urlencode([
            ('date', '2026-09-18'), ('section', section), ('pps', str(pps)), *extra]), role=role)

    def test_both_sections_board_totals_export_and_intersection(self):
        for section in ('rotation', 'recruitment'):
            for endpoint in ('people', 'board'):
                data = self.get(self.pps[0], section, endpoint=endpoint)
                self.assertEqual(data.status_code, 200, data.json)
                rows = data.json['rows'] if endpoint == 'people' else [r for lane in data.json['lanes'].values() for r in lane['rows']]
                self.assertEqual([r['id'] for r in rows], [self.f.worker])
                self.assertEqual(data.json['totals']['total'], 1)
            export = self.get(self.pps[0], section, endpoint='people/export')
            self.assertEqual(export.status_code, 200, export.data[:100])
            self.assertEqual(export.headers['X-Export-Row-Count'], '1')
        self.assertEqual(self.get(self.pps[0], extra=[('department', 'OTHER-SMU')]).json['totals']['total'], 0)
        self.assertEqual(self.get(self.pps[0], extra=[('pps', str(self.pps[1])), ('limit', '1'), ('offset', '1')]).json['totals']['total'], 2)

    def test_scope_and_reference_do_not_expose_other_pps(self):
        data = self.f.request('get', 'reference', role='rotation').json
        self.assertIn(self.pps[0], [r['id'] for r in data['pps']])
        self.assertNotIn(self.pps[1], [r['id'] for r in data['pps']])
        self.assertEqual(self.get(self.pps[1], role='rotation').json['rows'], [])
        self.assertEqual(self.get(9223372036854775807).json['rows'], [])

    def test_rebinding_smu_updates_cached_totals_and_bad_ids_are_rejected(self):
        self.f.app.config['TESTING'] = False
        self.assertEqual(self.get(self.pps[0]).json['totals']['total'], 1)
        self.f.db.native('UPDATE smu_catalog SET pps_id=%s WHERE id=%s', (self.pps[0], self.smu[1]))
        data = self.get(self.pps[0]).json
        self.assertEqual(data['totals']['total'], 2)
        self.assertEqual({r['id'] for r in data['rows']}, set(self.f.ids))
        for bad in ('bad', '-1', '0', '9223372036854775808'):
            self.assertEqual(self.get(bad).status_code, 400)
