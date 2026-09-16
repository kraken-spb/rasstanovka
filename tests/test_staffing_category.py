import unittest

import test_staffing as staffing_tests


class StaffingCategoryTest(unittest.TestCase):
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
        self.client = self.fixture.admin
        with self.module.app.app_context():
            db = self.module.get_db()
            self.category_id = db.execute('''INSERT INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES ('Новая категория','новая категория',1,'catalog-token',?,'now')''', (self.fixture.admin_id,)).lastrowid
            db.commit()
        self.row = next(row for row in self.fixture.table().get_json()['rows'] if row['personnel_no'] == '70001')
        self.url = f"/api/staffing/workers/{self.row['id']}/category"
        self.payload = {'category_id': self.category_id, 'category_token': 'catalog-token',
                        'expected_token': self.row['category_binding_token'], 'expected_crew_id': self.row['crew_id']}

    def write(self, **changes):
        return self.fixture.write(self.url, {**self.payload, **changes})

    def test_change_updates_staffing_index_directory_and_survives_import(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,crew_id,created_at)
                VALUES ('2026-09-12','1 смена',?,?,?,?,'now')''',
                       (site, self.row['id'], self.fixture.admin_id, self.row['crew_id']))
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM assignments')]
        self.assertEqual(self.write().status_code, 200)
        self.assertEqual(self.write().status_code, 409)
        self.fixture.apply()
        updated = next(row for row in self.fixture.table().get_json()['rows'] if row['id'] == self.row['id'])
        self.assertEqual(updated['category'], 'Новая категория')
        self.assertEqual(updated['source_category'], self.row['source_category'])
        self.assertEqual(updated['category_id'], self.category_id)
        self.assertNotEqual(updated['category_binding_token'], self.row['category_binding_token'])
        summary = self.client.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()['index']
        self.assertEqual(next(row for row in summary if row['id'] == self.row['id'])['category'], 'Новая категория')
        employee = next(row for row in self.client.get('/api/employees').get_json()['rows'] if row['id'] == self.row['id'])
        self.assertEqual(employee['category_id'], self.category_id)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM assignments')])

    def test_foreman_owner_csrf_and_disabled_worker(self):
        self.assertEqual(self.client.put(self.url, json=self.payload).status_code, 403)
        self.assertEqual(self.fixture.write(self.url, self.payload, self.fixture.viewer).status_code, 403)
        self.assertEqual(self.fixture.write(self.url, self.payload, self.fixture.foreman).status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.fixture.foreman_id, self.row['crew_id']))
            db.commit()
        self.assertEqual(self.fixture.write(self.url, self.payload, self.fixture.foreman).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (self.row['id'],))
            db.commit()
        self.assertEqual(self.write().status_code, 409)

    def test_stale_membership_catalog_and_invalid_category_never_write(self):
        for changes, code in (({'expected_crew_id': -1}, 409), ({'expected_token': 'stale'}, 409),
                              ({'category_token': 'stale'}, 409), ({'category_id': 999999}, 400),
                              ({'category_id': True}, 400), ({'category_id': None}, 400)):
            self.assertEqual(self.write(**changes).status_code, code)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE gdlr_categories SET active=0 WHERE id=?', (self.category_id,))
            db.commit()
        self.assertEqual(self.write().status_code, 400)
        with self.module.app.app_context():
            self.assertEqual(self.module.get_db().execute('SELECT category_id FROM employee_gdlr WHERE worker_id=?',
                (self.row['id'],)).fetchone()[0], self.row['category_id'])

    def test_employee_screen_change_invalidates_staffing_editor(self):
        employee = next(row for row in self.client.get('/api/employees').get_json()['rows'] if row['id'] == self.row['id'])
        response = self.fixture.write(f"/api/employees/{self.row['id']}/category", {
            'category_id': self.category_id, 'category_token': 'catalog-token', 'expected_token': employee['membership_token']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.write().status_code, 409)
