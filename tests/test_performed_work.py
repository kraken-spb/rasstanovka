import unittest
import test_staffing


class PerformedWorkTest(unittest.TestCase):
    setUpClass = classmethod(test_staffing.StaffingWorkflowTest.setUpClass.__func__)
    tearDownClass = classmethod(test_staffing.StaffingWorkflowTest.tearDownClass.__func__)
    setUp = test_staffing.StaffingWorkflowTest.setUp
    client = test_staffing.StaffingWorkflowTest.client
    write = test_staffing.StaffingWorkflowTest.write
    apply = test_staffing.StaffingWorkflowTest.apply
    group_table = test_staffing.StaffingWorkflowTest.group_table

    def payload(self, rows, description='Монтаж опалубки\nБетонирование', day='2026-09-12'):
        return {'date': day, 'description': description, 'worker_ids': [r['id'] for r in rows],
                'expected_tokens': {str(r['id']): r['performed_work_token'] for r in rows},
                'expected_day_tokens': {str(r['id']): r['day_token'] for r in rows},
                'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}

    def save(self, data, client=None):
        return self.write('/api/staffing/performed-work', data, client)

    def test_selected_only_multiline_persistence_and_date_isolation(self):
        self.apply()
        rows = self.group_table()
        result = self.save(self.payload(rows[:2]))
        self.assertEqual(result.status_code, 200, result.json)
        current = {r['id']: r for r in self.group_table()}
        for row in rows[:2]:
            self.assertEqual(current[row['id']]['performed_work'], 'Монтаж опалубки\nБетонирование')
        self.assertEqual(current[rows[2]['id']]['performed_work'], '')
        later = self.admin.get('/api/staffing?date=2026-09-13&shift=all').json['rows']
        self.assertTrue(all(r['performed_work'] == '' for r in later))
        with self.app.app_context():
            self.module.init_db()
        self.assertEqual(self.group_table()[0]['performed_work'], 'Монтаж опалубки\nБетонирование')

    def test_clear_keeps_stale_protection_and_batch_rolls_back(self):
        self.apply()
        rows = self.group_table()
        stale = self.payload(rows)
        self.assertEqual(self.save(self.payload(rows[:1])).status_code, 200)
        self.assertEqual(self.save(stale).status_code, 409)
        fresh = self.group_table()
        self.assertTrue(all(r['performed_work'] == '' for r in fresh[1:]))
        self.assertEqual(self.save(self.payload(fresh[:1], '')).status_code, 200)
        self.assertEqual(self.save(stale).status_code, 409)
        self.assertEqual(self.group_table()[0]['performed_work'], '')
        self.assertIsNotNone(self.group_table()[0]['performed_work_token'])

    def test_shift_change_rejects_stale_form_and_preserves_both_shift_values(self):
        self.apply()
        row = next(r for r in self.group_table() if r['personnel_no'] == '70001')
        self.assertEqual(self.save(self.payload([row], 'Дневные работы')).status_code, 200)
        stale = self.payload([next(r for r in self.group_table() if r['id'] == row['id'])])
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_shifts(work_date,worker_id,shift,edit_token,updated_by,updated_at) VALUES (?,?,?,?,?,?)",
                       ('2026-09-12', row['id'], '2 смена', 'night', self.admin_id, 'now'))
            db.commit()
        self.assertEqual(self.save(stale).status_code, 409)
        fresh = next(r for r in self.group_table() if r['id'] == row['id'])
        self.assertEqual(fresh['performed_work'], '')
        self.assertEqual(self.save(self.payload([fresh], 'Ночные работы')).status_code, 200)
        with self.app.app_context():
            rows = self.module.get_db().execute('SELECT shift,description FROM staffing_performed_work WHERE worker_id=? ORDER BY shift', (row['id'],)).fetchall()
            self.assertEqual([tuple(r) for r in rows], [('1 смена', 'Дневные работы'), ('2 смена', 'Ночные работы')])

    def test_permissions_membership_inactive_and_invalid_inputs(self):
        self.apply()
        rows = self.group_table()
        data = self.payload(rows)
        self.assertEqual(self.save(data, self.viewer).status_code, 403)
        self.assertEqual(self.save(data, self.foreman).status_code, 403)
        self.assertEqual(self.admin.put('/api/staffing/performed-work', json=data).status_code, 403)
        for fields in ({'description': 'я' * 2001}, {'description': 1}, {'description': 'x\x00'}, {'worker_ids': []}, {'worker_ids': [True]}, {'expected_tokens': {}}, {'date': 'bad'}):
            self.assertEqual(self.save({**data, **fields}).status_code, 400)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=?', (self.foreman_id,))
            db.commit()
        self.assertEqual(self.save(data).status_code, 409)
        self.assertEqual(self.save(self.payload(self.group_table()), self.foreman).status_code, 200)
        fresh = self.payload(self.group_table())
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (rows[-1]['id'],))
            db.commit()
        self.assertEqual(self.save(fresh).status_code, 409)
