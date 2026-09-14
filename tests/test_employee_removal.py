import json
import unittest
from unittest.mock import patch

import test_staffing
from staffing_import import ImportProblem, apply_attendance, parse_attendance


class EmployeeRemovalTest(unittest.TestCase):
    setUpClass = classmethod(test_staffing.StaffingWorkflowTest.setUpClass.__func__)
    tearDownClass = classmethod(test_staffing.StaffingWorkflowTest.tearDownClass.__func__)
    client = test_staffing.StaffingWorkflowTest.client
    write = test_staffing.StaffingWorkflowTest.write
    apply = test_staffing.StaffingWorkflowTest.apply

    def setUp(self):
        test_staffing.StaffingWorkflowTest.setUp(self)
        self.apply()
        self.row = next(r for r in self.admin.get('/api/employees').get_json()['rows'] if r['personnel_no'] == '70001')
        self.url = f"/api/employees/{self.row['id']}"
        with self.app.app_context():
            db = self.module.get_db()
            site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
            for day in ['2026-09-11', '2026-09-12', '2026-09-13']:
                db.execute("""INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at)
                    VALUES (?,'1 смена',?,?,'ЛГСС',?,?)""", (day, site, self.row['id'], self.admin_id, self.module.utc_now()))
            db.commit()

    def preview(self, day='2026-09-12'):
        response = self.admin.get(self.url + '/removal-preview', query_string={'date': day})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def remove(self, payload=None, client=None):
        if payload is None:
            payload = {**self.preview(), 'reason': 'Увольнение по собственному желанию'}
        return (client or self.admin).delete(self.url, json=payload, headers={'X-CSRF-Token': 'staffing-csrf'})

    def test_deletion_preserves_history_logs_reason_actor_and_snapshot(self):
        preview = self.preview()
        self.assertEqual(preview['assignment_count'], 2)
        response = self.remove({**preview, 'reason': '  Увольнение по собственному желанию  '})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['cleared_assignments'], 2)
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT active FROM workers WHERE id=?', (self.row['id'],)).fetchone()[0], 0)
            self.assertEqual([r[0] for r in db.execute('SELECT work_date FROM assignments WHERE worker_id=?', (self.row['id'],))], ['2026-09-11'])
            record = db.execute('SELECT * FROM employee_removals WHERE worker_id=?', (self.row['id'],)).fetchone()
            self.assertEqual(len(json.loads(record['removed_assignments_json'])), 2)
            db.execute("UPDATE workers SET full_name='Новое имя' WHERE id=?", (self.row['id'],))
            db.commit()
        log = self.admin.get('/api/logs', query_string={'action': 'employee_delete', 'q': 'СОБСТВЕННОМУ'}).get_json()
        self.assertEqual(log['total'], 1)
        self.assertEqual(log['rows'][0]['worker_name'], self.row['full_name'])
        self.assertEqual(log['rows'][0]['reason'], 'Увольнение по собственному желанию')
        self.assertEqual(log['rows'][0]['actor_id'], self.admin_id)
        self.assertEqual(log['rows'][0]['work_date'], '2026-09-12')
        self.assertTrue(log['rows'][0]['changed_at'])
        self.assertEqual(self.remove({**preview, 'reason': 'Повтор'}).status_code, 409)
        self.assertEqual(self.admin.get('/api/logs?action=employee_delete').get_json()['total'], 1)
        rows = self.admin.get('/api/staffing?date=2026-09-12&shift=all').get_json()['rows']
        self.assertNotIn(self.row['id'], [r['id'] for r in rows])
        for day, count in [('2026-09-11', 1), ('2026-09-12', 0), ('2026-09-13', 0)]:
            calendar = self.admin.get('/api/calendar', query_string={'start': day, 'days': 1}).get_json()
            self.assertEqual(sum(f['day_count'] + f['night_count'] for f in calendar['facts']), count)

    def test_reason_permissions_csrf_and_date_are_required(self):
        payload = {**self.preview(), 'reason': 'Увольнение'}
        for value in ['', '   ', None, ['x'], 'x' * 1001]:
            self.assertEqual(self.remove({**payload, 'reason': value}).status_code, 400)
        for day in ['', 'invalid', '9999-12-31']:
            self.assertEqual(self.remove({**payload, 'date': day}).status_code, 400)
        for client in [self.foreman, self.viewer]:
            self.assertEqual(self.remove(payload, client).status_code, 403)
            self.assertEqual(client.get(self.url + '/removal-preview?date=2026-09-12').status_code, 403)
        self.assertEqual(self.admin.delete(self.url, json=payload).status_code, 403)
        self.assertEqual(self.admin.get('/api/logs?action=employee_delete').get_json()['total'], 0)

    def test_changed_assignment_invalidates_preview(self):
        payload = {**self.preview(), 'reason': 'Увольнение'}
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET edit_token='changed' WHERE worker_id=?", (self.row['id'],))
            db.commit()
        self.assertEqual(self.remove(payload).status_code, 409)
        fresh = next(r for r in self.admin.get('/api/employees').get_json()['rows'] if r['id'] == self.row['id'])
        self.assertEqual(fresh['active'], 1)
        self.assertEqual(self.admin.get('/api/logs?action=employee_delete').get_json()['total'], 0)

    def test_log_failure_rolls_back_removal(self):
        payload = {**self.preview(), 'reason': 'Увольнение'}
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("CREATE TRIGGER fail_removal BEFORE INSERT ON employee_removals BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            db.commit()
        with patch.dict(self.app.config, {'PROPAGATE_EXCEPTIONS': False}):
            self.assertEqual(self.remove(payload).status_code, 500)
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT active FROM workers WHERE id=?', (self.row['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignments WHERE worker_id=?', (self.row['id'],)).fetchone()[0], 3)

    def test_reimport_never_restores_removed_identity(self):
        self.assertEqual(self.remove().status_code, 200)
        parsed = parse_attendance(test_staffing.workbook_bytes([{'category': 'Новая категория'}, {'number': 'new-worker'}]), 'повтор.xlsx')
        with self.app.app_context():
            db = self.module.get_db()
            apply_attendance(db, parsed, self.admin_id, self.module.utc_now)
            self.assertEqual(db.execute('SELECT active FROM workers WHERE id=?', (self.row['id'],)).fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT 1 FROM workers WHERE personnel_no='new-worker'").fetchone())
            bad = parse_attendance(test_staffing.workbook_bytes([{'name': 'Другой сотрудник'}]), 'ошибка.xlsx')
            with self.assertRaises(ImportProblem):
                apply_attendance(db, bad, self.admin_id, self.module.utc_now)

    def test_absences_before_removal_remain_after_removal_hidden(self):
        with self.app.app_context():
            db = self.module.get_db()
            for day in ['2026-09-11', '2026-09-12']:
                db.execute("INSERT INTO staffing_attendance VALUES (?,?,'Вых','token',?,'now')", (day, self.row['id'], self.admin_id))
            db.commit()
        self.assertEqual(self.remove().status_code, 200)
        data = self.admin.get('/api/calendar?start=2026-09-11&days=3').get_json()
        self.assertEqual([r['work_date'] for r in data['absences']], ['2026-09-11'])
