import json
import sqlite3
import unittest
from unittest.mock import patch

import test_locations as location_tests


class SmuCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        location_tests.LocationCatalogTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        location_tests.LocationCatalogTest.tearDownClass()

    def setUp(self):
        self.fixture = location_tests.LocationCatalogTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.module = self.fixture.module
        self.client = self.fixture.admin
        self.headers = {'X-CSRF-Token': 'locations-csrf'}

    def rows(self):
        return self.client.get('/api/smu').get_json()['rows']

    def create(self, name):
        response = self.client.post('/api/smu', json={'name': name}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.get_json())
        return next(r for r in self.rows() if r['id'] == response.json['id'])

    def update(self, row, name, **extra):
        return self.client.patch('/api/smu/' + str(row['id']), headers=self.headers,
                                 json={'name': name, 'active': True, 'expected_token': row['edit_token'], **extra})

    def worker(self, db, name, department):
        return db.execute('INSERT INTO workers(full_name,personnel_no,department) VALUES (?,?,?)',
                          (name, name, department)).lastrowid

    def test_backfill_exact_blanks_idempotent_and_source_unchanged(self):
        from smu_api import migrate_smu_catalog
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE workers(id INTEGER PRIMARY KEY,department TEXT);
            INSERT INTO workers VALUES (1,'СМУ 1'),(2,'СМУ 1'),(3,''),(4,'   '),(5,'СМУ  1');''')
        before = db.execute('SELECT * FROM workers').fetchall()
        with patch('backup_api.create_backup') as backup:
            migrate_smu_catalog(db)
            backup.assert_called_once()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM smu_catalog').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM employee_smu').fetchone()[0], 3)
        rows = db.execute('SELECT * FROM employee_smu').fetchall()
        with patch('backup_api.create_backup') as backup:
            migrate_smu_catalog(db)
            backup.assert_not_called()
        self.assertEqual(db.execute('SELECT * FROM workers').fetchall(), before)
        self.assertEqual(db.execute('SELECT * FROM employee_smu').fetchall(), rows)

    def test_rename_preserves_binding_source_and_selected_access_then_import_alias(self):
        from user_smu_access import allowed_workers, access_view
        old = 'Строительно-монтажный участок № 15.1'
        new = 'Строительно-монтажный участок № 15.1.1'
        with self.module.app.app_context():
            db = self.module.get_db()
            a = self.worker(db, 'Первый', old)
            b = self.worker(db, 'Второй', 'Строительно-монтажный участок № 19.1')
            user = db.execute("SELECT * FROM users WHERE role='foreman'").fetchone()
            db.execute('INSERT INTO user_smu_access VALUES (?,?,?,?,?,?)',
                       (user['id'], 'selected', json.dumps([old], ensure_ascii=False), 'old', self.fixture.admin_id, 'now'))
            db.commit()
            rights = allowed_workers(db, [a, b], user)
            token = access_view(db, user)['expected_token']
        row = next(r for r in self.rows() if r['name'] == old)
        self.assertEqual(self.update(row, new).status_code, 200)
        self.assertEqual(self.update(row, old).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(allowed_workers(db, [a, b], user), rights)
            self.assertNotEqual(access_view(db, user)['expected_token'], token)
            self.assertEqual(db.execute('SELECT department FROM workers WHERE id=?', (a,)).fetchone()[0], new)
            self.assertEqual(tuple(db.execute('SELECT smu_id,source_department FROM employee_smu WHERE worker_id=?', (a,)).fetchone()), (row['id'], old))
            c = self.worker(db, 'Повторный импорт', old)
            self.assertEqual(db.execute('SELECT department FROM workers WHERE id=?', (c,)).fetchone()[0], new)
            self.assertEqual(db.execute('SELECT smu_id FROM employee_smu WHERE worker_id=?', (c,)).fetchone()[0], row['id'])
            db.execute('UPDATE workers SET department=? WHERE id=?', ('Строительно-монтажный участок № 19.1', c))
            self.assertNotEqual(db.execute('SELECT smu_id FROM employee_smu WHERE worker_id=?', (c,)).fetchone()[0], row['id'])
            db.execute("UPDATE workers SET department='' WHERE id=?", (c,))
            self.assertIsNone(db.execute('SELECT * FROM employee_smu WHERE worker_id=?', (c,)).fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_smu_access_events').fetchone()[0], 1)
            db.commit()
        self.assertEqual(self.client.post('/api/smu', json={'name': old}, headers=self.headers).status_code, 409)

    def test_roles_csrf_stale_delete_conflicts_and_options(self):
        from smu_api import active_smu_names
        from user_smu_access import department_options
        row = self.create('Строительно-монтажный участок № 88')
        url = '/api/smu/' + str(row['id'])
        for method in ('PATCH', 'DELETE'):
            self.assertEqual(self.client.open(url, method=method, json={}).status_code, 403)
            for role in ('foreman', 'viewer'):
                self.assertEqual(self.fixture.clients[role].open(url, method=method, json={}, headers=self.headers).status_code, 403)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertIn(row['name'], active_smu_names(db))
            self.assertIn(row['id'], [r['id'] for r in department_options(db)])
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.fixture.admin_id,)); db.commit()
        self.assertEqual(self.update(row, 'Новое').status_code, 403)
        self.assertEqual(self.client.get('/api/smu').status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.fixture.admin_id,)); db.commit()
        self.assertEqual(self.update(row, row['name'], active=False).status_code, 200)
        self.assertEqual(self.client.delete(url, json={'expected_token': row['edit_token']}, headers=self.headers).status_code, 409)
        current = next(r for r in self.rows() if r['id'] == row['id'])
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertNotIn(row['name'], active_smu_names(db))
            a = self.worker(db, 'Занято', row['name']); db.commit()
        self.assertEqual(self.client.delete(url, json={'expected_token': current['edit_token']}, headers=self.headers).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db(); db.execute("UPDATE workers SET department='' WHERE id=?", (a,)); db.commit()
        self.assertEqual(self.client.delete(url, json={'expected_token': current['edit_token']}, headers=self.headers).status_code, 200)
        with self.module.app.app_context():
            self.module.init_db()
        self.assertNotIn(row['id'], [r['id'] for r in self.rows()])

    def test_rename_backup_failure_and_acl_collision_roll_back(self):
        row = self.create('Первое СМУ')
        with self.module.app.app_context():
            db = self.module.get_db()
            worker = self.worker(db, 'Работник', row['name'])
            user = db.execute("SELECT id FROM users WHERE role='foreman'").fetchone()[0]
            db.execute('INSERT INTO user_smu_access VALUES (?,?,?,?,?,?)',
                       (user, 'selected', json.dumps(['Новое СМУ'], ensure_ascii=False), 'old', self.fixture.admin_id, 'now'))
            db.commit()
        with patch('backup_api.create_backup', side_effect=OSError('disk full')):
            self.assertEqual(self.update(row, 'Новое СМУ').status_code, 503)
        self.assertEqual(self.update(row, 'Новое СМУ').status_code, 409)
        self.assertEqual(next(r for r in self.rows() if r['id'] == row['id']), {**row, 'employee_count': 1})
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT department FROM workers WHERE id=?', (worker,)).fetchone()[0], row['name'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_smu_access_events').fetchone()[0], 0)

    def test_short_rename_remains_available_and_outstaff_recognizes_old_number(self):
        from outstaff_api import prepare_import, parse_outstaff
        from test_outstaff import workbook
        from user_smu_access import access_view
        old = 'Строительно-монтажный участок № 15.1'
        row = self.create(old)
        with self.module.app.app_context():
            db = self.module.get_db()
            worker = self.worker(db, 'Сотрудник СМУ', old)
            user = db.execute("SELECT * FROM users WHERE role='foreman'").fetchone()
            crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Бригада СМУ',?,'now')", (user['id'],)).lastrowid
            db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (crew, worker))
            db.commit()
        self.assertEqual(self.update(row, 'Участок Северный').status_code, 200)
        self.assertIn('Участок Северный', [r['name'] for r in self.client.get('/api/crew-departments').json['rows']])
        self.assertIn('Участок Северный', self.client.get('/api/employees/create-options').json['departments'])
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertIn('Участок Северный', access_view(db, user)['departments'])
            preview = prepare_import(db, parse_outstaff(workbook(), 'source.xlsx'), [], {})
            self.assertEqual(preview['rows'][0]['mapped_department'], 'Участок Северный')
