"""Read only the reviewed monthly-plan columns of the workbook's Свод sheet."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

NODES = json.loads(Path(__file__).with_name('smg_hierarchy.json').read_text(encoding='utf-8'))
MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']


def normalized(value):
    return ' '.join(str(value or '').split()).casefold()


def number(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 <= result <= 100000000:
            raise ValueError
        return str(result)
    except (InvalidOperation, ValueError):
        raise ValueError(f'Некорректное значение плана: {value!s}') from None


def parse_workbook(content, filename, year):
    if not 2000 <= year <= 2100:
        raise ValueError('Укажите год от 2000 до 2100.')
    if len(content) > 20 * 1024 * 1024:
        raise ValueError('Размер файла должен быть не больше 20 МБ.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 10000 or sum(i.file_size for i in archive.infolist()) > 250 * 1024 * 1024:
                raise ValueError('Слишком большой распакованный Excel-файл.')
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
            sheets = [s for s in workbook.findall('{*}sheets/{*}sheet') if normalized(s.get('name')) == 'свод']
            if len(sheets) != 1:
                raise ValueError('В файле должен быть один лист «Свод».')
            relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
            relation_id = sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            relation = next(r for r in relations if r.get('Id') == relation_id)
            if relation.get('TargetMode') == 'External':
                raise ValueError('Внешний лист не поддерживается.')
            target = relation.get('Target', '')
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in PurePosixPath(path).parts:
                raise ValueError('Некорректная ссылка на лист.')
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(t.text or '' for t in item.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'))
                           for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            sheet = ET.fromstring(archive.read(path))
            rows, uncached = {}, set()
            for row in sheet.findall('{*}sheetData/{*}row'):
                index = int(row.get('r'))
                if index > 5000:
                    raise ValueError('Лист «Свод» содержит больше 5000 строк.')
                cells = {}
                for cell in row.findall('{*}c'):
                    address = cell.get('r', '')
                    column = re.sub(r'\d', '', address)
                    value = cell.find('{*}v')
                    value = value.text if value is not None else None
                    if cell.find('{*}f') is not None and value is None:
                        uncached.add(address)
                    if cell.get('t') == 's' and value is not None:
                        value = strings[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('{*}is/{*}t'))
                    cells[column] = value
                rows[index] = cells
    except (BadZipFile, KeyError, ET.ParseError, StopIteration, IndexError):
        raise ValueError('Не удалось прочитать Excel. Сохраните книгу в формате .xlsx.') from None
    headers = sorted((i, row) for i, row in rows.items() if normalized(row.get('A')).startswith('наименование людских'))
    if not headers:
        raise ValueError('На листе «Свод» не найдены блоки людских ресурсов.')
    file_unit = re.search(r'ППС[\s№-]*(\d+)', filename, re.I)
    lookup = {n['name_key']: n for n in NODES}
    blocks = []
    for position, (index, header) in enumerate(headers):
        name = ' '.join(header['A'].split())
        code = re.search(r'\b(\d{2}(?:[.,]\d+)?)\b', name)
        code = code.group(1).replace(',', '.') if code else file_unit.group(1) if file_unit else None
        if code is None:
            raise ValueError(f'Не удалось определить ППС или участок в строке {index}.')
        scope = 'ППС-' + code
        end = headers[position + 1][0] - 2 if position + 1 < len(headers) else max(rows)
        labels, errors = {}, []
        caption = str(rows.get(index - 1, {}).get('A') or '')
        caption_code = re.match(r'^(\d{2}[.,]\d+)\b', caption)
        if caption_code and caption_code.group(1).replace(',', '.') != code:
            errors.append(f'Подпись над блоком «{caption}» не совпадает с заголовком «{name}».')
        for row_number in range(index + 1, end + 1):
            label = rows.get(row_number, {}).get('A')
            if not label:
                continue
            node = lookup.get(normalized(label))
            if not node:
                # Block captions occupy the row preceding the next header.
                if row_number == end and normalized(label).startswith(('ппс', 'сму')):
                    continue
                errors.append(f'A{row_number}: неизвестная строка «{label}».')
                continue
            if node['id'] in labels:
                errors.append(f'A{row_number}: повтор категории «{label}».')
            labels[node['id']] = row_number
        missing = [n['label'] for n in NODES if n['id'] not in labels]
        if missing:
            errors.append('Нет строк: ' + ', '.join(missing))
        months = []
        plan_year, previous_month = year, None
        for column, title in header.items():
            match = re.fullmatch(r'план\s+(' + '|'.join(MONTHS) + ')', normalized(title))
            if not match:
                continue
            month_number = MONTHS.index(match.group(1)) + 1
            if previous_month == 12 and month_number == 1:
                plan_year += 1
            period = f'{plan_year}-{month_number:02d}-01'
            previous_month = month_number
            values, warnings, month_errors = {}, [], list(errors)
            for node in NODES:
                address = column + str(labels.get(node['id'], ''))
                if address in uncached:
                    month_errors.append(address + ': у формулы нет сохранённого результата. Пересчитайте и сохраните книгу в Excel.')
                try:
                    values[node['id']] = number(rows.get(labels.get(node['id']), {}).get(column))
                except ValueError as error:
                    month_errors.append(address + ': ' + str(error))
            for node in NODES:
                if not node['group']:
                    continue
                children = [n for n in NODES if n['parent_id'] == node['id']]
                nums = [values.get(n['id']) for n in children]
                own = values.get(node['id'])
                if own is not None and all(v is not None for v in nums):
                    total = sum((Decimal(v) for v in nums), Decimal(0))
                    if abs(Decimal(own) - total) > Decimal('0.000001'):
                        warnings.append(f'{node["label"]}: в файле {own}, сумма подгрупп {total}.')
            if not any(values.get(n['id']) is not None for n in NODES if not n['group']):
                month_errors.append('Нет значений плана по профессиям.')
            months.append({'period': period, 'label': title, 'column': column, 'values': values,
                           'warnings': warnings, 'errors': month_errors})
        if not months:
            raise ValueError(f'В блоке строки {index} нет колонок «План <месяц>».')
        if len({m['period'] for m in months}) != len(months):
            for month in months:
                month['errors'].append('В блоке несколько плановых столбцов за один месяц.')
        horizon_errors = []
        indexes = [int(m['period'][:4]) * 12 + int(m['period'][5:7]) for m in months]
        if len(indexes) != 3 or indexes != list(range(indexes[0], indexes[0] + 3)):
            horizon_errors.append('План ППС должен содержать три последовательных месяца слева направо.')
        blocks.append({'scope': scope, 'label': name, 'row': index, 'months': months, 'errors': horizon_errors,
                       'aggregate': len(headers) > 1 and '.' not in code})
    frequencies = Counter(b['scope'] for b in blocks)
    for block in blocks:
        block['duplicate'] = frequencies[block['scope']] > 1
    return {'filename': Path(filename).name, 'sha256': sha256(content).hexdigest(), 'sheet': 'Свод',
            'year': year, 'blocks': blocks}
