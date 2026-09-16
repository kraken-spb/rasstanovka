import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CrewWorkflowTest(unittest.TestCase):
    """Exercise brigade boundaries using independent, disposable databases."""

    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            "DATABASE_PATH": str(Path(cls.bootstrap.name) / "bootstrap.db"),
            "ADMIN_PASSWORD": "crew-test-password-123",
            "SECRET_KEY": "crew-test-secret-key-at-least-thirty-two-characters",
        }):
            cls.module = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous_path = self.module.DATABASE_PATH
        previous_testing = self.module.app.config["TESTING"]
        self.addCleanup(setattr, self.module, "DATABASE_PATH", previous_path)
        self.addCleanup(self.module.app.config.__setitem__, "TESTING", previous_testing)
        self.module.DATABASE_PATH = Path(temporary.name) / "crews.db"
        self.module.app.config["TESTING"] = True
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()["id"]
            self.super_admin_id = self.insert_user(db, "crew_super", "super_admin")
            self.owner_a = self.insert_user(db, "crew_owner_a", "foreman")
            self.owner_b = self.insert_user(db, "crew_owner_b", "foreman")
            self.viewer_id = self.insert_user(db, "crew_viewer", "viewer")
            self.empty_owner = self.insert_user(db, "crew_empty_owner", "foreman")
            self.worker_ids = []
            for number in range(4):
                cursor = db.execute(
                    "INSERT INTO workers(full_name, personnel_no, employer) VALUES (?, ?, ?)",
                    (f"Crew Worker {number}", f"crew-test-{number}", "Test employer"),
                )
                self.worker_ids.append(cursor.lastrowid)
            self.staffing_category_token = 'crew-fixture-category'
            category_name = 'Монтажник'
            self.staffing_category_id = db.execute(
                '''INSERT INTO gdlr_categories
                   (name,name_key,active,staffing_allowed,edit_token,updated_by,updated_at)
                   VALUES (?,?,1,1,?,?,?)''',
                (category_name, category_name.casefold(), self.staffing_category_token,
                 self.admin_id, 'now'),
            ).lastrowid
            for worker_id in self.worker_ids:
                db.execute(
                    '''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                       VALUES (?,?,?,?,?)''',
                    (worker_id, self.staffing_category_id, self.staffing_category_token,
                     self.admin_id, 'now'),
                )
            object_id = db.execute("INSERT INTO objects(name) VALUES ('Crew test object')").lastrowid
            self.sites = [
                db.execute("INSERT INTO subobjects(object_id, name) VALUES (?, ?)",
                           (object_id, f"Test site {number}")).lastrowid
                for number in range(3)
            ]
            db.commit()
        self.admin = self.client_for(self.admin_id)
        self.super_admin = self.client_for(self.super_admin_id)
        self.foreman_a = self.client_for(self.owner_a)
        self.foreman_b = self.client_for(self.owner_b)
        self.viewer = self.client_for(self.viewer_id)
        self.crew_a = self.create_crew("Alpha", self.owner_a)
        self.crew_a2 = self.create_crew("Alpha second", self.owner_a)
        self.crew_b = self.create_crew("Beta", self.owner_b)

    def catalog_admin(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            user = db.execute("SELECT id FROM users WHERE username='catalog_super'").fetchone()
            user_id = user[0] if user else self.insert_user(db, 'catalog_super', 'super_admin')
            db.commit()
        return self.client_for(user_id)

    def test_category_catalog_is_explicit_unique_and_admin_managed(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET category='Категория из файла'")
            db.commit()
            self.module.init_db()
        rows = self.admin.get('/api/gdlr-categories').get_json()['rows']
        self.assertEqual([row['id'] for row in rows], [self.staffing_category_id])
        url = '/api/gdlr-categories'
        self.assertEqual(self.viewer.get(url).status_code, 403)
        self.assertEqual(self.write(self.foreman_a, 'POST', url, {'name': 'Монтажники'}).status_code, 403)
        self.assertEqual(self.admin.post(url, json={'name': 'Монтажники'}).status_code, 403)
        for name in ('', ' ', 'x' * 201, 'a\nb', None):
            self.assertEqual(self.write(self.catalog_admin(), 'POST', url, {'name': name}).status_code, 400)
        self.assertEqual(self.write(self.catalog_admin(), 'POST', url, {'name': 'Монтажники'}).status_code, 201)
        self.assertEqual(self.write(self.catalog_admin(), 'POST', url, {'name': '  МОНТАЖНИКИ  '}).status_code, 409)
        category = next(row for row in self.admin.get(url).get_json()['rows'] if row['name'] == 'Монтажники')
        update = {'name': 'Монтажники ТТ', 'active': True, 'expected_token': category['edit_token']}
        self.assertEqual(self.write(self.catalog_admin(), 'PATCH', url + '/' + str(category['id']), update).status_code, 200)
        self.assertEqual(self.write(self.catalog_admin(), 'PATCH', url + '/' + str(category['id']), update).status_code, 409)

    def test_category_binding_checks_owner_tokens_and_preserves_source(self):
        self.add(self.crew_a, [self.worker_ids[0]])
        worker = self.worker_ids[0]
        catalog_url = '/api/gdlr-categories'
        self.write(self.catalog_admin(), 'POST', catalog_url, {'name': 'Монтажники ТТ'})
        category = next(row for row in self.admin.get(catalog_url).get_json()['rows'] if row['name'] == 'Монтажники ТТ')
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE gdlr_categories SET staffing_allowed=1 WHERE id=?', (category['id'],))
            db.commit()
        url = f'/api/employees/{worker}/category'
        payload = {'category_id': category['id'], 'category_token': category['edit_token'],
                   'expected_token': self.employee(worker)['membership_token']}
        self.assertEqual(self.write(self.foreman_b, 'PUT', url, payload).status_code, 403)
        self.assertEqual(self.write(self.viewer, 'PUT', url, payload).status_code, 403)
        self.assertEqual(self.foreman_a.put(url, json=payload).status_code, 403)
        self.assertEqual(self.write(self.foreman_a, 'PUT', url, {**payload, 'category_id': 999999}).status_code, 400)
        self.assertEqual(self.write(self.foreman_a, 'PUT', url, {**payload, 'category_token': 'stale'}).status_code, 409)
        self.assertEqual(self.write(self.foreman_a, 'PUT', url, payload).status_code, 200)
        self.assertEqual(self.write(self.foreman_a, 'PUT', url, payload).status_code, 409)
        current = self.employee(worker)
        self.assertEqual(current['category'], 'Монтажники ТТ')
        self.assertEqual(current['category_id'], category['id'])
        self.assertEqual(current['source_category'], '')
        self.assertEqual(current['crew_id'], self.crew_a)
        update = {'name': 'Монтажники трубопроводов', 'active': False, 'expected_token': category['edit_token']}
        self.assertEqual(self.write(self.catalog_admin(), 'PATCH', catalog_url + '/' + str(category['id']), update).status_code, 200)
        current = self.employee(worker)
        self.assertEqual(current['category'], 'Монтажники трубопроводов')
        self.assertEqual(current['category_active'], 0)
        self.assertEqual(self.write(self.foreman_a, 'PUT', url, {**payload, 'expected_token': current['membership_token']}).status_code, 400)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT category FROM workers WHERE id=?', (worker,)).fetchone()[0], '')
            self.assertEqual(db.execute('SELECT updated_by FROM employee_gdlr WHERE worker_id=?', (worker,)).fetchone()[0], self.owner_a)

    def test_employee_counts_use_qualification_and_unique_people_on_report_date(self):
        self.add(self.crew_a, self.worker_ids[:2])
        self.assertEqual(self.place([self.worker_ids[0]], self.sites[0]).status_code, 200)
        self.assertEqual(self.place([self.worker_ids[0]], self.sites[1], shift='2 смена').status_code, 200)
        self.assertEqual(self.place([self.worker_ids[1]], self.sites[0], work_date='2026-09-12').status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            batch = db.execute('''INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json)
                VALUES ('counts-test','counts.xlsx',?,'now',4,'{}')''', (self.admin_id,)).lastrowid
            for index, qualification in enumerate(['Рабочие', 'Специалисты', '', 'Рабочие']):
                db.execute('''INSERT INTO staffing_people(import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew)
                    VALUES (?,?,?,?,?,?,?,?)''', (batch, index + 2, f'Crew Worker {index}', f'crew-test-{index}', '', '', qualification, ''))
            # A GDLR text that looks like a qualification must not affect the count.
            db.execute("UPDATE workers SET category='Рабочие' WHERE id=?", (self.worker_ids[1],))
            db.commit()
        first = self.admin.get('/api/employees?date=2026-09-11').get_json()
        self.assertEqual(first['summary']['total'], len(first['rows']))
        self.assertEqual(first['summary']['workers'], 2)
        self.assertEqual(first['summary']['assigned'], 1)
        self.assertEqual(first['summary']['unknown_qualification'], len(first['rows']) - 3)
        second = self.admin.get('/api/employees?date=2026-09-12').get_json()
        self.assertEqual(second['summary']['assigned'], 1)
        first_person = next(row for row in first['rows'] if row['id'] == self.worker_ids[0])
        second_person = next(row for row in second['rows'] if row['id'] == self.worker_ids[0])
        self.assertTrue(first_person['assigned_on_date'])
        self.assertFalse(second_person['assigned_on_date'])
        self.assertEqual(first_person['membership_token'], second_person['membership_token'])
        self.assertEqual(self.admin.get('/api/employees?date=2026-09-13').get_json()['summary']['assigned'], 0)
        for invalid in ['', 'bad', '2026-02-30']:
            self.assertEqual(self.admin.get('/api/employees', query_string={'date': invalid}).status_code, 400)
        self.assertEqual(self.viewer.get('/api/employees?date=2026-09-11').status_code, 403)
        self.assertEqual(self.foreman_a.get('/api/employees?date=2026-09-11').get_json()['summary'], first['summary'])
        with self.module.app.app_context():
            db = self.module.get_db()
            batch = db.execute('''INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json)
                VALUES ('counts-test-new','counts-new.xlsx',?,'now',1,'{}')''', (self.admin_id,)).lastrowid
            for number, qualification in enumerate(['Рабочие', 'Специалисты']):
                db.execute('''INSERT INTO staffing_people(import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew)
                    VALUES (?,?,'Crew Worker 0','crew-test-0','','',?,'')''', (batch, number + 2, qualification))
            db.commit()
        ambiguous = self.admin.get('/api/employees?date=2026-09-11').get_json()
        self.assertEqual(ambiguous['summary']['workers'], 1)
        self.assertEqual(ambiguous['summary']['assigned'], 1)

    def insert_user(self, db, name, role):
        return db.execute(
            "INSERT INTO users(username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, "unused-test-hash", name, role, self.module.utc_now()),
        ).lastrowid

    def client_for(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "crew-test-csrf-token"
        return client

    def write(self, client, method, url, payload=None):
        return client.open(url, method=method, json=payload,
                           headers={"X-CSRF-Token": "crew-test-csrf-token"})

    def create_crew(self, name, owner):
        response = self.write(self.admin, "POST", "/api/crews", {"name": name, "owner_user_id": owner})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def add(self, crew, ids, client=None):
        return self.write(client or self.foreman_a, "POST", f"/api/crews/{crew}/members", {"worker_ids": ids})

    def board(self, crew=None, client=None, work_date="2026-09-11", shift="1 смена"):
        response = (client or self.foreman_a).get(
            f"/api/crews/{crew or self.crew_a}/board", query_string={"date": work_date, "shift": shift})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def place(self, ids, site, tokens=None, crew=None, client=None,
              work_date="2026-09-11", shift="1 смена"):
        return self.write(client or self.foreman_a, "PUT", f"/api/crews/{crew or self.crew_a}/assignments", {
            "date": work_date, "shift": shift, "worker_ids": ids, "subobject_id": site,
            "expected_tokens": tokens if tokens is not None else {str(item): None for item in ids},
        })

    def rows(self, table):
        self.assertIn(table, {"assignments", "assignment_events", "crew_members"})
        with self.module.app.app_context():
            return [dict(row) for row in self.module.get_db().execute(f"SELECT * FROM {table}")]

    def test_existing_user_roles_change_immediately_and_keep_an_admin(self):
        url = f'/api/users/{self.owner_a}'
        change = {'role': 'viewer', 'expected_role': 'foreman'}
        self.assertEqual(self.write(self.foreman_a, 'PATCH', url, change).status_code, 403)
        self.assertEqual(self.write(self.admin, 'PATCH', url, change).status_code, 403)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {**change, 'role': 'unknown'}).status_code, 400)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {'role': 'viewer'}).status_code, 400)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, change).status_code, 200)
        self.assertEqual(self.foreman_a.get('/api/crews').status_code, 403)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {**change, 'active': False}).status_code, 409)
        user = next(u for u in self.admin.get('/api/users').get_json()['rows'] if u['id'] == self.owner_a)
        self.assertEqual(user['active'], 1)
        self.assertEqual(user['crew_count'], 2)
        self.assertEqual(self.write(self.super_admin, 'PATCH', f'/api/users/{self.admin_id}',
                                   {'role': 'foreman', 'expected_role': 'admin'}).status_code, 200)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {'role': 'admin', 'expected_role': 'viewer'}).status_code, 200)
        self.assertEqual(self.foreman_a.get('/api/users').status_code, 200)
        self.assertEqual(self.admin.get('/api/users').status_code, 403)

    def test_user_full_name_edit_validates_permissions_and_concurrent_changes(self):
        url = f'/api/users/{self.owner_a}'
        original = next(u for u in self.admin.get('/api/users').get_json()['rows'] if u['id'] == self.owner_a)
        change = {'full_name': '  Иванов Иван Иванович  ', 'expected_full_name': original['full_name']}
        self.assertEqual(self.write(self.foreman_a, 'PATCH', url, change).status_code, 403)
        self.assertEqual(self.write(self.viewer, 'PATCH', url, change).status_code, 403)
        self.assertEqual(self.write(self.admin, 'PATCH', url, change).status_code, 403)
        for invalid in ('', '   ', None, 42, 'Я' * 201):
            self.assertEqual(self.write(self.super_admin, 'PATCH', url, {**change, 'full_name': invalid}).status_code, 400)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {'full_name': 'Новое ФИО'}).status_code, 400)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, change).status_code, 200)
        self.assertEqual(self.write(self.super_admin, 'PATCH', url, {**change, 'active': False}).status_code, 409)
        current = next(u for u in self.admin.get('/api/users').get_json()['rows'] if u['id'] == self.owner_a)
        self.assertEqual(current['full_name'], 'Иванов Иван Иванович')
        for key in ('username', 'active', 'role', 'crew_count'):
            self.assertEqual(current[key], original[key])

    def department_fixture(self):
        self.department = 'Строительно-монтажный участок № 15.2'
        self.other_department = 'Строительно-монтажный участок № 15.4'
        self.add(self.crew_a, [self.worker_ids[0]])
        self.add(self.crew_a2, [self.worker_ids[1]])
        self.add(self.crew_b, self.worker_ids[2:], self.foreman_b)
        with self.module.app.app_context():
            db = self.module.get_db()
            for worker_id in self.worker_ids[:3]:
                db.execute('UPDATE workers SET department=? WHERE id=?', (self.department, worker_id))
            db.execute('UPDATE workers SET department=? WHERE id=?', (self.other_department, self.worker_ids[3]))
            db.commit()

    def department_preview(self, **overrides):
        return self.write(self.admin, 'POST', '/api/crews/assign-department/preview', {
            'department': self.department, 'owner_user_id': self.empty_owner, 'include_mixed': False, **overrides})

    def department_apply(self, preview, client=None):
        return self.write(client or self.admin, 'POST', '/api/crews/assign-department/apply', {'preview_token': preview['preview_token']})

    def test_department_bulk_ownership_preserves_roster_and_assignments(self):
        self.department_fixture()
        self.place([self.worker_ids[0]], self.sites[0])
        before = {table: self.rows(table) for table in ('assignments', 'assignment_events', 'crew_members')}
        catalog = self.admin.get('/api/crew-departments').get_json()['rows']
        self.assertEqual(next(d for d in catalog if d['name'] == self.department)['crew_count'], 3)
        preview = self.department_preview().get_json()
        self.assertEqual({c['id'] for c in preview['crews']}, {self.crew_a, self.crew_a2})
        self.assertEqual([c['id'] for c in preview['excluded']], [self.crew_b])
        result = self.department_apply(preview)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['updated'], 2)
        backup = self.module.DATABASE_PATH.parent / 'backups' / result.get_json()['backup']
        self.assertTrue(backup.exists())
        for table, rows in before.items():
            self.assertEqual(self.rows(table), rows)
        self.assertEqual(self.foreman_a.get(f'/api/crews/{self.crew_a}/board?date=2026-09-11&shift=1%20смена').status_code, 403)
        target = self.client_for(self.empty_owner)
        self.assertEqual({c['id'] for c in target.get('/api/crews').get_json()['rows']}, {self.crew_a, self.crew_a2})
        self.assertFalse(self.board(self.crew_a, target)['members'][0]['locked'])
        mixed = self.department_preview(include_mixed=True).get_json()
        self.assertEqual(mixed['excluded'], [])
        self.assertEqual(self.department_apply(mixed).get_json()['updated'], 1)
        self.assertEqual(len(target.get('/api/crews').get_json()['rows']), 3)

    def test_department_preview_rejects_stale_conflicting_and_unauthorized_batches(self):
        self.department_fixture()
        preview = self.department_preview().get_json()
        self.assertEqual(self.foreman_a.get('/api/crew-departments').status_code, 403)
        self.assertEqual(self.department_apply(preview, self.foreman_a).status_code, 403)
        self.assertEqual(self.admin.post('/api/crews/assign-department/apply', json={'preview_token': preview['preview_token']}).status_code, 403)
        self.assertEqual(self.department_preview(owner_user_id=self.viewer_id).status_code, 400)
        self.assertEqual(self.department_preview(include_mixed='yes').status_code, 400)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET department=? WHERE id=?', (self.other_department, self.worker_ids[1]))
            db.commit()
        self.assertEqual(self.department_apply(preview).status_code, 409)
        current = {c['id']: c for c in self.admin.get('/api/crews').get_json()['rows']}
        self.assertEqual(current[self.crew_a]['owner_user_id'], self.owner_a)
        self.assertEqual(current[self.crew_a2]['owner_user_id'], self.owner_a)
        self.create_crew('Alpha', self.empty_owner)
        collision = self.department_preview().get_json()
        self.assertIn('Alpha', collision['conflicts'])
        self.assertEqual(self.department_apply(collision).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='viewer' WHERE id=?", (self.empty_owner,))
            db.commit()
        self.assertEqual(self.department_apply(preview).status_code, 400)

    def test_admin_creates_and_binds_multiple_crews(self):
        self.assertEqual(len(self.admin.get("/api/crews").get_json()["rows"]), 3)
        self.assertEqual(
            {row["id"] for row in self.foreman_a.get("/api/crews").get_json()["rows"]},
            {self.crew_a, self.crew_a2},
        )
        self.assertEqual(self.write(self.foreman_a, "POST", "/api/crews", {
            "name": "Unauthorized", "owner_user_id": self.owner_a,
        }).status_code, 403)
        self.assertEqual(self.write(self.admin, "POST", "/api/crews", {
            "name": "Viewer crew", "owner_user_id": self.viewer_id,
        }).status_code, 400)
        response = self.write(self.admin, "PATCH", f"/api/crews/{self.crew_a2}", {"owner_user_id": self.owner_b})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.foreman_a.get("/api/crews").get_json()["rows"]), 1)
        self.assertEqual(len(self.foreman_b.get("/api/crews").get_json()["rows"]), 2)

    def employee(self, worker, client=None):
        return next(row for row in (client or self.admin).get('/api/employees').get_json()['rows'] if row['id'] == worker)

    def test_reference_cache_revalidates_changes_and_authentication(self):
        url = '/api/reference?scope=locations'
        first = self.admin.get(url)
        self.assertNotIn('workers', first.get_json())
        self.assertEqual(first.get_json()['objects'], self.admin.get('/api/reference').get_json()['objects'])
        token = first.headers['ETag']
        self.assertIn('private', first.headers['Cache-Control'])
        self.assertIn('must-revalidate', first.headers['Cache-Control'])
        self.assertEqual(self.admin.get(url, headers={'If-None-Match': token}).status_code, 304)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE subobjects SET name=? WHERE id=?', ('Новое название', self.sites[0]))
            db.commit()
        changed = self.admin.get(url, headers={'If-None-Match': token})
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.headers['ETag'], token)
        self.assertTrue(any(row['name'] == 'Новое название' for row in changed.get_json()['subobjects']))
        unauthorized = self.module.app.test_client().get(url, headers={'If-None-Match': changed.headers['ETag']})
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers['Cache-Control'], 'no-store')
        self.assertNotIn('ETag', unauthorized.headers)
        self.assertEqual(self.admin.get('/api/employees').headers['Cache-Control'], 'no-store')

    def test_static_cache_is_bound_to_content_version(self):
        version = self.module.ASSET_VERSIONS['styles.css']
        current = self.admin.get('/static/styles.css?v=' + version)
        self.assertEqual(current.status_code, 200)
        self.assertIn('immutable', current.headers['Cache-Control'])
        current.close()
        old = self.admin.get('/static/styles.css?v=outdated')
        self.assertNotIn('immutable', old.headers['Cache-Control'])
        old.close()
        page = self.admin.get('/')
        self.assertIn(('styles.css?v=' + version).encode(), page.data)
        self.assertIn(b'reference-cache.js?v=', page.data)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')

    def transfer(self, worker, target, client=None, token=None):
        return self.write(client or self.admin, 'PUT', f'/api/employees/{worker}/crew', {
            'crew_id': target, 'expected_token': token or self.employee(worker)['membership_token']})

    def test_employee_directory_includes_unassigned_inactive_and_categories(self):
        worker = self.worker_ids[0]
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET category=?,department=?,gsp_profession=?,active=0 WHERE id=?',
                       ('Монтажник ТТ', 'Участок № 15', 'Монтажник', worker))
            db.commit()
        row = self.employee(worker)
        self.assertEqual(row['category'], 'Монтажник')
        self.assertEqual(row['department'], 'Участок № 15')
        self.assertIsNone(row['crew_id'])
        self.assertFalse(row['can_edit'])
        self.assertEqual(self.transfer(worker, self.crew_a).status_code, 409)
        self.assertEqual(self.viewer.get('/api/employees').status_code, 403)
        self.assertEqual(self.module.app.test_client().get('/api/employees').status_code, 401)

    def test_employee_transfer_preserves_assignments_and_checks_stale_token(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        self.place([worker], self.sites[0])
        before = self.rows('assignments')
        token = self.employee(worker)['membership_token']
        self.assertEqual(self.transfer(worker, self.crew_b, token=token).status_code, 200)
        self.assertEqual(self.employee(worker)['crew_id'], self.crew_b)
        self.assertEqual(self.rows('assignments'), before)
        self.assertEqual(self.transfer(worker, self.crew_a, token=token).status_code, 409)
        self.assertEqual(self.employee(worker)['crew_id'], self.crew_b)
        self.assertEqual(self.transfer(worker, None).status_code, 200)
        self.assertIsNone(self.employee(worker)['crew_id'])

    def test_employee_transfer_enforces_both_owners_and_csrf(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        self.assertFalse(self.employee(worker, self.foreman_b)['can_edit'])
        self.assertEqual(self.transfer(worker, self.crew_b, self.foreman_a).status_code, 403)
        self.assertEqual(self.transfer(worker, self.crew_b, self.foreman_b).status_code, 403)
        self.assertEqual(self.transfer(worker, None, self.foreman_b).status_code, 403)
        self.assertEqual(self.transfer(worker, self.crew_a2, self.foreman_a).status_code, 200)
        self.assertEqual(self.foreman_a.put(f'/api/employees/{worker}/crew', json={'crew_id': None}).status_code, 403)
        self.assertEqual(self.transfer(worker, True).status_code, 400)
        self.assertEqual(self.transfer(worker, 999999).status_code, 404)
        self.assertEqual(self.employee(worker)['crew_id'], self.crew_a2)

    def test_owner_scoping_rejects_foreign_ids_on_reads_and_writes(self):
        worker = self.worker_ids[0]
        self.assertEqual(self.add(self.crew_b, [worker], self.foreman_b).status_code, 200)
        for endpoint in ("board?date=2026-09-11&shift=1+смена", "candidates"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.foreman_a.get(f"/api/crews/{self.crew_b}/{endpoint}").status_code, 403)
        self.assertEqual(self.add(self.crew_b, [self.worker_ids[1]]).status_code, 403)
        self.assertEqual(self.place([worker], self.sites[0], crew=self.crew_b).status_code, 403)
        self.assertEqual(self.write(self.foreman_a, "DELETE", f"/api/crews/{self.crew_b}/members/{worker}").status_code, 403)
        self.assertEqual(self.write(self.foreman_a, "PATCH", f"/api/crews/{self.crew_b}", {"owner_user_id": self.owner_a}).status_code, 403)
        self.assertEqual(self.rows("crew_members"), [{"crew_id": self.crew_b, "worker_id": worker}])
        self.assertEqual(self.rows("assignments"), [])
        empty = self.client_for(self.empty_owner)
        self.assertEqual(empty.get("/api/crews").get_json()["rows"], [])
        self.assertEqual(self.add(self.crew_a, [worker], empty).status_code, 403)

    def test_membership_conflicts_do_not_silently_transfer_or_partially_add(self):
        free, taken = self.worker_ids[:2]
        self.assertEqual(self.add(self.crew_b, [taken], self.foreman_b).status_code, 200)
        self.assertEqual(self.add(self.crew_a, [free, taken]).status_code, 409)
        self.assertEqual(self.rows("crew_members"), [{"crew_id": self.crew_b, "worker_id": taken}])
        self.assertEqual(self.add(self.crew_a, [taken], self.admin).status_code, 409)
        self.assertEqual(self.add(self.crew_a, [free, 999999]).status_code, 400)
        self.assertEqual(len(self.rows("crew_members")), 1)

    def test_invalid_periods_reject_without_assignments(self):
        worker = self.worker_ids[0]
        self.assertEqual(self.add(self.crew_a, [worker]).status_code, 200)
        for work_date, shift in (("2026-02-30", "1 смена"), ("", "1 смена"),
                                 ("2026-09-11", "third"), ("2026-09-11", "")):
            with self.subTest(work_date=work_date, shift=shift):
                self.assertEqual(self.place([worker], self.sites[0], work_date=work_date, shift=shift).status_code, 400)
        self.assertEqual(self.rows("assignments"), [])

    def test_equivalent_iso_dates_share_one_assignment(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        response = self.place([worker], self.sites[0], work_date="20260911")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["date"], "2026-09-11")
        retry = self.place([worker], self.sites[0])
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["changed"], 0)
        self.assertEqual(len(self.rows("assignments")), 1)

    def test_mixed_nonmembers_and_stale_batches_rollback_every_change(self):
        first, second, outsider = self.worker_ids[:3]
        self.add(self.crew_a, [first, second])
        self.assertEqual(self.place([first, outsider], self.sites[0]).status_code, 409)
        self.assertEqual(self.rows("assignments"), [])
        self.assertEqual(self.place([first, second], self.sites[0]).status_code, 200)
        before = self.rows("assignments")
        events = self.rows("assignment_events")
        tokens = {str(row["worker_id"]): row["edit_token"] for row in before}
        tokens[str(second)] = "outdated-token"
        self.assertEqual(self.place([first, second], self.sites[1], tokens).status_code, 409)
        self.assertEqual(self.rows("assignments"), before)
        self.assertEqual(self.rows("assignment_events"), events)

    def test_optimistic_conflict_refresh_and_identical_retry(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        self.assertEqual(self.place([worker], self.sites[0]).status_code, 200)
        row = self.board()["members"][0]
        old_tokens = {str(worker): row["edit_token"]}
        self.assertEqual(self.place([worker], self.sites[1], old_tokens).status_code, 200)
        retry = self.place([worker], self.sites[1], old_tokens)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["changed"], 0)
        self.assertEqual(len(self.rows("assignment_events")), 2)
        self.assertEqual(self.place([worker], self.sites[2], old_tokens).status_code, 409)
        refreshed = self.board()["members"][0]
        self.assertNotEqual(refreshed["edit_token"], row["edit_token"])
        new_tokens = {str(worker): refreshed["edit_token"]}
        self.assertEqual(self.place([worker], self.sites[2], new_tokens).status_code, 200)
        self.assertEqual(self.board()["members"][0]["subobject_id"], self.sites[2])

    def test_date_and_shift_history_are_independent_and_owner_scoped(self):
        mine, theirs = self.worker_ids[:2]
        self.add(self.crew_a, [mine])
        self.add(self.crew_b, [theirs], self.foreman_b)
        self.assertEqual(self.place([mine], self.sites[0], work_date="2026-09-10").status_code, 200)
        self.assertEqual(self.place([mine], self.sites[1]).status_code, 200)
        self.assertEqual(self.place([mine], self.sites[2], shift="2 смена").status_code, 200)
        self.assertEqual(self.place([theirs], self.sites[0], crew=self.crew_b, client=self.foreman_b,
                                    work_date="2026-09-09").status_code, 200)
        self.assertEqual(self.board(work_date="2026-09-10")["members"][0]["subobject_id"], self.sites[0])
        self.assertEqual(self.board()["members"][0]["subobject_id"], self.sites[1])
        self.assertEqual(self.board(shift="2 смена")["members"][0]["subobject_id"], self.sites[2])
        history = self.foreman_a.get("/api/activity-dates").get_json()["rows"]
        self.assertEqual({row["work_date"] for row in history}, {"2026-09-10", "2026-09-11"})
        self.assertEqual(self.foreman_a.get("/api/assignments?date=2026-09-09").get_json()["rows"], [])
        self.assertEqual(len(self.admin.get("/api/assignments?date=2026-09-09").get_json()["rows"]), 1)

    def test_membership_removal_keeps_history_and_foreign_assignment_lock(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        self.place([worker], self.sites[0])
        before = self.rows("assignments")
        response = self.write(self.foreman_a, "DELETE", f"/api/crews/{self.crew_a}/members/{worker}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows("assignments"), before)
        self.assertEqual(self.board()["members"], [])
        self.assertEqual(len(self.foreman_a.get("/api/assignments?date=2026-09-11").get_json()["rows"]), 1)
        self.assertEqual(self.add(self.crew_b, [worker], self.foreman_b).status_code, 200)
        locked = self.board(self.crew_b, self.foreman_b)["members"][0]
        self.assertTrue(locked["locked"])
        self.assertEqual(self.place([worker], self.sites[1], {str(worker): locked["edit_token"]},
                                    crew=self.crew_b, client=self.foreman_b).status_code, 409)
        self.assertEqual(self.rows("assignments"), before)
        self.assertEqual(self.place([worker], self.sites[1], crew=self.crew_b, client=self.foreman_b,
                                    work_date="2026-09-12").status_code, 200)
        self.assertEqual(len(self.rows("assignments")), 2)

    def test_admin_assignment_uses_crew_owner_and_records_actual_actor(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker], self.admin)
        self.assertEqual(self.place([worker], self.sites[0], client=self.admin).status_code, 200)
        self.assertEqual(self.rows("assignments")[0]["foreman_user_id"], self.owner_a)
        self.assertEqual(self.rows("assignment_events")[0]["changed_by"], self.admin_id)

    def test_legacy_assignment_endpoint_is_closed(self):
        response = self.write(self.foreman_a, "POST", "/api/assignments", {
            "date": "2026-09-11", "shift": "1 смена", "worker_ids": [self.worker_ids[0]],
            "subobject_id": self.sites[0],
        })
        self.assertEqual(response.status_code, 410)
        self.assertEqual(self.rows("assignments"), [])

    def test_legacy_night_assignment_is_read_and_updated_without_duplicate(self):
        worker = self.worker_ids[0]
        self.add(self.crew_a, [worker])
        with self.module.app.app_context():
            db = self.module.get_db()
            original_id = db.execute(
                """INSERT INTO assignments(work_date, shift, subobject_id, worker_id, employer,
                       foreman_user_id, created_at, crew_id, edit_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-09-11", "Ночная смена", self.sites[0], worker, "Test employer",
                 self.owner_a, self.module.utc_now(), None, "legacy-night-token"),
            ).lastrowid
            db.commit()
        board = self.board(shift="2 смена")["members"]
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["assignment_id"], original_id)
        self.assertEqual(board[0]["subobject_id"], self.sites[0])
        self.assertFalse(board[0]["locked"])
        response = self.place([worker], self.sites[1], {str(worker): board[0]["edit_token"]}, shift="2 смена")
        self.assertEqual(response.status_code, 200, response.get_json())
        rows = self.rows("assignments")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], original_id)
        self.assertEqual(rows[0]["subobject_id"], self.sites[1])
        self.assertEqual(self.board(shift="2 смена")["members"][0]["subobject_id"], self.sites[1])
        self.assertEqual(self.place([worker], self.sites[0], work_date="2026-09-12", shift="Ночная смена").status_code, 400)

    def test_viewer_cannot_manage_crews_members_or_workplaces(self):
        worker = self.worker_ids[0]
        self.assertEqual(self.viewer.get("/api/crews").status_code, 403)
        self.assertEqual(self.viewer.get(f"/api/crews/{self.crew_a}/candidates").status_code, 403)
        self.assertEqual(self.viewer.get(f"/api/crews/{self.crew_a}/board?date=2026-09-11&shift=1+смена").status_code, 403)
        self.assertEqual(self.write(self.viewer, "POST", "/api/crews", {
            "name": "Forbidden", "owner_user_id": self.owner_a,
        }).status_code, 403)
        self.assertEqual(self.add(self.crew_a, [worker], self.viewer).status_code, 403)
        self.assertEqual(self.place([worker], self.sites[0], client=self.viewer).status_code, 403)
        self.assertEqual(self.write(self.viewer, "DELETE", f"/api/crews/{self.crew_a}/members/{worker}").status_code, 403)
        self.assertEqual(self.rows("crew_members"), [])
        self.assertEqual(self.rows("assignments"), [])


if __name__ == "__main__":
    unittest.main()
