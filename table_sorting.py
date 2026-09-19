"""Validated account sorting and server-owned SQL expressions (before paging)."""
import json
import re
from flask import abort
from workforce_date_filters import FORECAST_SQL

STAFFING_SORT_FIELDS = {'name', 'personnel', 'object', 'subobject', 'contractor', 'employer',
    'category', 'itr', 'brigadier', 'shift', 'attendance', 'performed_work', 'work_type', 'assignment_author',
    'crew_number', 'department', 'pps'}
WORKFORCE_SORT_FIELDS = {'name', 'personnel', 'phone', 'email', 'citizenship', 'origin_city', 'project',
    'department', 'division', 'employer', 'profession', 'category', 'employment', 'accommodation', 'stage', 'stage_date',
    'arrival_date', 'forecast_departure_date', 'movement_direction', 'planned_date', 'movement_basis',
    'rotation_schedule', 'leave_start_date', 'leave_end_date', 'next_arrival_date'}
SORT_KEYS = {'staffingSort': STAFFING_SORT_FIELDS, 'rotationSort': WORKFORCE_SORT_FIELDS,
             'recruitmentSort': WORKFORCE_SORT_FIELDS, 'workforceSort': WORKFORCE_SORT_FIELDS}
MAX_LEVELS = 8


def validate_sort(value, allowed):
    if (not isinstance(value, list) or len(value) > MAX_LEVELS or any(
            not isinstance(item, dict) or set(item) != {'field', 'direction'}
            or not isinstance(item['field'], str) or item['field'] not in allowed
            or item['direction'] not in ('asc', 'desc') for item in value)
            or len({item['field'] for item in value}) != len(value)):
        abort(400, description='Сортировка: выберите до 8 разных столбцов и направление ↑ / ↓.')
    return value


def workforce_sort(query, db, actor, section):
    from user_preferences import staffing_preferences
    raw = query.get('sort')
    if raw is not None:
        if len(raw) > 3000:
            abort(400, description='Слишком много настроек сортировки.')
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            abort(400, description='Некорректная сортировка.')
    else:
        value = staffing_preferences(db, actor['id']).get((section or 'workforce') + 'Sort', [])
    value = validate_sort(value, WORKFORCE_SORT_FIELDS)
    if actor['role'] in ('foreman', 'viewer'):
        private = {'phone', 'email', 'origin_city'}
        if raw is not None and any(item['field'] in private for item in value):
            abort(403, description='Сортировка по закрытым реквизитам недоступна вашей роли.')
        value = [item for item in value if item['field'] not in private]
    return value


def sql_sort(levels, day):
    expressions = {
        'name': 'w.full_name', 'personnel': "CASE WHEN w.personnel_is_internal THEN NULL ELSE w.personnel_no::text END",
        'division': '(SELECT name FROM workforce_divisions WHERE id=p.division_id)',
        'department': 'w.department', 'profession': 'w.profession', 'phone': 'p.phone', 'email': 'p.email',
        'employer': '(SELECT name FROM workforce_organizations WHERE id=p.employer_id)',
        'category': '(SELECT COALESCE((SELECT name FROM gdlr_categories WHERE id=eg.category_id),w.category))',
        'citizenship': '(SELECT label FROM workforce_catalog WHERE code=p.citizenship_code)',
        'origin_city': "COALESCE((SELECT label FROM workforce_catalog WHERE code=p.origin_code),NULLIF(p.origin_city,''))",
        'employment': '(SELECT label FROM workforce_catalog WHERE code=p.employment_code)',
        'accommodation': '(SELECT label FROM workforce_catalog WHERE code=p.accommodation_code)',
        'stage': '(SELECT label FROM workforce_catalog WHERE code=st.stage_code)',
        'stage_date': 'st.effective_date', 'arrival_date': 'p.arrival_date', 'leave_start_date': 'p.leave_start_date',
        'forecast_departure_date': FORECAST_SQL,
        'project': '(SELECT pc.label FROM employee_smu es JOIN workforce_smu_projects sp ON sp.smu_id=es.smu_id JOIN workforce_catalog pc ON pc.code=sp.project_code WHERE es.worker_id=w.id)',
    }
    movement = """FROM workforce_movements m LEFT JOIN workforce_catalog mb ON mb.code=m.basis_code
        WHERE m.worker_id=w.id AND m.actual_date IS NULL AND COALESCE(m.result_code,'') NOT IN ('result.cancelled','result.happened')
          AND NOT EXISTS(SELECT 1 FROM workforce_movements n WHERE n.rescheduled_from=m.id)
        ORDER BY m.planned_date,m.id LIMIT 1"""
    for key, expression in [('movement_direction', "CASE m.direction WHEN 'arrival' THEN 'Заезд' ELSE 'Выезд' END"),
                            ('planned_date', 'm.planned_date'), ('movement_basis', 'mb.label')]:
        expressions[key] = '(SELECT ' + expression + ' ' + movement + ')'
    rotation = '''FROM workforce_rotations r JOIN workforce_rotation_schedules rs ON rs.id=r.schedule_id
        WHERE r.worker_id=w.id AND NOT r.cancelled AND r.actual_end_date IS NULL
        ORDER BY r.start_date DESC,r.id LIMIT 1'''
    for key, expression in [('rotation_schedule', 'rs.name'), ('leave_end_date', 'r.leave_end_date'), ('next_arrival_date', 'r.next_arrival_date')]:
        expressions[key] = '(SELECT ' + expression + ' ' + rotation + ')'
    extra, parameters = [], []
    for index, item in enumerate(levels):
        key = item['field']; alias = 'sort_' + str(index); expression = expressions[key]
        if key == 'forecast_departure_date': parameters.extend([day, day])
        extra.append(expression + ' AS ' + alias)
    return ''.join(', ' + value for value in extra), parameters


def ordered_ids(rows, levels):
    """Precompute keys once, then stable sorts from least to most significant.

    Only identifiers are cached, never private field values. Empty cells stay last
    for both directions; ID/name ties prevent page overlap as the limit changes.
    """
    def natural(value):
        return tuple((0, len(part.lstrip('0')), part.lstrip('0')) if part.isascii() and part.isdecimal()
                     else (1, part) for part in re.findall(r'[0-9]+|[^0-9]+', str(value).strip().casefold().replace('ё','е')))
    records = [dict(id=row['id'], name=natural(row['name_search']),
                    keys=[natural(row['sort_'+str(i)]) if row['sort_'+str(i)] is not None else () for i in range(len(levels))]) for row in rows]
    records.sort(key=lambda row:(row['name'],row['id']))
    for index in reversed(range(len(levels))):
        present=[row for row in records if row['keys'][index]]
        missing=[row for row in records if not row['keys'][index]]
        present.sort(key=lambda row:row['keys'][index],reverse=levels[index]['direction']=='desc')
        records=present+missing
    return [row['id'] for row in records]
