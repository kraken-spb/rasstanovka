import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LogsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                                    'ADMIN_PASSWORD': 'logs-test-password-123',
                                    'SECRET_KEY': 'logs-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'logs.db'
        self.clients = {}
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            for role in ('admin', 'foreman', 'viewer'):
                user_id = self.admin_id if role == 'admin' else db.execute('''INSERT INTO users
                    (username,password_hash,full_name,role,created_at) VALUES (?,'unused',?,?, 'now')''',
                    (role, 'Пользователь ' + role, role)).lastrowid
                client = self.module.app.test_client()
                with client.session_transaction() as session:
                    session['user_id'] = user_id
                self.clients[role] = client
            self.worker = db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Иванов Тестовый','001%_')").lastrowid
            self.crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Бригада журнала',?,'now')", (self.admin_id,)).lastrowid
            self.sites = [row[0] for row in db.execute('SELECT id FROM subobjects LIMIT 2')]
            for before, after, changed in ((None, self.sites[0], '2026-09-12T20:59:59Z'),
                                          (self.sites[0], self.sites[1], '2026-09-12T21:00:00Z'),
                                          (self.sites[1], None, '2026-09-13T20:59:59Z'),
                                          (self.sites[1], self.sites[1], '2026-09-13T21:00:00Z')):
                db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,
                    before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-14','1 смена',?,?,?,?)''',
                    (self.crew, self.worker, before, after, self.admin_id, changed))
            db.commit()

    def get(self, **query):
        return self.clients['admin'].get('/api/logs', query_string=query)

    def test_admin_only_and_read_only(self):
        for role in ('foreman', 'viewer'):
            self.assertEqual(self.clients[role].get('/api/logs').status_code, 403)
            self.assertNotIn('id="view-logs"', self.clients[role].get('/').get_data(as_text=True))
        self.assertEqual(self.module.app.test_client().get('/api/logs').status_code, 401)
        self.assertIn('id="view-logs"', self.clients['admin'].get('/').get_data(as_text=True))
        self.assertEqual(self.get().get_json()['total'], 4)

    def test_super_admin_access_when_supported(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
            if 'super_admin' not in schema:
                self.skipTest('This deployment does not yet have the super_admin role')
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.admin_id,))
            db.commit()
        self.assertEqual(self.get().status_code, 200)
        self.assertIn('/static/logs.js', self.clients['admin'].get('/').get_data(as_text=True))

    def test_event_types_names_search_and_date_boundaries(self):
        rows = self.get().get_json()['rows']
        self.assertEqual([r['action'] for r in rows], ['update', 'clear', 'move', 'assign'])
        self.assertEqual(rows[0]['actor_id'], self.admin_id)
        self.assertEqual(rows[0]['worker_name'], 'Иванов Тестовый')
        self.assertTrue(rows[0]['after_group'])
        bounded = self.get(**{'from': '2026-09-13', 'to': '2026-09-13'}).get_json()
        self.assertEqual([r['action'] for r in bounded['rows']], ['clear', 'move'])
        self.assertEqual(self.get(q='ИВАНОВ').get_json()['total'], 4)
        self.assertEqual(self.get(q='%_').get_json()['total'], 4)
        self.assertEqual(self.get(q="' OR 1=1 --").get_json()['total'], 0)
        self.assertEqual(self.get(action='clear', work_date='2026-09-14', actor=self.admin_id).get_json()['total'], 1)
        self.assertEqual(self.get(work_date='2026-09-13').get_json()['total'], 0)

    def test_pagination_stable_and_empty(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.executemany('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,
                before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-14','1 смена',NULL,?,?,'2026-09-15T00:00:00Z')''',
                [(self.crew, self.worker, self.sites[0], self.admin_id)] * 55)
            db.commit()
        first, second = self.get().get_json(), self.get(page=2).get_json()
        self.assertEqual((first['total'], len(first['rows']), len(second['rows'])), (59, 50, 9))
        self.assertFalse({r['id'] for r in first['rows']} & {r['id'] for r in second['rows']})
        self.assertEqual(self.get(page=999).get_json()['page'], 2)
        empty = self.get(q='Несуществующий сотрудник').get_json()
        self.assertEqual((empty['rows'], empty['page'], empty['pages']), ([], 1, 1))

    def test_invalid_filters(self):
        for query in ({'from': '2026-13-01'}, {'from': '2026-09-15', 'to': '2026-09-13'},
                      {'to': '9999-12-31'}, {'work_date': '20260913'}, {'page': '-1'}, {'page': 'abc'},
                      {'actor': 'no'}, {'actor': '999999999999999999999999999'}, {'action': 'delete'}, {'q': 'x' * 201}):
            self.assertEqual(self.get(**query).status_code, 400, query)


if __name__ == '__main__':
    unittest.main()
