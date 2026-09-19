"""Page the employee directory without changing the legacy full-list contract."""
import json
from pathlib import Path
import subprocess

from flask import abort, request
from filter_values import argument, values

SEARCH_FIELDS = ('full_name', 'personnel_no', 'crew_name', 'category', 'profession',
                 'gsp_profession', 'pps', 'department', 'employer', 'contractor',
                 'owner_name', 'linear_itr_name', 'brigadier_name')


def text(value):
    return '' if value is None else str(value)


def search_fields(row):
    return [text(row.get(key)) for key in SEARCH_FIELDS] + [
        ' '.join(text(value) for value in (row.get('outstaff') or {}).values())]


def summary(rows, outstaff=False):
    return {'total': len(rows), 'workers': sum(bool(row['category_id']) if outstaff else row['is_worker'] for row in rows),
            'assigned': sum(row['assigned_on_date'] for row in rows),
            'unknown_qualification': sum(not row['qualification'] for row in rows)}


def regex_ids(rows, query):
    # Use the same ECMAScript engine and SearchRegex.source as the browser. Never
    # evaluate user code. A separate process lets us stop pathological patterns.
    try:
        result = subprocess.run(['node', str(Path(__file__).parent / 'tools/employee_regex.cjs')],
            input=json.dumps({'query': query, 'rows': [{'id': row['id'], 'fields': search_fields(row)} for row in rows]},
                             ensure_ascii=False).encode('utf-8'),
            capture_output=True, timeout=1.5, check=True)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        abort(400, description='Regex выполняется слишком долго. Упростите выражение.')
    except (OSError, subprocess.CalledProcessError, ValueError):
        abort(503, description='Не удалось выполнить Regex-поиск. Повторите запрос.')
    if 'error' in data:
        abort(400, description='Некорректное регулярное выражение. Исправьте шаблон.')
    return set(data['ids'])


def employee_page(rows):
    try:
        page, size = int(request.args.get('page', '0')), int(request.args.get('page_size', '50'))
        if not 0 <= page <= 100000 or size not in (25, 50, 100):
            raise ValueError
    except ValueError:
        abort(400, description='Некорректная страница списка сотрудников.')
    query = request.args.get('q', '').strip()
    regex = request.args.get('regex', '0')
    if len(query) > 300 or regex not in ('0', '1'):
        abort(400, description='Некорректные параметры поиска сотрудников.')
    filters = {key: set(values(argument(key, ignore_empty=True))) for key in ('crew', 'category', 'active')}
    if filters['active'] - {'0', '1'} or any(
        value != 'none' and (not value.isascii() or not value.isdigit() or int(value) <= 0)
        for key in ('crew', 'category') for value in filters[key]):
        abort(400, description='Некорректный фильтр сотрудников.')
    matched = [row for row in rows if all(not filters[key] or text(value) in filters[key] for key, value in (
        ('crew', row['crew_id'] or 'none'), ('category', row['category_id'] if row['category_id'] is not None else 'none'),
        ('active', int(row['active']))))]
    if query and regex == '1':
        ids = regex_ids(matched, query)
        matched = [row for row in matched if row['id'] in ids]
    elif query:
        words = query.lower().replace('ё', 'е').split()
        def contains(row):
            haystack = ' '.join(search_fields(row)).lower().replace('ё', 'е')
            return all(word in haystack for word in words)
        matched = [row for row in matched if contains(row)]
    outstaff = request.args.get('scope') == 'outstaff'
    filtered_summary = summary(matched, outstaff)
    page = min(page, max(0, (len(matched) - 1) // size))
    crews = {row['crew_id']: row['crew_name'] for row in rows if row['crew_id']}
    return matched[page * size:(page + 1) * size], {
        'summary': summary(rows, outstaff), 'filtered_summary': filtered_summary,
        'pagination': {'page': page, 'page_size': size, 'total': len(matched)},
        'filters': {'crews': [{'id': key, 'name': name} for key, name in crews.items()]}}
