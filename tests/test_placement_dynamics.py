import unittest

import test_placement_report as daily


class PlacementDynamicsTest(unittest.TestCase):
    setUpClass = classmethod(daily.PlacementReportTest.setUpClass.__func__)
    tearDownClass = classmethod(daily.PlacementReportTest.tearDownClass.__func__)
    setUp = daily.PlacementReportTest.setUp
    assign = daily.PlacementReportTest.assign
    client = daily.PlacementReportTest.client
    get = daily.PlacementReportTest.get

    def set_category(self, db, worker, name, allowed=1):
        db.execute('''INSERT OR IGNORE INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
            VALUES (?,?,?,'dynamics',?,'now')''', (name, name.casefold(), allowed, self.admin))
        category = db.execute('SELECT id FROM gdlr_categories WHERE name_key=?', (name.casefold(),)).fetchone()[0]
        db.execute('UPDATE employee_gdlr SET category_id=? WHERE worker_id=?', (category, worker))

    def test_change_uses_previous_calendar_day_for_period_and_single_day(self):
        for start in ('2026-09-12', '2026-09-14'):
            trend = self.get(start=start, date='2026-09-14').get_json()['dynamics']
            self.assertEqual(trend['comparison_date'], '2026-09-13')
            self.assertEqual(trend['changes'], {'total': 0, 'assigned': -1, 'unassigned': 2, 'absent': -1})
            for key in trend['changes']:
                self.assertEqual(sum(group['changes'][key] for group in trend['categories']), trend['changes'][key])
                for group in trend['categories']:
                    self.assertEqual(sum(child['changes'][key] for child in group['pps']), group['changes'][key])
            if start == '2026-09-14':
                self.assertEqual(trend['dates'], ['2026-09-14'])
                self.assertEqual(trend['totals']['assigned'], [0])
        # Missing assignments yesterday mean zero for this metric; never search older dates.
        self.assertEqual(self.get(start='2026-09-13').get_json()['dynamics']['changes']['assigned'], 1)
        self.assertEqual(self.get(start='2026-09-15', date='2026-09-15').get_json()['dynamics']['changes']['assigned'], 0)

    def test_single_day_retains_previous_only_category_and_pdf_delta(self):
        from unittest.mock import patch
        import placement_report_pdf
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET active=0,category='Историческая категория' WHERE id=?", (self.people[0],))
            self.set_category(db, self.people[0], 'Историческая категория', allowed=0)
            db.commit()
        data = self.get(start='2026-09-14', date='2026-09-14', category='Историческая категория', pps_details='1').get_json()
        group = data['dynamics']['categories'][0]
        self.assertEqual(group['counts']['assigned'], [0])
        self.assertEqual(group['changes']['assigned'], -1)
        self.assertEqual(group['pps'][0]['changes']['assigned'], -1)
        text = []
        original = placement_report_pdf.Paragraph
        def capture(value, *args, **kwargs):
            text.append(value)
            return original(value, *args, **kwargs)
        with patch.object(placement_report_pdf, 'Paragraph', capture):
            self.assertTrue(placement_report_pdf.build_pdf(data, include_people=False).startswith(b'%PDF-'))
        self.assertIn('Изменение за день: 14.09.2026 минус 13.09.2026.', text)
        self.assertEqual(text.count('-1'), 3)

    def test_daily_exclusive_counts_category_pps_and_zero_days(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET category='Электромонтажники' WHERE id=?", (self.people[3],))
            self.set_category(db, self.people[3], 'Электромонтажники')
            db.commit()
            before = list(db.iterdump())
        data = self.get(start='2026-09-12', date='2026-09-14').get_json()
        trend = data['dynamics']
        self.assertEqual(trend['dates'], ['2026-09-12', '2026-09-13', '2026-09-14'])
        self.assertEqual(trend['totals'], {'total': [4, 4, 4], 'assigned': [0, 1, 0],
                                          'unassigned': [4, 2, 4], 'absent': [0, 1, 0]})
        category = next(group for group in trend['categories'] if group['category'] == 'Электромонтажники')
        self.assertEqual(category['counts']['total'], [4, 4, 4])
        self.assertEqual([group['pps'] for group in category['pps']], ['ППС15', 'ППС19'])
        for key in trend['totals']:
            self.assertEqual([sum(group['counts'][key][i] for group in trend['categories']) for i in range(3)], trend['totals'][key])
            self.assertEqual([sum(group['counts'][key][i] for group in category['pps']) for i in range(3)], category['counts'][key])
        self.assertEqual(data['totals']['assigned'], 0)
        with self.module.app.app_context():
            self.assertEqual(list(self.module.get_db().iterdump()), before)

    def test_historical_only_category_keeps_zero_end_and_filter_option(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET active=0,category='Историческая категория' WHERE id=?", (self.people[0],))
            self.set_category(db, self.people[0], 'Историческая категория', allowed=0)
            db.commit()
        data = self.get(start='2026-09-13', date='2026-09-14').get_json()
        group = next(group for group in data['dynamics']['categories'] if group['category'] == 'Историческая категория')
        self.assertEqual(group['counts']['assigned'], [1, 0])
        self.assertIn('Историческая категория', data['options']['categories'])

    def test_filtered_series_matches_single_day_and_permissions(self):
        for user in [self.admin, self.foreman, self.viewer]:
            data = self.get(user, start='2026-09-12', date='2026-09-14', pps='ППС15').get_json()
            for index, day in enumerate(data['dynamics']['dates']):
                snapshot = self.get(user, date=day, pps='ППС15').get_json()
                for key, value in snapshot['totals'].items():
                    self.assertEqual(data['dynamics']['totals'][key][index], value)
        self.assertEqual(self.client().get('/api/placement-report?date=2026-09-14&start=2026-09-12').status_code, 401)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (?,'selected','[\"СМУ 19\"]','t',?,'now')", (self.foreman, self.admin))
            db.commit()
        self.assertEqual(self.get(self.foreman, start='2026-09-12').get_json()['dynamics']['totals']['total'], [1, 1])

    def test_validation_single_day_empty_and_legacy_contract(self):
        for filters in [dict(start=''), dict(start='2026-02-30'), dict(start='2026-09-14'),
                        dict(start='2026-06-12'), dict(start='2026-09-13', metric='invalid'),
                        dict(start='2026-09-13', pps_details='yes')]:
            self.assertEqual(self.get(**filters).status_code, 400, filters)
        one = self.get(start='2026-09-13', category='').get_json()['dynamics']
        self.assertEqual(one['totals']['total'], [0])
        self.assertEqual(one['categories'], [])
        empty = self.get(start='2026-09-12', category='Нет такой категории').get_json()['dynamics']
        self.assertEqual(empty['categories'], [])
        self.assertEqual(empty['totals']['total'], [0, 0])
        self.assertNotIn('dynamics', self.get().get_json())

    def test_pdf_period_metric_pps_and_long_period(self):
        from placement_report_pdf import build_pdf
        data = self.get(start='2026-09-01', metric='absent', pps_details='1').get_json()
        detailed = build_pdf(data, include_people=True)
        compact = build_pdf(data, include_people=False)
        self.assertTrue(compact.startswith(b'%PDF-'))
        self.assertGreater(len(detailed), len(compact))
        response = self.client(self.admin).get('/api/placement-report/pdf?date=2026-09-13&start=2026-09-01&metric=absent&pps_details=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
