import unittest
import test_staffing_export as fixtures


class PlacementVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.StaffingExportTest.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.StaffingExportTest.tearDownClass()
    def setUp(self):
        self.f=fixtures.StaffingExportTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.module=self.f.module
        with self.module.app.app_context():
            db=self.module.get_db()
            for worker,crew in [(self.f.visible_worker,self.f.crew_id),(self.f.legacy_worker,self.f.crew_id),(self.f.hidden_worker,self.f.other_crew_id)]:
                db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)',(crew,worker))
            db.commit()
        self.url='/api/staffing/verification'

    def get(self,client=None,day='2026-09-13',shift='all'):
        response=(client or self.f.admin).get(self.url,query_string={'date':day,'shift':shift})
        self.assertEqual(response.status_code,200,response.get_json());return response.get_json()['rows']

    def post(self,rows,action='verify',client=None,**extra):
        return (client or self.f.admin).post(self.url,json={'date':'2026-09-13','shift':'all','action':action,
            'assignment_ids':[r['assignment_id'] for r in rows],
            'expected_tokens':{str(r['assignment_id']):r['expected_token'] for r in rows},**extra},headers={'X-CSRF-Token':'export-csrf'})

    def test_confirmation_clear_audit_and_no_placement_changes(self):
        rows=self.get();self.assertEqual(len(rows),4)
        self.assertTrue(all(r['verification_status']=='unverified' for r in rows))
        with self.module.app.app_context():
            before=[tuple(r) for r in self.module.get_db().execute('SELECT * FROM assignments')]
        self.assertEqual(self.post(rows[:2]).status_code,200)
        fresh=self.get();confirmed=[r for r in fresh if r['verification_status']=='verified']
        self.assertEqual(len(confirmed),2);self.assertTrue(all(r['verified_by'] and r['verified_at'] for r in confirmed))
        self.assertEqual(self.post(rows[:2],'clear').status_code,409)
        self.assertEqual(self.post(confirmed,'clear').status_code,200)
        self.assertTrue(all(r['verification_status']=='unverified' for r in self.get()))
        with self.module.app.app_context():
            db=self.module.get_db()
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM assignments')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM placement_verification_events').fetchone()[0],4)

    def test_changed_data_needs_review_and_bulk_is_atomic(self):
        rows=self.get();self.assertEqual(self.post(rows).status_code,200)
        old=self.get()
        with self.module.app.app_context():
            db=self.module.get_db();db.execute("UPDATE workers SET department='Другое СМУ' WHERE id=?",(self.f.visible_worker,));db.commit()
        changed=next(r for r in self.get() if r['worker_id']==self.f.visible_worker)
        self.assertEqual(changed['verification_status'],'changed')
        self.assertEqual(self.post(old,'clear').status_code,409)
        self.assertEqual(sum(r['verification_status']=='verified' for r in self.get()),3)
        self.assertEqual(self.post([changed]).status_code,200)
        with self.module.app.app_context():
            db=self.module.get_db();db.execute("UPDATE assignments SET edit_token='moved-and-returned' WHERE worker_id=?",(self.f.visible_worker,));db.commit()
        self.assertEqual(next(r for r in self.get() if r['worker_id']==self.f.visible_worker)['verification_status'],'changed')

    def test_access_date_shift_csrf_and_stale_rights(self):
        mine=self.get(self.f.foreman)
        self.assertEqual({r['worker_id'] for r in mine},{self.f.visible_worker,self.f.legacy_worker})
        self.assertEqual(self.get(shift='2 смена')[0]['worker_id'],self.f.legacy_worker)
        self.assertEqual(self.get(day='2026-09-14'),[])
        self.assertEqual(self.f.viewer.get(self.url,query_string={'date':'2026-09-13'}).status_code,403)
        self.assertEqual(self.module.app.test_client().get(self.url).status_code,401)
        self.assertEqual(self.f.admin.post(self.url,json={}).status_code,403)
        outside=next(r for r in self.get() if r['worker_id']==self.f.hidden_worker)
        self.assertEqual(self.post([outside],client=self.f.foreman).status_code,409)
        self.assertEqual(self.post(mine,client=self.f.foreman).status_code,200)
        fresh=self.get(self.f.foreman)
        with self.module.app.app_context():
            db=self.module.get_db();db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[]','t',?,'now')",(self.f.foreman_id,self.f.admin_id));db.commit()
        self.assertEqual(self.get(self.f.foreman),[])
        self.assertEqual(self.post(fresh,'clear',self.f.foreman).status_code,409)
        self.assertEqual(self.post(mine,date='bad').status_code,400)
        self.assertEqual(self.post(mine,action='other').status_code,400)

    def test_attendance_invalidates_and_next_date_not_inherited(self):
        row=next(r for r in self.get() if r['worker_id']==self.f.visible_worker)
        self.assertEqual(self.post([row]).status_code,200)
        with self.module.app.app_context():
            db=self.module.get_db()
            db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-13',?,'Больн','att',?,'now')",(self.f.visible_worker,self.f.admin_id))
            db.execute("INSERT INTO assignments(work_date,shift,worker_id,subobject_id,employer,foreman_user_id,created_at,edit_token) VALUES ('2026-09-14','1 смена',?,?,'',?,'now','new-day')",(self.f.visible_worker,self.f.site_id,self.f.admin_id));db.commit()
        updated=next(r for r in self.get() if r['worker_id']==self.f.visible_worker)
        self.assertEqual(updated['verification_status'],'changed');self.assertEqual(updated['attendance_status'],'Больн')
        self.assertEqual(self.get(day='2026-09-14')[0]['verification_status'],'unverified')


if __name__=='__main__':unittest.main()
