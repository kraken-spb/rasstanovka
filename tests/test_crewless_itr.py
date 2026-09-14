import unittest

import test_staffing as staffing_tests


class CrewlessItrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        staffing_tests.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        staffing_tests.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.fixture = staffing_tests.StaffingWorkflowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.apply()
        self.module = self.fixture.module
        self.client = self.fixture.admin
        self.ids = [r['id'] for r in self.rows() if r['personnel_no'] in ('70001', '70002')]
        with self.module.app.app_context():
            db = self.module.get_db()
            self.crew_id = db.execute('SELECT crew_id FROM crew_members WHERE worker_id=?', (self.ids[0],)).fetchone()[0]
            db.executemany('DELETE FROM crew_members WHERE worker_id=?', [(i,) for i in self.ids])
            db.commit()

    def rows(self):
        return self.client.get('/api/staffing?date=2026-09-12&shift=all').get_json()['rows']

    def payload(self):
        rows = [r for r in self.rows() if r['id'] in self.ids]
        return {'field': 'linear_itr', 'value': 'Новый линейный ИТР', 'linear_itr_person_id': None,
                'worker_ids': self.ids, 'expected_crews': {str(r['id']): r['crew_id'] for r in rows},
                'expected_tokens': {str(r['id']): r['row_token'] for r in rows},
                'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}

    def write(self, payload, client=None):
        return self.fixture.write('/api/staffing/groups/responsible', payload, client)

    def assert_unchanged(self):
        self.assertTrue(all(not r['linear_itr_override'] for r in self.rows()))

    def test_bulk_assignment_clear_and_history_preserve_membership(self):
        before = self.rows()
        response = self.write(self.payload())
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['updated'], 2)
        for row in self.rows():
            if row['id'] in self.ids:
                self.assertEqual(row['linear_itr_name'], 'Новый линейный ИТР')
                self.assertIsNone(row['crew_id'])
            else:
                self.assertEqual(row, next(r for r in before if r['id'] == row['id']))
        for direction in ('undo', 'redo'):
            action = self.client.get('/api/staffing/history').get_json()[direction]
            replay = self.client.post('/api/staffing/history/' + direction,
                json={'id': action['id'], 'token': action['token']}, headers={'X-CSRF-Token': 'staffing-csrf'})
            self.assertEqual(replay.status_code, 200, replay.get_json())
            if direction == 'undo':
                self.assert_unchanged()
        payload = self.payload()
        payload['value'] = None
        self.assertEqual(self.write(payload).status_code, 200)
        self.assert_unchanged()
        self.assertTrue(all(r['crew_id'] is None for r in self.rows() if r['id'] in self.ids))

    def test_mixed_crew_and_crewless_batch(self):
        self.ids.append(next(r['id'] for r in self.rows() if r['id'] not in self.ids))
        response = self.write(self.payload())
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['updated'], 3)

    def test_missing_membership_and_stale_tokens_reject_entire_batch(self):
        payload = self.payload()
        del payload['expected_crews'][str(self.ids[0])]
        self.assertEqual(self.write(payload).status_code, 400)
        payload = self.payload()
        payload['expected_tokens'][str(self.ids[0])] = 'stale'
        self.assertEqual(self.write(payload).status_code, 409)
        payload = self.payload()
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.crew_id, self.ids[0]))
            db.commit()
        self.assertEqual(self.write(payload).status_code, 409)
        self.assert_unchanged()

    def test_roles_and_csrf_remain_enforced(self):
        payload = self.payload()
        self.assertEqual(self.write(payload, self.fixture.foreman).status_code, 403)
        self.assertEqual(self.write(payload, self.fixture.viewer).status_code, 403)
        self.assertEqual(self.client.put('/api/staffing/groups/responsible', json=payload).status_code, 403)
        self.assert_unchanged()

    def test_other_responsible_field_still_requires_crew(self):
        payload = self.payload()
        payload['field'] = 'brigadier'
        self.assertEqual(self.write(payload).status_code, 400)
        self.assert_unchanged()
