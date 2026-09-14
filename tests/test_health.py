from contextlib import closing
import hashlib
import importlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from database_health import database_ready, open_readonly
from telegram_worker import healthcheck


ROOT = Path(__file__).resolve().parents[1]
TOKEN = '123456789:' + 'x' * 35


class HealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'DATABASE_PATH': str(Path(folder) / 'bootstrap.db'),
                    'ADMIN_PASSWORD': 'synthetic-health-password', 'SECRET_KEY': 'synthetic-health-secret-' * 2}):
                cls.module = importlib.import_module('app')

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / 'Проверка базы #1.db'
        env = patch.dict(os.environ, {'DATABASE_PATH': str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        target = patch.object(self.module, 'DATABASE_PATH', self.path)
        target.start()
        self.addCleanup(target.stop)
        self.client = self.module.app.test_client()

    def create_db(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY);
                CREATE TABLE workers(id INTEGER PRIMARY KEY);
                CREATE TABLE assignments(id INTEGER PRIMARY KEY);
                CREATE TABLE schema_versions(version INTEGER PRIMARY KEY);
                CREATE TABLE telegram_state(bot_id INTEGER PRIMARY KEY, polled_at INTEGER);
                INSERT INTO workers VALUES (42);
                INSERT INTO schema_versions VALUES (3);
            ''')

    def configure(self, text=None):
        (self.folder / 'telegram.env').write_text(text if text is not None else
            f'TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_BOT_USERNAME=placement_test_bot\n', encoding='utf-8')

    def set_polled_at(self, timestamp):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO telegram_state VALUES (?, ?)', (123456789, timestamp))

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            dump = tuple(db.iterdump())
            version = db.execute('PRAGMA schema_version').fetchone()[0]
        return dump, version, hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_liveness_does_not_open_database_even_with_session(self):
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        with patch.object(self.module, 'get_db', side_effect=AssertionError('Unexpected database access')):
            with patch('sqlite3.connect', side_effect=AssertionError('Unexpected SQLite connection')):
                for method in ('get', 'head'):
                    response = getattr(self.client, method)('/health')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertFalse(self.path.exists())

    def test_missing_database_and_parent_are_not_created(self):
        self.path = self.folder / 'missing' / 'placement.db'
        with patch.object(self.module, 'DATABASE_PATH', self.path), patch.dict(os.environ, {'DATABASE_PATH': str(self.path)}):
            response = self.client.get('/ready')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.get_json(), {'status': 'unavailable',
                'error': 'База данных недоступна или не подготовлена.'})
            self.assertNotIn(str(self.path), response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(healthcheck(), 1)
        self.assertFalse(self.path.parent.exists())

    def test_corrupt_database_is_unavailable_without_changes(self):
        self.path.write_bytes(b'This is not a SQLite database')
        before = self.path.read_bytes()
        self.assertEqual(self.client.get('/ready').status_code, 503)
        self.assertEqual(healthcheck(), 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_file_and_incomplete_schema_are_not_ready(self):
        self.path.touch()
        self.assertFalse(database_ready(self.path))
        self.create_db()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE assignments')
        before = self.snapshot()
        self.assertEqual(self.client.get('/ready').status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_ready_and_disabled_bot_do_not_change_schema_or_data(self):
        self.create_db()
        before = self.snapshot()
        for _ in range(2):
            response = self.client.get('/ready')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {'status': 'ok'})
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(healthcheck(), 0)
        self.assertEqual(self.snapshot(), before)

    def test_real_initialized_database_is_ready(self):
        with self.module.app.app_context():
            self.module.init_db()
        before = self.snapshot()
        self.assertEqual(self.client.get('/ready').status_code, 200)
        self.assertEqual(healthcheck(), 0)
        self.assertEqual(self.snapshot(), before)

    def test_connection_rejects_writes(self):
        self.create_db()
        before = self.snapshot()
        with open_readonly(self.path) as db:
            for sql in ('DELETE FROM workers', 'CREATE TABLE accidental(id INTEGER)'):
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute(sql)
        self.assertEqual(self.snapshot(), before)

    def test_locked_database_fails_with_short_timeout(self):
        self.create_db()
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute('BEGIN EXCLUSIVE')
            for probe in (lambda: self.client.get('/ready').status_code, healthcheck):
                started = time.monotonic()
                self.assertIn(probe(), (503, 1))
                self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(database_ready(self.path))

    def test_polling_freshness_boundary_and_missing_state(self):
        self.create_db()
        self.configure()
        self.assertEqual(healthcheck(), 1)
        for stamp, expected in ((0, 1), (880, 1), (881, 0), (1000, 0)):
            with self.subTest(stamp=stamp):
                self.set_polled_at(stamp)
                before = self.snapshot()
                with patch('telegram_worker.time.time', return_value=1000):
                    self.assertEqual(healthcheck(), expected)
                self.assertEqual(self.snapshot(), before)

    def test_polling_reads_committed_wal_without_checkpoint(self):
        self.create_db()
        self.configure()
        self.set_polled_at(0)
        writer = sqlite3.connect(self.path)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('UPDATE telegram_state SET polled_at=?', (int(time.time()),))
            writer.commit()
            before = {path.name: path.read_bytes() for path in (self.path, Path(str(self.path) + '-wal'))}
            self.assertEqual(healthcheck(), 0)
            self.assertTrue(database_ready(self.path))
            self.assertEqual({path.name: path.read_bytes() for path in (self.path, Path(str(self.path) + '-wal'))}, before)
        finally:
            writer.close()

    def run_guarded_cli(self):
        # A separate process catches eager imports even if other tests imported app.
        # Also run without startup secrets: this probe must not initialize Flask.
        script = '''
import importlib.abc, runpy, socket, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('app', 'telegram_report_bot'):
            raise AssertionError('Healthcheck imported application')
sys.meta_path.insert(0, Guard())
def no_network(*args, **kwargs):
    raise AssertionError('Healthcheck attempted network access')
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
sys.argv = ['telegram_worker.py', '--healthcheck']
runpy.run_path('telegram_worker.py', run_name='__main__')
'''
        env = {key: value for key, value in os.environ.items() if key not in
               ('ADMIN_PASSWORD', 'ADMIN_USERNAME', 'SECRET_KEY')}
        return subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_cli_no_application_import_network_or_mutation(self):
        self.create_db()
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                if enabled:
                    self.configure()
                    self.set_polled_at(int(time.time()))
                before = self.snapshot()
                result = self.run_guarded_cli()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout + result.stderr, '')
                self.assertEqual(self.snapshot(), before)

    def test_cli_errors_are_silent_and_do_not_initialize_schema(self):
        self.create_db()
        self.configure()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE telegram_state')
        before = self.snapshot()
        result = self.run_guarded_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout + result.stderr, '')
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE telegram_state(bot_id INTEGER PRIMARY KEY, polled_at INTEGER)')
        for text in (f'TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_BOT_USERNAME=bad!\n', 'broken',
                     'TELEGRAM_BOT_TOKEN=' + '9' * 20 + ':' + 'x' * 35 +
                     '\nTELEGRAM_BOT_USERNAME=placement_test_bot\n'):
            self.configure(text)
            result = self.run_guarded_cli()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout + result.stderr, '')
        (self.folder / 'telegram.env').write_bytes(b'\xff')
        self.assertEqual(healthcheck(), 1)


if __name__ == '__main__':
    unittest.main()
