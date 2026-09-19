import unittest

import test_staffing as staffing_tests
from staffing_import import apply_attendance, parse_attendance


class EmployerTest(unittest.TestCase):
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
        self.headers = {'X-CSRF-Token': 'staffing-csrf'}

    def rows(self):
        return self.client.get('/api/staffing?date=2026-09-12&shift=all').json['rows']

    def payload(self, rows=None, name='ООО Новый работодатель'):
        rows = self.rows() if rows is None else rows
        return {'employer': name, 'worker_ids': [r['id'] for r in rows],
                'expected_tokens': {str(r['id']): r['employer_token'] for r in rows},
                'expected_crews': {str(r['id']): r['crew_id'] for r in rows},
                'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}

    def save(self, payload, client=None, headers=None):
        return (client or self.client).put('/api/staffing/groups/employer', json=payload,
                                         headers=self.headers if headers is None else headers)

    def replay(self, direction):
        action = self.client.get('/api/staffing/history').json[direction]
        return self.client.post('/api/staffing/history/' + direction,
            json={'id': action['id'], 'token': action['token']}, headers=self.headers)

    def test_persistent_correction_import_and_assignment_snapshots(self):
        row = next(r for r in self.rows() if r['personnel_no'] == '70001')
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at)
                VALUES ('2026-09-11','1 смена',1,?,'Прежний',?,'now')''', (row['id'], self.fixture.admin_id))
            db.commit()
        response = self.save(self.payload([row]))
        self.assertEqual(response.status_code, 200, response.json)
        with self.module.app.app_context():
            db = self.module.get_db()
            parsed = parse_attendance(staffing_tests.workbook_bytes([{'category': 'Другая категория'}]), 'повторный.xlsx')
            apply_attendance(db, parsed, self.fixture.admin_id, self.module.utc_now)
            self.assertEqual(db.execute('SELECT employer FROM workers WHERE id=?', (row['id'],)).fetchone()[0], 'ООО Новый работодатель')
            self.assertEqual(db.execute('SELECT employer FROM assignments WHERE worker_id=?', (row['id'],)).fetchone()[0], 'Прежний')
            correction = db.execute('SELECT * FROM employee_employers WHERE worker_id=?', (row['id'],)).fetchone()
            self.assertEqual(correction['source_employer'], row['employer'])
            self.assertEqual(correction['updated_by'], self.fixture.admin_id)
            self.assertTrue(correction['updated_at'])
        current = next(r for r in self.rows() if r['id'] == row['id'])
        response = self.fixture.write('/api/staffing/groups/assignments', {
            'date': '2026-09-12', 'subobject_id': 1, 'worker_ids': [row['id']],
            'expected_tokens': {str(row['id']): current['day_token']},
            'expected_crews': {str(row['id']): current['crew_id']},
            'expected_group_tokens': {str(row['id']): current['group_token']}})
        self.assertEqual(response.status_code, 200, response.json)
        with self.module.app.app_context():
            self.assertEqual(self.module.get_db().execute("SELECT employer FROM assignments WHERE worker_id=? AND work_date='2026-09-12'", (row['id'],)).fetchone()[0], 'ООО Новый работодатель')

    def test_selected_only_crewless_and_atomic_conflicts(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("DELETE FROM crew_members WHERE worker_id=(SELECT id FROM workers WHERE personnel_no='70003')")
            db.commit()
        before = self.rows()
        payload = self.payload(before[:2])
        payload['expected_tokens'][str(before[1]['id'])] = 'stale'
        self.assertEqual(self.save(payload).status_code, 409)
        self.assertEqual([r['employer'] for r in self.rows()], [r['employer'] for r in before])
        crewless = next(r for r in before if r['crew_id'] is None)
        self.assertEqual(self.save(self.payload([crewless])).status_code, 200)
        after = {r['id']: r for r in self.rows()}
        for row in before:
            self.assertEqual(after[row['id']]['employer'], 'ООО Новый работодатель' if row['id'] == crewless['id'] else row['employer'])

    def test_permissions_csrf_input_and_membership_guards(self):
        payload = self.payload([self.rows()[0]])
        self.assertEqual(self.save(payload, self.fixture.foreman).status_code, 403)
        self.assertEqual(self.save(payload, self.fixture.viewer).status_code, 403)
        self.assertEqual(self.save(payload, headers={}).status_code, 403)
        for name in ('', ' ', 'a' * 201, 'bad\x00name', 1, None):
            self.assertEqual(self.save({**payload, 'employer': name}).status_code, 400)
        self.assertEqual(self.save({**payload, 'expected_crews': {str(payload['worker_ids'][0]): None}}).status_code, 409)
        self.assertEqual(self.save({**payload, 'expected_group_tokens': {str(payload['worker_ids'][0]): 'stale'}}).status_code, 409)
        self.assertEqual(self.save(payload).status_code, 200)
        self.assertEqual(self.save(payload).status_code, 409)

    def test_undo_redo_restores_effective_value_and_rotates_token(self):
        row = self.rows()[0]
        self.assertEqual(self.save(self.payload([row])).status_code, 200)
        token = self.rows()[0]['employer_token']
        self.assertEqual(self.replay('undo').status_code, 200)
        self.assertEqual(self.rows()[0]['employer'], row['employer'])
        self.assertEqual(self.replay('redo').status_code, 200)
        self.assertEqual(self.rows()[0]['employer'], 'ООО Новый работодатель')
        self.assertNotEqual(self.rows()[0]['employer_token'], token)
        self.assertEqual(self.save(self.payload([self.rows()[0]], 'Другая организация')).status_code, 200)
        self.assertEqual(self.replay('undo').status_code, 200)
        self.assertEqual(self.rows()[0]['employer'], 'ООО Новый работодатель')

    def test_source_change_invalidates_initial_edit_and_redo(self):
        row = self.rows()[0]
        payload = self.payload([row])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET employer='Новый источник' WHERE id=?", (row['id'],))
            db.commit()
        self.assertEqual(self.save(payload).status_code, 409)
        self.assertEqual(self.save(self.payload([self.rows()[0]])).status_code, 200)
        self.assertEqual(self.replay('undo').status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET employer='Другой источник' WHERE id=?", (row['id'],))
            db.commit()
        self.assertEqual(self.replay('redo').status_code, 409)

    def test_employer_options_are_authenticated_and_never_cached_publicly(self):
        response = self.client.get('/api/staffing/employers')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertTrue(all(set(row) == {'name'} and row['name'].strip() for row in response.json['rows']))
        self.assertEqual(len(response.json['rows']), len({row['name'] for row in response.json['rows']}))
        self.assertIn(self.module.app.test_client().get('/api/staffing/employers').status_code, (302, 401))

    def test_foreman_own_crew_and_inactive_worker(self):
        row = next(r for r in self.rows() if r['personnel_no'] == '70001')
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.fixture.foreman_id, row['crew_id']))
            db.commit()
        row = next(r for r in self.rows() if r['id'] == row['id'])
        self.assertEqual(self.save(self.payload([row]), self.fixture.foreman).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET active=0 WHERE id=?", (row['id'],))
            db.commit()
        self.assertEqual(self.save(self.payload([row])).status_code, 409)
