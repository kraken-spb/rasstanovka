"""Exact identity-field filters; option lists use the same authorized registry scope."""
from flask import abort

from workforce_core import text_value


NUMBER_SQL = "CASE WHEN w.personnel_is_internal THEN '' ELSE COALESCE(w.personnel_no::text,'') END"
FIELDS = {'full_name': 'w.full_name', 'personnel_no': NUMBER_SQL}
EMPTY_NUMBER = '__none__'


def add_filters(query, clauses, args):
    for key, column in FIELDS.items():
        values = sorted(set(value for value in query.getlist(key) if value))
        if len(values) > 100:
            abort(400, description='Можно выбрать до 100 значений фильтра.')
        for value in values:
            text_value(value, 'Значение фильтра', 500, required=True)
        if key == 'personnel_no':
            values = ['' if value == EMPTY_NUMBER else value for value in values]
        if values:
            clauses.append(column + '=ANY(%s::text[])')
            args.append(values)


def option_values(db, key, source, bound):
    if key not in FIELDS:
        abort(400, description='Неизвестный фильтр сотрудников.')
    rows = db.native('SELECT DISTINCT ' + FIELDS[key] + ' value ' + source + ' ORDER BY value', bound).fetchall()
    return [row['value'] if row['value'] else EMPTY_NUMBER for row in rows]
