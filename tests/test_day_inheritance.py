import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class DayInheritanceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(temporary.name) / 'bootstrap.db'),
                'ADMIN_PASSWORD': 'inherit-test-password', 'SECRET_KEY': 'inherit-test-secret-at-least-thirty-two-characters'}):
            self.module = importlib.import_module('app')
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'test.db'
        self.app = self.module.app
        with self.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.foreman_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('inherit-f','unused','Прораб','foreman','now')").lastrowid
            self.viewer_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('inherit-v','unused','Просмотр','viewer','now')").lastrowid
            from gdlr_api import STAFFING_CATEGORY_NAMES
            category_name = STAFFING_CATEGORY_NAMES[0]
            category_id = db.execute('''INSERT INTO gdlr_categories
                (name,name_key,active,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES (?,?,1,1,'inherit-fixture',?,'now')''',
                (category_name, ' '.join(category_name.split()).casefold(), self.admin_id)).lastrowid
            self.crew_id = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Тестовая бригада',?,'now')", (self.foreman_id,)).lastrowid
            self.ids = []
            for number in ('inherit-1', 'inherit-2'):
                worker_id = db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Тестовый сотрудник',?)", (number,)).lastrowid
                self.ids.append(worker_id)
                db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                    VALUES (?,?,'inherit-fixture',?,'now')''', (worker_id, category_id, self.admin_id))
                db.execute('INSERT INTO crew_members VALUES (?,?)', (self.crew_id, worker_id))
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token)
                    VALUES ('2026-09-13','2 смена',1,?,'ЛГСС',?,'old',?,'old-assignment')''', (worker_id, self.foreman_id, self.crew_id))
                db.execute("INSERT INTO staffing_shifts VALUES ('2026-09-13',?,'2 смена','old-shift',?,'old')", (worker_id, self.admin_id))
                db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-13',?,'Больн','old-status',?,'old')", (worker_id, self.admin_id))
                db.execute("INSERT INTO staffing_performed_work VALUES ('2026-09-13',?,'2 смена','Монтаж трубопровода','old-work',?,'old')", (worker_id, self.admin_id))
            db.commit()
        self.admin = self.client(self.admin_id)

    def client(self, actor):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = actor
            session['csrf_token'] = 'inherit-csrf'
        return client

    def inherit(self, day='2026-09-14', client=None):
        return (client or self.admin).post('/api/staffing/inherit-previous-day',
            json={'date': day}, headers={'X-CSRF-Token': 'inherit-csrf'})

    def test_copies_whole_day_with_fresh_tokens_audit_and_backup(self):
        before = self.rows('assignments', '2026-09-13')
        result = self.inherit().get_json()
        self.assertEqual(result['status'], 'inherited')
        self.assertEqual((result['copied'], result['assignments'], result['shifts'], result['statuses']), (2, 2, 2, 2))
        after = self.rows('assignments')
        self.assertEqual([r['shift'] for r in after], ['2 смена', '2 смена'])
        self.assertEqual([r['subobject_id'] for r in after], [1, 1])
        self.assertTrue(all(r['edit_token'] != 'old-assignment' for r in after))
        self.assertEqual(before, self.rows('assignments', '2026-09-13'))
        self.assertTrue(all(r['status'] == 'Больн' and r['edit_token'] != 'old-status' for r in self.rows('staffing_attendance')))
        self.assertEqual({r['changed_by'] for r in self.rows('assignment_events')}, {self.admin_id})
        self.assertEqual(result['work_descriptions'], 2)
        self.assertTrue(all(r['description'] == 'Монтаж трубопровода' and r['edit_token'] != 'old-work'
                            for r in self.rows('staffing_performed_work')))
        backup = self.module.DATABASE_PATH.parent / 'backups' / result['backup']
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE work_date='2026-09-14'").fetchone()[0], 0)

    def rows(self, table, day='2026-09-14'):
        with self.app.app_context():
            return [dict(r) for r in self.module.get_db().execute(f'SELECT * FROM {table} WHERE work_date=? ORDER BY worker_id', (day,))]

    def test_repeat_and_explicitly_cleared_date_are_not_repopulated(self):
        self.assertEqual(self.inherit().get_json()['status'], 'inherited')
        before = self.rows('assignments')
        self.assertEqual(self.inherit().get_json()['status'], 'existing')
        self.assertEqual(before, self.rows('assignments'))
        with self.app.app_context():
            db = self.module.get_db()
            for table in ('assignments', 'staffing_shifts', 'staffing_attendance', 'staffing_performed_work', 'assignment_events'):
                db.execute(f"DELETE FROM {table} WHERE work_date='2026-09-14'")
            db.commit()
        self.assertEqual(self.inherit().get_json()['status'], 'existing')
        self.assertEqual(self.rows('assignments'), [])

    def test_concurrent_initializations_copy_once(self):
        clients = [self.client(self.admin_id), self.client(self.admin_id)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda client: self.inherit(client=client).get_json(), clients))
        self.assertEqual(sorted(r['status'] for r in responses), ['existing', 'inherited'])
        self.assertEqual(len(self.rows('assignments')), 2)
        self.assertEqual(len(self.rows('assignment_events')), 2)

    def test_any_saved_target_state_preserves_entire_date(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-14',?,'Вых','target-token',?,'target')", (self.ids[0], self.admin_id))
            db.commit()
        self.assertEqual(self.inherit().get_json()['status'], 'existing')
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.rows('staffing_attendance')[0]['edit_token'], 'target-token')

    def test_uses_exact_previous_calendar_day_and_get_does_not_copy(self):
        self.admin.get('/api/staffing?date=2026-09-14&shift=all')
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.inherit('2026-09-15').get_json()['status'], 'no_source')
        self.assertEqual(self.rows('assignments', '2026-09-15'), [])

    def test_target_with_only_work_description_is_preserved(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_performed_work VALUES ('2026-09-14',?,'1 смена','Сварка','target-work',?,'now')", (self.ids[0], self.admin_id))
            db.commit()
        self.assertEqual(self.inherit().get_json()['status'], 'existing')
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.rows('staffing_performed_work')[0]['description'], 'Сварка')

    def test_foreman_copies_only_owned_crews_and_viewer_csrf_are_blocked(self):
        with self.app.app_context():
            db = self.module.get_db()
            crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Чужая бригада',?,'now')", (self.admin_id,)).lastrowid
            db.execute('UPDATE crew_members SET crew_id=? WHERE worker_id=?', (crew, self.ids[1]))
            db.execute('UPDATE assignments SET crew_id=? WHERE worker_id=?', (crew, self.ids[1]))
            db.commit()
        response = self.inherit(client=self.client(self.foreman_id))
        self.assertEqual(response.get_json()['copied'], 1)
        self.assertEqual([r['worker_id'] for r in self.rows('assignments')], [self.ids[0]])
        self.assertEqual(self.inherit(client=self.client(self.viewer_id)).status_code, 403)
        self.assertEqual(self.admin.post('/api/staffing/inherit-previous-day', json={'date': '2026-09-16'}).status_code, 403)

    def test_inactive_workers_are_not_reintroduced(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (self.ids[1],))
            db.commit()
        self.assertEqual(self.inherit().get_json()['copied'], 1)
        self.assertEqual([r['worker_id'] for r in self.rows('assignments')], [self.ids[0]])

    def test_ambiguous_previous_shift_rejects_atomic_copy(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,created_at,crew_id)
                VALUES ('2026-09-13','Ночная смена',1,? ,?,'old',?)''', (self.ids[0], self.foreman_id, self.crew_id))
            db.commit()
        self.assertEqual(self.inherit().status_code, 409)
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.rows('staffing_attendance'), [])

    def test_invalid_input_and_backup_failure_do_not_copy(self):
        for value in ('invalid', '0001-01-01', ''):
            self.assertEqual(self.inherit(value).status_code, 400)
        with patch('day_inheritance.create_backup', side_effect=OSError('backup unavailable')):
            self.assertEqual(self.inherit().status_code, 503)
        self.assertEqual(self.rows('assignments'), [])
        self.assertEqual(self.rows('staffing_attendance'), [])

    def freshness(self, day='2026-09-14'):
        from day_inheritance import freshness_states
        with self.app.app_context():
            return freshness_states(self.module.get_db(), day, self.ids)

    def test_freshness_tracks_untouched_partial_and_fully_updated_fields(self):
        self.assertEqual({v['status'] for v in self.freshness().values()}, {'empty'})
        self.assertEqual({v['status'] for v in self.freshness('2026-09-13').values()}, {'current'})
        self.inherit()
        fresh = self.freshness()[self.ids[0]]
        self.assertEqual(fresh['status'], 'inherited')
        self.assertEqual(fresh['source_date'], '2026-09-13')
        with self.app.app_context():
            db = self.module.get_db()
            # Even saving the same value is a new revision of that field, not of the whole row.
            db.execute("UPDATE staffing_attendance SET edit_token='manual-status' WHERE work_date='2026-09-14' AND worker_id=?", (self.ids[0],))
            db.commit()
        fresh = self.freshness()[self.ids[0]]
        self.assertEqual(fresh['status'], 'mixed')
        self.assertEqual(fresh['updated_fields'], ['Статус явки'])
        self.assertIn('Смена', fresh['inherited_fields'])
        self.assertEqual(self.freshness()[self.ids[1]]['status'], 'inherited')
        with self.app.app_context():
            db = self.module.get_db()
            for table in ('assignments', 'staffing_shifts', 'staffing_performed_work'):
                db.execute(f"UPDATE {table} SET edit_token='manual' WHERE work_date='2026-09-14' AND worker_id=?", (self.ids[0],))
            db.commit()
        self.assertEqual(self.freshness()[self.ids[0]]['status'], 'current')

    def test_legacy_copy_without_row_history_is_unknown_and_read_only(self):
        self.inherit()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM staffing_inherited_rows')
            db.commit()
        before = self.rows('assignments')
        self.assertEqual({v['status'] for v in self.freshness().values()}, {'unknown'})
        self.assertEqual(before, self.rows('assignments'))

    def test_work_save_returns_updated_freshness_and_summary_matches_rows(self):
        from staffing_shifts import day_states, responsibility_states
        self.inherit()
        with self.app.app_context():
            db = self.module.get_db()
            daily = day_states(db, '2026-09-14', self.ids)
            groups = responsibility_states(db, self.ids)
            token = db.execute("SELECT edit_token FROM staffing_performed_work WHERE work_date='2026-09-14' AND worker_id=?", (self.ids[0],)).fetchone()[0]
        response = self.admin.put('/api/staffing/performed-work', json={'date': '2026-09-14',
            'worker_ids': [self.ids[0]], 'description': 'Сварка трубопровода',
            'expected_tokens': {str(self.ids[0]): token},
            'expected_day_tokens': {str(self.ids[0]): daily[self.ids[0]]['day_token']},
            'expected_group_tokens': {str(self.ids[0]): groups[self.ids[0]]['group_token']}},
            headers={'X-CSRF-Token': 'inherit-csrf'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['rows'][0]['freshness']['status'], 'mixed')
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE staffing_attendance SET status='Явка' WHERE work_date='2026-09-14'")
            db.commit()
        url = '/api/staffing?date=2026-09-14&shift=all&calendar_sites=1'
        rows = self.admin.get(url).get_json()['rows']
        index = self.admin.get(url + '&view=summary').get_json()['index']
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['id']: r['freshness'] for r in rows}, {r['id']: r['freshness'] for r in index})
