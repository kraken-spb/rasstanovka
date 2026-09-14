import unittest

import test_staffing


class AttendanceStatusTest(unittest.TestCase):
    setUpClass = classmethod(test_staffing.StaffingWorkflowTest.setUpClass.__func__)
    tearDownClass = classmethod(test_staffing.StaffingWorkflowTest.tearDownClass.__func__)
    setUp = test_staffing.StaffingWorkflowTest.setUp
    client = test_staffing.StaffingWorkflowTest.client
    write = test_staffing.StaffingWorkflowTest.write
    apply = test_staffing.StaffingWorkflowTest.apply
    group_table = test_staffing.StaffingWorkflowTest.group_table

    def payload(self, rows, status='Вых', day='2026-09-12'):
        return {'date': day, 'status': status, 'worker_ids': [r['id'] for r in rows],
                'expected_tokens': {str(r['id']): r['attendance_token'] for r in rows},
                'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}

    def calendar(self, start='2026-09-12', days=1, **filters):
        return self.admin.get('/api/calendar', query_string={'start': start, 'days': days, **filters}).get_json()

    def assign(self, rows):
        rows = [r for r in rows if r['crew_id'] == rows[0]['crew_id']]
        with self.app.app_context():
            site = self.module.get_db().execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
        result = self.write(f"/api/staffing/crews/{rows[0]['crew_id']}/assignments", {
            'date': '2026-09-12', 'worker_ids': [r['id'] for r in rows], 'subobject_id': site,
            'expected_tokens': {str(r['id']): r['day_token'] for r in rows}})
        self.assertEqual(result.status_code, 200, result.get_json())
        return site

    def test_default_date_isolation_statuses_restore_and_migration(self):
        self.apply()
        rows = self.group_table()
        self.assertTrue(all(r['attendance_status'] == 'Явка' and r['attendance_token'] is None for r in rows))
        for status in ['Вых', 'Без сод', 'Больн', 'МО']:
            row = self.group_table()[0]
            response = self.write('/api/staffing/status', self.payload([row], status))
            self.assertEqual(response.status_code, 200, response.get_json())
            absences = self.calendar()['absences']
            self.assertEqual(len(absences), 1)
            self.assertEqual(absences[0]['status'], status)
            self.assertEqual(self.calendar('2026-09-13')['absences'], [])
        with self.app.app_context():
            self.module.init_db()
            self.module.init_db()
        row = self.group_table()[0]
        self.assertEqual(row['attendance_status'], 'МО')
        self.assertEqual(self.write('/api/staffing/status', self.payload([row], 'Явка')).status_code, 200)
        self.assertEqual(self.calendar()['absences'], [])

    def test_absence_excludes_fact_and_drill_retains_assignment_including_two_shifts(self):
        self.apply()
        site = self.assign([r for r in self.group_table() if r['personnel_no'] in ('70001', '70002')])
        row = next(r for r in self.group_table() if r['personnel_no'] == '70001')
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("""INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id)
                VALUES ('2026-09-12','2 смена',?,?,?,?,?,?)""",
                (site, row['id'], row['employer'], self.admin_id, self.module.utc_now(), row['crew_id']))
            db.commit()
        self.assertEqual(self.write('/api/staffing/status', self.payload([row], 'Больн')).status_code, 200)
        data = self.calendar()
        self.assertEqual(sum(f['day_count'] + f['night_count'] for f in data['facts']), 1)
        self.assertEqual(len(data['absences']), 1)
        drill = self.admin.get('/api/staffing', query_string={'date': '2026-09-12', 'shift': 'all', 'calendar_sites': site}).get_json()
        self.assertEqual(len(drill['rows']), 1)
        self.assertEqual(drill['calendar_assignment_count'], 1)
        fresh = next(r for r in self.group_table() if r['id'] == row['id'])
        self.assertTrue(fresh['assignment_id'])
        self.assertTrue(fresh['shift_conflict'])
        self.assertEqual(self.write('/api/staffing/status', self.payload([fresh], 'Явка')).status_code, 200)
        self.assertEqual(sum(f['day_count'] + f['night_count'] for f in self.calendar()['facts']), 3)

    def test_bulk_is_atomic_stale_and_group_membership_protected(self):
        self.apply()
        rows = self.group_table()
        stale = self.payload(rows)
        self.assertEqual(self.write('/api/staffing/status', self.payload(rows[:1], 'МО')).status_code, 200)
        self.assertEqual(self.write('/api/staffing/status', stale).status_code, 409)
        self.assertEqual(sum(r['attendance_status'] != 'Явка' for r in self.group_table()), 1)
        fresh = self.group_table()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM crew_members WHERE worker_id=?', (fresh[0]['id'],))
            db.commit()
        self.assertEqual(self.write('/api/staffing/status', self.payload(fresh)).status_code, 409)

    def test_roles_csrf_validation_and_category_filter(self):
        self.apply()
        rows = self.group_table()
        payload = self.payload(rows)
        self.assertEqual(self.write('/api/staffing/status', payload, self.viewer).status_code, 403)
        self.assertEqual(self.write('/api/staffing/status', payload, self.foreman).status_code, 403)
        self.assertEqual(self.admin.put('/api/staffing/status', json=payload).status_code, 403)
        for value in ['Новый статус', None, [], 1]:
            self.assertEqual(self.write('/api/staffing/status', {**payload, 'status': value}).status_code, 400)
        self.assertEqual(self.write('/api/staffing/status', {**payload, 'date': 'bad'}).status_code, 400)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=?', (self.foreman_id,))
            db.commit()
        owned = [r for r in self.group_table() if r['crew_id']]
        self.assertEqual(self.write('/api/staffing/status', self.payload(owned), self.foreman).status_code, 200)
        self.assertEqual(len(self.calendar(category=owned[0]['category'])['absences']), len(owned))
        self.assertEqual(self.calendar(category='Несуществующая')['absences'], [])
        self.assertEqual(len(self.calendar(days=7)['absences']), len(owned))

    def test_unassigned_absence_and_summary_index(self):
        self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM crew_members WHERE worker_id=(SELECT MIN(worker_id) FROM crew_members)')
            db.commit()
        row = next(r for r in self.group_table() if r['crew_id'] is None)
        self.assertEqual(self.write('/api/staffing/status', self.payload([row], 'Без сод')).status_code, 200)
        self.assertEqual(len(self.calendar()['absences']), 1)
        summary = self.admin.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()
        entry = next(r for r in summary['index'] if r['id'] == row['id'])
        self.assertEqual(entry['attendance_status'], 'Без сод')
        self.assertIsNotNone(entry['attendance_token'])
