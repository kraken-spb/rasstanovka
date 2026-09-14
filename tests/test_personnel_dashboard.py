import unittest

import test_crews


class PersonnelDashboardTest(unittest.TestCase):
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

    def dashboard(self, client=None, **query):
        return (client or self.case.admin).get('/api/personnel-dashboard', query_string={
            'start': '2026-09-11', 'end': '2026-09-13', **query})

    def test_distinct_people_dates_actual_authors_and_zero_days(self):
        c = self.case
        ids = c.worker_ids[:2]
        self.assertEqual(c.add(c.crew_a, ids).status_code, 200)
        self.assertEqual(c.place(ids, c.sites[0]).status_code, 200)
        self.assertEqual(c.place(ids[:1], c.sites[0], client=c.admin, shift='2 смена').status_code, 200)
        self.assertEqual(c.place(ids[:1], c.sites[0], work_date='2026-09-12').status_code, 200)
        result = self.dashboard().get_json()
        self.assertEqual(result['counts'], [2, 1, 0])
        self.assertEqual(result['unique_count'], 2)
        users = {u['id']: u for u in result['users']}
        self.assertEqual(users[str(c.owner_a)]['counts'], [2, 1, 0])
        self.assertEqual(users[str(c.admin_id)]['counts'], [1, 0, 0])
        self.assertEqual(users[str(c.empty_owner)]['counts'], [0, 0, 0])
        self.assertEqual(users[str(c.owner_a)]['unique_count'], 2)
        self.assertEqual(self.dashboard(c.viewer).get_json(), result)
        # Existing author identity remains distinct even with identical or corrected names.
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute("UPDATE users SET full_name='Иванов Иван Иванович' WHERE id IN (?,?)", (c.owner_a, c.admin_id))
            db.execute('UPDATE workers SET active=0 WHERE id=?', (ids[0],))
            db.commit()
        renamed = self.dashboard().get_json()
        self.assertEqual(renamed['counts'], [2, 1, 0])
        self.assertEqual(len([u for u in renamed['users'] if u['full_name'] == 'Иванов Иван Иванович']), 2)

    def test_scope_cleared_assignments_and_missing_author_history(self):
        c = self.case
        self.assertEqual(c.add(c.crew_a, c.worker_ids[:1]).status_code, 200)
        self.assertEqual(c.add(c.crew_b, c.worker_ids[1:2], c.foreman_b).status_code, 200)
        self.assertEqual(c.place(c.worker_ids[:1], c.sites[0], client=c.admin).status_code, 200)
        self.assertEqual(c.place(c.worker_ids[1:2], c.sites[0], crew=c.crew_b, client=c.foreman_b).status_code, 200)
        own = self.dashboard(c.foreman_a).get_json()
        self.assertEqual(own['scope'], 'own_crews')
        self.assertEqual(own['counts'], [1, 0, 0])
        self.assertEqual([u['id'] for u in own['users']], [str(c.admin_id)])
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute("UPDATE assignments SET created_at='2099-01-01T00:00:00Z' WHERE worker_id=?", (c.worker_ids[0],))
            db.execute('DELETE FROM assignments WHERE worker_id=?', (c.worker_ids[1],))
            db.commit()
        result = self.dashboard().get_json()
        self.assertEqual(result['counts'], [1, 0, 0])
        self.assertEqual(next(u for u in result['users'] if u['id'] == 'unknown')['counts'], [1, 0, 0])
        self.assertEqual(next(u for u in result['users'] if u['id'] == str(c.owner_b))['unique_count'], 0)

    def test_range_validation_authentication_and_empty_period(self):
        for query in ({'start': ''}, {'end': 'wrong'}, {'start': '2026-09-14'},
                      {'start': '2026-01-01'}, {'end': '2026-02-30'}):
            self.assertEqual(self.dashboard(**query).status_code, 400)
        result = self.dashboard(start='2026-06-14').get_json()
        self.assertEqual(len(result['dates']), 92)
        self.assertEqual(result['counts'], [0] * 92)
        self.assertEqual(result['unique_count'], 0)
        self.assertEqual(self.dashboard(start='2026-09-13').get_json()['counts'], [0])
        guest = self.case.module.app.test_client()
        self.assertEqual(self.dashboard(guest).status_code, 401)
        self.assertEqual(len(self.case.admin.get('/api/personnel-dashboard').get_json()['dates']), 14)
