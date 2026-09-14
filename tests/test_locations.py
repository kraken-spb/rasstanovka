import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LocationCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                                    'ADMIN_PASSWORD': 'locations-test-password-123',
                                    'SECRET_KEY': 'locations-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'locations.db'
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.admin_id,))
            self.clients = {}
            for role in ('admin', 'foreman', 'viewer'):
                user = self.admin_id if role == 'admin' else db.execute('''INSERT INTO users
                    (username,password_hash,full_name,role,created_at) VALUES (?,'unused',?,?,?)''',
                    (role, role, role, self.module.utc_now())).lastrowid
                client = self.module.app.test_client()
                with client.session_transaction() as session:
                    session['user_id'] = user
                    session['csrf_token'] = 'locations-csrf'
                self.clients[role] = client
            db.commit()
        self.admin = self.clients['admin']

    def write(self, method, suffix, data, role='admin'):
        return self.clients[role].open('/api/locations/' + suffix, method=method, json=data,
                                       headers={'X-CSRF-Token': 'locations-csrf'})

    def create(self, kind, name, **extra):
        response = self.write('POST', kind, {'name': name, **extra})
        self.assertEqual(response.status_code, 201, response.get_json())
        return self.row(kind, response.get_json()['id'])

    def row(self, kind, location_id):
        return next(row for row in self.admin.get('/api/locations').get_json()[kind] if row['id'] == location_id)

    def test_admin_and_csrf_required(self):
        group = self.create('objects', 'Проверка доступа')
        for role in ('foreman', 'viewer'):
            self.assertEqual(self.clients[role].get('/api/locations').status_code, 403)
            for method, suffix in [('POST', 'objects'), ('PATCH', f"objects/{group['id']}")]:
                self.assertEqual(self.write(method, suffix, {'name': 'Новое'}, role).status_code, 403)
        self.assertEqual(self.admin.post('/api/locations/objects', json={'name': 'Новое'}).status_code, 403)
        self.assertEqual(self.admin.patch(f"/api/locations/objects/{group['id']}", json={'name': 'Новое'}).status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.admin_id,)); db.commit()
        self.assertNotEqual(self.write('POST', 'objects', {'name': 'Отключённый'}).status_code, 201)

    def test_names_scoped_uniqueness_and_validation(self):
        group = self.create('objects', '  Цех   Северный ')
        self.assertEqual(group['name'], 'Цех Северный')
        self.assertEqual(self.write('POST', 'objects', {'name': 'цех северный'}).status_code, 409)
        other = self.create('objects', 'Цех Южный')
        self.create('subobjects', 'Насосная', object_id=group['id'])
        self.create('subobjects', 'Насосная', object_id=other['id'])
        self.assertEqual(self.write('POST', 'subobjects', {'name': ' НАСОСНАЯ ', 'object_id': group['id']}).status_code, 409)
        for name in ('', ' ', 'x' * 301, 'Имя\nстрока', None, 5):
            self.assertEqual(self.write('POST', 'objects', {'name': name}).status_code, 400)
        for parent in (None, True, '1', -1, 999999):
            self.assertEqual(self.write('POST', 'subobjects', {'name': 'Узел', 'object_id': parent}).status_code, 400)
        self.assertEqual(self.write('POST', 'users', {'name': 'Не группа'}).status_code, 404)

    def test_conflicts_do_not_overwrite_and_tokens_survive_restart(self):
        group = self.create('objects', 'Группа исходная')
        url = f"objects/{group['id']}"
        self.assertEqual(self.write('PATCH', url, {'name': 'Без версии'}).status_code, 409)
        data = {'name': 'Группа новая', 'expected_token': group['edit_token']}
        self.assertEqual(self.write('PATCH', url, data).status_code, 200)
        self.assertEqual(self.write('PATCH', url, data).status_code, 409)
        updated = self.row('objects', group['id'])
        with self.module.app.app_context():
            self.module.init_db()
        self.assertEqual(self.row('objects', group['id']), updated)
        # A -> B -> A must not resurrect an old editor token.
        self.assertEqual(self.write('PATCH', url, {'name': group['name'], 'expected_token': updated['edit_token']}).status_code, 200)
        self.assertEqual(self.write('PATCH', url, data).status_code, 409)

    def test_move_and_rename_preserve_assignments_plans_events_and_reference_etag(self):
        first = self.create('objects', 'Первая группа')
        second = self.create('objects', 'Вторая группа')
        site = self.create('subobjects', 'Узел', object_id=first['id'])
        occupied = self.create('subobjects', 'Занято', object_id=second['id'])
        with self.module.app.app_context():
            db = self.module.get_db()
            worker = db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Работник','location-test')").lastrowid
            crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Бригада',?,'now')", (self.admin_id,)).lastrowid
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,created_at,crew_id)
                VALUES ('2026-09-13','1 смена',?,?,?,'now',?)''', (site['id'], worker, self.admin_id, crew))
            db.execute('''INSERT INTO daily_staffing_plans VALUES (?,'2026-09-13',5,?,'now','plan-token')''', (site['id'], self.admin_id))
            db.execute('''INSERT INTO staffing_plans(work_date,shift,subobject_id,planned_count,created_by,updated_at)
                VALUES ('2026-09-13','1 смена',?,5,?,'now')''', (site['id'], self.admin_id))
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,after_subobject_id,changed_by,changed_at)
                VALUES (?,?,'2026-09-13','1 смена',?,?,'now')''', (crew, worker, site['id'], self.admin_id))
            db.commit()
            tables = ('assignments', 'daily_staffing_plans', 'staffing_plans', 'assignment_events')
            before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)] for table in tables}
        etag = self.admin.get('/api/reference?scope=locations').headers['ETag']
        url = f"subobjects/{site['id']}"
        self.assertEqual(self.write('PATCH', url, {'name': occupied['name'], 'object_id': second['id'], 'expected_token': site['edit_token']}).status_code, 409)
        self.assertEqual(self.row('subobjects', site['id']), site)
        self.assertEqual(self.write('PATCH', url, {'name': 'Узел новый', 'object_id': second['id'], 'expected_token': site['edit_token']}).status_code, 200)
        self.assertEqual(self.write('PATCH', f"objects/{second['id']}", {'name': 'Группа переименована', 'expected_token': second['edit_token']}).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            after = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)] for table in tables}
        self.assertEqual(before, after)
        response = self.admin.get('/api/reference?scope=locations', headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers['ETag'], etag)
        self.assertEqual(self.row('subobjects', site['id'])['object_id'], second['id'])


if __name__ == '__main__':
    unittest.main()
