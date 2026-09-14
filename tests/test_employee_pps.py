import unittest
import test_crews as fixtures


class EmployeePpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.CrewWorkflowTest.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.CrewWorkflowTest.tearDownClass()
    def setUp(self):
        self.f = fixtures.CrewWorkflowTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.worker = self.f.worker_ids[0]
        self.url = '/api/employees/' + str(self.worker) + '/pps'

    def payload(self, pps='ППС19'):
        return {'pps': pps, 'expected_token': self.f.employee(self.worker)['membership_token']}

    def test_manual_worker_pps_changes_reach_placement_and_preserve_identity(self):
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute("INSERT INTO manual_employees VALUES (?,?,'now','Рабочие','pps-test','hash')", (self.worker, self.f.admin_id))
            db.commit()
            original = dict(db.execute('SELECT * FROM workers WHERE id=?', (self.worker,)).fetchone())
        first = self.payload()
        self.assertEqual(self.f.write(self.f.admin, 'PUT', self.url, first).status_code, 200)
        self.assertEqual(self.f.write(self.f.admin, 'PUT', self.url, {**first, 'pps':'ППС15'}).status_code, 409)
        for value in ('ППС19', 'ППС15', ''):
            self.assertEqual(self.f.write(self.f.admin, 'PUT', self.url, self.payload(value)).status_code, 200)
            rows = self.f.admin.get('/api/staffing?date=2026-09-14&shift=all').get_json()['rows']
            self.assertEqual(next(r for r in rows if r['id']==self.worker)['pps'], value)
            with self.f.module.app.app_context():
                current = dict(self.f.module.get_db().execute('SELECT * FROM workers WHERE id=?', (self.worker,)).fetchone())
            self.assertEqual(current, {**original, 'pps': value})

    def test_validation_csrf_permissions_and_inactive_employee(self):
        self.assertEqual(self.f.admin.put(self.url, json=self.payload()).status_code, 403)
        self.assertEqual(self.f.write(self.f.viewer, 'PUT', self.url, self.payload()).status_code, 403)
        for value in (None, [], {}, True, 'ППС20'):
            self.assertEqual(self.f.write(self.f.admin, 'PUT', self.url, self.payload(value)).status_code, 400)
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.f.crew_a,self.worker))
            db.commit()
        self.assertEqual(self.f.write(self.f.foreman_b, 'PUT', self.url, self.payload()).status_code, 403)
        self.assertEqual(self.f.write(self.f.foreman_a, 'PUT', self.url, self.payload()).status_code, 200)
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[]','t',?,'now')", (self.f.owner_a,self.f.admin_id))
            db.commit()
        self.assertEqual(self.f.write(self.f.foreman_a, 'PUT', self.url, self.payload('ППС15')).status_code, 403)
        with self.f.module.app.app_context():
            db = self.f.module.get_db(); db.execute('UPDATE workers SET active=0 WHERE id=?', (self.worker,)); db.commit()
        self.assertEqual(self.f.write(self.f.admin, 'PUT', self.url, self.payload()).status_code, 409)


if __name__ == '__main__': unittest.main()
