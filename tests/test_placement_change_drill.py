import unittest
import test_placement_report as daily


class PlacementChangeDrillTest(unittest.TestCase):
    setUpClass = classmethod(daily.PlacementReportTest.setUpClass.__func__)
    tearDownClass = classmethod(daily.PlacementReportTest.tearDownClass.__func__)
    setUp = daily.PlacementReportTest.setUp
    client = daily.PlacementReportTest.client
    assign = daily.PlacementReportTest.assign

    def board(self, user=None, **query):
        return self.client(self.admin if user is None else user).get('/api/staffing', query_string={
            'date': '2026-09-14', 'shift': 'all', 'change_metric': 'assigned', **query})

    def test_zero_delta_reveals_arrival_and_departure_and_lazy_crew_matches(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assign(db, self.people[1], '1 смена')
            db.execute("UPDATE assignments SET work_date='2026-09-14' WHERE worker_id=?", (self.people[1],))
            db.commit()
            before = list(db.iterdump())
        data = self.board().get_json()
        self.assertEqual(data['report_change']['delta'], 0)
        self.assertEqual((data['report_change']['arrived'], data['report_change']['left']), (1, 1))
        rows = {row['id']: row for row in data['rows']}
        self.assertEqual(set(rows), {self.people[0], self.people[1]})
        self.assertEqual(rows[self.people[0]]['report_change']['direction'], 'left')
        self.assertEqual(rows[self.people[1]]['report_change']['direction'], 'arrived')
        self.assertEqual(rows[self.people[0]]['report_change']['current_status'], 'Не расставлен')
        summary = self.board(view='summary').get_json()
        self.assertEqual({row['id'] for row in summary['index']}, set(rows))
        self.assertTrue(all(row['report_change'] for row in summary['index']))
        self.assertEqual({row['id'] for row in self.board(crew_id=self.crew).get_json()['rows']}, set(rows))
        with self.module.app.app_context():
            self.assertEqual(list(self.module.get_db().iterdump()), before)

    def test_inactive_previous_only_identity_is_visible_but_locked(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (self.people[0],))
            db.commit()
        row = self.board().get_json()['rows'][0]
        self.assertEqual(row['id'], self.people[0])
        self.assertTrue(row['locked'])
        self.assertEqual(row['report_change']['current_status'], 'Нет в составе')

    def test_filters_scope_and_validation(self):
        self.assertEqual(len(self.board(change_pps='ППС15', change_category='Электромонтажники').get_json()['rows']), 1)
        self.assertEqual(self.board(change_pps='ППС19').get_json()['rows'], [])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (?,'selected','[\"СМУ 19\"]','t',?,'now')", (self.foreman, self.admin))
            db.commit()
        self.assertEqual(self.board(self.foreman).get_json()['rows'], [])
        self.assertEqual(self.board(self.foreman, crew_id=self.crew).get_json()['rows'], [])
        self.assertEqual(self.board(self.viewer).status_code, 403)
        self.assertEqual(self.client().get('/api/staffing?date=2026-09-14&shift=all&change_metric=assigned').status_code, 401)
        for query in [dict(change_metric='bad'), dict(calendar_sites=str(self.site)), dict(shift='1 смена'), dict(date='0001-01-01')]:
            self.assertEqual(self.board(**query).status_code, 400)

    def test_metric_sets_respect_absences_and_total_population(self):
        assigned = self.board().get_json()
        self.assertEqual([row['id'] for row in assigned['rows']], [self.people[0]])
        absent = self.board(change_metric='absent').get_json()
        self.assertEqual([row['id'] for row in absent['rows']], [self.people[2]])
        self.assertEqual(absent['rows'][0]['report_change']['direction'], 'left')
        self.assertEqual(self.board(change_metric='total').get_json()['rows'], [])
