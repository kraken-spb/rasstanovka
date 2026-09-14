import unittest
import test_export_unassigned as fixtures


class ExportSmuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.ExportUnassignedTest.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.ExportUnassignedTest.tearDownClass()
    def setUp(self):
        self.f = fixtures.ExportUnassignedTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        with self.f.fixture.module.app.app_context():
            db = self.f.fixture.module.get_db()
            db.execute('UPDATE workers SET department=? WHERE id IN (?,?)', ('СМУ тест', self.f.fixture.visible_worker, self.f.fixture.hidden_worker))
            db.commit()

    def test_department_filters_assigned_and_unassigned_and_counts(self):
        response = self.f.export(department='СМУ тест')
        rows = self.f.rows(response)
        self.assertEqual(response.headers['X-Export-Count'], '3')
        self.assertEqual(response.headers['X-Export-Unassigned-Count'], '1')
        self.assertEqual({row[13] for row in rows}, {'СМУ тест'})
        self.assertEqual({row[6] for row in rows}, {'000123', '000125', '000201'})
        self.assertEqual(self.f.export(department='СМУ тест', shift='2 смена').status_code, 404)
        self.assertEqual(self.f.export(department='СМУ неизвестное').status_code, 404)
        self.assertEqual(self.f.export(department='x'*501).status_code, 400)
        self.assertEqual(self.f.export().headers['X-Export-Count'], '8')

    def test_summary_and_access_remain_filtered(self):
        for kind in ('staffing', 'summary'):
            response = self.f.export(client=self.f.fixture.foreman, department='СМУ тест', kind=kind, include_unassigned='0')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['X-Export-Count'], '1')
        response = self.f.export(client=self.f.fixture.foreman, department='СМУ тест')
        self.assertEqual({r[6] for r in self.f.rows(response)}, {'000123', '000201'})
        with self.f.fixture.module.app.app_context():
            db = self.f.fixture.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[\"Другое СМУ\"]','t',?,'now')", (self.f.fixture.foreman_id,self.f.fixture.admin_id));db.commit()
        self.assertEqual(self.f.export(client=self.f.fixture.foreman,department='СМУ тест').status_code,404)


if __name__ == '__main__': unittest.main()
