import json
import unittest
import test_preferences


class CrewCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preferences.PreferencesTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_preferences.PreferencesTest.tearDownClass()

    def setUp(self):
        self.fx = test_preferences.PreferencesTest()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.client = self.fx.clients['admin']
        self.headers = {'X-CSRF-Token':'preferences-csrf'}
        response = self.client.post('/api/crews',json={'name':'Бригада тест'},headers=self.headers)
        self.assertEqual(response.status_code,201)
        self.crew = response.json['id']
        with self.fx.module.app.app_context():
            db = self.fx.module.get_db()
            self.ids = []
            for n,department in enumerate(('СМУ А','СМУ Б')):
                worker = db.execute("INSERT INTO workers(full_name,personnel_no,department) VALUES (?,?,?)",
                    ('Сотрудник ' + str(n), 'catalog-' + str(n), department)).lastrowid
                self.ids.append(worker)
                db.execute('INSERT INTO crew_members VALUES (?,?)',(self.crew,worker))
            db.commit()

    def item(self):
        return next(row for row in self.client.get('/api/crew-catalog').json['rows'] if row['id']==self.crew)

    def save(self,item=None,**changes):
        item = item or self.item()
        data = {key:item[key] for key in ('name','linear_itr','brigadier','expected_token')}
        return self.client.patch('/api/crew-catalog/' + str(self.crew),json={**data,**changes},headers=self.headers)

    def test_list_members_rename_and_stale_write_preserve_roster(self):
        item = self.item()
        self.assertEqual(item['member_count'],2)
        self.assertEqual(item['departments'],['СМУ А','СМУ Б'])
        self.assertEqual(self.save(item,name='Новое имя',linear_itr='Иванов ИТР').status_code,200)
        self.assertEqual(self.save(item,name='Устаревшее имя').status_code,409)
        fresh = self.item()
        self.assertEqual(fresh['name'],'Новое имя')
        self.assertEqual(fresh['linear_itr'],'Иванов ИТР')
        members = self.client.get(f'/api/crew-catalog/{self.crew}/members').json['rows']
        self.assertEqual({row['id'] for row in members},set(self.ids))

    def test_scope_filters_members_and_blocks_whole_mixed_crew_edit(self):
        with self.fx.module.app.app_context():
            db = self.fx.module.get_db()
            db.execute('INSERT INTO user_smu_access VALUES (?,?,?,?,?,?)',
                (self.fx.admin,'selected',json.dumps(['СМУ А']),'access',self.fx.admin,'now'))
            db.commit()
        item = self.item()
        self.assertEqual(item['member_count'],1)
        self.assertEqual(item['departments'],['СМУ А'])
        self.assertFalse(item['can_edit'])
        self.assertEqual(self.save(item,name='Запрещено').status_code,403)
        members = self.client.get(f'/api/crew-catalog/{self.crew}/members').json['rows']
        self.assertEqual([row['id'] for row in members],[self.ids[0]])

    def test_validation_csrf_roles_and_name_conflict_are_atomic(self):
        self.client.post('/api/crews',json={'name':'Занято'},headers=self.headers)
        original = self.item()
        self.assertEqual(self.save(name='Занято',linear_itr='Не сохранять').status_code,409)
        for changes in ({'name':''},{'name':'x'*121},{'owner_user_id':self.fx.admin},
                        {'linear_itr_person_id':True},{'linear_itr_person_id':999999,'linear_itr':'Неизвестный'}):
            self.assertEqual(self.save(**changes).status_code,400,changes)
        self.assertEqual(self.item(),original)
        self.assertEqual(self.client.patch(f'/api/crew-catalog/{self.crew}',json={}).status_code,403)
        for role in ('foreman','viewer'):
            client = self.fx.clients[role]
            self.assertEqual(client.get('/api/crew-catalog').status_code,403)
            self.assertEqual(client.get(f'/api/crew-catalog/{self.crew}/members').status_code,403)
            self.assertEqual(client.patch(f'/api/crew-catalog/{self.crew}',json={},headers=self.headers).status_code,403)

    def test_responsible_binding_is_preserved_and_conflicts_with_staffing_edit(self):
        with self.fx.module.app.app_context():
            db = self.fx.module.get_db()
            batch = db.execute("INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json) VALUES ('catalog-test','test',?,'now',0,'{}')",(self.fx.admin,)).lastrowid
            person = db.execute("INSERT INTO staffing_people(import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew) VALUES (?,1,'Ответственный','p1','','','','')",(batch,)).lastrowid
            db.commit()
        self.assertEqual(self.save(linear_itr='Ответственный',linear_itr_person_id=person).status_code,200)
        item = self.item()
        self.assertEqual(self.save(item,name='Переименованная').status_code,200)
        self.assertEqual(self.item()['linear_itr_person_id'],person)
        stale = self.item()
        with self.fx.module.app.app_context():
            db = self.fx.module.get_db()
            db.execute("UPDATE crews SET details_token='changed',brigadier='Другой' WHERE id=?",(self.crew,))
            db.commit()
        self.assertEqual(self.save(stale,name='Устаревшая').status_code,409)
