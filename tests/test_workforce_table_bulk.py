import os
import unittest
from uuid import uuid4
from psycopg.types.json import Jsonb
import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class TableBulkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.db = self.f.db

    def prepare(self, ids=None, role='admin'):
        r = self.f.request('post','bulk/table/prepare',{'ids':ids or self.f.ids,'date':'2026-09-18'},role)
        self.assertEqual(r.status_code,200,r.json)
        return r.json

    def body(self, data, field, value, extra=None):
        return {'date':data['date'],'people':[{k:r[k] for k in ('id','token')} for r in data['people']],
                'field':field,'value':value,'reference_token':data['fields'][field]['token'],
                'extra':extra or {},'reason':'Массовая проверка','request_key':str(uuid4())}

    def apply(self, field, value, extra=None, ids=None, role='admin'):
        data = self.prepare(ids,role)
        return self.f.request('post','bulk/table/apply',self.body(data,field,value,extra),role)

    def movement(self, worker):
        return self.db.native('''INSERT INTO workforce_movements(worker_id,direction,planned_date,basis_code,
            travel_details,created_by,updated_by,request_key) VALUES (%s,'arrival','2026-09-20','basis.ticket',
            'Индивидуальный билет',%s,%s,%s) RETURNING id''',
            (worker,self.f.users['admin']['id'],self.f.users['admin']['id'],str(uuid4()))).fetchone()['id']

    def schedule(self, days=30):
        return dict(self.db.native('''INSERT INTO workforce_rotation_schedules(name,onsite_days,leave_days,travel_days,updated_by)
            VALUES (%s,%s,30,2,%s) RETURNING *''',('Тест '+uuid4().hex,days,self.f.users['admin']['id'])).fetchone())

    def rotation(self, worker, schedule):
        from workforce_core import plain
        return self.db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,start_date,
            planned_end_date,leave_end_date,next_arrival_date,created_by,updated_by,request_key)
            VALUES (%s,%s,%s,'2026-09-01','2026-09-30','2026-10-30','2026-11-01',%s,%s,%s) RETURNING id''',
            (worker,schedule['id'],Jsonb(plain(schedule)),self.f.users['admin']['id'],self.f.users['admin']['id'],str(uuid4()))).fetchone()['id']

    def test_profile_date_selected_only_stale_atomic_and_retry(self):
        selected = self.f.worker
        data = self.prepare([selected])
        body = self.body(data,'arrival_date','2026-09-17')
        first = self.f.request('post','bulk/table/apply',body)
        self.assertEqual(first.status_code,200,first.json)
        self.assertEqual(self.f.request('post','bulk/table/apply',body).json,first.json)
        self.assertIsNone(self.db.native('SELECT arrival_date FROM workforce_profiles WHERE worker_id=%s',(self.f.ids[1],)).fetchone()[0])
        both = self.prepare()
        self.db.native('UPDATE workforce_profiles SET phone=%s WHERE worker_id=%s',('changed',self.f.ids[1]))
        stale = self.f.request('post','bulk/table/apply',self.body(both,'arrival_date','2026-09-16'))
        self.assertEqual(stale.status_code,409,stale.json)
        self.assertEqual(str(self.db.native('SELECT arrival_date FROM workforce_profiles WHERE worker_id=%s',(selected,)).fetchone()[0]),'2026-09-17')

    def test_leave_start_is_selected_only_validated_and_role_scoped(self):
        self.db.native("UPDATE workforce_profiles SET leave_end_date='2026-10-15' WHERE worker_id=%s", (self.f.worker,))
        invalid = self.apply('leave_start_date', '2026-10-16')
        self.assertEqual(invalid.status_code, 400, invalid.json)
        self.assertTrue(all(r[0] is None for r in self.db.native('SELECT leave_start_date FROM workforce_profiles WHERE worker_id=ANY(%s)', (self.f.ids,))))
        response = self.apply('leave_start_date', '2026-09-20', ids=[self.f.worker], role='rotation')
        self.assertEqual(response.status_code, 200, response.json)
        values = {r['worker_id']:r['leave_start_date'] for r in self.db.native('SELECT worker_id,leave_start_date FROM workforce_profiles WHERE worker_id=ANY(%s)', (self.f.ids,))}
        self.assertEqual(str(values[self.f.worker]), '2026-09-20')
        self.assertIsNone(values[self.f.ids[1]])
        self.db.native("UPDATE workforce_profiles SET employment_code='employment.recruitment' WHERE worker_id=%s", (self.f.worker,))
        self.assertFalse(self.prepare([self.f.worker],role='recruitment')['fields']['leave_start_date']['enabled'])
        self.assertEqual(self.apply('leave_start_date','2026-09-21',ids=[self.f.worker],role='recruitment').status_code,403)

    def test_movement_fields_and_reschedule_preserve_ticket_history(self):
        old = [self.movement(w) for w in self.f.ids]
        for field,value in [('movement_direction','departure'),('movement_basis','basis.request')]:
            r = self.apply(field,value);self.assertEqual(r.status_code,200,r.json)
        data = self.prepare()
        body = self.body(data,'planned_date','2026-09-25')
        r = self.f.request('post','bulk/table/apply',body)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.f.request('post','bulk/table/apply',body).json,r.json)
        rows = self.db.native('SELECT * FROM workforce_movements WHERE worker_id=ANY(%s)',(self.f.ids,)).fetchall()
        self.assertEqual(len(rows),4)
        for row in rows:
            self.assertEqual(row['travel_details'],'Индивидуальный билет')
            self.assertEqual(row['result_code'],'result.postponed' if row['id'] in old else None)
            if row['id'] not in old:
                self.assertIn(row['rescheduled_from'],old)
                self.assertEqual(str(row['planned_date']),'2026-09-25')

    def test_missing_movement_requires_explicit_creation_and_validates_all_first(self):
        old = self.movement(self.f.worker)
        r = self.apply('movement_direction','departure')
        self.assertEqual(r.status_code,400,r.json)
        self.assertEqual(self.db.native('SELECT direction FROM workforce_movements WHERE id=%s',(old,)).fetchone()[0],'arrival')
        r = self.apply('movement_direction','departure',{'planned_date':'2026-10-01','destination_kind':'home'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',(self.f.ids,)).fetchone()[0],2)

    def test_rotation_dates_recalculate_only_when_selected_and_guard_order(self):
        schedule = self.schedule()
        ids = [self.rotation(w,schedule) for w in self.f.ids]
        r = self.apply('forecast_departure_date','2026-10-02')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(str(self.db.native('SELECT leave_end_date FROM workforce_rotations WHERE id=%s',(ids[0],)).fetchone()[0]),'2026-10-30')
        r = self.apply('leave_end_date','2026-11-04')
        self.assertEqual(r.status_code,400,r.json)
        r = self.apply('leave_end_date','2026-11-04',{'recalculate':True})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(str(self.db.native('SELECT next_arrival_date FROM workforce_rotations WHERE id=%s',(ids[0],)).fetchone()[0]),'2026-11-06')
        r = self.apply('next_arrival_date','2026-11-09')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(str(self.db.native('SELECT next_arrival_date FROM workforce_rotations WHERE id=%s',(ids[0],)).fetchone()[0]),'2026-11-09')

    def test_profile_forecast_without_cycle_cannot_silently_skip_recalculation(self):
        data = self.prepare()
        self.assertFalse(data['fields']['forecast_departure_date']['recalculate_enabled'])
        r = self.apply('forecast_departure_date','2026-10-01',{'recalculate':True})
        self.assertEqual(r.status_code,400,r.json)
        r = self.apply('forecast_departure_date','2026-10-01')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['changed'],len(self.f.ids))

    def test_rotation_schedule_creates_explicit_cycle_and_updates_existing_cycle(self):
        schedule = self.schedule()
        r = self.apply('rotation_schedule_id',str(schedule['id']))
        self.assertEqual(r.status_code,400,r.json)
        r = self.apply('rotation_schedule_id',str(schedule['id']),{'start_date':'2026-09-01'})
        self.assertEqual(r.status_code,200,r.json)
        r = self.apply('rotation_schedule_id',str(schedule['id']))
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['changed'],0)
        self.assertEqual(r.json['unchanged'],len(self.f.ids))
        other = self.schedule(40)
        r = self.apply('rotation_schedule_id',str(other['id']),{'recalculate':True})
        self.assertEqual(r.status_code,200,r.json)
        dates = self.db.native('SELECT planned_end_date FROM workforce_rotations WHERE worker_id=ANY(%s)',(self.f.ids,)).fetchall()
        self.assertEqual({str(r[0]) for r in dates},{'2026-10-10'})

    def test_stale_trip_and_rotation_reject_entire_selection(self):
        movements = [self.movement(w) for w in self.f.ids]
        data = self.prepare()
        self.db.native('UPDATE workforce_movements SET edit_token=gen_random_uuid() WHERE id=%s',(movements[0],))
        response = self.f.request('post','bulk/table/apply',self.body(data,'planned_date','2026-09-25'))
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(self.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',(self.f.ids,)).fetchone()[0],2)
        schedule = self.schedule()
        rotations = [self.rotation(w,schedule) for w in self.f.ids]
        data = self.prepare()
        self.db.native('UPDATE workforce_rotations SET edit_token=gen_random_uuid() WHERE id=%s',(rotations[0],))
        response = self.f.request('post','bulk/table/apply',self.body(data,'leave_end_date','2026-10-29'))
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual({str(r[0]) for r in self.db.native('SELECT leave_end_date FROM workforce_rotations WHERE id=ANY(%s)',(rotations,))},{'2026-10-30'})

    def test_status_date_creates_correction_and_stale_timeline_rejects(self):
        old = []
        for worker in self.f.ids:
            old.append(self.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
                reason,created_by,request_key) VALUES (%s,'stage.onsite','2026-09-01',TRUE,'Тест',%s,%s) RETURNING id''',
                (worker,self.f.users['admin']['id'],str(uuid4()))).fetchone()[0])
        r = self.apply('stage_date','2026-09-03')
        self.assertEqual(r.status_code,200,r.json)
        events = self.db.native('SELECT * FROM workforce_stage_events WHERE replaces_id=ANY(%s)',(old,)).fetchall()
        self.assertEqual(len(events),2)
        self.assertEqual({str(r['effective_date']) for r in events},{'2026-09-03'})

    def test_roles_reference_changes_and_date_validation(self):
        r = self.f.request('post','bulk/table/prepare',{'ids':[self.f.worker],'date':'2026-09-18'},'recruitment')
        self.assertEqual(r.status_code,403)
        worker = self.f.ids[1]
        self.db.native("UPDATE workers SET department='TEST-SMU' WHERE id=%s",(worker,))
        data = self.prepare([worker],'recruitment')
        self.assertTrue(data['fields']['arrival_date']['enabled'])
        self.assertFalse(data['fields']['movement_basis']['enabled'])
        r = self.f.request('post','bulk/table/apply',self.body(data,'movement_basis','basis.request'),'recruitment')
        self.assertEqual(r.status_code,403,r.json)
        r = self.apply('arrival_date','2026-02-30');self.assertEqual(r.status_code,400,r.json)
        data = self.prepare()
        self.db.native("UPDATE workforce_catalog SET edit_token=gen_random_uuid() WHERE code='basis.request'")
        r = self.f.request('post','bulk/table/apply',self.body(data,'movement_basis','basis.request'))
        self.assertEqual(r.status_code,409,r.json)

    def test_scoped_prepare_preserves_all_bulk_tokens_and_dated_values(self):
        from workforce_table_bulk import TABLE_FIELDS
        worker = self.f.worker
        self.movement(worker)
        schedule = self.schedule()
        self.rotation(worker, schedule)
        full = self.prepare([worker])
        for field in sorted(TABLE_FIELDS | {'project_code'}):
            with self.subTest(field=field):
                result = self.f.request('post', 'bulk/table/prepare', {'ids':[worker], 'date':'2026-09-18', 'field':field})
                self.assertEqual(result.status_code, 200, result.json)
                data = result.json
                self.assertEqual(data['fields'][field], full['fields'][field])
                self.assertEqual(data['people'][0]['token'], full['people'][0]['token'])
                self.assertIn(field, data['people'][0]['values'])
                if field == 'category_id':
                    self.assertEqual(set(data['fields']), {'category_id'})
                expected = {'rotation_schedule_id':str(schedule['id']), 'planned_date':'2026-09-20',
                            'movement_direction':'arrival', 'movement_basis':'basis.ticket',
                            'forecast_departure_date':'2026-09-30', 'leave_end_date':'2026-10-30',
                            'next_arrival_date':'2026-11-01'}
                if field in expected:
                    self.assertEqual(data['people'][0]['values'][field], expected[field])

    def test_scoped_prepare_apply_role_stale_and_invalid_field(self):
        body = {'ids':[self.f.worker], 'date':'2026-09-18', 'field':'arrival_date'}
        for invalid in [None, [], 'not_a_field', 'stage_code']:
            self.assertEqual(self.f.request('post','bulk/table/prepare',{**body,'field':invalid}).status_code,400)
        self.assertEqual(self.f.request('post','bulk/table/prepare',body,'recruitment').status_code,403)
        self.assertEqual(self.f.request('post','bulk/table/prepare',body,'viewer').status_code,403)
        data = self.f.request('post','bulk/table/prepare',body).json
        payload = self.body(data,'arrival_date','2026-09-17')
        payload['reason'] = ''
        result = self.f.request('post','bulk/table/apply',payload)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json['changed'],1)
        payload['request_key'] = str(uuid4())
        self.assertEqual(self.f.request('post','bulk/table/apply',payload).status_code,409)
