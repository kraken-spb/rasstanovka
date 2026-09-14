import importlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tools.sync_gdlr_catalog import apply, preview, protected_hashes


class CategorySyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                        'SECRET_KEY': 'category-sync-test-secret-at-least-thirty-two-characters',
                        'ADMIN_PASSWORD': 'category-sync-test-password'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'sync.db'
        previous = self.module.DATABASE_PATH
        self.module.DATABASE_PATH = self.path
        try:
            with self.module.app.app_context():
                self.module.init_db()
        finally:
            self.module.DATABASE_PATH = previous
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.db.close)
        self.actor = self.db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
        self.db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.actor,))
        batch = self.db.execute('''INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json)
            VALUES ('sync-test','Текущий файл.xlsx',?,'now',3,'{}')''', (self.actor,)).lastrowid
        self.people = []
        for n, category in enumerate((' Монтажник  ТТ ', 'монтажник ТТ', 'Сварщик МК', '')):
            worker = self.db.execute('INSERT INTO workers(full_name,personnel_no,category) VALUES (?,?,?)',
                                     ('Сотрудник ' + str(n), 'sync-' + str(n), category)).lastrowid
            self.people.append(worker)
            if n < 3:
                self.db.execute('INSERT INTO staffing_import_members(import_id,worker_id,source_row,source_crew) VALUES (?,?,?,?)',
                                (batch, worker, n + 2, 'Тестовая'))
        site = self.db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
        for worker in self.people:
            self.db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,foreman_user_id,created_at)
                VALUES ('2026-09-13','1 смена',?,?,?,'now')''', (site, worker, self.actor))
        self.db.commit()

    def test_deduplicates_binds_and_preserves_placement_with_verified_backup(self):
        plan = preview(self.db)
        self.assertEqual(plan['new_categories'], 2)
        self.assertEqual(len(plan['bindings']), 3)
        self.assertEqual([p['id'] for p in plan['unresolved_assigned']], [self.people[3]])
        before = protected_hashes(self.db)
        result = apply(self.db, plan['fingerprint'], self.actor, Path(self.temporary.name) / 'backups')
        self.assertEqual(result['employees_bound'], 3)
        self.assertEqual(protected_hashes(self.db), before)
        bindings = dict(self.db.execute('SELECT worker_id,category_id FROM employee_gdlr'))
        self.assertEqual(bindings[self.people[0]], bindings[self.people[1]])
        with closing(sqlite3.connect(result['backup'])) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM employee_gdlr').fetchone()[0], 0)
            self.assertEqual(backup.execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 4)
        again = preview(self.db)
        self.assertFalse(again['bindings'])
        self.assertEqual(again['new_categories'], 0)
        self.assertTrue(apply(self.db, again['fingerprint'], self.actor, self.temporary.name)['already_applied'])

    def test_stale_preview_refuses_and_existing_manual_binding_is_preserved(self):
        old = preview(self.db)
        category = self.db.execute('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
            VALUES ('Ручная категория','ручная категория','keep',?,'now')''', (self.actor,)).lastrowid
        self.db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
            VALUES (?,?,'keep',?,'now')''', (self.people[0], category, self.actor))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'изменились'):
            apply(self.db, old['fingerprint'], self.actor, self.temporary.name)
        plan = preview(self.db)
        self.assertEqual(plan['kept_bindings'], 1)
        apply(self.db, plan['fingerprint'], self.actor, self.temporary.name)
        self.assertEqual(tuple(self.db.execute('SELECT category_id,edit_token FROM employee_gdlr WHERE worker_id=?',
                                             (self.people[0],)).fetchone()), (category, 'keep'))

    def test_explicit_assignment_requires_known_category_and_admin(self):
        with self.assertRaisesRegex(ValueError, 'отсутствует'):
            preview(self.db, {self.people[3]: 'Выдуманная'})
        explicit = {self.people[3]: 'Сварщик МК'}
        plan = preview(self.db, explicit)
        self.assertEqual(len(plan['bindings']), 4)
        self.assertFalse(plan['unresolved_assigned'])
        with self.assertRaisesRegex(ValueError, 'администратор'):
            apply(self.db, plan['fingerprint'], -1, self.temporary.name, explicit)
        result = apply(self.db, plan['fingerprint'], self.actor, self.temporary.name, explicit)
        self.assertEqual(result['employees_bound'], 4)
