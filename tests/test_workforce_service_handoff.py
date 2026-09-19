import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class ServiceHandoffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f=fixtures.WorkforceApiTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.app.config['TESTING']=False  # Exercise revision-based read caches too.

    def source(self, worker=None):
        self.f.add_source_record(worker or self.f.worker,'urp:К15',role='recruitment',sheet='ПВП')

    def listing(self, section, day='2026-09-16', endpoint='people', role='admin', extra=''):
        result=self.f.request('get',f'{endpoint}?section={section}&date={day}&q={self.f.suffix}{extra}',role=role)
        self.assertEqual(result.status_code,200,result.json if result.is_json else result.data)
        return result

    def count(self, section, expected, day='2026-09-16'):
        self.assertEqual(self.listing(section,day).json['totals']['total'],expected)

    def event(self, code='stage.onsite', day='2026-09-16', **extra):
        result=self.f.request('post',f'people/{self.f.worker}/stage',dict(stage_code=code,
            effective_date=day,confirmed=True,reason='Проверка передачи',request_key=str(uuid4()),**extra))
        self.assertEqual(result.status_code,201,result.json)
        return result.json['id']

    def test_bulk_transition_moves_source_and_manual_people_across_all_reads(self):
        f=self.f;self.source()
        f.db.native("INSERT INTO workforce_registry_memberships(worker_id,service,created_by) VALUES (%s,'recruitment',%s)",
                    (f.ids[1],f.users['admin']['id']))
        before=[r[0] for r in f.db.native('SELECT employment_code FROM workforce_profiles WHERE worker_id=ANY(%s) ORDER BY worker_id',(f.ids,))]
        self.count('recruitment',2);self.count('rotation',0)
        people=[{'id':w,'token':f.board_row(w)['stage_token']} for w in f.ids]
        payload=f.board_move('stage.onsite',people)
        result=f.request('post','transitions',payload)
        self.assertEqual(result.status_code,201,result.json)
        self.assertEqual(f.request('post','transitions',payload).status_code,200)
        self.count('recruitment',0);self.count('rotation',2)
        self.count('recruitment',2,'2026-09-15');self.count('rotation',0,'2026-09-15')
        board=self.listing('rotation',endpoint='board').json
        self.assertEqual({r['id'] for lane in board['lanes'].values() for r in lane['rows']},set(f.ids))
        self.assertEqual(self.listing('recruitment',endpoint='board').json['totals']['total'],0)
        for section,total in [('rotation',2),('recruitment',0)]:
            export=self.listing(section,endpoint='people/export');self.addCleanup(export.close)
            self.assertEqual(export.headers['X-Export-Row-Count'],str(total))
        self.assertEqual(self.listing('rotation',role='rotation').json['totals']['total'],1)
        self.assertEqual(self.listing('rotation',extra='&stage=stage.pvp').json['totals']['total'],0)
        self.assertEqual([r[0] for r in f.db.native('SELECT employment_code FROM workforce_profiles WHERE worker_id=ANY(%s) ORDER BY worker_id',(f.ids,))],before)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_source_records WHERE worker_id=%s AND active',(f.worker,)).fetchone()[0],1)

    def test_later_leave_keeps_handoff_but_preserves_report_date(self):
        self.source();self.event();self.event('stage.leave','2026-09-17')
        self.count('rotation',1,'2026-09-17');self.count('recruitment',0,'2026-09-17')
        self.assertEqual(self.listing('rotation','2026-09-17').json['rows'][0]['stage_code'],'stage.leave')
        self.count('recruitment',1,'2026-09-15')

    def test_correction_and_retraction_restore_original_section_as_of_correction(self):
        self.source();first=self.event()
        self.event('stage.pvp','2026-09-17',replaces_id=first)
        self.count('rotation',1,'2026-09-16');self.count('recruitment',1,'2026-09-17')
        self.count('rotation',0,'2026-09-17')
        second=self.event('stage.onsite','2026-09-18')
        self.count('rotation',1,'2026-09-18')
        self.f.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
            retracted,reason,replaces_id,created_by,request_key) VALUES (%s,'stage.onsite','2026-09-19',FALSE,
            TRUE,'Отмена ошибочной явки',%s,%s,%s)''',(self.f.worker,second,self.f.users['admin']['id'],uuid4()))
        self.count('rotation',1,'2026-09-18');self.count('rotation',0,'2026-09-19');self.count('recruitment',1,'2026-09-19')

    def test_unconfirmed_future_and_unknown_source_do_not_transfer(self):
        self.source()
        self.f.add_stage_record(self.f.worker,'stage.onsite',confirmed=False)
        self.f.add_stage_record(self.f.worker,'stage.onsite',day='2026-09-18')
        self.f.add_stage_record(self.f.ids[1],'stage.onsite')
        self.count('recruitment',1);self.count('rotation',0)
        self.count('recruitment',0,'2026-09-18');self.count('rotation',1,'2026-09-18')

    def test_existing_rotation_membership_is_not_duplicated_or_removed_by_correction(self):
        self.source();self.f.add_source_record(self.f.worker,'urp:П15')
        onsite=self.event();self.count('rotation',1);self.count('recruitment',0)
        self.event('stage.pvp','2026-09-17',replaces_id=onsite)
        self.count('rotation',1,'2026-09-17');self.count('recruitment',1,'2026-09-17')

    def test_failed_bulk_scope_check_does_not_transfer_anyone(self):
        for w in self.f.ids:self.source(w)
        people=[{'id':w,'token':self.f.board_row(w)['stage_token']} for w in self.f.ids]
        result=self.f.request('post','transitions',self.f.board_move('stage.onsite',people),role='rotation')
        self.assertEqual(result.status_code,404)
        self.count('recruitment',2);self.count('rotation',0)
