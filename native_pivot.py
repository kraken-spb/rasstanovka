"""Native OOXML PivotTable backed by the exported Excel table."""
from collections import Counter

from openpyxl.pivot.cache import CacheDefinition, CacheField, CacheSource, SharedItems, WorksheetSource
from openpyxl.pivot.fields import Index, Missing, Number, Text
from openpyxl.pivot.record import Record, RecordList
from openpyxl.pivot.table import DataField, FieldItem, Location, PivotField, PivotTableStyle, RowColField, RowColItem, TableDefinition
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.escape import escape
from openpyxl.worksheet.table import Table, TableStyleInfo


ROW_FIELDS = (1, 2)
COL_FIELDS = (3, 4, 12)
NAME_FIELD = 5
SOURCE_NAME = 'StaffingSource'


def _value(value):
    return None if value in (None, '') else value


def _label(value):
    return '(пусто)' if value is None else str(value)


def _axis_paths(records, fields, top_subtotals=False):
    leaves = {tuple(record[field] for field in fields) for record in records}
    paths = []

    def visit(prefix):
        children = sorted({leaf[len(prefix)] for leaf in leaves if leaf[:len(prefix)] == prefix},
                          key=lambda value: _label(value).casefold())
        for value in children:
            child = (*prefix, value)
            leaf = len(child) == len(fields)
            if leaf or top_subtotals:
                paths.append(child)
            if not leaf:
                visit(child)
                if not top_subtotals:
                    paths.append(child)
    visit(())
    return [*paths, ()]


