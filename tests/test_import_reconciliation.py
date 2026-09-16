import io
import json
import unittest
from unittest.mock import patch

import test_staffing
from staffing_import import apply_attendance, parse_attendance


class ImportReconciliationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_staffing.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_staffing.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.c = test_staffing.StaffingWorkflowTest()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def upload(self, route, content, token='', decisions=None, label='ППС15', client=None):
        return (client or self.c.admin).post('/api/staffing/import/' + route,
            data={'file': (io.BytesIO(content), 'явка.xlsx'), 'source_label': label,
                  'preview_token': token, 'decisions': json.dumps(decisions or {})},
            headers={'X-CSRF-Token': 'staffing-csrf'})

    def seed(self, rows, label='ППС15'):
        parsed = parse_attendance(test_staffing.workbook_bytes(rows), 'исходная.xlsx')
        parsed['source_label'] = label
        with self.c.app.app_context():
            return apply_attendance(self.c.module.get_db(), parsed, self.c.admin_id, self.c.module.utc_now)

    def manual(self, name='Иванов Иван Иванович', number='MAN-001', category='Актуальная категория'):
        with self.c.app.app_context():
            db = self.c.module.get_db()
            worker = db.execute('INSERT INTO workers(full_name,personnel_no,category,pps) VALUES (?,?,?,?)',
                                (name, number, category, 'ППС15')).lastrowid
            db.execute('INSERT INTO manual_employees VALUES (?,?,?,?,?,?)',
                       (worker, self.c.admin_id, 'now', 'Рабочие', 'req-' + number, 'hash'))
            db.execute('''INSERT OR IGNORE INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES (?,?,1,'fixture',?,'now')''', (category, category.casefold(), self.c.admin_id))
            category_id = db.execute('SELECT id FROM gdlr_categories WHERE name_key=?', (category.casefold(),)).fetchone()[0]
            db.execute("INSERT INTO employee_gdlr VALUES (?,?,'fixture',?,'now')", (worker, category_id, self.c.admin_id))
            db.commit()
            return worker

    def test_preview_lists_deltas_per_pps_without_writing_and_requires_apply(self):
        self.seed([{}, {'number': '2', 'name': 'Убывающий Работник'}])
        self.seed([{'number': '19', 'name': 'Другой ППС'}], 'ППС19')
        manual = self.manual('Кузнецов Алексей Сергеевич')
        content = test_staffing.workbook_bytes([{}, {'number': '3', 'name': 'Новый Работник'}])
        preview = self.upload('preview', content)
        self.assertEqual(preview.status_code, 200, preview.json)
        plan = preview.json['reconciliation']
        self.assertEqual([r['personnel_no'] for r in plan['added']], ['3'])
        self.assertEqual([r['personnel_no'] for r in plan['missing']], ['2'])
        self.assertEqual([r['id'] for r in plan['manual_kept']], [manual])
        with self.c.app.app_context():
            db = self.c.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_imports').fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT id FROM workers WHERE personnel_no='3'").fetchone())
        result = self.upload('apply', content, preview.json['preview_token'])
        self.assertEqual(result.status_code, 200, result.json)
        rows = self.c.admin.get('/api/staffing?date=2026-09-12&shift=all').json['rows']
        self.assertEqual({r['personnel_no'] for r in rows}, {'70001', '3', '19', 'MAN-001'})
        with self.c.app.app_context():
            db = self.c.module.get_db()
            self.assertIsNotNone(db.execute("SELECT id FROM workers WHERE personnel_no='2'").fetchone())
            journal = json.loads(db.execute('SELECT summary_json FROM staffing_imports ORDER BY id DESC').fetchone()[0])
            self.assertEqual(journal['reconciliation']['counts']['missing'], 1)

    def test_manual_match_is_explicit_preserves_id_category_crew_and_assignments(self):
        worker = self.manual(name='Иванов Иван Иванович')
        with self.c.app.app_context():
            db = self.c.module.get_db()
            crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Ручная бригада',?,'now')", (self.c.admin_id,)).lastrowid
            db.execute('INSERT INTO crew_members VALUES (?,?)', (crew, worker))
            db.execute("INSERT INTO staffing_shifts VALUES ('2026-09-12',?,'2 смена','token',?,'now')", (worker, self.c.admin_id))
            db.commit()
        content = test_staffing.workbook_bytes([{'name': 'Иванов Иван Ивановичь', 'category': 'Из Excel'}])
        preview = self.upload('preview', content)
        plan = preview.json['reconciliation']
        self.assertEqual(plan['unresolved'], 1)
        self.assertEqual(self.upload('apply', content, preview.json['preview_token']).status_code, 400)
        key = plan['reviews'][0]['key']
        reviewed = self.upload('preview', content, decisions={key: 'match:' + str(worker)})
        self.assertEqual(reviewed.json['reconciliation']['unresolved'], 0)
        applied = self.upload('apply', content, reviewed.json['preview_token'])
        self.assertEqual(applied.status_code, 200, applied.json)
        with self.c.app.app_context():
            db = self.c.module.get_db()
            row = db.execute('SELECT * FROM workers WHERE id=?', (worker,)).fetchone()
            self.assertEqual((row['personnel_no'], row['full_name'], row['category']), ('70001', 'Иванов Иван Ивановичь', 'Актуальная категория'))
            self.assertEqual(db.execute('SELECT crew_id FROM crew_members WHERE worker_id=?', (worker,)).fetchone()[0], crew)
            self.assertEqual(db.execute('SELECT shift FROM staffing_shifts WHERE worker_id=?', (worker,)).fetchone()[0], '2 смена')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workers WHERE personnel_no='70001'").fetchone()[0], 1)

    def test_identical_personnel_name_difference_needs_explicit_decision(self):
        self.seed([{}])
        content = test_staffing.workbook_bytes([{'name': 'Исправленный Иван Иванович', 'category': 'Устаревшая категория'}])
        preview = self.upload('preview', content)
        self.assertEqual(preview.json['reconciliation']['unresolved'], 1)
        reviewed = self.upload('preview', content, decisions={'2': 'keep'})
        self.assertEqual(self.upload('apply', content, reviewed.json['preview_token']).status_code, 200)
        with self.c.app.app_context():
            row = self.c.module.get_db().execute("SELECT full_name,category FROM workers WHERE personnel_no='70001'").fetchone()
            self.assertEqual(tuple(row), ('Иванов Иван Иванович', 'Монтажник ТТ'))

    def test_category_and_new_manual_records_invalidate_preview_atomically(self):
        self.seed([{}])
        content = test_staffing.workbook_bytes([{}, {'number': '2', 'name': 'Новый Работник'}])
        preview = self.upload('preview', content)
        with self.c.app.app_context():
            db = self.c.module.get_db()
            db.execute("UPDATE workers SET category='Исправлено в системе' WHERE personnel_no='70001'")
            db.commit()
        self.assertEqual(self.upload('apply', content, preview.json['preview_token']).status_code, 400)
        preview = self.upload('preview', content)
        self.manual('Другой Ручной Работник')
        self.assertEqual(self.upload('apply', content, preview.json['preview_token']).status_code, 400)
        with self.c.app.app_context():
            db = self.c.module.get_db()
            self.assertIsNone(db.execute("SELECT id FROM workers WHERE personnel_no='2'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_imports').fetchone()[0], 1)

    def test_state_changed_during_backup_is_rechecked_under_write_lock(self):
        self.seed([{}])
        content = test_staffing.workbook_bytes([{}, {'number': '2'}])
        preview = self.upload('preview', content)
        from staffing_import import backup_database
        def changed(db):
            path = backup_database(db)
            db.execute("UPDATE workers SET full_name='Изменено параллельно' WHERE personnel_no='70001'")
            db.commit()
            return path
        with patch('staffing_import.backup_database', side_effect=changed):
            result = self.upload('apply', content, preview.json['preview_token'])
        self.assertEqual(result.status_code, 400)
        with self.c.app.app_context():
            self.assertIsNone(self.c.module.get_db().execute("SELECT id FROM workers WHERE personnel_no='2'").fetchone())

    def test_duplicate_manual_link_and_unknown_decisions_rejected(self):
        worker = self.manual()
        content = test_staffing.workbook_bytes([{}, {'number': '2'}])
        preview = self.upload('preview', content, decisions={'2': 'match:' + str(worker), '3': 'match:' + str(worker)})
        self.assertTrue(preview.json['issues'])
        self.assertEqual(self.upload('apply', content, preview.json['preview_token']).status_code, 400)
        self.assertEqual(self.upload('preview', content, decisions={'2': 'match:999999'}).status_code, 400)

    def test_typo_in_first_letter_and_name_order_still_require_manual_review(self):
        worker = self.manual('Петров Петр Петрович')
        for name in ['Бетров Петр Петрович', 'Петр Петрович Петров']:
            preview = self.upload('preview', test_staffing.workbook_bytes([{'name': name}]))
            self.assertEqual(preview.json['reconciliation']['unresolved'], 1)
            self.assertEqual(preview.json['reconciliation']['reviews'][0]['candidates'][0]['id'], worker)

    def test_same_file_retry_does_not_restore_an_old_roster(self):
        content = test_staffing.workbook_bytes([{}])
        first = self.upload('preview', content)
        self.assertEqual(self.upload('apply', content, first.json['preview_token']).status_code, 200)
        self.seed([{'number': '2', 'name': 'Другой Работник'}])
        preview = self.upload('preview', content)
        self.assertTrue(preview.json['reconciliation']['already_imported'])
        self.assertEqual(preview.json['reconciliation']['missing'], [])
        response = self.upload('apply', content, first.json['preview_token'])
        self.assertTrue(response.json['already_imported'])
        rows = self.c.admin.get('/api/staffing?date=2026-09-12&shift=all').json['rows']
        self.assertEqual([r['personnel_no'] for r in rows], ['2'])
