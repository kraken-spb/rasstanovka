"""The summary workbook must reconcile to calendar facts and respect export scope."""
from tests import test_staffing_export


class ReportMatrixTest(test_staffing_export.StaffingExportTest):
    def summary(self, client=None, **params):
        return (client or self.admin).get('/api/staffing/export', query_string={
            'date': '2026-09-13', 'shift': 'all', 'kind': 'summary', **params})

    def test_matrix_totals_match_calendar_and_layout(self):
        response = self.summary()
        self.assertEqual(response.status_code, 200)
        sheet = self.workbook(response)['Сводная таблица']
        self.assertEqual(sheet.title, 'Сводная таблица')
        self.assertEqual(sheet.cell(16, 1).value, 'Позиция')
        self.assertEqual(sheet.cell(sheet.max_row, 1).value, 'Общий итог')
        calendar = self.admin.get('/api/calendar?start=2026-09-13&days=1').get_json()
        expected = sum(f['day_count'] + f['night_count'] for f in calendar['facts'])
        self.assertEqual(sheet.cell(sheet.max_row, sheet.max_column).value, expected)
        self.assertEqual(sheet.cell(17, sheet.max_column).value, expected)
        self.assertEqual(sheet.cell(18, sheet.max_column).value, expected)
        formula_company = next(cell for row in sheet for cell in row if cell.value == '=Работодатель')
        self.assertEqual(formula_company.data_type, 's')
        self.assertEqual(sheet.freeze_panes, 'B17')
        self.assertEqual(sheet.page_setup.fitToWidth, 1)

    def test_category_shift_search_and_empty_result(self):
        response = self.summary(category='Ручная ГДЛР', shift='1 смена', query='группа подобъект')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Export-Count'], '1')
        sheet = self.workbook(response)['Сводная таблица']
        self.assertEqual(sheet.cell(10, 2).value, 'Ручная ГДЛР')
        self.assertNotIn('Ночь', [cell.value for cell in sheet[16]])
        self.assertEqual(self.summary(category='Несуществующая').status_code, 404)
        self.assertEqual(self.summary(query='несуществующий').status_code, 404)
        self.assertEqual(self.summary(kind='invalid').status_code, 400)

    def test_summary_uses_same_list_including_absences_and_role_scope(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO staffing_attendance VALUES ('2026-09-13',?,'Больн','token',?,'now')", (self.visible_worker, self.admin_id))
            db.commit()
        response = self.summary()
        self.assertEqual(response.headers['X-Export-Count'], '4')
        self.assertEqual(self.summary(self.foreman).headers['X-Export-Count'], '3')
        self.assertEqual(self.summary(self.viewer).status_code, 403)
        self.assertEqual(self.export(self.admin).headers['X-Export-Count'], '4')

    def test_every_download_has_two_tabs_with_reconciled_totals(self):
        for kind in ('report', 'staffing', 'summary'):
            response = self.summary(kind=kind)
            book = self.workbook(response)
            self.assertEqual(book.sheetnames, ['Список сотрудников', 'Сводная таблица'])
            count = book['Список сотрудников'].max_row - 1
            summary = book['Сводная таблица']
            self.assertEqual(summary.cell(summary.max_row, summary.max_column).value, count)
            self.assertEqual(int(response.headers['X-Export-Count']), count)

    def test_category_applies_to_list_and_summary_in_every_mode(self):
        for kind in ('report', 'staffing', 'summary'):
            response = self.summary(kind=kind, category='Ручная ГДЛР')
            book = self.workbook(response)
            rows = list(book['Список сотрудников'].iter_rows(min_row=2, values_only=True))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][9], 'Ручная ГДЛР')
            summary = book['Сводная таблица']
            self.assertEqual(summary.cell(summary.max_row, summary.max_column).value, 1)
