import unittest

import test_outstaff


class OutstaffRemovalTest(unittest.TestCase):
    setUpClass = classmethod(test_outstaff.OutstaffTest.setUpClass.__func__)
    tearDownClass = classmethod(test_outstaff.OutstaffTest.tearDownClass.__func__)
    setUp = test_outstaff.OutstaffTest.setUp
    post = test_outstaff.OutstaffTest.post
    apply = test_outstaff.OutstaffTest.apply

    def test_imported_outstaff_removal_keeps_source_history_and_audit(self):
        self.assertEqual(self.apply().status_code, 200)
        row = self.client.get('/api/employees?scope=outstaff').get_json()['rows'][0]
        self.assertTrue(row['can_remove'])
        url = f"/api/employees/{row['id']}"
        with self.module.app.app_context():
            db = self.module.get_db()
            site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
            for day in ['2026-09-11', '2026-09-12']:
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,
                    employer,foreman_user_id,created_at) VALUES (?,'1 смена',?,?,?,?,?)''',
                    (day, site, row['id'], row['employer'], self.admin, self.module.utc_now()))
            db.commit()
        preview = self.client.get(url + '/removal-preview?date=2026-09-12').get_json()
        headers = {'X-CSRF-Token': 'outstaff-csrf-token'}
        self.assertEqual(self.client.delete(url, json={**preview, 'reason': ' '}, headers=headers).status_code, 400)
        payload = {**preview, 'reason': 'Увольнение сотрудника аутстаффа'}
        response = self.client.delete(url, json=payload, headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['cleared_assignments'], 1)
        archived = self.client.get('/api/employees?scope=outstaff').get_json()['rows'][0]
        self.assertFalse(archived['active'])
        self.assertFalse(archived['can_remove'])
        self.assertEqual(archived['removal']['reason'], payload['reason'])
        self.assertEqual(archived['outstaff'], row['outstaff'])
        log = self.client.get('/api/logs?action=employee_delete').get_json()
        self.assertEqual(log['total'], 1)
        self.assertEqual(log['rows'][0]['worker_name'], row['full_name'])
        self.assertEqual(log['rows'][0]['actor_id'], self.admin)
        self.assertEqual(log['rows'][0]['reason'], payload['reason'])
        self.assertEqual(self.client.delete(url, json=payload, headers=headers).status_code, 409)
        review = self.post('preview').get_json()
        self.assertGreater(review['unresolved'], 0)
        repeated = self.post('apply', token=review['token'])
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.get_json()['already_imported'])
        self.content = test_outstaff.workbook([{'departure': '2026-09-12'}])
        review = self.post('preview').get_json()
        self.assertGreater(review['unresolved'], 0)
        self.assertEqual(self.post('apply', token=review['token']).status_code, 400)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT active FROM workers WHERE id=?', (row['id'],)).fetchone()[0], 0)
            self.assertEqual([r[0] for r in db.execute('SELECT work_date FROM assignments WHERE worker_id=?',
                                                   (row['id'],))], ['2026-09-11'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outstaff_import_rows').fetchone()[0], 1)
