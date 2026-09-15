from contextlib import closing
import importlib
import inspect
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import g
from werkzeug.exceptions import HTTPException

from user_roles import migrate_user_roles, promote_super_admin


class SuperAdminTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
            'ADMIN_PASSWORD': 'super-admin-test-password',
            'SECRET_KEY': 'super-admin-test-secret-at-least-thirty-two-characters',
        }):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'roles.db'
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = self.path
        self.ids, self.clients = {}, {}
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.ids['admin'] = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            for role in ('super_admin', 'foreman', 'viewer'):
                self.ids[role] = db.execute('INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES (?,?,?,?,?)',
                    (role, 'test-hash', role, role, 'now')).lastrowid
            db.commit()
        for role, user_id in self.ids.items():
            client = self.module.app.test_client()
            with client.session_transaction() as session:
                session['user_id'] = user_id
                session['csrf_token'] = 'roles-csrf'
            self.clients[role] = client

    def write(self, role, method, path, payload):
        return self.clients[role].open(path, method=method, json=payload, headers={'X-CSRF-Token': 'roles-csrf'})

    def test_catalog_writes_require_super_admin_and_keep_reads(self):
        for path, payload in (
            ('/api/gdlr-categories', {'name': 'Категория тест'}),
            ('/api/contractors', {'name': 'Подрядчик тест'}),
            ('/api/locations/objects', {'name': 'Группа тест'}),
        ):
            with self.subTest(path=path):
                for role in ('admin', 'foreman', 'viewer'):
                    self.assertEqual(self.write(role, 'POST', path, payload).status_code, 403)
                self.assertEqual(self.clients['super_admin'].post(path, json=payload).status_code, 403)
                created = self.write('super_admin', 'POST', path, payload)
                self.assertEqual(created.status_code, 201, created.get_json())
                item_id = created.get_json()['id']
                read_path = '/api/locations' if '/locations/' in path else path
                for role in ('admin', 'super_admin'):
                    self.assertEqual(self.clients[role].get(read_path).status_code, 200)
                data = self.clients['super_admin'].get(read_path).get_json()
                item = next(row for row in data.get('rows', data.get('objects', [])) if row['id'] == item_id)
                update = {'name': payload['name'] + ' новая', 'active': False, 'expected_token': item['edit_token']}
                for role in ('admin', 'foreman', 'viewer'):
                    self.assertEqual(self.write(role, 'PATCH', f'{path}/{item_id}', update).status_code, 403)
                self.assertEqual(self.write('super_admin', 'PATCH', f'{path}/{item_id}', update).status_code, 200)
                self.assertEqual(self.write('super_admin', 'PATCH', f'{path}/{item_id}', update).status_code, 409)
        group = self.clients['admin'].get('/api/locations').get_json()['objects'][0]
        payload = {'name': 'Подобъект тест', 'object_id': group['id']}
        for role in ('admin', 'foreman', 'viewer'):
            self.assertEqual(self.write(role, 'POST', '/api/locations/subobjects', payload).status_code, 403)
        created = self.write('super_admin', 'POST', '/api/locations/subobjects', payload)
        self.assertEqual(created.status_code, 201)
        site = next(row for row in self.clients['admin'].get('/api/locations').get_json()['subobjects'] if row['id'] == created.get_json()['id'])
        payload.update(name='Новое название', expected_token=site['edit_token'])
        for role in ('admin', 'foreman', 'viewer'):
            self.assertEqual(self.write(role, 'PATCH', f"/api/locations/subobjects/{site['id']}", payload).status_code, 403)
        self.assertEqual(self.write('super_admin', 'PATCH', f"/api/locations/subobjects/{site['id']}", payload).status_code, 200)

    def test_admin_cannot_escalate_or_modify_super_account(self):
        payload = {'username': 'second-super', 'full_name': 'Второй супер-администратор', 'password': 'test-password-123', 'role': 'super_admin'}
        self.assertEqual(self.write('admin', 'POST', '/api/users', payload).status_code, 403)
        for target in ('admin', 'foreman'):
            self.assertEqual(self.write('admin', 'PATCH', f'/api/users/{self.ids[target]}',
                {'role': 'super_admin', 'expected_role': target}).status_code, 403)
        for change in ({'password': 'changed-password'}, {'active': False},
                       {'full_name': 'Переименован', 'expected_full_name': 'super_admin'},
                       {'role': 'admin', 'expected_role': 'super_admin'}):
            self.assertEqual(self.write('admin', 'PATCH', f"/api/users/{self.ids['super_admin']}", change).status_code, 403)
        self.assertEqual(self.write('super_admin', 'POST', '/api/users', payload).status_code, 201)
        self.assertEqual(self.write('admin', 'POST', '/api/users', {**payload, 'username': 'ordinary-admin', 'role': 'admin'}).status_code, 403)

    def test_only_super_admin_can_create_accounts_and_change_credentials_or_roles(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.ids['viewer'],))
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
        for actor in ('admin', 'foreman'):
            for target_role in ('viewer', 'foreman', 'admin', 'super_admin'):
                payload = {'username': 'new-' + target_role, 'full_name': 'Новая учётная запись',
                           'password': 'access-test-password', 'role': target_role}
                self.assertEqual(self.write(actor, 'POST', '/api/users', payload).status_code, 403)
            for target in ('admin', 'foreman', 'viewer', 'super_admin'):
                for change in ({'active': True}, {'password': 'replacement-password'},
                               {'role': 'admin', 'expected_role': target},
                               {'full_name': 'Новое ФИО', 'expected_full_name': target}):
                    self.assertEqual(self.write(actor, 'PATCH', f'/api/users/{self.ids[target]}', change).status_code, 403)
        with self.module.app.app_context():
            self.assertEqual([tuple(row) for row in self.module.get_db().execute('SELECT * FROM users ORDER BY id')], before)
        payload = {'username': 'issued-by-super', 'full_name': 'Выданный доступ',
                   'password': 'access-test-password', 'role': 'foreman'}
        self.assertEqual(self.clients['super_admin'].post('/api/users', json=payload).status_code, 403)
        created = self.write('super_admin', 'POST', '/api/users', payload)
        self.assertEqual(created.status_code, 201)
        path = '/api/users/' + str(created.get_json()['id'])
        self.assertEqual(self.write('super_admin', 'PATCH', path, {'role': 'viewer', 'expected_role': 'foreman'}).status_code, 200)
        self.assertEqual(self.write('super_admin', 'PATCH', path, {'role': 'admin', 'expected_role': 'foreman'}).status_code, 409)
        self.assertEqual(self.clients['super_admin'].patch(path, json={'active': False}).status_code, 403)
        self.assertEqual(self.write('super_admin', 'PATCH', path, {'active': False, 'password': 'replacement-password'}).status_code, 200)

    def test_transaction_rechecks_super_admin_after_request_role_was_read(self):
        target = self.ids['foreman']
        token = next(row['expected_token'] for row in self.clients['super_admin'].get('/api/user-smu-access').get_json()['rows']
                     if row['user_id'] == target)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.ids['super_admin'],))
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
        cases = [
            ('create_user', '/api/users', 'POST', {}, {'username': 'revoked-super', 'full_name': 'Не создавать',
                'password': 'test-password-123', 'role': 'foreman'}),
            ('update_user', f'/api/users/{target}', 'PATCH', {'user_id': target}, {'active': False}),
            ('save_access', f'/api/users/{target}/smu-access', 'PUT', {'user_id': target},
                {'mode': 'all', 'departments': [], 'expected_token': token}),
        ]
        for endpoint, path, method, kwargs, payload in cases:
            with self.subTest(endpoint=endpoint), self.module.app.test_request_context(path, method=method, json=payload):
                g.user = {'id': self.ids['super_admin'], 'role': 'super_admin'}
                try:
                    response = self.module.app.make_response(inspect.unwrap(self.module.app.view_functions[endpoint])(**kwargs))
                except HTTPException as error:
                    response = error.get_response()
                self.assertEqual(response.status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')], before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_smu_access').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_smu_access_events').fetchone()[0], 0)

    def test_last_super_admin_and_immediate_revocation(self):
        path = f"/api/users/{self.ids['super_admin']}"
        change = {'role': 'admin', 'expected_role': 'super_admin'}
        self.assertEqual(self.write('super_admin', 'PATCH', path, change).status_code, 409)
        self.assertEqual(self.write('super_admin', 'PATCH', path, {'active': False}).status_code, 400)
        admin_path = f"/api/users/{self.ids['admin']}"
        self.assertEqual(self.write('super_admin', 'PATCH', admin_path, {'role': 'super_admin', 'expected_role': 'admin'}).status_code, 200)
        self.assertEqual(self.write('super_admin', 'PATCH', path, change).status_code, 200)
        self.assertEqual(self.write('super_admin', 'POST', '/api/gdlr-categories', {'name': 'Запрещено'}).status_code, 403)
        self.assertEqual(self.write('admin', 'POST', '/api/gdlr-categories', {'name': 'Разрешено'}).status_code, 201)

    def test_super_inherits_admin_workflows_and_role_ui(self):
        for path in ('/', '/api/users', '/api/crews', '/api/staffing?date=2026-09-13&shift=all', '/api/employees', '/api/crew-departments', '/api/personnel-dashboard'):
            with self.subTest(path=path):
                self.assertEqual(self.clients['super_admin'].get(path).status_code, 200)
        for role in ('admin', 'super_admin'):
            html = self.clients[role].get('/').get_data(as_text=True)
            self.assertIn('id="view-catalogs"', html)
            self.assertEqual('value="super_admin"' in html, role == 'super_admin')
            self.assertEqual('id="account-form"' in html, role == 'super_admin')
            self.assertEqual('Учётные записи и доступ изменяет супер-администратор.' in html, role == 'admin')
            self.assertEqual('Только просмотр. Редактирование' in html, role == 'admin')
        for role in ('admin', 'super_admin'):
            self.assertEqual(self.write(role, 'POST', '/api/crews', {'name': role, 'owner_user_id': self.ids['super_admin']}).status_code, 201)

    def test_explicit_promotion_is_backed_up_and_idempotent(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            username = db.execute('SELECT username FROM users WHERE id=?', (self.ids['admin'],)).fetchone()[0]
            before = [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
            backup = promote_super_admin(db, username)
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(saved.execute('SELECT * FROM users ORDER BY id').fetchall(), before)
            self.assertIsNone(promote_super_admin(db, username))
            self.module.init_db()
            self.assertEqual(db.execute('SELECT role FROM users WHERE id=?', (self.ids['admin'],)).fetchone()[0], 'super_admin')
            with self.assertRaises(ValueError):
                promote_super_admin(db, 'foreman')


class UserRoleMigrationTest(unittest.TestCase):
    def test_existing_users_links_and_backup_survive_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'old.db'
            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                db.executescript("""
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        password_hash TEXT NOT NULL,full_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('admin','foreman','viewer')),
                        active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
                    CREATE TABLE links(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id));
                    INSERT INTO users VALUES (42,'Логин','original-hash','Иванов Иван','admin',1,'original-date');
                    INSERT INTO links VALUES (7,42);
                    CREATE INDEX users_active ON users(active);
                """)
                before = [tuple(row) for row in db.execute('SELECT * FROM users')]
                migrate_user_roles(db)
                migrate_user_roles(db)
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM users')], before)
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM links')], [(7, 42)])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='users_active'").fetchone())
                backups = list((Path(temporary) / 'backups').glob('*.db'))
                self.assertEqual(len(backups), 1)
                with closing(sqlite3.connect(backups[0])) as saved:
                    self.assertEqual(saved.execute('SELECT * FROM users').fetchall(), before)
                db.execute("UPDATE users SET role='super_admin' WHERE id=42")
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('DELETE FROM users WHERE id=42')
