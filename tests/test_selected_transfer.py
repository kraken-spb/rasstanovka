import unittest
from unittest.mock import patch
import test_day_inheritance


class SelectedTransferTest(unittest.TestCase):
    setUp = test_day_inheritance.DayInheritanceTest.setUp
    client = test_day_inheritance.DayInheritanceTest.client
    rows = test_day_inheritance.DayInheritanceTest.rows

    def preview(self, ids=None, client=None, day='2026-09-14'):
        return (client or self.admin).post('/api/staffing/transfer-selected/preview',
            json={'date': day, 'worker_ids': ids if ids is not None else self.ids}, headers={'X-CSRF-Token': 'inherit-csrf'})

    def transfer(self, plan, ids=None, client=None):
        return (client or self.admin).post('/api/staffing/transfer-selected',
            json={'date': plan['date'], 'worker_ids': ids if ids is not None else self.ids,
                  'expected_token': plan['expected_token']}, headers={'X-CSRF-Token': 'inherit-csrf'})

    def test_preview_readonly_and_copy_only_checked_with_backup_and_provenance(self):
        before = self.rows('assignments', '2026-09-13')
        plan = self.preview(self.ids[:1]).json
        self.assertEqual(plan['ready'], 1)
        self.assertEqual(self.rows('assignments'), [])
        with patch('selected_transfer.create_backup', wraps=__import__('backup_api').create_backup) as backup:
            result = self.transfer(plan, self.ids[:1])
            self.assertEqual(result.status_code, 200, result.json)
            backup.assert_called_once()
        self.assertEqual([r['worker_id'] for r in self.rows('assignments')], self.ids[:1])
        self.assertEqual(before, self.rows('assignments', '2026-09-13'))
        self.assertEqual(self.rows('staffing_attendance')[0]['status'], 'Больн')
        self.assertEqual(self.rows('staffing_performed_work')[0]['description'], 'Монтаж трубопровода')
        self.assertEqual(self.rows('staffing_shifts')[0]['shift'], '2 смена')
        self.assertEqual(self.rows('assignment_events')[0]['changed_by'], self.admin_id)
        from day_inheritance import freshness_states
        with self.app.app_context():
            self.assertEqual(freshness_states(self.module.get_db(), '2026-09-14', self.ids)[self.ids[0]]['status'], 'inherited')
        self.assertEqual(self.transfer(plan, self.ids[:1]).status_code, 409)
        self.assertEqual(len(self.rows('assignments')), 1)

    def test_partial_current_day_skips_occupied_and_preserves_explicit_clear(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_performed_work(work_date,worker_id,shift,description,edit_token,updated_by,updated_at) VALUES ('2026-09-14',?,'1 смена','','cleared',?,'now')", (self.ids[0], self.admin_id))
            db.commit()
        plan = self.preview().json
        self.assertEqual((plan['ready'], plan['skipped']), (1, 1))
        self.assertEqual(self.transfer(plan).json['copied'], 1)
        self.assertEqual([r['worker_id'] for r in self.rows('assignments')], self.ids[1:])
        self.assertEqual(self.rows('staffing_performed_work')[0]['edit_token'], 'cleared')

    def test_source_and_target_changes_invalidate_whole_preview(self):
        for table,day in [('assignments', '2026-09-13'), ('staffing_performed_work', '2026-09-13')]:
            plan = self.preview().json
            with self.app.app_context():
                db = self.module.get_db()
                db.execute(f'UPDATE {table} SET edit_token=? WHERE work_date=? AND worker_id=?', ('changed-'+table, day, self.ids[-1]))
                db.commit()
            self.assertEqual(self.transfer(plan).status_code, 409)
            self.assertEqual(self.rows('assignments'), [])
        plan = self.preview().json
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-14',?,'Явка','current',?,'now')", (self.ids[-1], self.admin_id))
            db.commit()
        self.assertEqual(self.transfer(plan).status_code, 409)
        self.assertEqual(self.rows('assignments'), [])

    def test_permissions_invalid_payload_and_backup_failure(self):
        self.assertEqual(self.preview(client=self.client(self.viewer_id)).status_code, 403)
        self.assertEqual(self.admin.post('/api/staffing/transfer-selected/preview', json={'date': '2026-09-14','worker_ids': self.ids}).status_code, 403)
        for ids in [[], [True], [-1]]:
            self.assertEqual(self.preview(ids).status_code, 400)
        self.assertEqual(self.preview(day='bad').status_code, 400)
        self.assertEqual(self.preview(day='0001-01-01').status_code, 400)
        self.assertEqual(self.preview([999999]).status_code, 409)
        plan = self.preview(client=self.client(self.foreman_id)).json
        with patch('selected_transfer.create_backup', side_effect=OSError('unavailable')):
            self.assertEqual(self.transfer(plan, client=self.client(self.foreman_id)).status_code, 503)
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.transfer(plan, client=self.client(self.foreman_id)).status_code, 200)

    def test_no_source_inactive_moved_and_foreign_workers(self):
        self.assertEqual(self.preview(day='2026-09-16').json['ready'], 0)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (self.ids[0],))
            db.execute('DELETE FROM crew_members WHERE worker_id=?', (self.ids[1],))
            db.commit()
        self.assertEqual(self.preview().json['ready'], 0)
        self.assertEqual(self.preview(client=self.client(self.foreman_id)).status_code, 403)

    def test_cleared_assignment_audit_prevents_recreation(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at)
                VALUES (?,?,'2026-09-14','2 смена',1,NULL,?,'now')''', (self.crew_id,self.ids[0],self.admin_id))
            db.commit()
        plan = self.preview().json
        self.assertEqual((plan['ready'],plan['skipped']), (1,1))
        self.assertEqual(self.transfer(plan).json['copied'],1)
