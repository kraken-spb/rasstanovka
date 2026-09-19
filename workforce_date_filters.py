"""Registry date filters share the displayed forecast and preserve scoped reads."""
from flask import abort

from workforce_core import date_value
from workforce_export_filters import PENDING_MOVEMENT


FORECAST_SQL = '''COALESCE((SELECT r.planned_end_date FROM workforce_rotations r WHERE r.worker_id=w.id
    AND NOT r.cancelled AND r.start_date<=%s AND COALESCE(r.actual_end_date,r.next_arrival_date)>=%s
    ORDER BY r.start_date DESC LIMIT 1),p.forecast_departure_date)'''
DATE_FIELDS = ('stage_date', 'arrival_date', 'forecast_departure_date', 'planned_date',
               'leave_start_date', 'leave_end_date', 'next_arrival_date')
EMPTY_DATE = '__none__'


def expression_for(key, day):
    if key == 'forecast_departure_date':
        return FORECAST_SQL, [day, day]
    if key == 'stage_date':
        return 'st.effective_date', []
    if key == 'arrival_date':
        return 'p.arrival_date', []
    if key == 'leave_start_date':
        return 'p.leave_start_date', []
    if key == 'planned_date':
        return '(SELECT mv.planned_date ' + PENDING_MOVEMENT + ')', []
    # Filter the same open rotation that the registry displays.
    return (f'''(SELECT ro.{key} FROM workforce_rotations ro WHERE ro.worker_id=w.id
        AND NOT ro.cancelled AND ro.actual_end_date IS NULL
        ORDER BY ro.start_date DESC,ro.id LIMIT 1)''', [])


def date_filters(query):
    result = {}
    for key in DATE_FIELDS:
        values = sorted(set(value for value in query.getlist(key) if value))
        if len(values) > 100:
            abort(400, description='Можно выбрать до 100 дат фильтра.')
        values = [value if value == EMPTY_DATE else date_value(value, required=True) for value in values]
        bounds = {}
        for suffix in ('from', 'to', 'empty'):
            supplied = query.getlist(key + '_' + suffix)
            if len(supplied) > 1:
                abort(400, description='Укажите одно значение границы периода.')
            bounds[suffix] = supplied[0] if supplied else ''
        start = date_value(bounds['from'])
        end = date_value(bounds['to'])
        empty = bounds['empty']
        if empty not in ('', 'only', 'include', 'exclude'):
            abort(400, description='Неверное условие для незаполненных дат.')
        if start and end and start > end:
            abort(400, description='Начало периода не может быть позже окончания.')
        if values and (start or end or empty) or empty == 'only' and (start or end):
            abort(400, description='Выберите один способ фильтрации даты.')
        result[key] = {'values': values, 'from': start, 'to': end, 'empty': empty}
    return result


def filter_source(source, bound, selected, day):
    bound = list(bound)
    for key, condition in selected.items():
        values = condition['values']
        start, end, empty = condition['from'], condition['to'], condition['empty']
        if not (values or start or end or empty):
            continue
        if empty == 'include' and not (start or end):
            continue
        expression, expression_args = expression_for(key, day)
        clauses = []
        dates = [value for value in values if value != EMPTY_DATE]
        if dates:
            clauses.append(expression + '=ANY(%s::date[])')
            bound.extend(expression_args + [dates])
        if start or end:
            interval = []
            for value, operator in ((start, '>='), (end, '<=')):
                if value:
                    interval.append(expression + operator + '%s::date')
                    bound.extend(expression_args + [value])
            clauses.append('(' + ' AND '.join(interval) + ')')
        if EMPTY_DATE in values or empty in ('only', 'include'):
            clauses.append(expression + ' IS NULL')
            bound.extend(expression_args)
        if empty == 'exclude' and not (start or end):
            clauses.append(expression + ' IS NOT NULL')
            bound.extend(expression_args)
        source += ' AND (' + ' OR '.join(clauses) + ')'
    return source, bound


def options(db, source, bound, day):
    columns, args = [], []
    for key in DATE_FIELDS:
        expression, expression_args = expression_for(key, day)
        columns.append('array_agg(DISTINCT ' + expression + ') ' + key)
        args.extend(expression_args)
    row = db.native('SELECT ' + ','.join(columns) + ' ' + source, [*args, *bound]).fetchone()
    return {key: sorted(value.isoformat() if value else EMPTY_DATE for value in (row[key] or []))
            for key in DATE_FIELDS}
