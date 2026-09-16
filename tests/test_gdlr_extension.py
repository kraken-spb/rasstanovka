import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.extend_gdlr_catalog import apply, preview


class GdlrExtensionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'catalog.db'
        self.backups = Path(self.temp.name) / 'backups'
        self.db = sqlite3.connect(self.database)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT NOT NULL, active INTEGER NOT NULL);
            CREATE TABLE workers (id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);
            CREATE TABLE gdlr_categories (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1, edit_token TEXT NOT NULL, updated_by INTEGER,
                updated_at TEXT NOT NULL, staffing_allowed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE employee_gdlr (worker_id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL);
        ''')
        self.db.execute("INSERT INTO users VALUES (1,'super_admin',1)")
        self.db.execute("INSERT INTO users VALUES (2,'admin',1)")
        self.db.execute("INSERT INTO workers VALUES (1,'Связанный сотрудник')")
        category = self.db.execute("INSERT INTO gdlr_categories VALUES (1,'Допущенная','допущенная',1,'token',1,'now',1)").lastrowid
        self.db.execute('INSERT INTO employee_gdlr VALUES (1,?)', (category,))
        self.db.commit()
        self.candidates = [{'name': 'УРП новая категория', 'sources': ['fixture']}]

    def test_stale_preview_rejects_apply_without_catalog_write(self):
        expected = preview(self.db, self.candidates)['fingerprint']
        self.db.execute("INSERT INTO gdlr_categories VALUES (2,'Параллельная','параллельная',1,'token',1,'now',0)")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'изменился после проверки'):
            apply(self.db, self.candidates, expected, 1, self.backups)
        self.assertIsNone(self.db.execute("SELECT 1 FROM gdlr_categories WHERE name='УРП новая категория'").fetchone())

    def test_only_active_super_admin_can_apply(self):
        expected = preview(self.db, self.candidates)['fingerprint']
        with self.assertRaisesRegex(ValueError, 'супер-администратор'):
            apply(self.db, self.candidates, expected, 2, self.backups)
        self.assertIsNone(self.db.execute("SELECT 1 FROM gdlr_categories WHERE name='УРП новая категория'").fetchone())

    def test_apply_is_idempotent_and_preserves_employee_binding(self):
        expected = preview(self.db, self.candidates)['fingerprint']
        result = apply(self.db, self.candidates, expected, 1, self.backups)
        self.assertEqual(result['created'], 1)
        added = self.db.execute("SELECT staffing_allowed FROM gdlr_categories WHERE name='УРП новая категория'").fetchone()
        self.assertEqual(added['staffing_allowed'], 0)
        self.assertEqual(self.db.execute('SELECT category_id FROM employee_gdlr WHERE worker_id=1').fetchone()['category_id'], 1)
        second = apply(self.db, self.candidates, preview(self.db, self.candidates)['fingerprint'], 1, self.backups)
        self.assertEqual(second, {'already_applied': True, 'created': 0})
