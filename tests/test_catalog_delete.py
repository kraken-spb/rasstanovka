import unittest

import test_locations as location_tests


class CatalogDeleteTest(unittest.TestCase):
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

    def create(self, path, **data):
        response = self.client.post(path, json={'name': 'Строка для удаления', **data}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.get_json())
        row_id = response.get_json()['id']
        if path.startswith('/api/locations/'):
            rows = self.client.get('/api/locations').get_json()[path.rsplit('/', 1)[1]]
        else:
            rows = self.client.get(path).get_json()['rows']
        return next(row for row in rows if row['id'] == row_id)

    def delete(self, path, row, **overrides):
        return self.client.delete(path + '/' + str(row['id']),
                                  json={'expected_token': row['edit_token'], **overrides}, headers=self.headers)

    def test_all_catalogs_require_role_csrf_and_current_revision(self):
        parent = self.create('/api/locations/objects', name='Родитель')
        for path, extra in (('/api/gdlr-categories', {}), ('/api/contractors', {}),
                            ('/api/locations/objects', {}),
                            ('/api/locations/subobjects', {'object_id': parent['id']})):
            with self.subTest(path=path):
                row = self.create(path, **extra)
                url = path + '/' + str(row['id'])
                body = {'expected_token': row['edit_token']}
                self.assertEqual(self.client.delete(url, json=body).status_code, 403)
                for role in ('foreman', 'viewer'):
                    self.assertEqual(self.fixture.clients[role].delete(url, json=body, headers=self.headers).status_code, 403)
                with self.module.app.app_context():
                    db = self.module.get_db()
                    db.execute("UPDATE users SET role='admin' WHERE id=?", (self.fixture.admin_id,))
                    db.commit()
                self.assertEqual(self.delete(path, row).status_code, 403)
                with self.module.app.app_context():
                    db = self.module.get_db()
                    db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.fixture.admin_id,))
                    db.commit()
                self.assertEqual(self.delete(path, row, expected_token=None).status_code, 409)
                update = self.client.patch(url, json={**body, **extra, 'name': 'Изменённая строка', 'active': True}, headers=self.headers)
                self.assertEqual(update.status_code, 200)
                self.assertEqual(self.delete(path, row).status_code, 409)
                rows = (self.client.get('/api/locations').get_json()[path.rsplit('/', 1)[1]]
                        if path.startswith('/api/locations/') else self.client.get(path).get_json()['rows'])
                current = next(item for item in rows if item['id'] == row['id'])
                self.assertEqual(self.delete(path, current).status_code, 200)
                self.assertEqual(self.delete(path, current).status_code, 404)

    def test_bound_categories_and_contractors_preserve_employee_links(self):
        for path, table, column in (('/api/gdlr-categories', 'employee_gdlr', 'category_id'),
                                    ('/api/contractors', 'employee_contractors', 'contractor_id')):
            row = self.create(path)
            with self.module.app.app_context():
                db = self.module.get_db()
                worker = db.execute('SELECT id FROM workers LIMIT 1').fetchone()[0]
                db.execute(f'''INSERT INTO {table}(worker_id,{column},edit_token,updated_by,updated_at)
                    VALUES (?,?,'bound',?,'now') ON CONFLICT(worker_id) DO UPDATE SET {column}=excluded.{column}''',
                           (worker, row['id'], self.fixture.admin_id))
                db.commit()
            self.assertEqual(self.delete(path, row).status_code, 409)
            with self.module.app.app_context():
                self.assertEqual(self.module.get_db().execute(
                    f'SELECT {column} FROM {table} WHERE worker_id=?', (worker,)).fetchone()[0], row['id'])

    def test_locations_preserve_children_plans_assignments_and_history(self):
        group = self.create('/api/locations/objects')
        site = self.create('/api/locations/subobjects', object_id=group['id'])
        self.assertEqual(self.delete('/api/locations/objects', group).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db()
            worker = db.execute('SELECT id FROM workers LIMIT 1').fetchone()[0]
            crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Удаление',?,'now')",
                              (self.fixture.admin_id,)).lastrowid
            db.commit()
        cases = (
            ('daily_staffing_plans', "INSERT INTO daily_staffing_plans VALUES (?,'2026-09-13',0,?,'now','token')", (site['id'], self.fixture.admin_id)),
            ('staffing_plans', "INSERT INTO staffing_plans(work_date,shift,subobject_id,planned_count,created_by,updated_at) VALUES ('2026-09-13','1 смена',?,0,?,'now')", (site['id'], self.fixture.admin_id)),
            ('assignments', "INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,created_at) VALUES ('2026-09-13','1 смена',?,?,?,'now')", (site['id'], worker, self.fixture.admin_id)),
            ('assignment_events', "INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-13','1 смена',?,?,'now')", (crew, worker, site['id'], self.fixture.admin_id)),
            ('assignment_events', "INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,after_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-13','1 смена',?,?,'now')", (crew, worker, site['id'], self.fixture.admin_id)),
        )
        for table, sql, values in cases:
            with self.subTest(table=table, sql=sql):
                with self.module.app.app_context():
                    db = self.module.get_db()
                    db.execute(sql, values)
                    db.commit()
                    before = [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                self.assertEqual(self.delete('/api/locations/subobjects', site).status_code, 409)
                with self.module.app.app_context():
                    db = self.module.get_db()
                    self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM ' + table)])
                    db.execute('DELETE FROM ' + table)
                    db.commit()
        etag = self.client.get('/api/reference?scope=locations').headers['ETag']
        self.assertEqual(self.delete('/api/locations/subobjects', site).status_code, 200)
        self.assertNotEqual(etag, self.client.get('/api/reference?scope=locations').headers['ETag'])
        self.assertEqual(self.delete('/api/locations/objects', group).status_code, 200)

    def test_emptied_location_catalog_stays_empty_on_restart(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM subobjects')
            db.execute('DELETE FROM objects')
            db.commit()
            self.module.init_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM objects').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM subobjects').fetchone()[0], 0)
