import unittest
import test_staffing


class OutstaffResponsibleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_staffing.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_staffing.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.f = test_staffing.StaffingWorkflowTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.client = self.f.admin
        self.name = 'Ответственный Аутстафф Тестовый'
        with self.f.app.app_context():
            db = self.f.module.get_db()
            self.crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Аутстафф тест',?,'now')", (self.f.admin_id,)).lastrowid
            batch = db.execute("INSERT INTO outstaff_imports(request_key,filename,sheet,file_hash,selection_json,imported_by,imported_at,selected_count) VALUES ('outstaff-test','test.xlsx','Аустаффинг','hash','[]',?,'now',2)", (self.f.admin_id,)).lastrowid
            self.ids = []
            for index in range(2):
                worker = db.execute("INSERT INTO workers(full_name,personnel_no,profession,department,employer,category) VALUES (?,?,'Мастер','Строительно-монтажный участок № 15.1','УПМА','Монтажник ТТ')", (self.name, 'OUT-TEST-' + str(index))).lastrowid
                db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.crew, worker))
                category = db.execute("SELECT id FROM gdlr_categories WHERE name='Монтажник ТТ'").fetchone()[0]
                db.execute("INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,'binding',?,'now')", (worker, category, self.f.admin_id))
                db.execute("INSERT INTO outstaff_members VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (worker, 'identity' + str(index), batch, index + 2, 'СМУ 15.1', 'Монтажник ТТ', 'Монтаж', 'УПМА', 'Аутстафф', '', '', 'token'))
                self.ids.append(worker)
            db.commit()
        self.ref = 'outstaff:' + str(self.ids[0])

    def people(self):
        return self.client.get('/api/staffing/people').get_json()['people']

    def board(self):
        return self.client.get('/api/staffing?date=2026-09-12&shift=all').get_json()

    def rows(self):
        return [r for r in self.board()['rows'] if r['id'] in self.ids]

    def row_save(self, row, ref=None, name=None, client=None):
        return self.f.write(f"/api/staffing/crews/{self.crew}/workers/{row['id']}/linear-itr", {
            'linear_itr_override': self.name if name is None else name,
            'linear_itr_person_id': self.ref if ref is None else ref, 'expected_token': row['row_token']}, client)

    def test_directory_without_attendance_import_and_cache_invalidation(self):
        response = self.client.get('/api/staffing/people')
        self.assertIsNone(response.json['import_id'])
        people = [p for p in response.json['people'] if p['source_kind'] == 'outstaff']
        self.assertEqual({p['id'] for p in people}, {self.ref, 'outstaff:' + str(self.ids[1])})
        self.assertTrue(all(p['employer'] == 'УПМА' for p in people))
        self.assertEqual(self.client.get('/api/staffing/people', headers={'If-None-Match': response.headers['ETag']}).status_code, 304)
        with self.f.app.app_context():
            db = self.f.module.get_db(); db.execute('UPDATE workers SET active=0 WHERE id=?', (self.ids[0],)); db.commit()
        changed = self.client.get('/api/staffing/people', headers={'If-None-Match': response.headers['ETag']})
        self.assertEqual(changed.status_code, 200)
        self.assertNotIn(self.ref, {p['id'] for p in changed.json['people']})
        self.assertEqual(self.f.viewer.get('/api/staffing/people').status_code, 403)

    def test_row_binding_persists_and_same_names_keep_distinct_hierarchy(self):
        self.f.apply()
        rows = self.rows()
        self.assertEqual(self.row_save(rows[0]).status_code, 200)
        self.assertEqual(self.row_save(rows[1], 'outstaff:' + str(self.ids[1])).status_code, 200)
        rows = self.rows()
        self.assertEqual(rows[0]['linear_itr_person_id'], self.ref)
        self.assertEqual(len({r['itr_group_key'] for r in rows}), 2)
        self.assertTrue(all('Аутстафф' in r['itr_group_label'] for r in rows))
        with self.f.app.app_context():
            self.f.module.init_db()
        self.assertEqual(self.rows()[0]['linear_itr_person_id'], self.ref)
        self.assertEqual(self.row_save(rows[0], name='Неверное ФИО').status_code, 409)
        self.assertEqual(self.row_save(rows[0], ref=True).status_code, 400)
        self.assertEqual(self.row_save(rows[0], ref='outstaff:9999999').status_code, 409)

    def test_bulk_atomic_stale_tokens_csrf_and_roles(self):
        rows = self.rows()
        payload = {**self.f.group_payload(rows, True), 'field':'linear_itr', 'value':self.name, 'linear_itr_person_id':self.ref}
        path = '/api/staffing/groups/responsible'
        self.assertEqual(self.f.write(path, payload, self.f.viewer).status_code, 403)
        self.assertEqual(self.f.write(path, payload, self.f.foreman).status_code, 403)
        self.assertEqual(self.client.put(path, json=payload).status_code, 403)
        stale = {**payload, 'expected_tokens':{**payload['expected_tokens'],str(rows[1]['id']):'stale'}}
        self.assertEqual(self.f.write(path, stale).status_code, 409)
        self.assertTrue(all(r['linear_itr_person_id'] is None for r in self.rows()))
        self.assertEqual(self.f.write(path, payload).status_code, 200)
        self.assertTrue(all(r['linear_itr_person_id'] == self.ref for r in self.rows()))

    def test_crew_default_catalog_rename_and_creation_preserve_binding(self):
        crew = next(c for c in self.board()['crews'] if c['id'] == self.crew)
        self.assertEqual(self.f.write(f'/api/staffing/crews/{self.crew}/details', {
            'linear_itr':self.name, 'linear_itr_person_id':self.ref, 'brigadier':'', 'brigadier_person_id':None,
            'expected_token':crew['details_token']}).status_code, 200)
        self.assertTrue(all(r['crew_linear_itr_person_id'] == self.ref for r in self.rows()))
        catalog = next(c for c in self.client.get('/api/crew-catalog').json['rows'] if c['id'] == self.crew)
        self.assertEqual(catalog['linear_itr_person_id'], self.ref)
        headers = {'X-CSRF-Token':'staffing-csrf'}
        payload = {'name':'Переименованная бригада', 'linear_itr':self.name, 'brigadier':'', 'expected_token':catalog['expected_token']}
        self.assertEqual(self.client.patch(f'/api/crew-catalog/{self.crew}', json=payload, headers=headers).status_code, 200)
        self.assertTrue(all(r['crew_linear_itr_person_id'] == self.ref for r in self.rows()))
        created = self.client.post('/api/crews', json={'name':'Новая аутстафф', 'linear_itr':self.name, 'linear_itr_person_id':self.ref}, headers=headers)
        self.assertEqual(created.status_code, 201, created.json)
        new = next(c for c in self.client.get('/api/crew-catalog').json['rows'] if c['id'] == created.json['id'])
        self.assertEqual(new['linear_itr_person_id'], self.ref)

    def test_inactive_candidate_rejected_and_manual_replacement_clears_link(self):
        row = self.rows()[1]
        self.assertEqual(self.row_save(row).status_code, 200)
        with self.f.app.app_context():
            db = self.f.module.get_db(); db.execute('UPDATE workers SET active=0 WHERE id=?', (self.ids[0],)); db.commit()
        row = next(r for r in self.rows() if r['id'] == self.ids[1])
        self.assertEqual(self.row_save(row).status_code, 409)
        url = f"/api/staffing/crews/{self.crew}/workers/{row['id']}/linear-itr"
        self.assertEqual(self.f.write(url, {'linear_itr_override':'Вручную', 'linear_itr_person_id':None, 'expected_token':row['row_token']}).status_code, 200)
        row = next(r for r in self.rows() if r['id'] == self.ids[1])
        self.assertIsNone(row['linear_itr_worker_id'])
        self.assertIsNone(row['linear_itr_person_id'])

    def test_history_undo_redo_restores_worker_reference(self):
        self.assertEqual(self.row_save(self.rows()[1]).status_code, 200)
        for direction in ('undo','redo'):
            action = self.client.get('/api/staffing/history').json[direction]
            result = self.client.post('/api/staffing/history/' + direction, json={'id':action['id'],'token':action['token']},headers={'X-CSRF-Token':'staffing-csrf'})
            self.assertEqual(result.status_code, 200, result.json)
            row = next(r for r in self.rows() if r['id'] == self.ids[1])
            self.assertEqual(row['linear_itr_person_id'], None if direction == 'undo' else self.ref)

    def test_brigadier_binding_and_return_to_crew_default(self):
        row = self.rows()[1]
        url = f"/api/staffing/crews/{self.crew}/workers/{row['id']}/brigadier"
        result = self.f.write(url, {'brigadier_override':self.name, 'brigadier_person_id':self.ref, 'expected_token':row['row_token']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.rows()[1]['brigadier_person_id'], self.ref)
        result = self.f.write(url, {'brigadier_override':None, 'brigadier_person_id':None, 'expected_token':result.json['row_token']})
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(self.rows()[1]['brigadier_worker_id'])
