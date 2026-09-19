from datetime import date, timedelta
import unittest

import test_crews


class DatePeriodLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_crews.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_crews.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.case = test_crews.CrewWorkflowTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    @staticmethod
    def period(end, days):
        return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()

    def test_analytics_and_placement_accept_leap_year_sized_period_only(self):
        end = date(2026, 9, 13)
        start, finish = self.period(end, 366)
        dashboard = self.case.admin.get('/api/personnel-dashboard', query_string={'start': start, 'end': finish})
        self.assertEqual(dashboard.status_code, 200, dashboard.get_json())
        self.assertEqual(len(dashboard.get_json()['dates']), 366)
        report = self.case.admin.get('/api/placement-report', query_string={'start': start, 'date': finish})
        self.assertEqual(report.status_code, 200, report.get_json())
        self.assertEqual(len(report.get_json()['dynamics']['dates']), 366)
        too_early, _ = self.period(end, 367)
        self.assertEqual(self.case.admin.get('/api/personnel-dashboard', query_string={'start': too_early, 'end': finish}).status_code, 400)
        self.assertEqual(self.case.admin.get('/api/placement-report', query_string={'start': too_early, 'date': finish}).status_code, 400)

    def test_log_work_date_range_is_independent_from_event_time_range(self):
        c = self.case
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,
                after_subobject_id,changed_by,changed_at) VALUES (?,?,?,?,?,?,?,?)''',
                (c.crew_a, c.worker_ids[0], '2026-09-12', '1 смена', None, c.sites[0], c.admin_id, '2026-09-01T10:00:00+00:00'))
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,
                after_subobject_id,changed_by,changed_at) VALUES (?,?,?,?,?,?,?,?)''',
                (c.crew_a, c.worker_ids[0], '2026-09-14', '1 смена', None, c.sites[0], c.admin_id, '2026-09-01T10:01:00+00:00'))
            db.commit()
        response = c.admin.get('/api/logs', query_string={'work_date_from': '2026-09-12', 'work_date_to': '2026-09-12'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual({row['work_date'] for row in response.get_json()['rows']}, {'2026-09-12'})
        self.assertEqual(c.admin.get('/api/logs', query_string={'work_date_from': '2026-09-14', 'work_date_to': '2026-09-12'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
