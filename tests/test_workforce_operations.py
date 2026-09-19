import os
import unittest
from collections import defaultdict
from uuid import uuid4

from workforce_operations import readiness, forecast, issues_for, register_workforce_operations


def sample():
    return dict(id=1,full_name='Тест',department='СМУ',profession='Монтажник',active=1,
                category_id=1,category_allowed=True,staffing_ready=True,stage_code='stage.onsite',
                effective_date='2026-09-15',reason='Подтверждено',assigned=False)


def related():
    return {key:defaultdict(list) for key in ('movements','documents','conflicts','rotations')}


class OperationsCalculationTest(unittest.TestCase):
    def test_arrival_ticket_does_not_establish_readiness(self):
        person=sample();person['stage_code']='stage.inbound'
        result=readiness(person,[{'direction':'arrival','planned_date':'2026-09-17'}],[],'2026-09-17')
        self.assertFalse(result['ready'])
        self.assertIn('Нет подтверждённой явки',result['reasons'][0])

    def test_departure_and_document_expiry_are_date_specific(self):
        movement={'direction':'departure','result_code':'result.happened','actual_date':'2026-09-18'}
        doc={'expires_on':'2026-09-18','state_code':'docstate.ready'}
        self.assertTrue(readiness(sample(),[movement],[doc],'2026-09-17')['ready'])
        self.assertEqual(len(readiness(sample(),[movement],[doc],'2026-09-19')['reasons']),2)

    def test_forecast_deduplicates_people_and_excludes_cancelled_pvp_and_conflicts(self):
        data=related();data['movements'][1]=[
            {'direction':'departure','planned_date':'2026-09-18'},
            {'direction':'arrival','planned_date':'2026-09-19','destination_kind':'pvp'},
            {'direction':'arrival','planned_date':'2026-09-20','destination_kind':'site','result_code':'result.cancelled'},
            {'direction':'arrival','planned_date':'2026-09-21','destination_kind':'site'},
            {'direction':'departure','planned_date':'2026-09-21'},
            {'direction':'arrival','planned_date':'2026-09-22','destination_kind':'site'}]
        points=forecast([sample()],data,'2026-09-17',7,[])[0]['days']
        self.assertEqual([p['expected'] for p in points],[1,0,0,0,0,1,1])
        self.assertIsNone(points[0]['shortage'])
        self.assertEqual(points[1]['confirmed_base'],1)

    def test_explicit_zero_demand_differs_from_missing(self):
        demand={'day':'2026-09-17','department':'СМУ','profession':'Монтажник','required':0,'edit_token':'x'}
        rows=forecast([sample()],related(),'2026-09-17',2,[demand])[0]['days']
        self.assertEqual(rows[0]['required'],0);self.assertIsNone(rows[1]['required'])

    def test_attention_disappears_after_assignment(self):
        person=sample()
        self.assertEqual(issues_for(person,related(),'2026-09-17')[0]['kind'],'unassigned')
        person['assigned']=True
        self.assertEqual(issues_for(person,related(),'2026-09-17'),[])

    def test_daily_issues_have_separate_owners_and_disabled_category_warns(self):
        person=sample()
        self.assertNotEqual(issues_for(person,related(),'2026-09-17')[0]['key'],issues_for(person,related(),'2026-09-18')[0]['key'])
        person['category_allowed']=False
        self.assertFalse(readiness(person,[],[],'2026-09-17')['ready'])


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'),'Select an isolated PostgreSQL database.')
class OperationsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_workforce_api as fixtures
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        import test_workforce_api as fixtures
        self.f=fixtures.WorkforceApiTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        register_workforce_operations(self.f.app,lambda:self.f.db,lambda *roles:lambda fn:fn)
        self.f.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
            VALUES (%s,'stage.onsite','2026-09-15',TRUE,'Тест',%s,%s)''',(self.f.worker,self.f.users['admin']['id'],uuid4()))
        self.f.db.native('UPDATE workforce_profiles SET staffing_ready=TRUE WHERE worker_id=%s',(self.f.worker,))

    def test_scope_readiness_and_claim_optimistic_lock(self):
        f=self.f;path='operations?date=2026-09-17&department=TEST-SMU'
        response=f.request('get',path,role='rotation');self.assertEqual(response.status_code,200,response.data)
        item=next(r for r in response.json['rows'] if r['worker_id']==f.worker and r['kind']=='unassigned')
        body=dict(date='2026-09-17',due_date='2026-09-18',worker_id=f.worker,key=item['key'],expected_token='')
        self.assertEqual(f.request('post','operations/claim',body,'viewer').status_code,403)
        self.assertEqual(f.request('post','operations/claim',body,'rotation').status_code,200)
        self.assertEqual(f.request('post','operations/claim',body,'rotation').status_code,409)
        self.assertEqual(f.request('get',f'readiness/{f.ids[1]}?date=2026-09-17','', 'rotation').status_code,404)
        self.assertEqual(f.request('get','operations?date=2026-09-17&department=OTHER-SMU',role='rotation').json['total'],0)

    def test_demand_rights_missing_zero_and_stale(self):
        f=self.f;result=f.request('get','operations?date=2026-09-17&department=TEST-SMU').json
        profession=result['forecast'][0]['profession']
        body=dict(date='2026-09-17',department='TEST-SMU',profession=profession,required=0,expected_token='')
        self.assertEqual(f.request('post','demand',body,'rotation').status_code,403)
        self.assertEqual(f.request('post','demand',body).status_code,200)
        self.assertEqual(f.request('post','demand',body).status_code,409)
        point=f.request('get','operations?date=2026-09-17&department=TEST-SMU').json['forecast'][0]['days'][0]
        self.assertEqual(point['required'],0)

    def test_private_documents_hidden_from_staffing_roles(self):
        f=self.f
        f.db.native('''INSERT INTO workforce_documents(worker_id,document_code,state_code,expires_on,request_key,created_by,updated_by)
            VALUES (%s,'document.passport','docstate.ready','2026-09-18',%s,%s,%s)''',(f.worker,uuid4(),f.users['admin']['id'],f.users['admin']['id']))
        path='operations?date=2026-09-17&department=TEST-SMU'
        self.assertIn('document',f.request('get',path).json['counts'])
        self.assertNotIn('document',f.request('get',path,role='foreman').json['counts'])
        result=f.request('get',f'readiness/{f.worker}?date=2026-09-17',role='foreman').json
        self.assertIsNone(result['stage_reason'])

    def test_demand_survives_empty_workforce_and_allows_catalog_profession(self):
        f=self.f
        profession='Новая профессия '+f.suffix
        f.db.native("INSERT INTO workforce_catalog(code,kind,label) VALUES (%s,'profession',%s)",('profession.'+f.suffix,profession))
        body=dict(date='2026-09-17',department='TEST-SMU',profession=profession,required=5,expected_token='')
        self.assertEqual(f.request('post','demand',body).status_code,200)
        f.db.native('UPDATE workers SET active=0 WHERE id=%s',(f.worker,))
        rows=f.request('get','operations?date=2026-09-17&department=TEST-SMU').json['forecast']
        group=next(r for r in rows if r['profession']==profession)
        self.assertEqual(group['days'][0]['expected'],0)
        self.assertEqual(group['days'][0]['shortage'],5)

    def test_validation(self):
        for suffix in ('date=2026-02-30','date=2026-09-17&days=99','date=2026-09-17&offset=-1'):
            self.assertEqual(self.f.request('get','operations?'+suffix).status_code,400)

    def test_attention_does_not_build_forecast_and_forecast_pages_cover_all_groups(self):
        from unittest.mock import patch
        f=self.f;path='operations?date=2026-09-17&department=TEST-SMU'
        with patch('workforce_operations.forecast',side_effect=AssertionError('Unneeded forecast')):
            attention=f.request('get',path+'&forecast=0').json
            self.assertEqual(attention['forecast'],[])
        first=f.request('get',path+'&group_limit=1').json
        self.assertEqual(len(first['forecast']),1)
        self.assertGreaterEqual(first['forecast_total'],1)
        self.assertEqual(f.request('get',path+'&group_limit=0').status_code,400)
