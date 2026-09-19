from collections import Counter
from unittest.mock import patch

import test_placement_report as base


class PlacementPeriodBatchTest(base.PlacementReportTest):
    def compare_days(self, user, start, end, **filters):
        from flask import g
        import placement_report
        with self.module.app.app_context():
            db = self.module.get_db()
            row = db.execute('SELECT id,role,active FROM users WHERE id=?', (user,)).fetchone()
            g.user = dict(id=row['id'], role=row['role'], active=row['active'])
            db.execute('BEGIN')
            try:
                expected = [placement_report.report_data(db, day, **filters) for day in (start, '2026-09-12', end)]
                actual = placement_report.report_period_data(db, start, end, **filters)
            finally:
                db.rollback()
        self.assertEqual(actual['dynamics']['totals']['total'], [item['totals']['total'] for item in expected])
        self.assertEqual(actual['dynamics']['totals']['assigned'], [item['totals']['assigned'] for item in expected])
        self.assertEqual(actual['dynamics']['totals']['absent'], [item['totals']['absent'] for item in expected])
        self.assertEqual(actual['groups'], expected[-1]['groups'])
        expected_options = {'pps': set(), 'categories': set(), 'authors': set()}
        expected_labels = {}
        for item in expected:
            for key in expected_options:
                expected_options[key].update(item['options'][key])
            expected_labels.update(item['options']['author_labels'])
        self.assertEqual(actual['options'], {key: sorted(values, key=str.casefold) for key, values in expected_options.items()}
                         | {'author_labels': expected_labels})
        self.assertEqual(actual['date'], end)
        self.assertTrue(actual['note'].startswith(expected[-1]['note']))
        self.assertEqual({item['category']: item['counts']['total'] for item in actual['dynamics']['categories']},
                         {key: [sum(p['total'] for p in item['groups'] if p['category'] == key) for item in expected]
                          for key in {p['category'] for item in expected for p in item['groups']}})

    def test_batch_matches_daily_for_legacy_scope_attendance_and_historical_assignment(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            historic = db.execute("INSERT INTO workers(full_name,personnel_no,active) VALUES ('Исторический','period-historic',0)").lastrowid
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,crew_id,created_at,edit_token)
                VALUES ('2026-09-12','1 смена',?,?,?,?,?,'now','period')''', (self.site, historic, 'ЛГСС', self.foreman, self.crew))
            db.execute("UPDATE staffing_attendance SET status='Больн' WHERE worker_id=? AND work_date='2026-09-13'", (self.people[2],))
            db.commit()
        self.compare_days(self.foreman, '2026-09-11', '2026-09-13')
        self.compare_days(self.admin, '2026-09-11', '2026-09-13', author='unknown')
        # Current crew ownership still grants access to a historical assignment
        # made by another account without that crew attached to the assignment.
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.crew,historic))
            db.execute('UPDATE assignments SET foreman_user_id=?,crew_id=NULL WHERE worker_id=?', (self.admin,historic))
            db.commit()
        self.compare_days(self.foreman, '2026-09-11', '2026-09-13')


    def test_period_materializes_people_only_for_final_day(self):
        import placement_report
        from flask import g
        with self.module.app.app_context():
            db = self.module.get_db(); g.user = {'id': self.admin, 'role': 'admin', 'active': 1}
            original = placement_report.render_report
            with patch.object(placement_report, 'render_report', wraps=original) as reduced:
                period = placement_report.report_period_data(db, '2026-09-07', '2026-09-13')
            self.assertEqual(reduced.call_count, 1)
            self.assertEqual(period['date'], '2026-09-13')
            self.assertEqual(period['groups'], placement_report.report_data(db, '2026-09-13')['groups'])

    def test_period_query_count_is_bounded(self):
        import placement_report
        from flask import g
        with self.module.app.app_context():
            db = self.module.get_db(); g.user = {'id': self.admin, 'role': 'admin', 'active': 1}
            original = db.execute
            calls = Counter()
            def counted(*args, **kwargs):
                calls['sql'] += 1
                return original(*args, **kwargs)
            with patch.object(db, 'execute', side_effect=counted):
                placement_report.report_period_data(db, '2026-09-07', '2026-09-13')
            self.assertLessEqual(calls['sql'], 8)
            weekly = calls['sql']; calls.clear()
            with patch.object(db, 'execute', side_effect=counted):
                placement_report.report_period_data(db, '2025-09-13', '2026-09-13')
            self.assertEqual(calls['sql'], weekly)

    def test_batch_matches_daily_for_selected_smu(self):
        import json
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (?,'selected',?,'period',?,'now')",
                       (self.admin, json.dumps(['СМУ 15']), self.admin))
            db.commit()
        self.compare_days(self.admin, '2026-09-11', '2026-09-13')


if __name__ == '__main__':
    import unittest
    unittest.main()
