"""Reference filters for registry downloads, using the same records shown in each cell."""
from flask import abort

from workforce_core import uuid_value


EMPTY = '__none__'
PENDING_MOVEMENT = """FROM workforce_movements mv WHERE mv.worker_id=w.id AND mv.actual_date IS NULL
    AND COALESCE(mv.result_code,'') NOT IN ('result.cancelled','result.happened')
    AND NOT EXISTS(SELECT 1 FROM workforce_movements mn WHERE mn.rescheduled_from=mv.id)
    ORDER BY mv.planned_date,mv.id LIMIT 1"""

# SQL identifiers and expressions are exclusively server-owned constants.
FIELDS = {
    'department': ("NULLIF(w.department,'')", 'text'),
    'employer': ('p.employer_id', 'uuid'),
    'category': ('eg.category_id', 'integer'),
    'employment': ('p.employment_code', 'employment'),
    'accommodation': ('p.accommodation_code', 'accommodation'),
    'citizenship': ('p.citizenship_code', 'citizenship'),
    'origin': ('p.origin_code', 'travelpoint'),
    'profession': ('w.profession_code', 'profession'),
    'project': ('''(SELECT sp.project_code FROM employee_smu es
        JOIN workforce_smu_projects sp ON sp.smu_id=es.smu_id WHERE es.worker_id=w.id)''', 'project'),
    'movement_direction': ('(SELECT mv.direction ' + PENDING_MOVEMENT + ')', 'direction'),
    'movement_basis': ('(SELECT mv.basis_code ' + PENDING_MOVEMENT + ')', 'basis'),
    'rotation_schedule': ('''(SELECT ro.schedule_id FROM workforce_rotations ro
        WHERE ro.worker_id=w.id AND NOT ro.cancelled AND ro.actual_end_date IS NULL
        ORDER BY ro.start_date DESC LIMIT 1)''', 'uuid'),
}


def add_filters(query, actor, clauses, args):
    for key, (expression, kind) in FIELDS.items():
        selected = sorted({value for value in query.getlist(key) if value})
        if len(selected) > 100 or any(len(value) > 300 for value in selected):
            abort(400, description='Можно выбрать до 100 значений фильтра.')
        if not selected:
            continue
        if key == 'origin' and actor['role'] in {'foreman', 'viewer'}:
            abort(403, description='Фильтр города отправления недоступен для вашей роли.')
        values = [value for value in selected if value != EMPTY]
        if kind == 'uuid':
            values = [uuid_value(value, 'Фильтр') for value in values]
        elif kind == 'integer':
            try:
                values = [int(value) for value in values]
                if any(value <= 0 or value > 2147483647 for value in values):
                    raise ValueError
            except ValueError:
                abort(400, description='Выберите категории ГДЛР.')
        elif kind == 'direction':
            if any(value not in {'arrival', 'departure'} for value in values):
                abort(400, description='Выберите заезд или выезд.')
        elif kind != 'text' and any(not value.startswith(kind + '.') for value in values):
            abort(400, description='Неверное значение фильтра справочника.')
        parts = []
        if values:
            cast = '::uuid[]' if kind == 'uuid' else '::bigint[]' if kind == 'integer' else '::text[]'
            parts.append(expression + '=ANY(%s' + cast + ')')
            args.append(values)
        if EMPTY in selected:
            parts.append(expression + ' IS NULL')
        clauses.append('(' + ' OR '.join(parts) + ')')
