"""Specialties organize existing profession identities; changes stay audited and scoped."""
import os
import unittest
from uuid import uuid4

from tests import test_workforce_api as workforce_tests


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select isolated PostgreSQL staging explicitly.')
class ProfessionHierarchyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workforce_tests.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.fixture = workforce_tests.WorkforceApiTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.db = self.fixture.db
        self.request = self.fixture.request
        self.suffix = self.fixture.suffix

    def create(self, kind, **values):
        response = self.request('post', 'catalog/'+kind, {
            'label':'Проверка '+kind+' '+uuid4().hex, 'reason':'Проверка иерархии', 'request_key':str(uuid4()), **values})
        self.assertEqual(response.status_code, 201, response.data)
        return response.json

    def change(self, kind, row, **values):
        return self.request('patch', 'catalog/'+kind+'/'+row['code'], {
            'token':row['edit_token'],'request_key':str(uuid4()),'reason':'Уточнение справочника',**values})

    def profile(self, worker=None):
        return self.request('get',f'people/{worker or self.fixture.worker}',role='rotation').json['profile']

    def assign(self, values, worker=None):
        worker=worker or self.fixture.worker
        return self.request('patch', f'people/{worker}/profile', {
            'token':self.profile(worker)['token'],'reason':'Уточнение должности','request_key':str(uuid4()),**values},'rotation')

    def test_parent_child_creation_replay_and_stale_update_preserve_variants(self):
        parent=self.create('specialty')
        payload={'label':'Арматурщик 5 разряда '+self.suffix, 'specialty_code':parent['code'],'grade':5,
                 'reason':'Добавление разряда','request_key':str(uuid4())}
        first=self.request('post','catalog/profession',payload)
        self.assertEqual(first.status_code,201,first.data)
        repeated=self.request('post','catalog/profession',payload)
        self.assertEqual(repeated.status_code,200,repeated.data)
        self.assertEqual(first.json['code'],repeated.json['code'])
        variant=self.create('profession',specialty_code=parent['code'],grade=5)
        self.assertNotEqual(variant['code'],first.json['code'])
        revised=self.change('profession',first.json,grade=6)
        self.assertEqual(revised.status_code,200,revised.data)
        self.assertEqual(self.change('profession',first.json,grade=4).status_code,409)
        reference=self.request('get','reference').json['catalog']
        children={r['code']:r for r in reference if r.get('specialty_code')==parent['code']}
        self.assertEqual({r['grade'] for r in children.values()},{5,6})
        audit=self.db.native("SELECT after_json FROM workforce_audit WHERE entity_type='catalog.profession' AND entity_id=%s ORDER BY id DESC",(first.json['code'],)).fetchone()
        self.assertIsNotNone(audit)

    def test_hierarchy_validation_permissions_and_parent_foreign_key(self):
        parent=self.create('specialty')
        for values in ({'grade':0},{'grade':100},{'grade':True},{'grade':4.5},{'grade':'4'},
                       {'specialty_code':'citizenship.ru'},{'specialty_code':'specialty.missing'},
                       {'specialty_code':None}):
            response=self.request('post','catalog/profession',{
                'label':'Неверное '+uuid4().hex,'specialty_code':parent['code'],'grade':4,
                'reason':'Проверка','request_key':str(uuid4()),**values})
            self.assertEqual(response.status_code,400,response.data)
        invalid=self.request('post','catalog/specialty',{
            'label':'Неверная специальность','specialty_code':parent['code'],'grade':3,
            'reason':'Проверка','request_key':str(uuid4())})
        self.assertEqual(invalid.status_code,400,invalid.data)
        for role in ('foreman','rotation','recruitment','hr_viewer'):
            response=self.request('post','catalog/specialty',{
                'label':'Нет прав','reason':'Проверка','request_key':str(uuid4())},role)
            self.assertEqual(response.status_code,403,response.data)
        actor=self.fixture.users['admin']['id']
        self.db.native('''INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at)
            VALUES (%s,'selected','["TEST-SMU"]',%s,%s,'2026-09-17')''',(actor,self.suffix,actor))
        response=self.request('post','catalog/specialty',{'label':'Нет общего доступа','reason':'Проверка','request_key':str(uuid4())})
        self.assertEqual(response.status_code,403,response.data)

    def test_disabled_parent_rejects_new_assignments_and_preserves_current_profession(self):
        parent=self.create('specialty')
        leaf=self.create('profession',specialty_code=parent['code'],grade=4)
        assigned=self.assign({'profession_code':leaf['code']})
        self.assertEqual(assigned.status_code,200,assigned.data)
        original=self.profile()
        updated=self.change('specialty',parent,active=False)
        self.assertEqual(updated.status_code,200,updated.data)
        self.assertEqual(self.assign({'profession_code':leaf['code'],'phone':'000000'}).status_code,200)
        # Both code and legacy text inputs must honor the archived parent.
        other=self.fixture.ids[1]
        self.db.native("UPDATE workers SET department='TEST-SMU' WHERE id=%s",(other,))
        self.db.native("UPDATE workforce_profiles SET employment_code='employment.staff' WHERE worker_id=%s",(other,))
        for values in ({'profession_code':leaf['code']},{'profession':leaf['label']}):
            response=self.assign(values,other)
            self.assertEqual(response.status_code,400,response.data)
        fresh=self.request('post','catalog/profession',{'label':'Новый '+self.suffix,
            'specialty_code':parent['code'],'grade':5,'reason':'Проверка','request_key':str(uuid4())})
        self.assertEqual(fresh.status_code,400,fresh.data)
        retained=self.change('profession',leaf,specialty_code=parent['code'],grade=4,label=leaf['label'])
        self.assertEqual(retained.status_code,200,retained.data)
        current=self.profile()
        self.assertEqual(current['profession_code'],original['profession_code'])
        self.assertEqual(current['profession'],original['profession'])

    def test_group_rename_and_reparent_do_not_rewrite_worker_job_or_gdlr(self):
        parent=self.create('specialty')
        other=self.create('specialty')
        leaf=self.create('profession',specialty_code=parent['code'],grade=None)
        self.assertEqual(self.assign({'profession_code':leaf['code']}).status_code,200)
        query='SELECT profession,profession_code,category FROM workers WHERE id=%s'
        before=list(self.db.native(query,(self.fixture.worker,)).fetchone())
        rename=self.change('specialty',parent,label='Уточнённая специальность '+self.suffix)
        self.assertEqual(rename.status_code,200,rename.data)
        reparent=self.change('profession',leaf,specialty_code=other['code'],grade=5)
        self.assertEqual(reparent.status_code,200,reparent.data)
        self.assertEqual(list(self.db.native(query,(self.fixture.worker,)).fetchone()),before)
