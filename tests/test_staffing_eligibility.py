import unittest

from tests import test_staffing as staffing_tests


class StaffingEligibilityTest(unittest.TestCase):
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

    @property
    def module(self):
        return self.fixture.module

    def create_nonstaffing_category(self, name='УРП: не для расстановки'):
        response = self.fixture.super_admin.post('/api/gdlr-categories', json={'name': name},
            headers={'X-CSRF-Token': 'staffing-csrf'})
        self.assertEqual(response.status_code, 201, response.get_json())
        catalog = self.fixture.super_admin.get('/api/gdlr-categories').get_json()['rows']
        return next(row for row in catalog if row['id'] == response.get_json()['id'])

    def bind(self, worker_id, category_id):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,'eligibility',?,'now')
                ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
                edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (worker_id, category_id, self.fixture.admin_id))
            db.commit()

    def test_new_catalog_category_is_visible_in_employees_but_excluded_from_staffing_after_reimport(self):
        employee = next(row for row in self.fixture.admin.get('/api/employees').get_json()['rows']
                        if row['personnel_no'] == '70001')
        category = self.create_nonstaffing_category()
        self.assertFalse(category['staffing_allowed'])
        self.bind(employee['id'], category['id'])

        employees = self.fixture.admin.get('/api/employees').get_json()['rows']
        self.assertEqual(next(row for row in employees if row['id'] == employee['id'])['category'], category['name'])
        self.assertNotIn(employee['id'], {row['id'] for row in self.fixture.table().get_json()['rows']})
        summary = self.fixture.admin.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()
        self.assertNotIn(employee['id'], {row['id'] for row in summary['index']})

        self.fixture.parsed = staffing_tests.parse_attendance(staffing_tests.workbook_bytes([
            {'category': category['name']}]), 'reimport.xlsx')
        self.fixture.apply()
        with self.module.app.app_context():
            row = self.module.get_db().execute('SELECT category_id FROM employee_gdlr WHERE worker_id=?',
                (employee['id'],)).fetchone()
        self.assertEqual(row['category_id'], category['id'])
        self.assertNotIn(employee['id'], {row['id'] for row in self.fixture.table().get_json()['rows']})

    def test_manual_and_outstaff_people_without_canonical_category_do_not_enter_staffing(self):
        before = {row['id'] for row in self.fixture.table().get_json()['rows']}
        with self.module.app.app_context():
            db = self.module.get_db()
            manual_id = db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Ручной без ГДЛР','MAN-ELIGIBILITY')").lastrowid
            db.execute("INSERT INTO manual_employees(worker_id,created_by,created_at,qualification,request_key,payload_hash) VALUES (?,?,'now','','eligibility-manual','hash')",
                       (manual_id, self.fixture.admin_id))
            outstaff_id = db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Аутстафф без ГДЛР','OUT-ELIGIBILITY')").lastrowid
            batch = db.execute("INSERT INTO outstaff_imports(request_key,filename,sheet,file_hash,selection_json,imported_by,imported_at,selected_count) VALUES ('eligibility-outstaff','out.xlsx','Аутстафф','hash','{}',?,'now',1)",
                               (self.fixture.admin_id,)).lastrowid
            db.execute("""INSERT INTO outstaff_members(worker_id,identity_key,import_id,source_row,source_department,
                source_category,work_kind,vendor,staff_type,arrival,departure,edit_token)
                VALUES (?, 'out-eligibility', ?, 2, '', '', '', '', '', '', '', 'token')""", (outstaff_id, batch))
            db.commit()

        rows = {row['id'] for row in self.fixture.table().get_json()['rows']}
        self.assertEqual(rows, before)
        employee_ids = {row['id'] for row in self.fixture.admin.get('/api/employees').get_json()['rows']}
        self.assertTrue({manual_id, outstaff_id}.issubset(employee_ids))

    def test_disallowed_worker_cannot_place_or_change_shift_but_existing_assignment_can_clear(self):
        row = self.fixture.table().get_json()['rows'][0]
        category = self.create_nonstaffing_category('УРП: блок назначения')
        self.bind(row['id'], category['id'])
        with self.module.app.app_context():
            db = self.module.get_db()
            crew_id = db.execute('SELECT crew_id FROM crew_members WHERE worker_id=?', (row['id'],)).fetchone()['crew_id']
            from staffing_shifts import day_states
            state = day_states(db, '2026-09-12', [row['id']])[row['id']]
            site_id = db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 1').fetchone()['id']
        payload = {'date': '2026-09-12', 'worker_ids': [row['id']],
                   'expected_tokens': {str(row['id']): state['day_token']}}
        for operation, extra in [('assignments', {'subobject_id': site_id}), ('shifts', {'shift': '2 смена'})]:
            response = self.fixture.write(f'/api/staffing/crews/{crew_id}/{operation}', {**payload, **extra})
            self.assertEqual(response.status_code, 409, response.get_json())
            self.assertIn('Категория ГДЛР', response.get_json()['error'])
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_shifts').fetchone()[0], 0)

        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token) VALUES ('2026-09-12','1 смена',?,?, '',?,'now',?,'token')",
                       (site_id, row['id'], self.fixture.admin_id, crew_id))
            from staffing_shifts import day_states
            state = day_states(db, '2026-09-12', [row['id']])[row['id']]
            db.commit()
        response = self.fixture.write('/api/staffing/assignments/clear', {**payload,
            'expected_tokens': {str(row['id']): state['day_token']},
            'expected_crews': {str(row['id']): crew_id}})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_source_text_and_inactive_catalog_do_not_grant_staffing_access(self):
        row = self.fixture.table().get_json()['rows'][0]
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM employee_gdlr WHERE worker_id=?', (row['id'],))
            from gdlr_api import STAFFING_CATEGORY_NAMES
            self.assertIn(db.execute('SELECT category FROM workers WHERE id=?', (row['id'],)).fetchone()[0], STAFFING_CATEGORY_NAMES)
            db.commit()
        self.assertNotIn(row['id'], {r['id'] for r in self.fixture.table().get_json()['rows']})
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE gdlr_categories SET active=0 WHERE id=?', (row['category_id'],))
            db.commit()
        self.assertEqual(self.fixture.table().get_json()['rows'], [])

    def test_client_cannot_enable_new_category_for_staffing(self):
        response = self.fixture.super_admin.post('/api/gdlr-categories',
            json={'name': 'Водитель вахты', 'staffing_allowed': True}, headers={'X-CSRF-Token': 'staffing-csrf'})
        self.assertEqual(response.status_code, 201)
        category = next(r for r in self.fixture.super_admin.get('/api/gdlr-categories').get_json()['rows']
                        if r['id'] == response.get_json()['id'])
        self.assertEqual(category['staffing_allowed'], 0)

    def test_category_change_invalidates_transfer_and_preserves_historical_fact(self):
        row = self.fixture.table().get_json()['rows'][0]
        with self.module.app.app_context():
            db = self.module.get_db()
            site = db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 1').fetchone()[0]
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,
                foreman_user_id,created_at,crew_id,edit_token) VALUES ('2026-09-11','1 смена',?,?,'',?,'now',?,'old')''',
                (site, row['id'], self.fixture.admin_id, row['crew_id']))
            db.commit()
        headers = {'X-CSRF-Token': 'staffing-csrf'}
        data = {'date': '2026-09-12', 'worker_ids': [row['id']]}
        old = self.fixture.admin.post('/api/staffing/transfer-selected/preview', json=data, headers=headers).get_json()
        self.assertEqual(old['ready'], 1)
        self.bind(row['id'], self.create_nonstaffing_category()['id'])
        response = self.fixture.admin.post('/api/staffing/transfer-selected',
            json={**data, 'expected_token': old['expected_token']}, headers=headers)
        self.assertEqual(response.status_code, 409, response.get_json())
        current = self.fixture.admin.post('/api/staffing/transfer-selected/preview', json=data, headers=headers).get_json()
        self.assertEqual(current['ready'], 0)
        self.assertIn('Категория ГДЛР', current['rows'][0]['reason'])
        inherited = self.fixture.admin.post('/api/staffing/inherit-previous-day', json={'date':'2026-09-12'}, headers=headers)
        self.assertEqual(inherited.status_code, 200, inherited.get_json())
        self.assertEqual(inherited.get_json()['assignments'], 0)
        history = self.fixture.admin.get('/api/placement-report?date=2026-09-11').get_json()
        self.assertIn(row['id'], {p['id'] for group in history['groups'] for p in group['people']})
        current_report = self.fixture.admin.get('/api/placement-report?date=2026-09-12').get_json()
        self.assertNotIn(row['id'], {p['id'] for group in current_report['groups'] for p in group['people']})

    def test_migration_marks_only_existing_approved_categories_and_runs_once(self):
        import sqlite3
        from gdlr_api import migrate_gdlr
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE workers(id INTEGER PRIMARY KEY);
            CREATE TABLE gdlr_categories(id INTEGER PRIMARY KEY,name TEXT,name_key TEXT,
                active INTEGER DEFAULT 1,edit_token TEXT,updated_by INTEGER,updated_at TEXT);
            INSERT INTO gdlr_categories(name,name_key) VALUES ('Монтажник ТТ','монтажник тт'),('Водитель вахты','водитель вахты');''')
        migrate_gdlr(db)
        self.assertEqual([r[0] for r in db.execute('SELECT staffing_allowed FROM gdlr_categories ORDER BY id')], [1, 0])
        db.execute('UPDATE gdlr_categories SET staffing_allowed=0 WHERE id=1')
        migrate_gdlr(db)
        self.assertEqual(db.execute('SELECT SUM(staffing_allowed) FROM gdlr_categories').fetchone()[0], 0)
