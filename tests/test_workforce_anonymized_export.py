"""Report classification is a business fact, independent of retained Excel files."""
import os
import unittest
from flask import g

@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'),'Select isolated PostgreSQL explicitly.')
class AnonymizedExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_workforce_api import WorkforceApiTest
        WorkforceApiTest.setUpClass()

    def test_removed_provenance_keeps_report_section(self):
        from test_workforce_api import WorkforceApiTest
        from workforce_export import report_records,rows_for_report
        f=WorkforceApiTest();f.setUp();self.addCleanup(f.tearDown)
        category=f.db.native("SELECT id FROM gdlr_categories WHERE name='Бетонщик'").fetchone()[0]
        f.db.native("INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (%s,%s,'x',%s,'2026-09-18')",(f.worker,category,f.users['admin']['id']))
        f.db.native("UPDATE workforce_profiles SET employment_code='employment.external',pure_outstaff=FALSE,has_main_registry_record=TRUE WHERE worker_id=%s",(f.worker,))
        with f.app.test_request_context():
            g.user=f.users['admin']
            records=[r for r in report_records(f.db,'2026-09-18') if r['id']==f.worker]
            self.assertEqual(len(records),1)
            self.assertTrue(records[0]['mixed_sources'])
            self.assertEqual([len(t) for t in rows_for_report(records)],[0,1])
            f.db.native('UPDATE workforce_profiles SET has_main_registry_record=FALSE WHERE worker_id=%s',(f.worker,))
            records=[r for r in report_records(f.db,'2026-09-18') if r['id']==f.worker]
            self.assertEqual([len(t) for t in rows_for_report(records)],[1,0])
