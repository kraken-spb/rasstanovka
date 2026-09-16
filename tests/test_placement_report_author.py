import unittest
from unittest.mock import patch
import test_placement_report as daily


class PlacementReportAuthorTest(unittest.TestCase):
    setUpClass = classmethod(daily.PlacementReportTest.setUpClass.__func__)
    tearDownClass = classmethod(daily.PlacementReportTest.tearDownClass.__func__)
    assign = daily.PlacementReportTest.assign
    client = daily.PlacementReportTest.client
    get = daily.PlacementReportTest.get

    def setUp(self):
        daily.PlacementReportTest.setUp(self)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET created_at='2026-09-13T06:00:00+00:00'")
            for shift,user in [('1 смена',self.admin),('2 смена',self.foreman)]:
                db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,
                    before_subobject_id,after_subobject_id,changed_by,changed_at)
                    VALUES (?,?,'2026-09-13',?,NULL,?,?,'2026-09-13T07:00:00+00:00')''',
                    (self.crew,self.people[0],shift,self.site,user))
            db.commit()

    def test_author_filters_assignments_without_duplicate_workers_or_owner_guess(self):
        data = self.get(author=str(self.admin)).json
        self.assertEqual(data['totals'],{'total':1,'assigned':1,'unassigned':0,'absent':0})
        people = [p for group in data['groups'] for p in group['people']]
        self.assertEqual([p['id'] for p in people],[self.people[0]])
        self.assertEqual([a['shift'] for a in people[0]['assignments']],['День'])
        combined = self.get(author=[str(self.admin),str(self.foreman)]).json
        self.assertEqual(combined['totals']['assigned'],1)
        self.assertEqual(len(combined['groups'][0]['people'][0]['assignments']),2)
        unknown = self.get(author='unknown').json
        self.assertEqual(unknown['totals'],{'total':1,'assigned':0,'unassigned':0,'absent':1})
        self.assertEqual(set(data['options']['authors']),{str(self.admin),str(self.foreman),'unknown'})

    def test_latest_matching_event_and_legacy_assignment_stay_unattributed(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET created_at='2026-09-13T09:00:00+00:00' WHERE shift='1 смена'")
            db.commit()
        self.assertEqual(self.get(author=str(self.admin)).json['totals']['total'],0)
        self.assertEqual(self.get(author=str(self.foreman)).json['totals']['assigned'],1)
        self.assertEqual(self.get(author='unknown').json['totals']['total'],2)

    def test_period_daily_drill_and_change_drill_share_author(self):
        data = self.get(start='2026-09-13',date='2026-09-14',author=str(self.admin)).json
        self.assertEqual(data['dynamics']['totals']['assigned'],[1,0])
        self.assertEqual(data['dynamics']['changes']['assigned'],-1)
        self.assertIn(str(self.admin),data['options']['authors'])
        response = self.client(self.admin).get('/api/staffing',query_string={
            'date':'2026-09-14','shift':'all','view':'summary','change_metric':'assigned','change_author':str(self.admin)})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual({r['id'] for r in response.json['index']},{self.people[0]})

    def test_scope_and_validation(self):
        for value in ['-1','0','01','invalid','9'*24]:
            self.assertEqual(self.get(author=value).status_code,400,value)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[\"СМУ 19\"]','scope',?,'now')",(self.foreman,self.admin))
            db.commit()
        data = self.get(self.foreman,author=str(self.admin)).json
        self.assertEqual(data['totals']['total'],0)
        self.assertEqual(data['options']['authors'],[])
        self.assertEqual(self.get(self.viewer,author=str(self.admin)).json['totals']['assigned'],1)

    def test_pdf_keeps_author_label_and_same_population(self):
        import placement_report_pdf
        data = self.get(author=str(self.admin)).json
        captured=[]
        original=placement_report_pdf.Paragraph
        def capture(text,*args,**kwargs):
            captured.append(text)
            return original(text,*args,**kwargs)
        with patch.object(placement_report_pdf,'Paragraph',capture):
            output=placement_report_pdf.build_pdf(data,include_people=True)
        self.assertTrue(output.startswith(b'%PDF-'))
        self.assertTrue(any('Кто расставил:' in text for text in captured))
        self.assertTrue(any('Сотрудник 0' in text for text in captured))
        self.assertFalse(any('Сотрудник 2' in text for text in captured))
