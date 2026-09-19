import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import Flask
from werkzeug.exceptions import HTTPException

from workforce_jobs import request_parameters


class JobPayloadTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_preserves_repeated_filters_and_rejects_reserved_or_invalid_values(self):
        with self.app.test_request_context():
            kind, day, params, _ = request_parameters({'type': 'registry', 'date': '2026-09-16', 'section': 'rotation',
                'filters': [['department', 'СМУ 1'], ['department', 'СМУ 2'], ['stage', 'stage.onsite']], 'request_key': str(uuid4())})
            self.assertEqual((kind, day, params), ('registry', '2026-09-16', {'section': 'rotation',
                'filters': [['department', 'СМУ 1'], ['department', 'СМУ 2'], ['stage', 'stage.onsite']]}))
            for filters in ([['date', '2026-09-16']], [['bad-key', 'x']], [['department', 1]]):
                with self.assertRaises(HTTPException) as error:
                    request_parameters({'type': 'placement_pdf', 'date': '2026-09-16', 'filters': filters, 'request_key': str(uuid4())})
                self.assertEqual(error.exception.code, 400)


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class DeferredJobIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.migrate_sqlite_to_postgres import load_environment
        load_environment(Path(os.environ['CREW_POSTGRES_TEST_ENV']))
        from postgres_db import get_pool
        with get_pool().connection() as connection:
            if connection.execute('SELECT max(version) FROM workforce_schema_versions').fetchone()[0] < 28:
                raise unittest.SkipTest('Apply migration 028 to the isolated test database.')

    def setUp(self):
        import test_workforce_api as fixtures
        self.fixture = fixtures.WorkforceApiTest('test_background_report_owned_scope_and_download')
        self.fixture.setUpClass(); self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        from placement_report import register_placement_report
        register_placement_report(self.fixture.app, lambda: self.fixture.db, lambda *roles: lambda fn: fn)
        for worker in self.fixture.ids:
            self.fixture.add_source_record(worker, 'urp:П15')

    def create(self, body, role='foreman'):
        return self.fixture.request('post', 'export-jobs', body, role)

    def test_registry_job_is_idempotent_owned_and_preserves_repeated_filters(self):
        from workforce_jobs import process_one
        key = str(uuid4())
        body = {'type': 'registry', 'date': '2026-09-16', 'section': 'rotation',
                'filters': [['department', 'TEST-SMU'], ['department', 'OTHER-SMU']], 'request_key': key}
        self.assertEqual(self.create(body).status_code, 202)
        self.assertEqual(self.create(body).status_code, 200)
        changed = {**body, 'filters': [['department', 'TEST-SMU']]}
        self.assertEqual(self.create(changed).status_code, 409)
        self.assertEqual(self.fixture.request('get', f'export-jobs/{key}', role='rotation').status_code, 404)
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'WORKFORCE_EXPORT_DIR': directory}):
            self.assertTrue(process_one(self.fixture.app, lambda: self.fixture.db, key))
            ready = self.fixture.request('get', f'export-jobs/{key}', role='foreman')
            self.assertEqual(ready.json['job_type'], 'registry')
            self.assertEqual(ready.json['state'], 'ready', ready.data)
            file = self.fixture.request('get', f'export-jobs/{key}/download', role='foreman')
            self.assertEqual(file.status_code, 200, file.data)
            self.assertTrue(file.data.startswith(b'PK'))
            file.close()
            self.assertEqual(file.headers['X-Export-Row-Count'], '1')

    def test_scope_change_fails_job_and_pdf_uses_the_protected_view(self):
        from workforce_jobs import endpoint_content, process_one
        stale = str(uuid4())
        registry = {'type': 'registry', 'date': '2026-09-16', 'section': 'rotation', 'filters': [], 'request_key': stale}
        self.assertEqual(self.create(registry).status_code, 202)
        self.fixture.db.native("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=%s", (self.fixture.users['foreman']['id'],))
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'WORKFORCE_EXPORT_DIR': directory}):
            self.assertTrue(process_one(self.fixture.app, lambda: self.fixture.db, stale))
        self.assertEqual(self.fixture.request('get', f'export-jobs/{stale}', role='foreman').status_code, 403)
        # placement_report rolls back its own read transaction. Its protected view
        # therefore runs in a separate app context and cannot share queue state.
        actor = self.fixture.users['admin']
        content, headers = endpoint_content(self.fixture.app, 'placement_report',
                                            '/api/placement-report/pdf?date=2026-09-16&details=0', actor)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertEqual(headers['Content-Type'], 'application/pdf')



if __name__ == '__main__':
    unittest.main()
