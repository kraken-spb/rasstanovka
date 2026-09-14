"""Cross-output contracts: same facts, deliberately different counting policies."""
from collections import Counter
import unittest

from report_queries import assignment_rows, calendar_facts, coverage_status, filter_assignment_rows
from tests import test_staffing_export as export_fixture


class ReportQueriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        export_fixture.StaffingExportTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        export_fixture.StaffingExportTest.tearDownClass()

    def setUp(self):
        self.fixture = export_fixture.StaffingExportTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_present_calendar_reconciles_to_assignment_list_and_absences_are_retained(self):
        f = self.fixture
        with f.module.app.app_context():
            db = f.module.get_db()
            db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-13',?,'Больн','t',?,'now')", (f.visible_worker, f.admin_id))
            db.commit()
            before = list(db.iterdump())
            actor = db.execute('SELECT * FROM users WHERE id=?', (f.admin_id,)).fetchone()
            rows = assignment_rows(db, '2026-09-13', ('1 смена', '2 смена'), user=actor)
            facts = calendar_facts(db, '2026-09-13', '2026-09-13')
            expected = Counter((r['subobject_id'], r['display_company'], r['contractor'], r['normalized_shift']) for r in rows if r['present'])
            actual = Counter()
            for fact in facts:
                for shift, field in [('1 смена', 'day_count'), ('2 смена', 'night_count')]:
                    actual[(fact['subobject_id'], fact['employer'], fact['contractor'], shift)] += fact[field]
            self.assertEqual(actual, expected)
            self.assertEqual(len(rows), 4)
            self.assertEqual(sum(expected.values()), 3)
            self.assertTrue(any(r['worker_id'] == f.inactive_worker for r in rows))
            self.assertEqual(list(db.iterdump()), before)
        book = f.workbook(f.export(f.admin))
        summary = book['Сводная таблица']
        self.assertEqual(book.active.max_row - 1, 4)
        self.assertEqual(summary.cell(summary.max_row, summary.max_column).value, 4)

    def test_detail_queries_require_explicit_actor_and_preserve_legacy_and_smu_scope(self):
        f = self.fixture
        with f.module.app.app_context():
            db = f.module.get_db()
            actor = db.execute('SELECT * FROM users WHERE id=?', (f.foreman_id,)).fetchone()
            with self.assertRaises(TypeError):
                assignment_rows(db, '2026-09-13', ('1 смена',))
            rows = assignment_rows(db, '2026-09-13', ('1 смена', '2 смена'), user=actor)
            self.assertEqual(len(rows), 3)
            self.assertNotIn(f.hidden_worker, {r['worker_id'] for r in rows})
            db.execute("UPDATE workers SET department='СМУ А' WHERE id=?", (f.hidden_worker,))
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[\"СМУ А\"]','t',?,'now')", (f.foreman_id, f.admin_id))
            rows = assignment_rows(db, '2026-09-13', ('1 смена', '2 смена'), user=actor)
            self.assertEqual([r['worker_id'] for r in rows], [f.hidden_worker])
            db.execute("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=?", (f.foreman_id,))
            self.assertEqual(assignment_rows(db, '2026-09-13', ('1 смена', '2 смена'), user=actor), [])

    def test_category_multiselect_and_legacy_night_filters_compose(self):
        f = self.fixture
        with f.module.app.app_context():
            db = f.module.get_db()
            actor = db.execute('SELECT * FROM users WHERE id=?', (f.admin_id,)).fetchone()
            rows = assignment_rows(db, '2026-09-13', ('1 смена', '2 смена'), user=actor)
            selected = filter_assignment_rows(rows, category=['Ручная ГДЛР', 'Ручная ГДЛР'], contractor=['Подрядчик'], query='группа подобъект')
            self.assertEqual([r['worker_id'] for r in selected], [f.visible_worker])
            self.assertFalse(filter_assignment_rows(rows, category=['Ручная ГДЛР'], query='другая площадка'))
            night = assignment_rows(db, '2026-09-13', ('2 смена',), user=actor)
            self.assertTrue(any(r['worker_id'] == f.legacy_worker for r in night))
            self.assertTrue(all(r['normalized_shift'] == '2 смена' for r in night))
            facts = calendar_facts(db, '2026-09-13', '2026-09-13', category=['Ручная ГДЛР', 'Нет такой категории'])
            self.assertEqual(sum(x['day_count'] + x['night_count'] for x in facts), 1)
            self.assertEqual(calendar_facts(db, '2026-09-14', '2026-09-14'), [])

    def test_coverage_status_is_exclusive_even_with_an_absent_assignment(self):
        self.assertEqual(coverage_status('Явка', True), 'assigned')
        self.assertEqual(coverage_status('Явка', False), 'unassigned')
        self.assertEqual(coverage_status('Больн', True), 'absent')
        self.assertEqual(coverage_status('Больн', False), 'absent')