def _snapshot(workbook, records, day, category, query, row_paths, col_paths):
    """Save visible values as well as the pivot cache for readers that cannot refresh."""
    sheet = workbook.create_sheet('Сводная таблица')
    for row, pair in enumerate((('Дата расстановки', '.'.join(reversed(day.split('-')))),
                               ('Выборка по расстановке', category if category else ('Без категории' if category == '' else 'Все категории ГДЛР')),
                               ('Поиск по позициям', query or 'Все позиции'),
                               ('Источник: лист «Список сотрудников»', f'Строк в списке: {len(records)}. После изменения списка: Данные → Обновить всё')), 1):
        for col, value in enumerate(pair, 1):
            cell = sheet.cell(row, col, value)
            cell.data_type = 's'
    counts = Counter()
    for record in records:
        if record[NAME_FIELD] is None:
            continue
        row_key = tuple(record[field] for field in ROW_FIELDS)
        col_key = tuple(record[field] for field in COL_FIELDS)
        for r in range(len(ROW_FIELDS) + 1):
            for c in range(len(COL_FIELDS) + 1):
                counts[(row_key[:r], col_key[:c])] += 1
    first = 5
    depth = len(COL_FIELDS)
    for col, path in enumerate(col_paths, 2):
        for level in range(depth):
            if not path:
                value = 'Общий итог' if level == 0 else ''
            elif level < len(path):
                value = _label(path[level])
            elif level == len(path):
                value = 'ИТОГО'
            else:
                value = ''
            cell = sheet.cell(first + 1 + level, col, value)
            cell.data_type = 's'
    sheet.cell(first, 1, 'Количество сотрудников')
    sheet.cell(first + depth, 1, 'Позиция')
    for row, path in enumerate(row_paths, first + depth + 1):
        label = 'Общий итог' if not path else ('Не расставлены' if path == (None,) else _label(path[-1]))
        cell = sheet.cell(row, 1, label)
        cell.data_type = 's'
        cell.alignment = Alignment(wrap_text=True, indent=1 if len(path) == len(ROW_FIELDS) else 0)
        for col, column_path in enumerate(col_paths, 2):
            sheet.cell(row, col, counts[(path, column_path)])
        sheet.row_dimensions[row].height = max(23, 15 * ((len(label) + 70) // 71))
    last_col = len(col_paths) + 1
    line = Side(style='thin', color='B8C9CC')
    for row in sheet.iter_rows(min_row=first, max_col=last_col):
        for cell in row:
            subtotal = cell.row > first + depth and len(row_paths[cell.row - first - depth - 1]) < len(ROW_FIELDS)
            header = cell.row <= first + depth
            cell.font = Font(name='Arial', size=10, bold=header or subtotal)
            cell.border = Border(left=line, right=line, top=line, bottom=line)
            if header or subtotal:
                cell.fill = PatternFill('solid', fgColor='DAE3F3' if header else 'E8EEF8')
            if cell.column > 1:
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    sheet.column_dimensions['A'].width = 80
    for col in range(2, last_col + 1):
        sheet.column_dimensions[get_column_letter(col)].width = 17
    for row in range(first, first + depth + 1):
        sheet.row_dimensions[row].height = 42
    sheet.freeze_panes = 'B9'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:8'
    sheet.print_area = f'A1:{get_column_letter(last_col)}{sheet.max_row}'
    return sheet


def summary_sheet(workbook, rows, day, category=None, query=''):
    source = workbook['Список сотрудников']
    headers = [str(cell.value) for cell in source[1]]
    records = [tuple(_value(value) for value in row) for row in source.iter_rows(min_row=2, values_only=True)]
    table = Table(displayName=SOURCE_NAME, ref=source.auto_filter.ref)
    table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
    source.add_table(table)
    # Excel does not allow a worksheet AutoFilter overlapping a table AutoFilter.
    source.auto_filter.ref = None
    indexes, cache_fields = [], []
    def cache_value(value):
        return Missing() if value is None else Number(v=value) if isinstance(value, (int, float)) else Text(v=escape(str(value)))

    for field, header in enumerate(headers):
        values = list(dict.fromkeys(record[field] for record in records))
        indexes.append({value: index for index, value in enumerate(values)})
        items = [cache_value(value) for value in values] if field in (*ROW_FIELDS, *COL_FIELDS) else []
        strings = any(isinstance(value, str) for value in values)
        numbers = any(isinstance(value, (int, float)) for value in values)
        cache_fields.append(CacheField(name=escape(header), numFmtId=49 if field == 6 else 0, sharedItems=SharedItems(
            _fields=items, containsString=None if strings else False, containsNumber=True if numbers else None,
            containsInteger=True if numbers else None, containsBlank=True if None in values else None,
            containsNonDate=False if not strings and not numbers else None,
            containsSemiMixedTypes=False if numbers and not strings else None,
            minValue=min(value for value in values if value is not None) if numbers else None,
            maxValue=max(value for value in values if value is not None) if numbers else None)))
    cache = CacheDefinition(cacheSource=CacheSource(type='worksheet', worksheetSource=WorksheetSource(name=SOURCE_NAME)),
                            cacheFields=cache_fields, recordCount=len(records), saveData=True,
                            enableRefresh=True, refreshOnLoad=True, createdVersion=6, refreshedVersion=6,
                            minRefreshableVersion=3, missingItemsLimit=0)
    cache.records = RecordList(r=[Record(_fields=[Index(v=indexes[field][value]) if field in (*ROW_FIELDS, *COL_FIELDS)
                                                else cache_value(value) for field, value in enumerate(record)]) for record in records])
    row_paths = _axis_paths(records, ROW_FIELDS, top_subtotals=True)
    col_paths = _axis_paths(records, COL_FIELDS)
    sheet = _snapshot(workbook, records, day, category, query, row_paths, col_paths)
    fields = []
    for field in range(len(headers)):
        axis = 'axisRow' if field in ROW_FIELDS else 'axisCol' if field in COL_FIELDS else None
        subtotal = field in (*ROW_FIELDS[:-1], *COL_FIELDS[:-1])
        items = [FieldItem(x=index, n='Не расставлены' if field == ROW_FIELDS[0] and value is None else None)
                 for value, index in indexes[field].items()] if axis else []
        if subtotal:
            items.append(FieldItem(t='default'))
        fields.append(PivotField(axis=axis, dataField=field == NAME_FIELD, items=items,
                                 defaultSubtotal=subtotal, showAll=False, sortType='ascending'))

    def axis_items(paths, axes):
        result, previous = [], ()
        for path in paths:
            repeated = 0
            while repeated < min(len(previous), len(path) - 1) and previous[repeated] == path[repeated]:
                repeated += 1
            kind = 'grand' if not path else 'data' if axes == ROW_FIELDS or len(path) == len(axes) else 'default'
            result.append(RowColItem(t=kind, r=repeated, x=[Index(v=indexes[axes[level]][path[level]])
                                                         for level in range(repeated, len(path))] or [Index(v=0)]))
            previous = path
        return result

    pivot = TableDefinition(name='StaffingPivot', cacheId=1, dataCaption='Количество сотрудников',
                            grandTotalCaption='Общий итог', rowHeaderCaption='Позиция',
                            location=Location(ref=f'A5:{get_column_letter(sheet.max_column)}{sheet.max_row}',
                                              firstHeaderRow=1, firstDataRow=4, firstDataCol=1),
                            pivotFields=fields, rowFields=[RowColField(x=index) for index in ROW_FIELDS],
                            colFields=[RowColField(x=index) for index in COL_FIELDS],
                            rowItems=axis_items(row_paths, ROW_FIELDS), colItems=axis_items(col_paths, COL_FIELDS),
                            dataFields=[DataField(name='Количество сотрудников', fld=NAME_FIELD, subtotal='count', baseField=0, baseItem=0)],
                            createdVersion=6, updatedVersion=6, minRefreshableVersion=3,
                            compact=True, compactData=True, outline=True, outlineData=True,
                            disableFieldList=False, enableDrill=True,
                            pivotTableStyleInfo=PivotTableStyle(name='PivotStyleMedium9', showRowHeaders=True,
                                                               showColHeaders=True, showRowStripes=False,
                                                               showColStripes=False, showLastColumn=True))
    pivot.cache = cache
    sheet.add_pivot(pivot)
    return len(records)
