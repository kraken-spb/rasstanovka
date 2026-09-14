import unittest

import test_locations as location_tests


class StageEditorTest(unittest.TestCase):
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

    def stage(self, stage_id):
        data = self.client.get('/api/locations').get_json()
        row = next(r for r in data['stages'] if r['id'] == stage_id)
        return {**row, **next(r for r in data['stage_details'] if r['id'] == stage_id)}

    def create(self, name='Этап новый'):
        response = self.fixture.write('POST', 'stages', {'name': name})
        self.assertEqual(response.status_code, 201, response.get_json())
        return self.stage(response.get_json()['id'])

    def test_create_rename_delete_and_stale_tokens(self):
        stage = self.create('  Этап   новый  ')
        self.assertEqual(stage['name'], 'Этап новый')
        url = f"stages/{stage['id']}"
        self.assertEqual(self.fixture.write('PATCH', url, {'name': 'Этап другой'}).status_code, 409)
        data = {'name': 'Этап другой', 'expected_token': stage['edit_token']}
        self.assertEqual(self.fixture.write('PATCH', url, data).status_code, 200)
        changed = self.stage(stage['id'])
        with self.module.app.app_context():
            self.module.init_db()
        self.assertEqual(self.stage(stage['id']), changed)
        self.assertEqual(self.fixture.write('PATCH', url, {'name': stage['name'], 'expected_token': changed['edit_token']}).status_code, 200)
        self.assertEqual(self.fixture.write('PATCH', url, data).status_code, 409)
        self.assertEqual(self.fixture.write('DELETE', url, {'expected_token': stage['edit_token']}).status_code, 409)
        current = self.stage(stage['id'])
        self.assertEqual(self.fixture.write('DELETE', url, {'expected_token': current['edit_token']}).status_code, 200)
        self.assertFalse(self.client.get('/api/locations').get_json()['stages'])

    def test_validation_and_normalized_duplicates(self):
        first = self.create()
        second = self.create('Этап второй')
        self.assertEqual(self.fixture.write('POST', 'stages', {'name': ' ЭТАП  НОВЫЙ '}).status_code, 409)
        response = self.fixture.write('PATCH', f"stages/{second['id']}", {'name': first['name'], 'expected_token': second['edit_token']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stage(second['id']), second)
        for name in ('', ' ', 'x' * 301, 'Имя\nстрока', None, 5):
            self.assertEqual(self.fixture.write('POST', 'stages', {'name': name}).status_code, 400)
        self.assertEqual(self.fixture.write('PATCH', 'stages/999999', {'name': 'Не найден'}).status_code, 404)

    def test_rename_preserves_links_history_and_updates_reference(self):
        stage = self.create()
        group = self.fixture.create('objects', 'Группа этапа', stage_id=stage['id'])
        site = self.fixture.create('subobjects', 'Подобъект этапа', object_id=group['id'])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,crew_id,created_at)
                VALUES ('2026-09-13','1 смена',?,(SELECT id FROM workers LIMIT 1),?,
                (SELECT id FROM crews LIMIT 1),'now')''', (site['id'], self.fixture.admin_id))
            db.commit()
            tables = ('objects', 'subobjects', 'assignments', 'assignment_events', 'daily_staffing_plans')
            before = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}
        etag = self.client.get('/api/reference?scope=locations').headers['ETag']
        url = f"stages/{stage['id']}"
        self.assertEqual(self.fixture.write('DELETE', url, {'expected_token': stage['edit_token']}).status_code, 409)
        self.assertEqual(self.fixture.write('PATCH', url, {'name': 'Этап уточнённый', 'expected_token': stage['edit_token']}).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(before, {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables})
        response = self.client.get('/api/reference?scope=locations', headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers['ETag'], etag)
        self.assertEqual(next(r for r in response.get_json()['objects'] if r['id'] == group['id'])['stage_name'], 'Этап уточнённый')
        # A group linked after an empty-stage editor was opened must also block deletion.
        target = self.create('Этап назначения')
        self.assertEqual(self.fixture.write('PATCH', f"objects/{group['id']}", {'name': group['name'], 'stage_id': target['id'], 'expected_token': group['edit_token']}).status_code, 200)
        self.assertEqual(self.fixture.write('DELETE', f"stages/{target['id']}", {'expected_token': target['edit_token']}).status_code, 409)
        self.assertEqual(self.fixture.write('DELETE', url, {'expected_token': self.stage(stage['id'])['edit_token']}).status_code, 200)

    def test_super_admin_and_csrf_required(self):
        stage = self.create()
        url = f"stages/{stage['id']}"
        data = {'name': 'Этап доступа', 'expected_token': stage['edit_token']}
        for method, suffix in (('POST', 'stages'), ('PATCH', url), ('DELETE', url)):
            self.assertEqual(self.client.open('/api/locations/' + suffix, method=method, json=data).status_code, 403)
            for role in ('foreman', 'viewer'):
                self.assertEqual(self.fixture.write(method, suffix, data, role).status_code, 403)
        self.assertIn('id="location-stage-add"', self.client.get('/').get_data(as_text=True))
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.fixture.admin_id,))
            db.commit()
        self.assertEqual(self.client.get('/api/locations').status_code, 200)
        for method, suffix in (('POST', 'stages'), ('PATCH', url), ('DELETE', url)):
            self.assertEqual(self.fixture.write(method, suffix, data).status_code, 403)
        self.assertIn('hidden id="location-stage-add"', self.client.get('/').get_data(as_text=True))
