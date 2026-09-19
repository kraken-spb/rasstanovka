import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from io import BytesIO

from ticket_parser import parse,date_value
from ticket_matching import recommend,name_evidence


class TicketParsingTest(unittest.TestCase):
    def test_multilingual_route_is_not_issue_date(self):
        text='''Date / Sana: 04Jul2026
Passenger / Yo‘lovchi: Ivanov Ivan Mr (ADT)
Ticket number / Chipta raqami: 250 1234567890
From
Qayerdan
IRKUTSK IRKUTSK
Terminal / Terminal: T1
NAMANGAN NAMANGAN/UZ HY9822 16:00
02Sep2026
17:40
02Sep2026
15:20'''
        result=parse([text])
        self.assertEqual(result['passenger'],'Ivanov Ivan')
        self.assertEqual(result['ticket_number'],'2501234567890')
        self.assertEqual(result['segments'][0]['arrival_date'],'2026-09-02')
        self.assertEqual(result['segments'][0]['flight'],'HY9822')

    def test_unknown_does_not_invent_values_or_use_filename(self):
        result=parse(['Issued 04 Jul 2026\nBooking number 1234'],'Иванов Иван 12345.pdf')
        self.assertEqual(result['passenger'],'')
        self.assertEqual(result['segments'][0]['arrival_date'],'')
        self.assertTrue(result['warnings'])
        self.assertEqual(date_value('32.09.2026'),'')

    def test_missing_year_requires_explicit_upload_year(self):
        text='''SCAT Airlines
Имя
IVAN
Фамилия
IVANOV
Билет
655-1234567890
Новосибирск, 04 сен — Шымкент, 04 сен, в пути 2ч
01:10
04 сен, пт
01:50
DV-832'''
        self.assertEqual(parse([text])['segments'][0]['departure_date'],'')
        value=parse([text],travel_year=2026)
        self.assertEqual(value['segments'][0]['departure_date'],'2026-09-04')
        self.assertTrue(value['warnings'])

    def test_names_first_then_project_and_date(self):
        ticket={'passenger':'IVAN IVANOV','segments':[{'origin':'Казань','destination':'Москва','departure_date':'2026-09-10','arrival_date':'2026-09-10'}]}
        workers=[{'id':1,'full_name':'Иванов Иван Иванович','department':'СМУ-1','personnel_no':'1','project_code':'project.moscow','plans':[{'direction':'arrival','planned_date':'2026-09-20'}]},
                 {'id':2,'full_name':'Иванов Иван Петрович','department':'СМУ-2','personnel_no':'2','project_code':'project.moscow','plans':[{'direction':'arrival','planned_date':'2026-09-11'}]},
                 {'id':3,'full_name':'Петров Иван Иванович','department':'СМУ-2','personnel_no':'3','project_code':'project.moscow','plans':[{'direction':'arrival','planned_date':'2026-09-10'}]}]
        found=recommend(ticket,workers,{'project.moscow':['Москва']})
        self.assertEqual([r['id'] for r in found],[2,1])
        self.assertEqual(found[0]['date_difference'],1)
        self.assertEqual(found[0]['direction'],'arrival')
        ticket['segments'][0]['origin']='Москва'
        self.assertEqual(recommend(ticket,workers,{'project.moscow':['Москва']})[0]['direction'],'')

    def test_equal_candidates_are_not_preselected(self):
        ticket={'passenger':'IVAN IVANOV','segments':[{'origin':'A','destination':'B','departure_date':'','arrival_date':''}]}
        workers=[{'id':i,'full_name':'Иванов Иван','department':'СМУ','personnel_no':str(i)} for i in [1,2]]
        self.assertFalse(recommend(ticket,workers,{})[0]['recommended'])

    def test_opposite_directions_do_not_break_equal_identity_tie(self):
        ticket={'passenger':'IVAN IVANOV','segments':[{'origin':'Москва','destination':'Казань','departure_date':'2026-09-10','arrival_date':'2026-09-10'}]}
        workers=[{'id':i,'full_name':'Иванов Иван','department':'СМУ','personnel_no':str(i),'project_code':code}
                 for i,code in [(1,'project.moscow'),(2,'project.kazan')]]
        candidates=recommend(ticket,workers,{'project.moscow':['Москва'],'project.kazan':['Казань']})
        self.assertEqual({c['direction'] for c in candidates},{'arrival','departure'})
        self.assertFalse(candidates[0]['recommended'])


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'),'Select disposable PostgreSQL explicitly.')
class TicketImportApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_workforce_api as fixture
        fixture.WorkforceApiTest.setUpClass()

    def setUp(self):
        import test_workforce_api as fixture
        from ticket_import import register_ticket_import
        self.f=fixture.WorkforceApiTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        # The fixture owns a rollback-only outer transaction. Browser tests exercise
        # the real READ COMMITTED upload transaction and parallel queue handling.
        native=self.f.db.native
        def native_in_fixture(query,parameters=()):
            if query=='BEGIN ISOLATION LEVEL READ COMMITTED':return native('SELECT 1')
            return native(query,parameters)
        self.f.db.native=native_in_fixture
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.env=patch.dict(os.environ,{'TICKET_IMPORT_DIR':self.folder.name});self.env.start();self.addCleanup(self.env.stop)
        register_ticket_import(self.f.app,lambda:self.f.db,lambda *roles:lambda fn:fn)

    def call(self,path,body=None,role='admin'):
        return self.f.client.open('/api/workforce/tickets/'+path,method='POST' if body is not None else 'GET',json=body,headers={'Test-Role':role})

    def job(self,role='admin'):
        key=str(uuid4())
        self.f.db.native("INSERT INTO workforce_ticket_jobs(id,actor_id,filename,file_hash,file_extension,state,preview_json) VALUES (%s,%s,'test.pdf',%s,'.pdf','ready','{}')",(key,self.f.users[role]['id'],key))
        return key

    def item(self,key):
        return {'job_id':key,'worker_id':self.f.worker,'direction':'arrival','passenger':'Тестовый Сотрудник','ticket_number':'1234567890123','transport':'air',
                'segments':[{'origin':'Казань','destination':'Москва','departure_date':'2026-09-10','arrival_date':'2026-09-10','departure_time':'10:00','arrival_time':'12:00','flight':'SU123','timezone_note':'UTC+3'}]}

    def test_confirm_replay_duplicate_and_no_attendance_mutation(self):
        key=self.job();item=self.item(key)
        before=self.f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s',(self.f.worker,)).fetchone()[0]
        response=self.call('confirm',{'items':[item]});self.assertEqual(response.status_code,201,response.json)
        self.assertEqual(self.call('confirm',{'items':[item]}).status_code,201)
        self.assertEqual(self.f.db.native('SELECT count(*) FROM workforce_tickets WHERE worker_id=%s',(self.f.worker,)).fetchone()[0],1)
        self.assertEqual(self.f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s',(self.f.worker,)).fetchone()[0],before)
        movement=self.f.db.native('SELECT * FROM workforce_movements WHERE worker_id=%s',(self.f.worker,)).fetchone()
        self.assertIsNone(movement['actual_date']);self.assertIsNone(movement['result_code'])
        duplicate=self.item(self.job());self.assertEqual(self.call('confirm',{'items':[duplicate]}).status_code,409)
        changed={**item,'direction':'departure'};self.assertEqual(self.call('confirm',{'items':[changed]}).status_code,409)

    def test_owner_scope_validation_and_search(self):
        key=self.job('rotation')
        self.assertEqual(self.call('uploads/'+key).status_code,404)
        item=self.item(key);item['worker_id']=self.f.ids[1]
        self.assertEqual(self.call('confirm',{'items':[item]},'rotation').status_code,404)
        item['worker_id']=self.f.worker;item['segments'][0]['arrival_date']=''
        self.assertEqual(self.call('confirm',{'items':[item]},'rotation').status_code,400)
        data=self.call('workers?q=Тестовый',role='rotation')
        self.assertEqual(data.status_code,200,data.json)
        self.assertEqual([x['id'] for x in data.json['rows']],[self.f.worker])

    def test_upload_magic_size_duplicate_and_original_owner(self):
        def upload(content):return self.f.client.post('/api/workforce/tickets/uploads',data={'file':(BytesIO(content),'test.pdf')},headers={'Test-Role':'rotation'})
        self.assertEqual(upload(b'not-pdf').status_code,400)
        response=upload(b'%PDF-1.7\nminimal');self.assertEqual(response.status_code,202,response.json)
        key=response.json['id'];self.assertEqual(upload(b'%PDF-1.7\nminimal').json['id'],key)
        self.assertEqual(self.call('uploads/'+key+'/source').status_code,404)
        result=self.call('uploads/'+key+'/source',role='rotation');self.assertEqual(result.status_code,200);result.close()

    def test_geography_empty_and_stale_update(self):
        data=self.call('geography');self.assertEqual(data.status_code,200,data.json)
        response=self.f.client.put('/api/workforce/tickets/geography',json={'token':'stale','links':[]},headers={'Test-Role':'admin'})
        self.assertEqual(response.status_code,409)

    def remove(self,key,state='ready',role='admin'):
        return self.f.client.delete('/api/workforce/tickets/uploads/'+key,json={'state':state},headers={'Test-Role':role})

    def test_delete_all_unconfirmed_states_is_owned_audited_and_not_confirmable(self):
        for state in ('pending','running','ready','failed'):
            key=self.job('rotation')
            self.f.db.native('UPDATE workforce_ticket_jobs SET state=%s WHERE id=%s',(state,key))
            self.assertEqual(self.remove(key,state).status_code,404)
            response=self.remove(key,state,'rotation')
            self.assertEqual(response.status_code,200,response.json)
            self.assertNotIn(key,[r['id'] for r in self.call('uploads',role='rotation').json['rows']])
            self.assertEqual(self.call('uploads/'+key,role='rotation').status_code,404)
            self.assertEqual(self.call('uploads/'+key+'/source',role='rotation').status_code,404)
            self.assertEqual(self.call('confirm',{'items':[self.item(key)]},'rotation').status_code,404)
            row=self.f.db.native('SELECT deleted_by,deleted_at FROM workforce_ticket_jobs WHERE id=%s',(key,)).fetchone()
            self.assertEqual(row['deleted_by'],self.f.users['rotation']['id'])
            self.assertIsNotNone(row['deleted_at'])
        count=self.f.db.native("SELECT count(*) FROM workforce_audit WHERE entity_type='ticket_upload' AND actor_id=%s",(self.f.users['rotation']['id'],)).fetchone()[0]
        self.assertEqual(count,4)

    def test_delete_applied_preserves_trip_and_rejects_stale_confirmation(self):
        key=self.job()
        self.assertEqual(self.call('confirm',{'items':[self.item(key)]}).status_code,201)
        before=self.f.db.native('SELECT * FROM workforce_tickets WHERE job_id=%s',(key,)).fetchone()
        self.assertEqual(self.remove(key,'ready').status_code,409)
        self.assertEqual(self.remove(key,'applied').status_code,200)
        self.assertIsNotNone(self.f.db.native('SELECT id FROM workforce_movements WHERE id=%s',(before['movement_id'],)).fetchone())
        self.assertIsNotNone(self.f.db.native('SELECT id FROM workforce_tickets WHERE job_id=%s',(key,)).fetchone())

    def test_worker_does_not_publish_result_after_deletion(self):
        from ticket_import import process_one
        self.f.db.native("UPDATE workforce_ticket_jobs SET state='failed' WHERE state IN ('pending','running')")
        key=self.job()
        self.f.db.native("UPDATE workforce_ticket_jobs SET state='pending' WHERE id=%s",(key,))
        def parse(*args):
            self.f.db.native('UPDATE workforce_ticket_jobs SET deleted_at=now() WHERE id=%s',(key,))
            return {'passenger':'Late result'}
        with patch('ticket_parser.extract',return_value=([],[])),patch('ticket_parser.parse',side_effect=parse):
            self.assertTrue(process_one(self.f.app,lambda:self.f.db))
        row=self.f.db.native('SELECT state,preview_json FROM workforce_ticket_jobs WHERE id=%s',(key,)).fetchone()
        self.assertEqual(row['state'],'running')
        self.assertEqual(row['preview_json'],{})

    def test_existing_preview_translates_airports_without_rewriting_job(self):
        from psycopg.types.json import Jsonb
        key=self.job()
        value=self.item(key)
        value['segments'][0].update(origin='IKT',destination='TAS')
        value['warnings']=[]
        self.f.db.native('UPDATE workforce_ticket_jobs SET preview_json=%s WHERE id=%s',(Jsonb(value),key))
        response=self.call('uploads/'+key)
        self.assertEqual(response.status_code,200,response.json)
        segment=response.json['ticket']['segments'][0]
        self.assertEqual((segment['origin'],segment['destination']),('Иркутск','Ташкент'))
        self.assertEqual(segment['origin_original'],'IKT')
        stored=self.f.db.native('SELECT preview_json FROM workforce_ticket_jobs WHERE id=%s',(key,)).fetchone()[0]
        self.assertEqual(stored['segments'][0]['origin'],'IKT')
        value['segments']=response.json['ticket']['segments']
        self.assertEqual(self.call('confirm',{'items':[value]}).status_code,201)
        saved=self.f.db.native('SELECT preview_json FROM workforce_ticket_jobs WHERE id=%s',(key,)).fetchone()[0]
        self.assertEqual(saved['segments'][0]['origin_original'],'IKT')
        self.assertEqual(saved['confirmed']['segments'][0]['origin'],'Иркутск')

    def test_preview_uses_scoped_birth_dates_and_card_arrival_without_rewriting_identity(self):
        from psycopg.types.json import Jsonb
        key=self.job();value=self.item(key);value.update(birth_date='1980-05-04',warnings=[])
        for worker, birthday, arrival in [(self.f.ids[0],'1980-05-04','2026-09-11'),(self.f.ids[1],'1981-05-04','2026-09-10')]:
            self.f.db.native('UPDATE workforce_profiles SET birth_date=%s,arrival_date=%s WHERE worker_id=%s',(birthday,arrival,worker))
        self.f.db.native('UPDATE workforce_ticket_jobs SET preview_json=%s WHERE id=%s',(Jsonb(value),key))
        response=self.call('uploads/'+key)
        self.assertEqual(response.status_code,200,response.json)
        best=response.json['candidates'][0]
        self.assertEqual(best['id'],self.f.worker)
        self.assertEqual(best['birth_date_match'],'match')
        self.assertTrue(best['recommended'])
        self.assertEqual(best['date_difference'],1)
        self.assertIn('Дата заезда из карточки',' '.join(best['reasons']))
        scoped=self.call('workers?q=Тестовый',role='rotation').json['rows']
        self.assertEqual([r['id'] for r in scoped],[self.f.worker])
        self.assertEqual(scoped[0]['birth_date'],'1980-05-04')
        stored=self.f.db.native('SELECT preview_json FROM workforce_ticket_jobs WHERE id=%s',(key,)).fetchone()[0]
        self.assertEqual(stored,value)


if __name__=='__main__':unittest.main()
