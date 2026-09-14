import json
import unittest
from unittest.mock import patch

import test_employee_removal
import test_staffing
from staffing_import import apply_attendance, parse_attendance


class EmployeeRestoreTest(unittest.TestCase):
    setUpClass = classmethod(test_employee_removal.EmployeeRemovalTest.setUpClass.__func__)
    tearDownClass = classmethod(test_employee_removal.EmployeeRemovalTest.tearDownClass.__func__)
    setUp = test_employee_removal.EmployeeRemovalTest.setUp
    client = test_employee_removal.EmployeeRemovalTest.client
    write = test_employee_removal.EmployeeRemovalTest.write
    apply = test_employee_removal.EmployeeRemovalTest.apply
    preview = test_employee_removal.EmployeeRemovalTest.preview
    remove = test_employee_removal.EmployeeRemovalTest.remove

    def employee(self, client=None):
        return next(row for row in (client or self.admin).get('/api/employees').get_json()['rows'] if row['id'] == self.row['id'])

    def restore(self, token=None, client=None):
        if token is None:
            token = self.employee()['restoration_token']
        return (client or self.admin).post(self.url + '/restore', json={'expected_token': token},
                                         headers={'X-CSRF-Token': 'staffing-csrf'})

    def test_restore_retains_identity_relationships_and_full_audit_without_replaying_assignments(self):
        self.assertEqual(self.remove().status_code, 200)
        removed = self.employee()
        self.assertTrue(removed['can_restore'])
        with self.app.app_context():
            db = self.module.get_db()
            snapshot = dict(db.execute('SELECT * FROM employee_removals WHERE worker_id=?', (self.row['id'],)).fetchone())
            assignments = [dict(r) for r in db.execute('SELECT * FROM assignments WHERE worker_id=?', (self.row['id'],))]
        result = self.restore()
        self.assertEqual(result.status_code, 200, result.get_json())
        active = self.employee()
        for key in ['id', 'full_name', 'personnel_no', 'crew_id', 'category_id', 'department', 'pps']:
            self.assertEqual(active[key], removed[key], key)
        self.assertEqual(active['active'], 1)
        self.assertFalse(active['can_restore'])
        self.assertTrue(active['can_remove'])
        self.assertIsNone(active['removal'])
        with self.app.app_context():
            db = self.module.get_db()
            archive = db.execute('SELECT * FROM employee_restorations WHERE worker_id=?', (self.row['id'],)).fetchone()
            self.assertEqual(json.loads(archive['removal_json']), snapshot)
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM assignments WHERE worker_id=?', (self.row['id'],))], assignments)
        self.assertEqual(self.admin.get('/api/logs?action=employee_delete').get_json()['total'], 1)
        log = self.admin.get('/api/logs?action=employee_restore').get_json()
        self.assertEqual(log['total'], 1)
        self.assertEqual(log['rows'][0]['actor_id'], self.admin_id)
        self.assertEqual(log['rows'][0]['worker_name'], removed['full_name'])
        self.assertEqual(log['rows'][0]['work_date'], result.get_json()['date'])
        self.assertEqual(self.restore(removed['restoration_token']).status_code, 409)

    def test_repeated_cycles_retain_all_logs_and_reject_old_tokens(self):
        self.assertEqual(self.remove().status_code, 200)
        first = self.employee()['restoration_token']
        self.assertEqual(self.restore(first).status_code, 200)
        self.assertEqual(self.remove().status_code, 200)
        self.assertEqual(self.restore(first).status_code, 409)
        self.assertEqual(self.restore().status_code, 200)
        for action in ['employee_delete', 'employee_restore']:
            rows = self.admin.get('/api/logs?action=' + action).get_json()['rows']
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row['id'] for row in rows}), 2)

    def test_old_deletion_preview_cannot_disable_restored_employee(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM assignments WHERE worker_id=?', (self.row['id'],))
            db.commit()
        old_preview = {**self.preview(), 'reason': 'Удаление из старого окна'}
        self.assertEqual(self.remove(old_preview).status_code, 200)
        self.assertEqual(self.restore().status_code, 200)
        self.assertEqual(self.remove(old_preview).status_code, 409)
        self.assertEqual(self.employee()['active'], 1)

    def test_permissions_csrf_stale_tokens_and_audit_failure_are_atomic(self):
        self.assertEqual(self.remove().status_code, 200)
        token = self.employee()['restoration_token']
        self.assertFalse(self.employee(self.foreman)['can_restore'])
        for client in [self.foreman, self.viewer]:
            self.assertEqual(self.restore(token, client).status_code, 403)
        self.assertEqual(self.admin.post(self.url + '/restore', json={'expected_token': token}).status_code, 403)
        for payload in [None, {}, {'expected_token': 1}]:
            self.assertEqual(self.admin.post(self.url + '/restore', json=payload,
                headers={'X-CSRF-Token': 'staffing-csrf'}).status_code, 400)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET full_name='Изменённое имя' WHERE id=?", (self.row['id'],))
            db.commit()
        self.assertEqual(self.restore(token).status_code, 409)
        token = self.employee()['restoration_token']
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("CREATE TRIGGER fail_restore BEFORE INSERT ON employee_restorations BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
            db.commit()
        with patch.dict(self.app.config, {'PROPAGATE_EXCEPTIONS': False}):
            self.assertEqual(self.restore(token).status_code, 500)
        self.assertEqual(self.employee()['active'], 0)
        self.assertIsNotNone(self.employee()['removal'])
        self.assertEqual(self.admin.get('/api/logs?action=employee_restore').get_json()['total'], 0)

    def test_smu_scope_is_enforced_and_super_admin_can_restore(self):
        self.assertEqual(self.remove().status_code, 200)
        token = self.employee()['restoration_token']
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at)
                VALUES (?,'selected','[]','scope',?,'now')''', (self.admin_id, self.admin_id))
            db.commit()
        self.assertFalse(self.employee()['can_restore'])
        self.assertEqual(self.restore(token).status_code, 403)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.admin_id,))
            db.commit()
        self.assertTrue(self.employee()['can_restore'])
        self.assertEqual(self.restore(token).status_code, 200)

    def test_legacy_disabled_worker_without_import_or_crew_appears_in_staffing(self):
        with self.app.app_context():
            db = self.module.get_db()
            worker = db.execute("INSERT INTO workers(full_name,personnel_no,active) VALUES ('Отключённый вручную','restore-only',0)").lastrowid
            db.commit()
        self.row = {'id': worker}
        self.url = f'/api/employees/{worker}'
        self.assertEqual(self.restore().status_code, 200)
        with self.app.app_context():
            db = self.module.get_db()
            self.assertIsNone(db.execute('SELECT removal_json FROM employee_restorations WHERE worker_id=?', (worker,)).fetchone()[0])
            self.assertIsNone(db.execute('SELECT 1 FROM crew_members WHERE worker_id=?', (worker,)).fetchone())
        rows = self.admin.get('/api/staffing?date=2026-09-14&shift=all').get_json()['rows']
        row = next(row for row in rows if row['id'] == worker)
        self.assertFalse(row['locked'])
        self.assertIsNone(row['crew_id'])

    def test_reimport_while_removed_does_not_hide_restored_worker_or_change_import_provenance(self):
        self.assertEqual(self.remove().status_code, 200)
        with self.app.app_context():
            db = self.module.get_db()
            parsed = parse_attendance(test_staffing.workbook_bytes([{'category': 'Новая категория'}]), 'после удаления.xlsx')
            applied = apply_attendance(db, parsed, self.admin_id, self.module.utc_now)
            self.assertIsNone(db.execute('SELECT 1 FROM staffing_import_members WHERE import_id=? AND worker_id=?',
                (applied['id'], self.row['id'])).fetchone())
        self.assertEqual(self.restore().status_code, 200)
        rows = self.admin.get('/api/staffing?date=2026-09-14&shift=all').get_json()['rows']
        self.assertEqual(sum(r['id'] == self.row['id'] for r in rows), 1)
        with self.app.app_context():
            db = self.module.get_db()
            self.assertIsNone(db.execute('SELECT 1 FROM staffing_import_members WHERE import_id=? AND worker_id=?',
                (applied['id'], self.row['id'])).fetchone())

    def test_absences_during_dismissal_remain_hidden_after_restoration(self):
        with self.app.app_context():
            db = self.module.get_db()
            for day in ['2026-09-11', '2026-09-12']:
                db.execute("INSERT INTO staffing_attendance VALUES (?,?,'Вых','token',?,'now')", (day, self.row['id'], self.admin_id))
            db.commit()
        self.assertEqual(self.remove().status_code, 200)
        self.assertEqual(self.restore().status_code, 200)
        calendar = self.admin.get('/api/calendar?start=2026-09-11&days=2').get_json()
        self.assertEqual([r['work_date'] for r in calendar['absences']], ['2026-09-11'])
