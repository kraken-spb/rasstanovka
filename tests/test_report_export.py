import json
import unittest

import test_staffing_export as export_tests


class ReportExportTest(unittest.TestCase):
    setUpClass = classmethod(export_tests.StaffingExportTest.setUpClass.__func__)
    tearDownClass = classmethod(export_tests.StaffingExportTest.tearDownClass.__func__)

    def setUp(self):
        self.fixture = export_tests.StaffingExportTest()
        self.fixture.module = self.module
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET department='СМУ А' WHERE id=?", (self.fixture.visible_worker,))
            db.execute("UPDATE workers SET department='СМУ Б' WHERE id=?", (self.fixture.hidden_worker,))
            db.commit()

    def options(self, client):
        return client.get('/api/staffing/export/options?date=2026-09-13&shift=all')

    def test_viewer_options_match_export_population_and_keep_history(self):
        response = self.options(self.fixture.viewer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.get_json(), self.options(self.fixture.admin).get_json())
        self.assertEqual(response.get_json()['departments'], ['СМУ А', 'СМУ Б'])
        self.assertIn('Ручная ГДЛР', response.get_json()['categories'])
        self.assertIn('Подрядчик', response.get_json()['contractors'])

    def test_scope_is_enforced_and_anonymous_access_denied(self):
        self.assertEqual(self.options(self.module.app.test_client()).status_code, 401)
        self.assertEqual(self.fixture.viewer.get('/api/staffing/export/options?date=invalid&shift=all').status_code, 400)
        self.assertEqual(self.options(self.fixture.foreman).get_json()['departments'], ['СМУ А'])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected',?,'token',?,'now')",
                       (self.fixture.admin_id, json.dumps(['СМУ Б']), self.fixture.admin_id))
            db.commit()
        self.assertEqual(self.options(self.fixture.admin).get_json()['departments'], ['СМУ Б'])
        self.assertEqual(self.options(self.fixture.viewer).get_json()['departments'], ['СМУ А', 'СМУ Б'])

    def test_export_form_is_in_reports_for_viewer_and_editor(self):
        for client in (self.fixture.viewer, self.fixture.foreman, self.fixture.admin):
            html = client.get('/').get_data(as_text=True)
            self.assertEqual(html.count('id="staffing-export-form"'), 1)
            self.assertGreater(html.index('id="staffing-export-form"'), html.index('id="view-dashboard"'))
            self.assertIn('/static/report-export.js?', html)
            self.assertIn('/static/report-export.css?', html)
