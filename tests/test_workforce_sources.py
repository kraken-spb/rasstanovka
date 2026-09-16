import io
import unittest
from zipfile import ZipFile

from openpyxl import Workbook
from workforce_sources import SourceProblem, parse_workbook, source_date


class WorkforceSourcesTest(unittest.TestCase):
    def workbook(self, sheets):
        book = Workbook()
        book.remove(book.active)
        for name, rows in sheets.items():
            sheet = book.create_sheet(name)
            for row in rows:
                sheet.append(row)
        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()

    def test_allowed_sheets_canonical_columns_and_conflicts(self):
        rows = [['ФИО', 'Таб. Номер', 'Организация-работодатель', 'Работодатель', 'Подразделение', 'Дата заезда'],
                ['Работник Тестовый', 123, 'ЛГСС', 'Другая организация', 'Строительно-монтажный участок № 19.2', '15.09.2026']]
        raw = self.workbook({'Явка': rows, 'РСО': rows, 'На объекте': rows, '15.09.2026': rows, 'Комплектация': rows})
        result = parse_workbook(raw, 'test.xlsx', 'rotation')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(len(result['excluded']), 4)
        record = result['records'][0]
        self.assertEqual(record['fields']['employer'], 'ЛГСС')
        self.assertEqual(record['fields']['tab'], '123')
        self.assertEqual(record['fields']['arrival'], '2026-09-15')
        self.assertEqual(record['fields']['smu'], 'СМУ-19.2')
        self.assertEqual(len(record['mapping_notes']), 1)
        self.assertEqual(record['raw']['D']['value'], 'Другая организация')

    def test_outstaff_requires_original_gdlr_column(self):
        headers = ['ФИО', 'Таб', 'Должность', 'Гражданство', 'Работодатель', 'СМУ', 'Категория ГДЛР ЛГСС', 'ГДЛР']
        raw = self.workbook({'Аустаффинг': [headers,
            ['Без ГДЛР', '', 'Монтажник', 'РФ', 'ЛГСС', 'СМУ-15', '', 'Монтажник ТТ'],
            ['С ГДЛР', '', 'Монтажник', 'РФ', 'ЛГСС', 'СМУ-15', 'Монтажник ТТ', '']]})
        result = parse_workbook(raw, 'test.xlsx', 'rotation')
        self.assertEqual([r['fields']['name'] for r in result['records']], ['С ГДЛР'])
        self.assertEqual(result['excluded'][0]['row'], 2)

    def test_recruitment_sections_and_no_invented_dates(self):
        raw = self.workbook({'ПВП': [['ФИО', 'Таб', 'Организация', 'Дата прибытия по билетам'],
            ['', '', 'Покупка билетов', ''], ['Тестовый Работник', 'примечание', 'ЛГСС', 'в пути']],
            'Комплектация': [['ФИО', 'Таб'], ['Исключённый', 123]]})
        record = parse_workbook(raw, 'test.xlsx', 'recruitment')['records'][0]
        self.assertEqual(record['section'], 'Покупка билетов')
        self.assertEqual(record['fields']['tab'], '')
        self.assertEqual(record['fields']['ticket_arrival'], 'в пути')
        self.assertIsNone(source_date(record['fields']['ticket_arrival']))

    def test_archives_with_dtd_are_rejected_before_xml_parse(self):
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('xl/workbook.xml', '<!DOCTYPE x [<!ENTITY y "abc">]><x>&y;</x>')
        with self.assertRaisesRegex(SourceProblem, 'DTD'):
            parse_workbook(buffer.getvalue(), 'test.xlsx', 'rotation')


if __name__ == '__main__':
    unittest.main()
