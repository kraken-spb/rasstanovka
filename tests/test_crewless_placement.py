import json
import unittest
from unittest.mock import patch

import test_manual_employees as manual_tests


class CrewlessPlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manual_tests.ManualEmployeeTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        manual_tests.ManualEmployeeTest.tearDownClass()

    def setUp(self):
        self.manual = manual_tests.ManualEmployeeTest()
        self.manual.setUp()
        self.addCleanup(self.manual.doCleanups)
        self.f = self.manual.f
        self.module = self.f.module
        self.client = self.f.admin
        self.headers = {'X-CSRF-Token': 'crews-csrf'}
        # Reuse the authenticated fixture's CSRF value rather than its password.
        with self.client.session_transaction() as session:
            self.headers['X-CSRF-Token'] = session['csrf_token']
        self.ids = []
        for n, department in enumerate(('СМУ 13.2', 'СМУ 19.1')):
            response = self.manual.create(self.manual.payload(crew_id=None, department=department,
                full_name='Ручной сотрудник ' + str(n), personnel_no='CREWLESS-' + str(n)))
            self.assertEqual(response.status_code, 201, response.json)
            self.ids.append(response.json['id'])

    def rows(self, client=None, day='2026-09-13'):
        return (client or self.client).get('/api/staffing?date=' + day + '&shift=all').json['rows']

    def body(self, ids=None, client=None):
        selected = [r for r in self.rows(client) if r['id'] in (ids or self.ids)]
        return {'date': '2026-09-13', 'worker_ids': [r['id'] for r in selected],
            'expected_crews': {str(r['id']): r['crew_id'] for r in selected},
            'expected_tokens': {str(r['id']): r['day_token'] for r in selected},
            'expected_group_tokens': {str(r['id']): r['group_token'] for r in selected}}

    def place(self, body=None, client=None, site=None):
        return (client or self.client).put('/api/staffing/groups/assignments',
            json={**(body or self.body()), 'subobject_id': self.f.sites[0] if site is None else site}, headers=self.headers)

    def scope(self, user_id, departments):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO user_smu_access VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                mode=excluded.mode,departments_json=excluded.departments_json''',
                (user_id, 'selected', json.dumps(departments, ensure_ascii=False), 'test', self.f.admin_id, 'now'))
            db.commit()

    def test_place_shift_clear_audit_and_no_synthetic_brigades(self):
        with self.module.app.app_context():
            before = [tuple(r) for r in self.module.get_db().execute('SELECT * FROM crews')]
        self.assertTrue(all(r['crew_id'] is None and not r['locked'] for r in self.rows()))
        self.assertEqual(self.place().status_code, 200)
        rows = self.rows()
        self.assertTrue(all(r['assignment_author']['user_id'] == self.f.admin_id for r in rows))
        shift = self.client.put('/api/staffing/groups/shifts', json={**self.body(), 'shift': '2 смена'}, headers=self.headers)
        self.assertEqual(shift.status_code, 200, shift.json)
        self.assertTrue(all(r['employee_shift'] == '2 смена' and r['subobject_id'] == self.f.sites[0] for r in self.rows()))
        logs = self.client.get('/api/logs?work_date=2026-09-13').json
        self.assertTrue(any(r['worker_id'] == self.ids[0] and r['crew_id'] is None for r in logs['rows']))
        result = self.client.put('/api/staffing/assignments/clear', json=self.body(), headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(all(r['assignment_id'] is None and r['employee_shift'] == '2 смена' for r in self.rows()))
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM crews')], before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM crew_members WHERE worker_id IN (?,?)', self.ids).fetchone()[0], 0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_smu_scope_roles_csrf_and_atomic_mixed_selection(self):
        foreign = self.body()
        self.scope(self.f.owner_a, ['СМУ 13.2'])
        self.assertEqual(self.place(self.body([self.ids[0]], self.f.foreman_a), self.f.foreman_a).status_code, 200)
        self.assertEqual(self.place(foreign, self.f.foreman_a).status_code, 403)
        self.assertEqual(self.place(self.body(), self.f.foreman_b).status_code, 403)
        self.assertEqual(self.place(self.body(), self.f.viewer).status_code, 403)
        self.assertEqual(self.client.put('/api/staffing/groups/assignments', json={**self.body(), 'subobject_id': self.f.sites[0]}).status_code, 403)
        self.scope(self.f.admin_id, ['СМУ 13.2'])
        self.assertEqual(self.place(foreign).status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignments WHERE worker_id=?', (self.ids[1],)).fetchone()[0], 0)

    def test_stale_membership_day_invalid_snapshot_and_foreign_assignment(self):
        before = self.body([self.ids[0]])
        missing = {**before, 'expected_crews': {}}
        self.assertEqual(self.place(missing).status_code, 400)
        self.assertEqual(self.place({**before, 'expected_crews': {str(self.ids[0]): False}}).status_code, 400)
        self.assertEqual(self.place(before).status_code, 200)
        self.assertEqual(self.place(before, site=self.f.sites[1]).status_code, 409)
        fresh = self.body([self.ids[0]])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.f.crew_a, self.ids[0])); db.commit()
        self.assertEqual(self.place(fresh).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM crew_members WHERE worker_id=?', (self.ids[0],))
            db.execute('UPDATE assignments SET crew_id=? WHERE worker_id=?', (self.f.crew_a, self.ids[0])); db.commit()
        row = next(r for r in self.rows() if r['id'] == self.ids[0])
        self.assertTrue(row['locked'])
        self.assertEqual(self.place(self.body([self.ids[0]])).status_code, 409)

    def test_transfer_tomorrow_and_undo_redo_with_null_crew(self):
        self.assertEqual(self.place().status_code, 200)
        action = self.client.get('/api/staffing/history').json['undo']
        result = self.client.post('/api/staffing/history/undo', json={'id': action['id'], 'token': action['token']}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(all(r['assignment_id'] is None for r in self.rows()))
        action = self.client.get('/api/staffing/history').json['redo']
        self.assertEqual(self.client.post('/api/staffing/history/redo', json={'id': action['id'], 'token': action['token']}, headers=self.headers).status_code, 200)
        body = {'date': '2026-09-14', 'worker_ids': self.ids}
        preview = self.client.post('/api/staffing/transfer-selected/preview', json=body, headers=self.headers)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['ready'], 2)
        copied = self.client.post('/api/staffing/transfer-selected',
            json={**body, 'expected_token': preview.json['expected_token']}, headers=self.headers)
        self.assertEqual(copied.status_code, 200, copied.json)
        self.assertEqual(copied.json['copied'], 2)
        self.assertTrue(all(r['crew_id'] is None and r['subobject_id'] == self.f.sites[0] for r in self.rows(day='2026-09-14')))

    def test_legacy_event_migration_preserves_rows_index_and_backup_failure_is_atomic(self):
        from staffing_shifts import migrate_crewless_assignments
        with self.module.app.app_context():
            db = self.module.get_db()
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='assignment_events'").fetchone()[0]
            db.execute('DROP TABLE assignment_events')
            db.execute(schema.replace('crew_id INTEGER REFERENCES', 'crew_id INTEGER NOT NULL REFERENCES'))
            db.execute('CREATE INDEX test_audit_worker ON assignment_events(worker_id)')
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at)
                VALUES (?,?,'2026-09-12','1 смена',NULL,?,?,'now')''', (self.f.crew_a, self.ids[0], self.f.sites[0], self.f.admin_id))
            db.commit()
            before = [tuple(r) for r in db.execute('SELECT * FROM assignment_events')]
            with patch('backup_api.create_backup', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    migrate_crewless_assignments(db)
            self.assertEqual(next(r for r in db.execute('PRAGMA table_info(assignment_events)') if r[1] == 'crew_id')[3], 1)
            migrate_crewless_assignments(db)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM assignment_events')], before)
            self.assertEqual(next(r for r in db.execute('PRAGMA table_info(assignment_events)') if r[1] == 'crew_id')[3], 0)
            self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='test_audit_worker'").fetchone())
            with patch('backup_api.create_backup') as backup:
                migrate_crewless_assignments(db)
                backup.assert_not_called()
