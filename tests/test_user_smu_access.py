import unittest
import test_staffing


class UserSmuAccessTest(unittest.TestCase):
    setUpClass = classmethod(test_staffing.StaffingWorkflowTest.setUpClass.__func__)
    tearDownClass = classmethod(test_staffing.StaffingWorkflowTest.tearDownClass.__func__)
    client = test_staffing.StaffingWorkflowTest.client
    write = test_staffing.StaffingWorkflowTest.write
    apply = test_staffing.StaffingWorkflowTest.apply
    table = test_staffing.StaffingWorkflowTest.table
    A = 'Строительно-монтажный участок № 15.2'
    B = 'Строительно-монтажный участок № 15.3'

    def setUp(self):
        test_staffing.StaffingWorkflowTest.setUp(self)
        self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET department=? WHERE personnel_no='70002'", (self.B,))
            db.commit()
        self.rows = self.table().get_json()['rows']
        self.own = next(r for r in self.rows if r['personnel_no'] == '70001')
        self.other = next(r for r in self.rows if r['personnel_no'] == '70002')

    def view(self, user_id):
        return next(r for r in self.admin.get('/api/user-smu-access').get_json()['rows'] if r['user_id'] == user_id)

    def scope(self, user_id, mode='selected', departments=None, **changes):
        return self.write(f'/api/users/{user_id}/smu-access', {
            'mode': mode, 'departments': departments if departments is not None else [self.A],
            'expected_token': self.view(user_id)['expected_token'], **changes})

    def status(self, rows, client):
        return self.write('/api/staffing/status', {'date': '2026-09-12', 'status': 'Вых',
            'worker_ids': [r['id'] for r in rows],
            'expected_tokens': {str(r['id']): r['attendance_token'] for r in rows},
            'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}, client)

    def test_shared_access_preserves_owners_and_scopes_lazy_rows(self):
        self.assertEqual(self.table(self.foreman).get_json()['rows'], [])
        self.assertEqual(self.scope(self.foreman_id).status_code, 200)
        self.assertEqual(self.scope(self.admin_id).status_code, 200)
        expected = {self.own['id'], next(r['id'] for r in self.rows if r['personnel_no'] == '70003')}
        for client in [self.admin, self.foreman]:
            self.assertEqual({r['id'] for r in self.table(client).get_json()['rows']}, expected)
            summary = client.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()
            self.assertEqual({r['id'] for r in summary['index']}, expected)
            detail = client.get(f"/api/staffing?date=2026-09-12&shift=all&crew_id={self.own['crew_id']}").get_json()
            self.assertEqual([r['id'] for r in detail['rows']], [self.own['id']])
            self.assertFalse(next(c for c in detail['crews'] if c['id'] == self.own['crew_id'])['can_edit_whole'])
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT owner_user_id FROM crews WHERE id=?', (self.own['crew_id'],)).fetchone()[0], self.admin_id)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_smu_access_events').fetchone()[0], 2)

    def test_mixed_bulk_is_atomic_and_revocation_is_immediate(self):
        self.scope(self.foreman_id)
        self.assertEqual(self.status([self.own, self.other], self.foreman).status_code, 403)
        self.assertEqual(self.status([self.own], self.foreman).status_code, 200)
        self.scope(self.foreman_id, departments=[])
        self.assertEqual(self.table(self.foreman).get_json()['rows'], [])
        self.assertEqual(self.status([self.other], self.foreman).status_code, 403)

    def test_all_foreman_can_edit_foreign_crew_but_cannot_manage_users(self):
        self.assertEqual(self.scope(self.foreman_id, 'all', []).status_code, 200)
        self.assertEqual(len(self.table(self.foreman).get_json()['rows']), len(self.rows))
        self.assertEqual(self.status([self.other], self.foreman).status_code, 200)
        self.assertEqual(self.foreman.get('/api/user-smu-access').status_code, 403)
        self.assertEqual(self.foreman.get('/api/users').status_code, 403)

    def test_whole_crew_denied_but_row_override_allowed(self):
        self.scope(self.foreman_id)
        url = f"/api/staffing/crews/{self.own['crew_id']}"
        self.assertEqual(self.write(url + '/details', {'linear_itr': 'Мастер', 'brigadier': 'Бригадир', 'expected_token': ''}, self.foreman).status_code, 403)
        for row, expected in [(self.own, 200), (self.other, 403)]:
            self.assertEqual(self.write(url + f"/workers/{row['id']}/brigadier", {
                'brigadier_override': 'Бригадир', 'expected_token': None}, self.foreman).status_code, expected)

    def test_admin_selected_scope_directory_and_direct_request(self):
        self.scope(self.admin_id)
        employees = {r['id']: r for r in self.admin.get('/api/employees').get_json()['rows']}
        self.assertTrue(employees[self.own['id']]['can_edit'])
        self.assertFalse(employees[self.other['id']]['can_edit'])
        self.assertFalse(employees[self.other['id']]['can_remove'])
        self.assertEqual(self.status([self.other], self.admin).status_code, 403)
        self.assertEqual(self.status([self.own], self.admin).status_code, 200)

    def test_validation_csrf_stale_role_and_audit(self):
        self.assertNotIn('', self.view(self.admin_id)['departments'])
        before = self.view(self.foreman_id)
        url = f'/api/users/{self.foreman_id}/smu-access'
        body = {'mode': 'all', 'departments': [], 'expected_token': before['expected_token']}
        self.assertEqual(self.admin.put(url, json=body).status_code, 403)
        self.assertEqual(self.write(url, body, self.viewer).status_code, 403)
        self.assertEqual(self.scope(self.foreman_id, departments=['Неизвестный СМУ']).status_code, 400)
        self.assertEqual(self.scope(self.viewer_id).status_code, 400)
        self.assertEqual(self.write(url, body).status_code, 200)
        self.assertEqual(self.write(url, body).status_code, 409)
        latest = self.view(self.foreman_id)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.foreman_id,))
            db.commit()
        self.assertEqual(self.write(url, {**body, 'expected_token': latest['expected_token']}).status_code, 409)

    def test_new_employee_department_is_automatically_in_scope(self):
        self.scope(self.foreman_id)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET department=? WHERE id=?', (self.A, self.other['id']))
            db.commit()
        self.assertIn(self.other['id'], {r['id'] for r in self.table(self.foreman).get_json()['rows']})


if __name__ == '__main__':
    unittest.main()
