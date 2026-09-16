import json
import unittest
from uuid import uuid4

import test_crews as crew_tests


class ManualEmployeeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        crew_tests.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        crew_tests.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.f = crew_tests.CrewWorkflowTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def payload(self, **fields):
        return {'request_key': str(uuid4()), 'full_name': 'Иванов Иван Иванович', 'personnel_no': 'MANUAL-001',
                'profession': 'Монтажник', 'qualification': 'Рабочие', 'crew_id': self.f.crew_a,
                'category_id': self.f.staffing_category_id,
                'category_token': self.f.staffing_category_token, **fields}

    def create(self, data, client=None):
        return self.f.write(client or self.f.admin, 'POST', '/api/employees', data)

    def staffing(self):
        response = self.f.admin.get('/api/staffing?date=2026-09-13&shift=all')
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_added_employee_is_available_without_import_and_can_be_placed(self):
        data = self.payload()
        response = self.create(data)
        self.assertEqual(response.status_code, 201, response.get_json())
        worker = response.get_json()['id']
        rows = self.staffing()['rows']
        self.assertEqual([r['id'] for r in rows], [worker])
        self.assertEqual(rows[0]['crew_id'], self.f.crew_a)
        people = self.f.admin.get('/api/employees?date=2026-09-13').get_json()['rows']
        person = next(r for r in people if r['id'] == worker)
        self.assertTrue(person['is_worker'])
        self.assertIsNotNone(person['manual_registration'])
        self.assertEqual(self.f.place([worker], self.f.sites[0], work_date='2026-09-13').status_code, 200)
        self.assertIsNotNone(self.staffing()['rows'][0]['assignment_id'])

    def test_retry_internal_number_and_duplicate_personnel_are_safe(self):
        data = self.payload(personnel_no='', internal_number=True, crew_id=None)
        first = self.create(data)
        self.assertEqual(first.status_code, 201)
        repeated = self.create(data)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(first.get_json()['id'], repeated.get_json()['id'])
        self.assertTrue(first.get_json()['personnel_no'].startswith('MAN-'))
        self.assertEqual(self.create({**data, 'full_name': 'Другой человек'}).status_code, 409)
        self.assertEqual(self.create(self.payload(personnel_no=first.get_json()['personnel_no'].lower())).status_code, 409)
        self.assertIsNone(self.staffing()['rows'][0]['crew_id'])

    def test_manual_record_survives_new_import_and_has_no_duplicate(self):
        worker = self.create(self.payload()).get_json()['id']
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            batch = db.execute("INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json) VALUES ('manual-test','Тест.xlsx',?,'now',1,'{}')", (self.f.admin_id,)).lastrowid
            db.execute("INSERT INTO staffing_import_members VALUES (?,?,2,'Бригада из файла')", (batch, worker))
            db.commit()
        self.assertEqual([r['id'] for r in self.staffing()['rows']], [worker])
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute("INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json) VALUES ('manual-test-2','Следующий.xlsx',?,'later',0,'{}')", (self.f.admin_id,))
            db.commit()
        self.assertEqual([r['id'] for r in self.staffing()['rows']], [worker])

    def test_catalog_bindings_are_atomic_and_check_stale_values(self):
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            category = db.execute("INSERT INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at) VALUES ('Монтажники','монтажники',1,'current',?,'now')", (self.f.admin_id,)).lastrowid
            contractor = db.execute("INSERT INTO contractors(name,name_key,edit_token,updated_by,updated_at) VALUES ('ЛГСС','лгсс','current',?,'now')", (self.f.admin_id,)).lastrowid
            db.commit()
        data = self.payload(category_id=category, category_token='stale', contractor_id=contractor, contractor_token='current')
        self.assertEqual(self.create(data).status_code, 409)
        data['category_token'] = 'current'
        result = self.create(data)
        self.assertEqual(result.status_code, 201)
        row = self.staffing()['rows'][0]
        self.assertEqual((row['category'], row['contractor']), ('Монтажники', 'ЛГСС'))

    def test_permissions_csrf_and_validation(self):
        for client in (self.f.foreman_a, self.f.viewer):
            self.assertEqual(self.create(self.payload(), client).status_code, 403)
            self.assertEqual(client.get('/api/employees/create-options').status_code, 403)
            self.assertNotIn('id="employees-create"', client.get('/').get_data(as_text=True))
        self.assertEqual(self.f.admin.post('/api/employees', json=self.payload()).status_code, 403)
        for fields in ({'full_name': ''}, {'personnel_no': ''}, {'crew_id': True}, {'request_key': 'bad'}, {'full_name': 'ФИО\nдругое'}, {'internal_number': True}):
            self.assertEqual(self.create(self.payload(**fields)).status_code, 400)

    def test_explicit_department_restrictions_roll_back_new_worker(self):
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='user_smu_access'").fetchone():
                self.skipTest('Explicit SMU scopes are not part of this release')
            db.execute('INSERT INTO user_smu_access VALUES (?,?,?,?,?,?)', (self.f.admin_id, 'selected', json.dumps(['Разрешённое СМУ']), 'token', self.f.admin_id, 'now'))
            db.commit()
        self.assertEqual(self.create(self.payload(department='Чужое СМУ', crew_id=None)).status_code, 403)
        self.assertEqual(self.create(self.payload(department='Разрешённое СМУ', crew_id=None)).status_code, 201)
