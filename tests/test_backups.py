import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from backup_api import create_backup, inspect_backup
import test_staffing as staffing_tests


class BackupSnapshotTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'placement.db'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE assignments(worker_id INTEGER,subobject_id INTEGER,work_date TEXT,shift TEXT)')
        self.db.executemany('INSERT INTO assignments VALUES (?,?,?,?)', [
            (1, 1, '2026-09-13', '1 смена'), (1, 2, '2026-09-13', '2 смена'),
            (2, 1, '2026-09-13', 'Ночная смена'), (3, 1, '2026-09-14', '1 смена'),
            (4, None, '2026-09-13', '1 смена')])
        self.db.commit()

    def read(self, path, day='2026-09-13'):
        stat = path.stat()
        return inspect_backup(str(path), stat.st_size, stat.st_mtime_ns, day)

    def test_snapshot_counts_unique_people_for_selected_date_and_preserves_source(self):
        before = self.db.execute('SELECT * FROM assignments').fetchall()
        target = create_backup(self.db, '2026-09-13')
        self.assertEqual(before, self.db.execute('SELECT * FROM assignments').fetchall())
        result = self.read(target)
        self.assertEqual((result['employee_count'], result['assignment_count']), (2, 3))
        self.assertEqual(result['time_source'], 'recorded')
        self.assertEqual(result['reason'], 'manual')
        self.assertIn('-2people-', target.name)
        self.assertTrue(result['created_at'].endswith('+00:00'))
        self.db.execute("DELETE FROM assignments WHERE work_date='2026-09-13'")
        self.db.commit()
        self.assertEqual(self.read(target)['employee_count'], 2)
        self.assertEqual(self.read(target, '2026-09-14')['employee_count'], 1)
        with closing(sqlite3.connect(target)) as saved:
            self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_backing_up_inside_transaction_captures_committed_state(self):
        self.db.execute('BEGIN IMMEDIATE')
        self.db.execute("INSERT INTO assignments VALUES (5,1,'2026-09-13','1 смена')")
        target = create_backup(self.db, '2026-09-13', reason='automatic')
        self.assertEqual(self.read(target)['employee_count'], 2)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_legacy_invalid_metadata_and_unreadable_file_are_reported(self):
        target = create_backup(self.db, '2026-09-13')
        target.with_suffix('.sqlite3.json').write_text('[]', encoding='utf-8')
        inspect_backup.cache_clear()
        self.assertEqual(self.read(target)['time_source'], 'file')
        self.assertEqual(self.read(target)['employee_count'], 2)
        broken = target.parent / 'broken.db'
        broken.write_bytes(b'not a database')
        result = self.read(broken)
        self.assertEqual(result['status'], 'unreadable')
        self.assertIsNone(result['employee_count'])


class BackupApiTest(unittest.TestCase):
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
        self.admin = self.fixture.admin
        self.headers = {'X-CSRF-Token': 'staffing-csrf'}
        self.set_role('super_admin')

    def set_role(self, role):
        with self.fixture.app.app_context():
            db = self.fixture.module.get_db()
            db.execute('UPDATE users SET role=? WHERE id=?', (role, self.fixture.admin_id))
            db.commit()

    def test_creation_listing_and_role_protected_download(self):
        result = self.admin.post('/api/backups', json={'date': '2026-09-13'}, headers=self.headers)
        self.assertEqual(result.status_code, 201)
        name = result.get_json()['name']
        listed = self.admin.get('/api/backups?date=2026-09-13').get_json()
        self.assertIn(name, [row['name'] for row in listed['rows']])
        self.assertTrue(listed['can_download'])
        self.set_role('admin')
        self.assertEqual(self.admin.get('/api/backups/' + name + '/download').status_code, 403)
        self.assertEqual(self.admin.get('/api/backups').status_code, 403)
        self.assertEqual(self.admin.post('/api/backups', json={'date': '2026-09-13'}, headers=self.headers).status_code, 403)
        self.set_role('super_admin')
        downloaded = self.admin.get('/api/backups/' + name + '/download')
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.data.startswith(b'SQLite format 3'))
        self.assertIn('no-store', downloaded.headers['Cache-Control'])
        downloaded.close()
        self.assertEqual(self.admin.get('/api/backups/' + name + '.json/download').status_code, 404)
        self.assertEqual(self.admin.get('/api/backups/..%5Cplacement.db/download').status_code, 404)

    def test_separate_tab_is_rendered_only_for_super_admin(self):
        html = self.admin.get('/').get_data(as_text=True)
        self.assertEqual(html.count('data-view="backups"'), 2)
        self.assertIn('id="view-backups"', html)
        accounts = html.split('id="view-accounts"', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('id="backup-panel"', accounts)
        for role in ('admin', 'foreman', 'viewer'):
            self.set_role(role)
            html = self.admin.get('/').get_data(as_text=True)
            self.assertNotIn('data-view="backups"', html)
            self.assertNotIn('id="view-backups"', html)
            self.assertNotIn('id="backup-panel"', html)
            self.assertNotIn('/static/backups.js', html)
            self.assertEqual(self.admin.get('/api/backups').status_code, 403)
            self.assertEqual(self.admin.post('/api/backups', json={'date': '2026-09-13'}, headers=self.headers).status_code, 403)

    def test_permissions_csrf_and_invalid_parameters(self):
        self.assertEqual(self.fixture.app.test_client().get('/api/backups').status_code, 401)
        self.assertEqual(self.fixture.foreman.get('/api/backups').status_code, 403)
        self.assertEqual(self.fixture.viewer.get('/api/backups').status_code, 403)
        self.assertEqual(self.admin.post('/api/backups', json={'date': '2026-09-13'}).status_code, 403)
        for day in (None, '20260913', '2026-02-30'):
            self.assertEqual(self.admin.post('/api/backups', json={'date': day}, headers=self.headers).status_code, 400)
        self.assertEqual(self.admin.get('/api/backups?page=0').status_code, 400)
        self.assertEqual(self.admin.get('/api/backups?date=broken').status_code, 400)

    def test_legacy_files_and_pagination(self):
        with self.fixture.app.app_context():
            database = self.fixture.module.DATABASE_PATH
            folder = database.parent / 'backups'
            folder.mkdir(exist_ok=True)
            for i in range(22):
                with closing(sqlite3.connect(folder / f'legacy-{i}.db')) as saved:
                    self.fixture.module.get_db().backup(saved)
            (folder / 'unfinished.partial').write_bytes(b'partial')
            (folder / 'ignore.json').write_text('{}')
        first = self.admin.get('/api/backups?date=2026-09-13').get_json()
        second = self.admin.get('/api/backups?date=2026-09-13&page=2').get_json()
        self.assertEqual(len(first['rows']), 20)
        self.assertFalse({row['name'] for row in first['rows']} & {row['name'] for row in second['rows']})
        self.assertGreaterEqual(first['total'], 22)
        legacy = next(row for row in first['rows'] if row['name'].startswith('legacy-'))
        self.assertEqual(legacy['time_source'], 'file')
        self.assertEqual(legacy['employee_count'], 0)
