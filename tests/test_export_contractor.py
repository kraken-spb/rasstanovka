import unittest
import test_export_smu as fixtures


class ExportContractorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.ExportSmuTest.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.ExportSmuTest.tearDownClass()
    def setUp(self):
        self.smu = fixtures.ExportSmuTest(); self.smu.setUp(); self.addCleanup(self.smu.doCleanups)
        self.f = self.smu.f
        with self.f.fixture.module.app.app_context():
            db = self.f.fixture.module.get_db()
            company = db.execute("INSERT INTO contractors(name,name_key,edit_token,updated_at) VALUES ('ЛГСС','лгсс','test','now')").lastrowid
            other = db.execute("INSERT INTO contractors(name,name_key,edit_token,updated_at) VALUES ('Подрядчик Б','подрядчик б','test','now')").lastrowid
            for worker, contractor in [(self.f.fixture.visible_worker,company),(self.f.fixture.hidden_worker,company),(self.f.free,company),(self.f.fixture.legacy_worker,other)]:
                db.execute("UPDATE workers SET contractor='Исходный подрядчик' WHERE id=?",(worker,))
                db.execute("INSERT INTO employee_contractors VALUES (?,?,'t',?,'now') ON CONFLICT(worker_id) DO UPDATE SET contractor_id=excluded.contractor_id",(worker,contractor,self.f.fixture.admin_id))
            db.commit()

    def test_current_contractor_with_smu_and_unassigned_filters_both_tabs(self):
        response = self.f.export(contractor='ЛГСС',department='СМУ тест')
        rows = self.f.rows(response)
        self.assertEqual({r[3] for r in rows}, {'ЛГСС'})
        self.assertEqual({r[6] for r in rows}, {'000123','000125','000201'})
        self.assertEqual(response.headers['X-Export-Count'], '3')
        self.assertEqual(response.headers['X-Export-Unassigned-Count'], '1')
        book = self.f.fixture.workbook(response)
        self.assertEqual(len(book.sheetnames), 2)
        summary = '\n'.join(str(cell.value) for row in book.worksheets[1] for cell in row if cell.value is not None)
        self.assertIn('Строк в списке: 3',summary)
        self.assertIn('=Работодатель',summary)
        self.assertNotIn('Подрядчик Б',summary)
        self.assertEqual(self.f.export(contractor='ЛГСС',department='СМУ тест',category='Ручная ГДЛР').headers['X-Export-Count'],'1')
        self.assertEqual(self.f.export(contractor='Подрядчик Б',department='СМУ тест').status_code,404)
        other = self.f.export(contractor='Подрядчик Б',shift='2 смена')
        self.assertEqual([r[6] for r in self.f.rows(other)],['000124'])

    def test_permissions_empty_invalid_and_all_contractors(self):
        response = self.f.export(client=self.f.fixture.foreman,contractor='ЛГСС')
        self.assertEqual({r[6] for r in self.f.rows(response)},{'000123','000201'})
        self.assertEqual(self.f.export(contractor='Исходный подрядчик').status_code,404)
        self.assertEqual(self.f.export(contractor='x'*501).status_code,400)
        self.assertEqual(self.f.export().headers['X-Export-Count'],'8')
        with self.f.fixture.module.app.app_context():
            db = self.f.fixture.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[]','t',?,'now')",(self.f.fixture.foreman_id,self.f.fixture.admin_id));db.commit()
        self.assertEqual(self.f.export(client=self.f.fixture.foreman,contractor='ЛГСС').status_code,404)


if __name__ == '__main__': unittest.main()
