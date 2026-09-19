import json
import unittest

import test_staffing_export as export_tests


class ExportUnassignedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        export_tests.StaffingExportTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        export_tests.StaffingExportTest.tearDownClass()

    def setUp(self):
        self.fixture = export_tests.StaffingExportTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.module = self.fixture.module
        self.admin = self.fixture.admin
        with self.module.app.app_context():
            db = self.module.get_db()
            self.free = self.fixture.worker(db, '=Нерасставленный', '000201')
            self.night = self.fixture.worker(db, 'Свободный ночной', '000202')
            self.external = self.fixture.worker(db, 'Аутстафф без назначения', '000203')
            self.obsolete = self.fixture.worker(db, 'Старый импорт', '000204')
            self.attendance = self.fixture.worker(db, 'Явка без назначения', '000205')
            category_id = db.execute(
                'SELECT category_id FROM employee_gdlr WHERE worker_id=?',
                (self.fixture.visible_worker,),
            ).fetchone()[0]
            db.execute('UPDATE gdlr_categories SET staffing_allowed=1 WHERE id=?', (category_id,))
            category_id = db.execute('''INSERT INTO gdlr_categories
                (name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES ('Исходная ГДЛР','исходная гдлр',1,'source-category',?,'now')''',
                (self.fixture.admin_id,)).lastrowid
            for worker in (self.free, self.night, self.external, self.obsolete, self.attendance,
                           self.fixture.inactive_worker):
                db.execute(
                    '''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
                           edit_token=excluded.edit_token, updated_by=excluded.updated_by,
                           updated_at=excluded.updated_at''',
                    (worker, category_id, 'export-unassigned-category', self.fixture.admin_id, 'now'),
                )
            for worker in (self.free, self.night, self.fixture.visible_worker, self.fixture.inactive_worker):
                db.execute('INSERT INTO manual_employees(worker_id,created_by,created_at,request_key,payload_hash) VALUES (?, ?,\'now\',?,\'hash\')', (worker, self.fixture.admin_id, str(worker)))
            for worker, crew in ((self.free, self.fixture.crew_id), (self.night, self.fixture.other_crew_id)):
                db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (crew, worker))
            db.execute("UPDATE workers SET department='СМУ тест' WHERE id=?", (self.free,))
            db.execute("INSERT INTO staffing_shifts VALUES ('2026-09-13',?,'2 смена','token',?,'now')", (self.night, self.fixture.admin_id))
            for key, workers in (('old', [self.obsolete]), ('new', [self.attendance, self.free, self.fixture.visible_worker])):
                batch = db.execute("INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json) VALUES (?,'test.xlsx',?,'now',?,'{}')", (key, self.fixture.admin_id, len(workers))).lastrowid
                for worker in workers:
                    db.execute("INSERT INTO staffing_import_members VALUES (?,?,?,'')", (batch, worker, worker))
            batch = db.execute("INSERT INTO outstaff_imports VALUES (NULL,'request','test.xlsx','Аутстафф','hash','{}',?,'now',2)", (self.fixture.admin_id,)).lastrowid
            for worker in (self.external, self.free):
                db.execute("INSERT INTO outstaff_members VALUES (?,?,?,?,'','','','','','','','token')", (worker, str(worker), batch, worker))
            db.commit()

    def export(self, client=None, **params):
        return (client or self.admin).get('/api/staffing/export', query_string={
            'date': '2026-09-13', 'shift': 'all', 'kind': 'staffing', 'include_unassigned': '1', **params})

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        return list(self.fixture.workbook(response)['Список сотрудников'].iter_rows(min_row=2, values_only=True))

    def test_opt_in_current_sources_deduplicate_and_keep_assignments(self):
        response = self.export()
        rows = self.rows(response)
        self.assertEqual(response.headers['X-Export-Count'], '8')
        self.assertEqual(response.headers['X-Export-Unassigned-Count'], '4')
        self.assertEqual(sum(r[6] == '000201' for r in rows), 1)
        self.assertEqual(sum(r[6] == '000123' for r in rows), 1)
        self.assertNotIn('000204', [r[6] for r in rows])
        unassigned = next(r for r in rows if r[6] == '000201')
        self.assertEqual(unassigned[1:3], (None, None))
        self.assertEqual(unassigned[10:14], ('ИТР бригады', 'Бригадир бригады', 'День', 'СМУ тест'))
        self.assertEqual(unassigned[-1], 'Не расставлен')
        self.assertEqual(sum(r[-1] == 'Расставлен' for r in rows), 4)
        sheet = self.fixture.workbook(response)['Список сотрудников']
        formula = next(r for r in sheet.iter_rows(min_row=2) if r[6].value == '000201')
        self.assertEqual(formula[5].data_type, 's')
        self.assertEqual(formula[6].number_format, '@')
        self.assertEqual(sheet.tables['StaffingSource'].autoFilter.ref, 'A1:Q9')
        default = self.export(include_unassigned='0')
        self.assertEqual(default.headers['X-Export-Count'], '4')
        self.assertEqual(self.fixture.workbook(default)['Список сотрудников'].max_column, 16)

    def test_shift_and_date_scope_no_opposite_shift_duplicates(self):
        night = self.rows(self.export(shift='2 смена'))
        self.assertEqual({r[6] for r in night}, {'000124', '000202'})
        day = self.rows(self.export(shift='1 смена'))
        self.assertNotIn('000124', [r[6] for r in day])
        self.assertNotIn('000202', [r[6] for r in day])
        tomorrow = self.rows(self.export(date='2026-09-14'))
        self.assertEqual(len(tomorrow), 5)
        self.assertTrue(all(r[-1] == 'Не расставлен' for r in tomorrow))
        self.assertNotIn('000126', [r[6] for r in tomorrow])

    def test_foreman_and_explicit_department_scope(self):
        response = self.export(self.fixture.foreman)
        rows = self.rows(response)
        self.assertEqual(response.headers['X-Export-Unassigned-Count'], '1')
        self.assertEqual({r[6] for r in rows if r[-1] == 'Не расставлен'}, {'000201'})
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected',?,'token',?,'now')", (self.fixture.admin_id, json.dumps(['СМУ тест'], ensure_ascii=False), self.fixture.admin_id))
            db.commit()
        self.assertEqual({r[6] for r in self.rows(self.export())}, {'000201'})

    def test_validation_and_viewer_export(self):
        viewer_export = self.export(self.fixture.viewer)
        self.assertEqual(viewer_export.status_code, 200)
        self.assertEqual(viewer_export.headers['X-Export-Count'], '8')
        self.assertEqual(viewer_export.headers['X-Export-Unassigned-Count'], '4')
        self.assertEqual(self.export(kind='summary').status_code, 200)
        for value in ('true', '', '2', '-1'):
            self.assertEqual(self.export(include_unassigned=value).status_code, 400)
        self.assertEqual(self.export(self.module.app.test_client()).status_code, 401)

    def test_unassigned_group_reconciles_to_list_in_both_tabs(self):
        for day in ('2026-09-13', '2026-09-14'):
            response = self.export(date=day)
            book = self.fixture.workbook(response)
            self.assertEqual(book.sheetnames, ['Список сотрудников', 'Сводная таблица'])
            sheet = book['Сводная таблица']
            unassigned = next(row for row in sheet.iter_rows(values_only=True) if row[0] == 'Не расставлены')
            self.assertEqual(unassigned[-1], int(response.headers['X-Export-Unassigned-Count']))
            self.assertEqual(sheet.cell(sheet.max_row, sheet.max_column).value, book['Список сотрудников'].max_row - 1)

    def test_category_filters_unassigned_before_counting_and_grouping(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET category='Только свободные' WHERE id=?", (self.free,))
            category_id = db.execute('''INSERT INTO gdlr_categories
                (name,name_key,active,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES ('Только свободные','только свободные',1,1,'free-category',?,'now')''',
                (self.fixture.admin_id,)).lastrowid
            db.execute('''UPDATE employee_gdlr SET category_id=?,edit_token='free-category',
                updated_by=?,updated_at='now' WHERE worker_id=?''',
                (category_id, self.fixture.admin_id, self.free))
            db.commit()
        response = self.export(category='Только свободные')
        self.assertEqual([row[6] for row in self.rows(response)], ['000201'])
        self.assertEqual(response.headers['X-Export-Unassigned-Count'], '1')
        sheet = self.fixture.workbook(response)['Сводная таблица']
        self.assertEqual(sheet.cell(sheet.max_row, sheet.max_column).value, 1)
