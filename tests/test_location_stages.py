import unittest
from unittest.mock import patch

import test_locations as location_tests
from tools.organize_location_stages import organize_stages


class LocationStagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        location_tests.LocationCatalogTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        location_tests.LocationCatalogTest.tearDownClass()

    def setUp(self):
        self.fixture = location_tests.LocationCatalogTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.module = self.fixture.module
        self.client = self.fixture.admin

    def catalog(self):
        return self.client.get('/api/locations').get_json()

    def apply(self):
        expected = self.catalog()['objects']
        with self.module.app.app_context():
            return organize_stages(self.module.get_db(), expected)

    def test_preserves_ids_children_assignments_and_restart(self):
        before = self.catalog()
        self.assertEqual(before['stages'], [])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,crew_id,created_at)
                VALUES ('2026-09-13','1 смена',(SELECT id FROM subobjects LIMIT 1),
                (SELECT id FROM workers LIMIT 1),?,(SELECT id FROM crews LIMIT 1),'now')''', (self.fixture.admin_id,))
            db.commit()
            original = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in ('subobjects', 'assignments', 'daily_staffing_plans')}
        result = self.apply()
        self.assertEqual(result['moved'], len(before['objects']))
        after = self.catalog()
        stage = next(s['id'] for s in after['stages'] if s['name'] == 'Этап 13')
        self.assertEqual([s['name'] for s in after['stages']], ['Этап 5', 'Этап 13', 'Этап 15'])
        self.assertEqual([(r['id'], r['name']) for r in before['objects']], [(r['id'], r['name']) for r in after['objects']])
        self.assertTrue(all(r['stage_id'] == stage for r in after['objects']))
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(original, {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in original})
            self.module.init_db()
        self.assertEqual(self.catalog(), after)
        self.assertTrue(self.apply()['already_applied'])
        reference = self.client.get('/api/reference?scope=locations').get_json()
        self.assertEqual(reference['stages'], after['stages'])
        self.assertTrue(all(r['stage_name'] == 'Этап 13' for r in reference['objects']))

    def test_parent_edit_validates_id_and_stale_token(self):
        self.apply()
        catalog = self.catalog()
        row = catalog['objects'][0]
        target = next(s['id'] for s in catalog['stages'] if s['name'] == 'Этап 5')
        data = {'name': row['name'], 'expected_token': row['edit_token'], 'stage_id': target}
        suffix = 'objects/' + str(row['id'])
        for bad in (True, '1', -1, 999999):
            self.assertEqual(self.fixture.write('PATCH', suffix, {**data, 'stage_id': bad}).status_code, 400)
        self.assertEqual(self.fixture.write('PATCH', suffix, data).status_code, 200)
        self.assertEqual(self.fixture.write('PATCH', suffix, data).status_code, 409)
        updated = next(r for r in self.catalog()['objects'] if r['id'] == row['id'])
        self.assertEqual(updated['stage_id'], target)
        # Older clients that only rename must preserve the parent.
        data = {'name': row['name'] + ' новая', 'expected_token': updated['edit_token']}
        self.assertEqual(self.fixture.write('PATCH', suffix, data).status_code, 200)
        self.assertEqual(next(r for r in self.catalog()['objects'] if r['id'] == row['id'])['stage_id'], target)

    def test_backup_failure_and_stale_snapshot_never_move_groups(self):
        before = self.catalog()
        with self.module.app.app_context():
            db = self.module.get_db()
            with patch('tools.organize_location_stages.create_backup', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    organize_stages(db, before['objects'])
            expected = [{**r, 'name': 'Устаревшее название'} for r in before['objects']]
            with self.assertRaises(ValueError):
                organize_stages(db, expected)
        self.assertEqual(self.catalog(), before)
