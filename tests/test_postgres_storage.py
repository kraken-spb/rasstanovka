"""Run integration cases only with an explicitly selected isolated PostgreSQL DB."""
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock
from uuid import uuid4

from postgres_db import Record, translate
from query_helpers import membership


class PostgresTranslationTest(unittest.TestCase):
    def test_parameter_literals_and_unknown_schema_commands(self):
        query, mutation, returning = translate("SELECT 'literal ? 100% %s', ?")
        self.assertIn("'literal ? 100%% %%s'", query)
        self.assertTrue(query.endswith('%s'))
        self.assertFalse(mutation or returning)
        for query in ('PRAGMA table_info(users)', 'DROP TABLE users', 'SELECT 1; DELETE FROM users'):
            with self.assertRaises(ValueError):
                translate(query)

    def test_record_keeps_positional_and_mapping_contracts(self):
        record = Record((13, 'Работник'), ('id', 'name'))
        self.assertEqual(record[0], record['id'])
        self.assertEqual(dict(record), {'id': 13, 'name': 'Работник'})
        self.assertEqual(tuple(record), (13, 'Работник'))

    def test_membership_uses_bound_array_and_rejects_identifiers(self):
        db = Mock(dialect='postgres')
        self.assertEqual(membership(db, 'w.id', [1, 2]), ('w.id = ANY(?)', [[1, 2]]))
        self.assertEqual(membership(db, 'w.id', []), ('1=0', []))
        with self.assertRaises(ValueError):
            membership(db, 'id); DROP TABLE users;', [1])


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL test environment explicitly.')
class PostgresStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.migrate_sqlite_to_postgres import load_environment
        load_environment(Path(os.environ['CREW_POSTGRES_TEST_ENV']))
        if os.environ.get('APP_ENVIRONMENT') != 'staging':
            raise RuntimeError('Integration tests require APP_ENVIRONMENT=staging.')

    def setUp(self):
        from postgres_db import PostgresConnection
        self.db = PostgresConnection()
        self.db.execute('BEGIN IMMEDIATE')
        self.key = 'integration-' + uuid4().hex

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_binding_identity_conflicts_arrays_and_rollback(self):
        statement = 'INSERT INTO workers(full_name,personnel_no) VALUES (?,?)'
        first = self.db.execute(statement, ("Работник ' ? %s", self.key)).lastrowid
        second = self.db.execute(statement, ('Второй работник', self.key + '-2')).lastrowid
        self.assertNotEqual(first, second)
        ignored = self.db.execute('INSERT OR IGNORE INTO workers(full_name,personnel_no) VALUES (?,?)', ('Дубль', self.key))
        self.assertEqual(ignored.rowcount, 0)
        self.assertIsNone(ignored.lastrowid)
        rows = self.db.execute('SELECT id FROM workers WHERE id=ANY(?) ORDER BY id', ([first, second],)).fetchall()
        self.assertEqual([row[0] for row in rows], [first, second])
        self.assertEqual(self.db.execute('SELECT full_name FROM workers WHERE personnel_no=? COLLATE NOCASE', (self.key.upper(),)).fetchone()[0], "Работник ' ? %s")
        self.db.rollback()
        self.assertIsNone(self.db.execute('SELECT id FROM workers WHERE id=?', (first,)).fetchone())

    def test_json_null_safe_comparison_and_unicode_search(self):
        value = '{"reason":"Причина","changed_by":17}'
        self.assertEqual(self.db.execute("SELECT json_extract(?,'$.reason')", (value,)).fetchone()[0], 'Причина')
        self.assertTrue(self.db.execute('SELECT NULL IS NULL').fetchone()[0])
        self.assertTrue(self.db.execute('SELECT ? IS NOT ?', (1, 2)).fetchone()[0])
        for value in ('ИВАНОВ', 'Straße', 'İ', 'Σςσ', 'ﬃ', ''):
            self.assertEqual(self.db.execute('SELECT log_casefold(?)', (value,)).fetchone()[0], value.casefold())

    def test_composite_conflict_target_updates_shift(self):
        worker = self.db.execute('INSERT INTO workers(full_name,personnel_no) VALUES (?,?)',
                                 ('Проверка смены', self.key)).lastrowid
        actor = self.db.execute('SELECT MIN(id) FROM users').fetchone()[0]
        sql = '''INSERT INTO staffing_shifts(work_date,worker_id,shift,edit_token,updated_by,updated_at)
            VALUES (?,?,?,?,?,?) ON CONFLICT(work_date,worker_id) DO UPDATE SET
            shift=excluded.shift,edit_token=excluded.edit_token'''
        self.db.execute(sql, ('2026-09-16', worker, '1 смена', 'first', actor, '2026-09-16'))
        self.db.execute(sql, ('2026-09-16', worker, '2 смена', 'second', actor, '2026-09-16'))
        rows = self.db.execute('SELECT shift,edit_token FROM staffing_shifts WHERE worker_id=?', (worker,)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [('2 смена', 'second')])

    def test_runtime_account_has_no_schema_or_role_privileges(self):
        row = self.db.native('''SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,
            has_schema_privilege(current_user,'public','CREATE') FROM pg_roles WHERE rolname=current_user''').fetchone()
        self.assertFalse(any(row))

    def test_integrity_errors_remain_visible_and_rollback(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,?,?,?)',
                            (-987654321, -987654321, self.key, -987654321, 'now'))
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT 1').fetchone()[0], 1)
