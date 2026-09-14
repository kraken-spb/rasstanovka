import sqlite3
import unittest
from unittest.mock import patch

import test_staffing as staffing_tests
from backup_api import create_backup


class BulkCategoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        staffing_tests.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        staffing_tests.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.fixture = staffing_tests.StaffingWorkflowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.apply()
        self.module = self.fixture.module
        with self.module.app.app_context():
            db = self.module.get_db()
            self.category = db.execute("""INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                VALUES ('Общая категория','общая категория','catalog-token',?,'now')""", (self.fixture.admin_id,)).lastrowid
            db.commit()
        self.rows = [r for r in self.fixture.table().get_json()['rows'] if r['personnel_no'] in ('70001', '70002')]
        self.assertEqual(len(self.rows), 2)
        self.payload = dict(category_id=self.category, category_token='catalog-token',
                            worker_ids=[r['id'] for r in self.rows],
                            expected_tokens={str(r['id']): r['category_binding_token'] for r in self.rows},
                            expected_crews={str(r['id']): r['crew_id'] for r in self.rows},
                            expected_group_tokens={str(r['id']): r['group_token'] for r in self.rows})

    def write(self, client=None, **changes):
        return self.fixture.write('/api/staffing/groups/category', {**self.payload, **changes}, client)

    def bindings(self):
        with self.module.app.app_context():
            return [tuple(r) for r in self.module.get_db().execute('SELECT * FROM employee_gdlr ORDER BY worker_id')]

    def test_selected_only_backup_and_import_persistence(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            source = [tuple(r) for r in db.execute('SELECT id,category FROM workers ORDER BY id')]
            placements = [tuple(r) for r in db.execute('SELECT * FROM assignments')]
        with patch('gdlr_api.create_backup', wraps=create_backup) as backup:
            response = self.write()
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()['saved'], 2)
            backup.assert_called_once()
        self.assertEqual(self.write().status_code, 409)
        self.fixture.apply()
        updated = self.fixture.table().get_json()['rows']
        for row in updated:
            if row['id'] in self.payload['worker_ids']:
                self.assertEqual(row['category'], 'Общая категория')
                self.assertEqual(row['category_id'], self.category)
            else:
                self.assertNotEqual(row['category'], 'Общая категория')
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(source, [tuple(r) for r in db.execute('SELECT id,category FROM workers ORDER BY id')])
            self.assertEqual(placements, [tuple(r) for r in db.execute('SELECT * FROM assignments')])

    def test_stale_second_worker_and_catalog_are_atomic(self):
        before = self.bindings()
        second = str(self.rows[1]['id'])
        for field, value in [('expected_tokens', 'stale'), ('expected_crews', -1), ('expected_group_tokens', 'stale')]:
            with self.subTest(field=field):
                self.assertEqual(self.write(**{field: {**self.payload[field], second: value}}).status_code, 409)
                self.assertEqual(self.bindings(), before)
        self.assertEqual(self.write(category_token='stale').status_code, 409)
        self.assertEqual(self.bindings(), before)

    def test_permissions_and_csrf(self):
        self.assertEqual(self.fixture.admin.put('/api/staffing/groups/category', json=self.payload).status_code, 403)
        self.assertEqual(self.write(self.fixture.viewer).status_code, 403)
        self.assertEqual(self.write(self.fixture.foreman).status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.fixture.foreman_id, self.rows[0]['crew_id']))
            db.commit()
        self.payload['expected_group_tokens'] = {str(r['id']): r['group_token'] for r in self.fixture.table().get_json()['rows']}
        self.assertEqual(self.write(self.fixture.foreman).status_code, 200)

    def test_backup_failure_prevents_all_changes(self):
        before = self.bindings()
        for error in (OSError('disk full'), sqlite3.DatabaseError('backup failed')):
            with patch('gdlr_api.create_backup', side_effect=error):
                self.assertEqual(self.write().status_code, 503)
            self.assertEqual(self.bindings(), before)

    def test_invalid_and_inactive_targets(self):
        before = self.bindings()
        for changes in ({'worker_ids': []}, {'worker_ids': [True]}, {'category_id': True},
                        {'category_id': 999999}, {'expected_tokens': {}}, {'expected_group_tokens': {}}):
            self.assertEqual(self.write(**changes).status_code, 400)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE gdlr_categories SET active=0 WHERE id=?', (self.category,))
            db.commit()
        self.assertEqual(self.write().status_code, 400)
        self.assertEqual(self.bindings(), before)
