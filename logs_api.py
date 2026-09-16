from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Read-only access to recorded assignment history for administrators."""
from datetime import date, datetime, time, timedelta, timezone
import math

from flask import abort, jsonify, request


ACTION = """CASE
    WHEN e.event_type IN ('employee_delete','employee_restore') THEN e.event_type
    WHEN e.before_subobject_id IS NULL AND e.after_subobject_id IS NOT NULL THEN 'assign'
    WHEN e.before_subobject_id IS NOT NULL AND e.after_subobject_id IS NULL THEN 'clear'
    WHEN e.before_subobject_id IS NOT e.after_subobject_id THEN 'move'
    ELSE 'update' END"""
JOINS = """FROM (
    SELECT id,changed_at,work_date,shift,changed_by,worker_id,crew_id,before_subobject_id,after_subobject_id,
        'assignment' event_type,NULL reason,NULL worker_snapshot,NULL personnel_snapshot,
        NULL crew_snapshot,NULL actor_snapshot,NULL username_snapshot FROM assignment_events
    UNION ALL
    SELECT -worker_id,changed_at,effective_date,'',changed_by,worker_id,crew_id,NULL,NULL,
        'employee_delete',reason,worker_name,personnel_no,crew_name,actor_name,actor_username
        FROM employee_removals
    UNION ALL
    SELECT 'deleted-'||id,json_extract(removal_json,'$.changed_at'),json_extract(removal_json,'$.effective_date'),'',
        json_extract(removal_json,'$.changed_by'),worker_id,json_extract(removal_json,'$.crew_id'),NULL,NULL,
        'employee_delete',json_extract(removal_json,'$.reason'),json_extract(removal_json,'$.worker_name'),
        json_extract(removal_json,'$.personnel_no'),json_extract(removal_json,'$.crew_name'),
        json_extract(removal_json,'$.actor_name'),json_extract(removal_json,'$.actor_username')
        FROM employee_restorations WHERE removal_json IS NOT NULL
    UNION ALL
    SELECT 'restored-'||id,changed_at,restored_date,'',changed_by,worker_id,crew_id,NULL,NULL,
        'employee_restore',NULL,worker_name,personnel_no,crew_name,actor_name,actor_username
        FROM employee_restorations
    ) e
    LEFT JOIN users u ON u.id=e.changed_by
    LEFT JOIN workers w ON w.id=e.worker_id
    LEFT JOIN crews c ON c.id=e.crew_id
    LEFT JOIN subobjects b ON b.id=e.before_subobject_id
    LEFT JOIN objects bo ON bo.id=b.object_id
    LEFT JOIN subobjects a ON a.id=e.after_subobject_id
    LEFT JOIN objects ao ON ao.id=a.object_id"""


def register_log_routes(app, get_db, roles_required):
    def day_arg(key):
        value = request.args.get(key, '')
        if not value:
            return None
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError()
            return parsed
        except ValueError:
            abort(400, description='Укажите дату в формате ГГГГ-ММ-ДД.')

    @app.get('/api/logs')
    @roles_required('admin')
    def assignment_logs():
        start, end, work_day = day_arg('from'), day_arg('to'), day_arg('work_date')
        if start and end and start > end:
            abort(400, description='Начало периода не может быть позже окончания.')
        query = request.args.get('q', '').strip()
        if len(query) > 200:
            abort(400, description='Поисковый запрос должен быть не длиннее 200 символов.')
        action = filter_argument('action',ignore_empty=True,allowed={'assign','move','clear','update','employee_delete','employee_restore'})
        actor = filter_argument('actor',ignore_empty=True)
        try:
            page = int(request.args.get('page', '1'))
            if page < 1 or any(not item.isdecimal() or not 1 <= int(item) <= 9223372036854775807 for item in filter_values(actor)):
                raise ValueError()
        except ValueError:
            abort(400, description='Некорректный номер страницы или пользователь.')
        where, params = [], []
        moscow = timezone(timedelta(hours=3))
        try:
            for day, op in ((start, '>='), (end + timedelta(days=1) if end else None, '<')):
                if day:
                    where.append('julianday(e.changed_at) ' + op + ' julianday(?)')
                    params.append(datetime.combine(day, time.min, moscow).isoformat())
        except OverflowError:
            abort(400, description='Дата окончания выходит за допустимый диапазон.')
        if work_day:
            where.append('e.work_date=?'); params.append(work_day.isoformat())
        if actor:
            where.append('e.changed_by IN (' + ','.join('?' for _ in filter_values(actor)) + ')'); params.extend(int(item) for item in filter_values(actor))
        if action:
            where.append('(' + ACTION + ') IN (' + ','.join('?' for _ in filter_values(action)) + ')'); params.extend(filter_values(action))
        if query:
            fields = ('COALESCE(e.worker_snapshot,w.full_name)', 'COALESCE(e.personnel_snapshot,w.personnel_no)',
                      'COALESCE(e.crew_snapshot,c.name)', 'COALESCE(e.actor_snapshot,u.full_name)',
                      'COALESCE(e.username_snapshot,u.username)', 'b.name', 'bo.name', 'a.name', 'ao.name', 'e.reason')
            where.append('(' + ' OR '.join('instr(log_casefold(COALESCE(' + field + ",'')),?)>0" for field in fields) + ')')
            params.extend([query.casefold()] * len(fields))
        db = get_db()
        postgres = getattr(db, 'dialect', None) == 'postgres'
        joins = JOINS
        order = 'julianday(e.changed_at) DESC,e.id DESC'
        if postgres:
            # PostgreSQL requires a common UNION type. Preserve public numeric IDs
            # and SQLite's ordering of tagged restoration IDs above numeric IDs.
            joins = joins.replace('SELECT id,changed_at', 'SELECT CAST(id AS TEXT) id,changed_at')
            joins = joins.replace('SELECT -worker_id,changed_at', 'SELECT CAST(-worker_id AS TEXT),changed_at')
            for field in ('changed_by', 'crew_id'):
                expression = "json_extract(removal_json,'$." + field + "')"
                joins = joins.replace(expression, 'CAST(' + expression + ' AS BIGINT)')
            order = "julianday(e.changed_at) DESC,(e.id LIKE '%-%' AND e.id NOT LIKE '-%') DESC," \
                    "CASE WHEN e.id NOT LIKE 'deleted-%' AND e.id NOT LIKE 'restored-%' THEN CAST(e.id AS BIGINT) END DESC,e.id DESC"
        else:
            db.create_function('log_casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        total = db.execute('SELECT COUNT(*) ' + joins + clause, params).fetchone()[0]
        pages = max(1, math.ceil(total / 50))
        page = min(page, pages)
        rows = [dict(row) for row in db.execute('''SELECT e.id,e.changed_at,e.work_date,e.shift,
            e.changed_by actor_id,COALESCE(e.actor_snapshot,u.full_name) actor_name,COALESCE(e.username_snapshot,u.username) actor_username,
            e.worker_id,COALESCE(e.worker_snapshot,w.full_name) worker_name,COALESCE(e.personnel_snapshot,w.personnel_no) personnel_no,
            e.crew_id,COALESCE(e.crew_snapshot,c.name) crew_name,e.reason,
            e.before_subobject_id,b.name before_name,bo.name before_group,
            e.after_subobject_id,a.name after_name,ao.name after_group,
            ''' + ACTION + ' action ' + joins + clause +
            ' ORDER BY ' + order + ' LIMIT 50 OFFSET ?', [*params, (page - 1) * 50])]
        if postgres:
            for row in rows:
                if str(row['id']).lstrip('-').isdigit():
                    row['id'] = int(row['id'])
        actors = [dict(row) for row in db.execute('''SELECT DISTINCT u.id,u.full_name,u.username
            FROM (SELECT changed_by FROM assignment_events UNION SELECT changed_by FROM employee_removals
                UNION SELECT changed_by FROM employee_restorations
                UNION SELECT CAST(json_extract(removal_json,'$.changed_by') AS BIGINT) FROM employee_restorations WHERE removal_json IS NOT NULL) e
            JOIN users u ON u.id=e.changed_by ORDER BY u.full_name,u.id''')]
        return jsonify({'rows': rows, 'total': total, 'page': page, 'pages': pages, 'page_size': 50, 'actors': actors})
