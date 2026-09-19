import unittest
import test_smu as smu_tests


class PpsCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smu_tests.SmuCatalogTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        smu_tests.SmuCatalogTest.tearDownClass()

    def setUp(self):
        self.fixture = smu_tests.SmuCatalogTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client, self.headers = self.fixture.client, self.fixture.headers

    def create(self, name='ППС 987'):
        r = self.client.post('/api/pps', json={'name': name}, headers=self.headers)
        self.assertEqual(r.status_code, 201, r.json)
        return self.row(r.json['id'])

    def row(self, pps_id):
        return next(r for r in self.client.get('/api/pps').json['rows'] if r['id'] == pps_id)

    def test_bind_move_clear_and_delete_protection(self):
        p, other = self.create(), self.create('ППС 988')
        smu = self.fixture.create('Участок для ППС')
        self.assertEqual(self.fixture.update(smu, smu['name'], pps_id=p['id']).status_code, 200)
        self.assertEqual(self.row(p['id'])['smu'][0]['id'], smu['id'])
        self.assertEqual(self.client.delete('/api/pps/'+str(p['id']), headers=self.headers,
                                          json={'expected_token': p['edit_token']}).status_code, 409)
        self.assertEqual(self.fixture.update(smu, smu['name'], pps_id=other['id']).status_code, 409)
        smu = next(r for r in self.fixture.rows() if r['id'] == smu['id'])
        self.assertEqual(self.fixture.update(smu, smu['name'], pps_id=other['id']).status_code, 200)
        self.assertEqual(self.row(p['id'])['smu'], [])
        smu = next(r for r in self.fixture.rows() if r['id'] == smu['id'])
        self.assertEqual(self.fixture.update(smu, smu['name'], pps_id=None).status_code, 200)
        self.assertEqual(self.client.delete('/api/pps/'+str(p['id']), headers=self.headers,
                                          json={'expected_token': p['edit_token']}).status_code, 200)

    def test_names_permissions_and_concurrent_edits(self):
        p = self.create()
        self.assertEqual(p['name'], 'ППС-987')
        for name in ('ппс987', ' ППС—987 '):
            self.assertEqual(self.client.post('/api/pps', json={'name': name}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/api/pps', json={'name': 'ППС-999'}).status_code, 403)
        for role in ('foreman', 'viewer'):
            self.assertEqual(self.fixture.fixture.clients[role].post('/api/pps', json={'name': 'ППС-999'}, headers=self.headers).status_code, 403)
        body = {'name': 'ППС Северный', 'active': True, 'expected_token': p['edit_token']}
        self.assertEqual(self.client.patch('/api/pps/'+str(p['id']), json=body, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.patch('/api/pps/'+str(p['id']), json=body, headers=self.headers).status_code, 409)

    def test_inactive_parent_retained_but_cannot_be_newly_assigned(self):
        p = self.create()
        a, b = self.fixture.create('Участок A'), self.fixture.create('Участок B')
        self.assertEqual(self.fixture.update(a, a['name'], pps_id=p['id']).status_code, 200)
        self.assertEqual(self.client.patch('/api/pps/'+str(p['id']), json={
            'name': p['name'], 'active': False, 'expected_token': p['edit_token']}, headers=self.headers).status_code, 200)
        a = next(r for r in self.fixture.rows() if r['id'] == a['id'])
        self.assertEqual(self.fixture.update(a, a['name'], pps_id=p['id']).status_code, 200)
        for value in (p['id'], True, '1', -1, 999999):
            self.assertEqual(self.fixture.update(b, b['name'], pps_id=value).status_code, 400)
        self.assertIsNone(next(r for r in self.fixture.rows() if r['id'] == b['id'])['pps_id'])

    def test_membership_does_not_rewrite_employee_or_access_data(self):
        p, smu = self.create(), self.fixture.create('Участок без вывода из номера')
        with self.fixture.module.app.app_context():
            db = self.fixture.module.get_db()
            worker = self.fixture.worker(db, 'Тестовый работник', smu['name'])
            db.commit()
            before = tuple(db.execute('SELECT * FROM workers WHERE id=?', (worker,)).fetchone())
            rights = [tuple(r) for r in db.execute('SELECT * FROM user_smu_access')]
        self.assertEqual(self.fixture.update(smu, smu['name'], pps_id=p['id']).status_code, 200)
        self.assertEqual(self.row(p['id'])['employee_count'], 1)
        with self.fixture.module.app.app_context():
            self.fixture.module.init_db()
            db = self.fixture.module.get_db()
            self.assertEqual(tuple(db.execute('SELECT * FROM workers WHERE id=?', (worker,)).fetchone()), before)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM user_smu_access')], rights)
        self.assertEqual(self.row(p['id'])['smu'][0]['id'], smu['id'])
