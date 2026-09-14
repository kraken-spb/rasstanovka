import io
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import Workbook, load_workbook
from openpyxl.utils.escape import unescape
from native_pivot import summary_sheet
from staffing_export import HEADERS


class NativePivotTest(unittest.TestCase):
    def report(self, rows):
        book=Workbook()
        source=book.active
        source.title='Список сотрудников'
        source.append(HEADERS)
        for row in rows:
            source.append(row)
        source.auto_filter.ref=f'A1:O{source.max_row}'
        summary_sheet(book, [], '2026-09-14')
        output=io.BytesIO()
        book.save(output)
        return output.getvalue()

    def row(self, name='Иванов Иван', company='Подрядчик', employer='Работодатель', shift='День'):
        return [1,'Группа','Подобъект',company,employer,name,'000123','Должность','Профессия',
                'Категория','ИТР','Бригадир',shift,'СМУ','Монтаж\nСварка']

    def test_real_pivot_cache_source_fields_and_count_aggregation(self):
        payload=self.report([self.row(),self.row(name=None),self.row(name='Другой',shift='Ночь')])
        book=load_workbook(io.BytesIO(payload))
        self.assertEqual(book.sheetnames,['Список сотрудников','Сводная таблица'])
        source, summary=book.worksheets
        self.assertIsNone(source.auto_filter.ref)
        self.assertEqual(source.tables['StaffingSource'].ref,'A1:O4')
        self.assertEqual(len(summary._pivots),1)
        pivot=summary._pivots[0]
        self.assertEqual([unescape(pivot.cache.cacheFields[f.x].name) for f in pivot.rowFields],HEADERS[1:3])
        self.assertEqual([unescape(pivot.cache.cacheFields[f.x].name) for f in pivot.colFields],[HEADERS[i] for i in (3,4,12)])
        self.assertEqual((pivot.dataFields[0].fld,pivot.dataFields[0].subtotal),(5,'count'))
        self.assertEqual(pivot.cache.cacheSource.worksheetSource.name,'StaffingSource')
        self.assertEqual(pivot.cache.recordCount,3)
        self.assertEqual(len(pivot.cache.records.r),3)
        self.assertFalse(pivot.disableFieldList)
        self.assertTrue(pivot.cache.enableRefresh)
        self.assertTrue(pivot.cache.refreshOnLoad)
        self.assertEqual(summary.cell(summary.max_row,summary.max_column).value,2)
        with ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn('xl/pivotTables/pivotTable1.xml',archive.namelist())
            self.assertIn('xl/pivotCache/pivotCacheRecords1.xml',archive.namelist())
            self.assertFalse(any('externalLink' in name for name in archive.namelist()))
            records=ET.fromstring(archive.read('xl/pivotCache/pivotCacheRecords1.xml'))
            self.assertEqual(unescape(records[0][14].get('v')),'Монтаж\nСварка')
            self.assertEqual(records[0][6].get('v'),'000123')

    def test_counts_come_from_source_sheet_with_distinct_company_employer_shift_axes(self):
        data=[self.row(company='А',employer='Один'),self.row(company='А',employer='Два',shift='Ночь'),
              self.row(company='Б',employer='Один'),self.row(company='',employer='')]
        book=load_workbook(io.BytesIO(self.report(data)))
        summary=book.worksheets[1]
        self.assertEqual(summary.cell(summary.max_row,summary.max_column).value,4)
        self.assertEqual({unescape(item.v) for item in summary._pivots[0].cache.cacheFields[3].sharedItems._fields if item.tagname == 's'},{'А','Б'})
        self.assertEqual({unescape(item.v) for item in summary._pivots[0].cache.cacheFields[4].sharedItems._fields if item.tagname == 's'},{'Один','Два'})
