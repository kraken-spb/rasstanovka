"""Bounded PostgreSQL registry reads and audited personnel operations."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import sqlite3
from uuid import uuid4

from flask import abort, g, jsonify, request
import psycopg

from workforce_core import (ADMINS, READERS, actor_scope, audit, catalog_value, date_value,
                            departure_warnings, plain, profile_snapshot, remember, replay,
                            require_worker, text_value, uuid_value)


# Identifiers are server-owned constants. Payload keys are never SQL identifiers.
ENTITIES = {
    'movement': {'table': 'workforce_movements', 'required': {'direction'},
                 'fields': {'direction': ('catalog_choice', 'direction', ('arrival', 'departure')),
                            'destination_kind': ('catalog_choice', 'destination', ('site', 'pvp', 'home', 'other')),
                            'planned_date': ('date',), 'actual_date': ('date',),
                            'basis_code': ('catalog', 'basis'), 'result_code': ('catalog', 'result'),
                            'origin': ('catalog_text', 'travelpoint', 300), 'destination': ('catalog_text', 'travelpoint', 300),
                            'origin_code': ('catalog', 'travelpoint'), 'destination_code': ('catalog', 'travelpoint'),
                            'travel_details': ('text', 5000), 'notes': ('text', 10000)}},
    'document': {'table': 'workforce_documents', 'required': {'document_code'},
                 'fields': {'document_code': ('catalog', 'document'), 'state_code': ('catalog', 'docstate'),
                            'number': ('text', 300), 'issued_on': ('date',), 'expires_on': ('date',),
                            'notes': ('text', 10000)}},
    'pvp': {'table': 'workforce_pvp_stays', 'required': set(),
            'fields': {'place_id': ('place',), 'planned_arrival': ('date',),
                       'arrived_on': ('date',), 'departed_on': ('date',), 'notes': ('text', 10000)}},
}

PROFILE_CATALOG_PAIRS = (('profession', 'profession_code'), ('origin_city', 'origin_code'))
MOVEMENT_CATALOG_PAIRS = (('origin', 'origin_code'), ('destination', 'destination_code'))


def changed_fields(data, rules, before=None, pairs=()):
    """Keep historical disabled references; validate only genuinely changed bindings."""
    result = {key:value for key,value in data.items() if key in rules and (before is None or value != before.get(key))}
    if before:
        for text_key, code_key in pairs:
            if code_key in data and data[code_key] in ('', None) and before.get(text_key):
                result[code_key] = data[code_key]
    return result


def clear_catalog_text(clean, pairs):
    for text_key, code_key in pairs:
        if text_key in clean and code_key in clean:
            abort(400, description='Укажите значение из справочника, не передавая одновременно исходный текст.')
        if code_key in clean and clean[code_key] is None:
            clean[text_key] = ''
    return clean


def payload(fields, required=()):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - set(fields) or set(required) - set(data):
        abort(400, description='Неверный состав полей запроса.')
    return data


def validate_fields(db, values, rules, required=()):
    clean = {}
    for key, value in values.items():
        spec = rules[key]
        if spec[0] == 'date':
            clean[key] = date_value(value, key, key in required)
        elif spec[0] == 'text':
            clean[key] = text_value(value, key, spec[1], key in required)
        elif spec[0] == 'catalog':
            clean[key] = catalog_value(db, value, spec[1], key in required)
        elif spec[0] == 'catalog_choice':
            if value not in spec[2]:
                abort(400, description='Выберите значение справочника: ' + spec[1])
            catalog_value(db, spec[1]+'.'+value, spec[1], True)
            clean[key] = value
        elif spec[0] == 'catalog_text':
            value = text_value(value, key, spec[2], key in required)
            row = db.native('SELECT code,label FROM workforce_catalog WHERE kind=%s AND label_key=log_casefold(trim(%s)) AND active',
                            (spec[1], value)).fetchone() if value else None
            if value and not row:
                abort(400, description='Выберите значение из справочника. Новые значения добавляет администратор.')
            if row:
                catalog_value(db, row['code'], spec[1], True)
            clean[key] = row['label'] if row else ''
        elif spec[0] == 'choice':
            if value not in spec[1]:
                abort(400, description='Выберите значение: ' + key)
            clean[key] = value
        elif spec[0] == 'place':
            clean[key] = uuid_value(value) if value else None
            if clean[key] and not db.native('SELECT id FROM workforce_pvp_places WHERE id=%s AND active',
                                           (clean[key],)).fetchone():
                abort(400, description='Выберите действующее место ПВП.')
    return clean


def validate_entity(kind, current):
    if kind == 'movement':
        if not current.get('planned_date') and not current.get('actual_date'):
            abort(400, description='Укажите плановую или фактическую дату поездки.')
        if current.get('result_code') == 'result.happened' and not current.get('actual_date'):
            abort(400, description='Для состоявшейся поездки укажите фактическую дату.')
    if kind == 'document' and current.get('issued_on') and current.get('expires_on'):
        if current['expires_on'] < current['issued_on']:
            abort(400, description='Окончание действия документа раньше даты выдачи.')
    if kind == 'pvp':
        if not current.get('planned_arrival') and not current.get('arrived_on'):
            abort(400, description='Укажите плановую или фактическую дату прибытия в ПВП.')
        if current.get('departed_on') and (not current.get('arrived_on') or current['departed_on'] < current['arrived_on']):
            abort(400, description='Выезд из ПВП должен быть не раньше фактического прибытия.')


def register_workforce_routes(app, get_db, roles_required):
    authorize = roles_required

    def roles_required(*roles):
        def decorate(view):
            @authorize(*roles)
            @wraps(view)
            def guarded(*args, **kwargs):
                try:
                    return view(*args, **kwargs)
                except sqlite3.IntegrityError:
                    get_db().rollback()
                    return jsonify(error='Запись уже существует или связанные данные изменились. Обновите карточку.'), 409
            return guarded
        return decorate

    def database():
        db = get_db()
        if getattr(db, 'dialect', None) != 'postgres':
            abort(404, description='Единый учёт доступен на тестовом стенде PostgreSQL.')
        return db

    from workforce_rotations import register_rotation_routes
    register_rotation_routes(app, database, roles_required)
    from workforce_export import register_workforce_export
    register_workforce_export(app, database, roles_required)
    from workforce_jobs import register_export_jobs
    register_export_jobs(app, database, roles_required)
    from workforce_imports import register_import_routes
    register_import_routes(app, database, roles_required)
    from workforce_board import register_board_routes, stage_token, transition_targets
    register_board_routes(app, database, roles_required)

    @app.get('/api/workforce/reference')
    @roles_required(*READERS)
    def workforce_reference():
        db = database()
        actor, scope, args = actor_scope(db)
        categories = plain(db.native('SELECT id,name,active FROM gdlr_categories ORDER BY name').fetchall())
        departments = plain(db.native(f'''SELECT DISTINCT w.department name FROM workers w
            WHERE ({scope}) AND w.department<>'' ORDER BY w.department''', args).fetchall())
        return jsonify({'catalog': plain(db.native('SELECT * FROM workforce_catalog ORDER BY kind,sort_order,label').fetchall()),
                        'organizations': plain(db.native('SELECT * FROM workforce_organizations ORDER BY name').fetchall()),
                        'places': plain(db.native('SELECT * FROM workforce_pvp_places ORDER BY name').fetchall()),
                        'rotation_schedules': plain(db.native('SELECT * FROM workforce_rotation_schedules ORDER BY name').fetchall()),
                        'categories': categories, 'departments': departments,
                        'permissions': {'profile': actor['role'] in ADMINS | {'rotation', 'recruitment'},
                                        'movement': actor['role'] in ADMINS | {'rotation'},
                                        'stage': actor['role'] in ADMINS | {'rotation'},
                                        'transition': actor['role'] in ADMINS | {'rotation', 'recruitment'},
                                        'document': actor['role'] in ADMINS | {'recruitment'},
                                        'pvp': actor['role'] in ADMINS | {'recruitment'},
                                        'check': actor['role'] in ADMINS | {'recruitment'},
                                        'catalog': actor['role'] in ADMINS and scope == 'TRUE'}})

    @app.get('/api/workforce/people')
    @roles_required(*READERS)
    def workforce_people():
        db = database()
        day = date_value(request.args.get('date') or datetime.now(timezone(timedelta(hours=3))).date().isoformat(), required=True)
        try:
            limit = min(100, max(1, int(request.args.get('limit', 50))))
            offset = max(0, min(100000, int(request.args.get('offset', 0))))
        except ValueError:
            abort(400, description='Неверная страница списка.')
        search = text_value(request.args.get('q', ''), 'Поиск', 200)
        with db:
            db.execute('BEGIN')
            db.native("SET LOCAL statement_timeout='1000ms'")
            actor, scope, args = actor_scope(db)
            clauses = [scope]
            queue = request.args.get('queue', '')
            if queue not in ('', 'movements', 'plans', 'pvp', 'rotations'):
                abort(400, description='Неизвестный раздел учёта.')
            if queue == 'movements':
                clauses.append("""(st.stage_code IN ('stage.leave','stage.inbound') OR EXISTS(
                    SELECT 1 FROM workforce_movements qm WHERE qm.worker_id=w.id AND qm.actual_date IS NULL
                    AND COALESCE(qm.result_code,'') NOT IN ('result.cancelled','result.happened')
                    AND NOT EXISTS(SELECT 1 FROM workforce_movements qn WHERE qn.rescheduled_from=qm.id)))""")
            elif queue == 'plans':
                clauses.append("""EXISTS(SELECT 1 FROM workforce_movements qm WHERE qm.worker_id=w.id
                    AND qm.actual_date IS NULL AND COALESCE(qm.result_code,'') NOT IN ('result.cancelled','result.happened')
                    AND NOT EXISTS(SELECT 1 FROM workforce_movements qn WHERE qn.rescheduled_from=qm.id))""")
            elif queue == 'pvp':
                clauses.append("""(st.stage_code='stage.pvp' OR p.employment_code IN ('employment.recruitment','employment.irs')
                    OR EXISTS(SELECT 1 FROM workforce_pvp_stays qp WHERE qp.worker_id=w.id AND qp.departed_on IS NULL))""")
            elif queue == 'rotations':
                clauses.append("EXISTS(SELECT 1 FROM workforce_rotations qr WHERE qr.worker_id=w.id AND NOT qr.cancelled AND qr.actual_end_date IS NULL)")
            active = request.args.get('active', '1')
            if active not in ('', '0', '1'):
                abort(400, description='Неизвестный фильтр активности.')
            if active:
                clauses.append('w.active=%s')
                args.append(int(active))
            if request.args.get('stage') == 'unconfirmed':
                clauses.append('st.stage_code IS NULL')
            for key, column in [('department', 'w.department'), ('employment', 'p.employment_code'),
                                *([] if request.args.get('stage') == 'unconfirmed' else [('stage', 'st.stage_code')])]:
                if request.args.get(key):
                    clauses.append(column + '=%s')
                    args.append(request.args[key])
            if request.args.get('employer'):
                clauses.append('p.employer_id=%s')
                args.append(uuid_value(request.args['employer']))
            if request.args.get('category'):
                try:
                    category_id = int(request.args['category'])
                except ValueError:
                    abort(400, description='Выберите категорию ГДЛР.')
                clauses.append('eg.category_id=%s')
                args.append(category_id)
            if request.args.get('conflicts') == '1':
                clauses.append("EXISTS(SELECT 1 FROM workforce_conflicts cf WHERE cf.worker_id=w.id AND cf.state='open')")
            if search:
                if request.args.get('regex') == '1':
                    clauses.append("(w.name_search ~* %s OR CASE WHEN w.personnel_is_internal THEN '' ELSE w.personnel_no::text END ~* %s)")
                    args.extend([search, search])
                else:
                    term = '%' + search.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                    clauses.append("(w.name_search LIKE %s OR CASE WHEN w.personnel_is_internal THEN '' ELSE w.personnel_no::text END ILIKE %s)")
                    args.extend([term, term])
            source = '''FROM workers w JOIN workforce_profiles p ON p.worker_id=w.id
                LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id
                LEFT JOIN (SELECT DISTINCT ON(e.worker_id) e.worker_id,e.stage_code,e.effective_date FROM workforce_stage_events e
                    WHERE e.confirmed AND NOT e.retracted AND e.effective_date<=%s
                    AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r WHERE r.replaces_id=e.id AND (r.confirmed OR r.retracted) AND r.effective_date<=%s)
                    ORDER BY e.worker_id,e.effective_date DESC,e.sequence DESC) st ON st.worker_id=w.id
                WHERE ''' + ' AND '.join('(' + clause + ')' for clause in clauses)
            bound = [day, day, *args]
            try:
                from workforce_read_cache import cached
                revision = db.native('SELECT revision FROM workforce_registry_revision WHERE singleton').fetchone()[0]
                cache_key = json.dumps([source, bound], default=str, ensure_ascii=False)
                totals = cached(db, revision, ('totals', cache_key), lambda: plain(db.native('''SELECT count(*) total,
                    count(*) FILTER(WHERE st.stage_code='stage.onsite') onsite,
                    count(*) FILTER(WHERE st.stage_code='stage.pvp') pvp,
                    count(*) FILTER(WHERE st.stage_code='stage.inbound') inbound,
                    count(*) FILTER(WHERE st.stage_code IS NULL) unconfirmed,
                    count(*) FILTER(WHERE st.stage_code='stage.leave') on_leave ''' + source, bound).fetchone()))
                rows = cached(db, revision, ('page', cache_key, limit, offset), lambda: plain(db.native('''WITH page AS MATERIALIZED (
                    SELECT w.id,w.name_search,st.stage_code,st.effective_date ''' + source + '''
                    ORDER BY w.name_search,w.id LIMIT %s OFFSET %s)
                    SELECT w.id,w.uuid,w.full_name,w.department,w.profession,w.active,
                    CASE WHEN w.personnel_is_internal THEN '' ELSE w.personnel_no::text END personnel_no,
                    o.name employer,pr.label project,em.label employment,ci.label citizenship,
                    p.arrival_date,COALESCE((SELECT r.planned_end_date FROM workforce_rotations r WHERE r.worker_id=w.id
                        AND NOT r.cancelled AND r.start_date<=%s AND COALESCE(r.actual_end_date,r.next_arrival_date)>=%s
                        ORDER BY r.start_date DESC LIMIT 1),p.forecast_departure_date) forecast_departure_date,
                    COALESCE(gc.name,w.category) category,
                    sc.label stage,page.stage_code,page.effective_date,
                    p.staffing_ready,
                    COALESCE((SELECT max(se.sequence) FROM workforce_stage_events se WHERE se.worker_id=w.id),0) stage_revision,
                    (SELECT count(*) FROM workforce_conflicts cf WHERE cf.worker_id=w.id AND cf.state='open') conflicts,
                    (SELECT jsonb_build_object('direction',mv.direction,'planned_date',mv.planned_date,'basis',mb.label,'result',mr.label)
                        FROM workforce_movements mv LEFT JOIN workforce_catalog mb ON mb.code=mv.basis_code
                        LEFT JOIN workforce_catalog mr ON mr.code=mv.result_code
                        WHERE mv.worker_id=w.id AND mv.actual_date IS NULL AND COALESCE(mv.result_code,'') NOT IN ('result.cancelled','result.happened')
                        AND NOT EXISTS(SELECT 1 FROM workforce_movements mn WHERE mn.rescheduled_from=mv.id)
                        ORDER BY mv.planned_date,mv.id LIMIT 1) movement,
                    (SELECT jsonb_build_object('schedule',rs.name,'start_date',ro.start_date,'planned_end_date',ro.planned_end_date,
                        'leave_end_date',ro.leave_end_date,'next_arrival_date',ro.next_arrival_date)
                        FROM workforce_rotations ro JOIN workforce_rotation_schedules rs ON rs.id=ro.schedule_id
                        WHERE ro.worker_id=w.id AND NOT ro.cancelled AND ro.actual_end_date IS NULL
                        ORDER BY ro.start_date DESC LIMIT 1) rotation
                    FROM page JOIN workers w ON w.id=page.id
                    JOIN workforce_profiles p ON p.worker_id=w.id
                    LEFT JOIN workforce_organizations o ON o.id=p.employer_id
                    LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
                    LEFT JOIN employee_smu es ON es.worker_id=w.id
                    LEFT JOIN workforce_smu_projects sp ON sp.smu_id=es.smu_id
                    LEFT JOIN workforce_catalog pr ON pr.code=sp.project_code
                    LEFT JOIN workforce_catalog em ON em.code=p.employment_code
                    LEFT JOIN workforce_catalog ci ON ci.code=p.citizenship_code
                    LEFT JOIN workforce_catalog sc ON sc.code=page.stage_code
                    ORDER BY page.name_search,page.id''', [*bound, limit, offset, day, day]).fetchall()))
            except psycopg.errors.InvalidRegularExpression:
                abort(400, description='Некорректное регулярное выражение.')
            except psycopg.errors.QueryCanceled:
                abort(422, description='Поиск слишком сложный. Уточните запрос или СМУ.')
            # Assignments change independently of the registry cache. Read their current
            # dated facts in this snapshot, and never put role-specific fields in cache.
            ids = [row['id'] for row in rows]
            assigned = {row['worker_id'] for row in db.native('''SELECT DISTINCT worker_id
                FROM assignments WHERE work_date=%s AND worker_id=ANY(%s)''', (day, ids)).fetchall()} if ids else set()
            addresses = {}
            if ids and actor['role'] in ADMINS | {'rotation', 'recruitment'}:
                addresses = {row['worker_id']: row['address'] for row in db.native('''
                    SELECT DISTINCT ON(s.worker_id) s.worker_id,COALESCE(NULLIF(p.address,''),p.name) address
                    FROM workforce_pvp_stays s JOIN workforce_pvp_places p ON p.id=s.place_id
                    WHERE s.worker_id=ANY(%s) AND s.arrived_on<=%s AND (s.departed_on IS NULL OR s.departed_on>%s)
                    ORDER BY s.worker_id,s.arrived_on DESC,s.updated_at DESC,s.id''', (ids, day, day)).fetchall()}
            rows = [{**{key: value for key, value in row.items() if key != 'stage_revision'},
                     'stage_token': stage_token(row['id'], day, row['stage_revision']),
                     'transition_targets': transition_targets(actor['role'], row['stage_code'], row['active']),
                     'pvp_address': addresses.get(row['id']), 'assigned': row['id'] in assigned} for row in rows]
        return jsonify({'rows': rows, 'totals': totals, 'offset': offset, 'limit': limit, 'date': day,
                        'revision': str(revision)})

    @app.get('/api/workforce/people/<int:worker_id>')
    @roles_required(*READERS)
    def workforce_person(worker_id):
        db = database()
        with db:
            db.execute('BEGIN')
            actor, _ = require_worker(db, worker_id)
            result = {'profile': profile_snapshot(db, worker_id)}
            for name, table in [('movements', 'workforce_movements'), ('documents', 'workforce_documents'),
                                ('pvp', 'workforce_pvp_stays'), ('checks', 'workforce_checks'), ('rotations', 'workforce_rotations')]:
                result[name] = plain(db.native(f'SELECT * FROM {table} WHERE worker_id=%s ORDER BY updated_at DESC LIMIT 200',
                                              (worker_id,)).fetchall())
            result['stages'] = plain(db.native('''SELECT e.*,u.full_name actor_name FROM workforce_stage_events e
                JOIN users u ON u.id=e.created_by WHERE worker_id=%s ORDER BY effective_date DESC,sequence DESC LIMIT 200''',
                (worker_id,)).fetchall())
            result['conflicts'] = plain(db.native('SELECT * FROM workforce_conflicts WHERE worker_id=%s ORDER BY created_at DESC LIMIT 200',
                                                 (worker_id,)).fetchall())
            result['history'] = plain(db.native('''SELECT a.*,u.full_name actor_name FROM workforce_audit a
                JOIN users u ON u.id=a.actor_id WHERE worker_id=%s ORDER BY a.id DESC LIMIT 100''', (worker_id,)).fetchall())
            result['sources'] = plain(db.native('''SELECT filename,sheet,source_row,mapping_notes,active
                FROM workforce_source_records WHERE worker_id=%s ORDER BY filename,sheet,source_row LIMIT 200''',
                (worker_id,)).fetchall())
            result['role'] = actor['role']
            result['private_details'] = actor['role'] not in {'foreman', 'viewer'}
            if not result['private_details']:
                # Staffing users need the prepared workforce, not passport, health,
                # recruitment notes or private source rows from HR spreadsheets.
                for field in ('birth_date', 'phone', 'messenger', 'origin_city', 'origin_code', 'notes'):
                    result['profile'].pop(field, None)
                for name in ('documents', 'checks', 'pvp', 'sources', 'history', 'conflicts'):
                    result[name] = []
                for row in result['movements']:
                    for field in ('notes', 'travel_details', 'origin', 'destination', 'origin_code', 'destination_code'):
                        row.pop(field, None)
                for row in result['rotations']:
                    row.pop('notes', None)
                for row in result['stages']:
                    row.pop('reason', None)
        return jsonify(result)

    @app.post('/api/workforce/people/<int:worker_id>/stage')
    @roles_required('admin', 'rotation')
    def workforce_stage(worker_id):
        data = payload({'stage_code', 'effective_date', 'confirmed', 'reason', 'replaces_id', 'request_key'},
                       {'stage_code', 'effective_date', 'confirmed', 'reason', 'request_key'})
        if type(data['confirmed']) is not bool:
            abort(400, description='Укажите, подтверждено ли событие.')
        day = date_value(data['effective_date'], required=True)
        reason = text_value(data['reason'], 'Основание изменения', 30000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'stage')
            operation = f'stage:{worker_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            code = catalog_value(db, data['stage_code'], 'stage', True)
            replaces = uuid_value(data['replaces_id']) if data.get('replaces_id') else None
            if replaces and not db.native('''SELECT id FROM workforce_stage_events e WHERE id=%s AND worker_id=%s
                AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r WHERE r.replaces_id=e.id)''',
                (replaces, worker_id)).fetchone():
                abort(409, description='Исправляемое событие недоступно или уже исправлено.')
            row = plain(db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
                reason,replaces_id,created_by,request_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (worker_id, code, day, data['confirmed'], reason, replaces, actor['id'], data['request_key'])).fetchone())
            audit(db, actor, 'create', 'stage', row['id'], None, row, worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row), 201

    @app.post('/api/workforce/people/<int:worker_id>/<kind>')
    @app.patch('/api/workforce/people/<int:worker_id>/<kind>/<uuid:entity_id>')
    @roles_required('admin', 'rotation', 'recruitment')
    def workforce_entity(worker_id, kind, entity_id=None):
        spec = ENTITIES.get(kind)
        if not spec:
            abort(404)
        data = payload(set(spec['fields']) | {'token', 'reason', 'request_key'},
                       {'request_key', 'reason'} | ({'token'} if entity_id else spec['required']))
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, kind)
            operation = f'{kind}:{worker_id}:{entity_id or "new"}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = None
            if entity_id:
                before = plain(db.native(f'SELECT * FROM {spec["table"]} WHERE id=%s AND worker_id=%s',
                                         (entity_id, worker_id)).fetchone())
                if not before:
                    abort(404, description='Запись сотрудника не найдена.')
                if before['edit_token'] != data['token']:
                    abort(409, description='Запись изменена другим пользователем. Обновите карточку.')
                if kind == 'movement':
                    if db.native('SELECT id FROM workforce_movements WHERE rescheduled_from=%s', (entity_id,)).fetchone():
                        abort(409, description='Этот заезд уже перенесён. Измените последнюю запись в цепочке.')
                    if 'planned_date' in data and date_value(data['planned_date']) != before['planned_date']:
                        abort(400, description='Для изменения плановой даты используйте «Перенести поездку». Прежняя дата сохранится в истории.')
            pairs = MOVEMENT_CATALOG_PAIRS if kind == 'movement' else ()
            clean = clear_catalog_text(validate_fields(db, changed_fields(data, spec['fields'], before, pairs),
                                                       spec['fields'], spec['required']), pairs)
            if not clean:
                abort(400, description='Не переданы изменённые поля.')
            validate_entity(kind, {**(before or {}), **clean})
            keys, values = list(clean), list(clean.values())
            if entity_id:
                sql = f'UPDATE {spec["table"]} SET ' + ','.join(key + '=%s' for key in keys)
                sql += ',edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *'
                values += [actor['id'], entity_id]
            else:
                keys += ['worker_id', 'created_by', 'updated_by', 'request_key']
                values += [worker_id, actor['id'], actor['id'], data['request_key']]
                sql = f'INSERT INTO {spec["table"]} (' + ','.join(keys) + ') VALUES (' + ','.join(['%s'] * len(keys)) + ') RETURNING *'
            row = plain(db.native(sql, values).fetchone())
            from workforce_presence import sync_operation_presence
            row = sync_operation_presence(db, actor, kind, before, row, reason)
            audit(db, actor, 'update' if before else 'create', kind, row['id'], before, row,
                  worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row), 200 if entity_id else 201

    @app.post('/api/workforce/people/<int:worker_id>/movements/<uuid:entity_id>/reschedule')
    @roles_required('admin', 'rotation')
    def workforce_reschedule(worker_id, entity_id):
        data = payload({'planned_date', 'reason', 'token', 'request_key'},
                       {'planned_date', 'reason', 'token', 'request_key'})
        day = date_value(data['planned_date'], 'Новая плановая дата', True)
        reason = text_value(data['reason'], 'Причина переноса', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'movement')
            operation = f'reschedule:{worker_id}:{entity_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = plain(db.native('SELECT * FROM workforce_movements WHERE id=%s AND worker_id=%s',
                                     (entity_id, worker_id)).fetchone())
            if not before:
                abort(404, description='Поездка сотрудника не найдена.')
            if before['edit_token'] != data['token']:
                abort(409, description='Поездка изменена другим пользователем. Обновите карточку.')
            if before['result_code'] in ('result.happened', 'result.cancelled') or before['actual_date']:
                abort(400, description='Состоявшуюся или отменённую поездку переносить нельзя. При необходимости создайте новый план.')
            if db.native('SELECT id FROM workforce_movements WHERE rescheduled_from=%s', (entity_id,)).fetchone():
                abort(409, description='Поездка уже перенесена. Откройте последнюю запись.')
            if before['planned_date'] == day:
                abort(400, description='Укажите другую плановую дату.')
            old = plain(db.native('''UPDATE workforce_movements SET result_code='result.postponed',
                edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''',
                (actor['id'], entity_id)).fetchone())
            new = plain(db.native('''INSERT INTO workforce_movements(worker_id,direction,planned_date,basis_code,
                origin,destination,travel_details,notes,source_record_id,created_by,updated_by,request_key,rescheduled_from,destination_kind,
                origin_code,destination_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (worker_id, before['direction'], day, before['basis_code'], before['origin'], before['destination'],
                 before['travel_details'], before['notes'], before['source_record_id'], actor['id'], actor['id'],
                 data['request_key'], entity_id, before['destination_kind'], before['origin_code'], before['destination_code'])).fetchone())
            audit(db, actor, 'reschedule', 'movement', entity_id, before, old, worker_id=worker_id, reason=reason)
            audit(db, actor, 'create_after_reschedule', 'movement', new['id'], None, new, worker_id=worker_id, reason=reason)
            result = {'previous': old, 'current': new}
            remember(db, actor, operation, data, result)
        return jsonify(result), 201

    @app.patch('/api/workforce/people/<int:worker_id>/profile')
    @roles_required('admin', 'rotation', 'recruitment')
    def workforce_profile(worker_id):
        rules = {'full_name': ('text', 240), 'profession': ('catalog_text', 'profession', 500),
                 'profession_code': ('catalog', 'profession'), 'origin_code': ('catalog', 'travelpoint'),
                 'citizenship_code': ('catalog', 'citizenship'), 'employment_code': ('catalog', 'employment'),
                 'birth_date': ('date',), 'phone': ('text', 300), 'messenger': ('text', 300),
                 'origin_city': ('catalog_text', 'travelpoint', 300), 'rotation_schedule': ('text', 500), 'arrival_date': ('date',),
                 'forecast_departure_date': ('date',), 'leave_start_date': ('date',), 'leave_end_date': ('date',),
                 'notes': ('text', 30000)}
        data = payload(set(rules) | {'employer_id', 'rotation_schedule_id', 'token', 'reason', 'request_key'}, {'token', 'reason', 'request_key'})
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, worker = require_worker(db, worker_id, 'profile')
            operation = f'profile:{worker_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = profile_snapshot(db, worker_id)
            if data['token'] != before['token']:
                abort(409, description='Карточка изменена другим пользователем. Обновите данные.')
            clean = clear_catalog_text(validate_fields(db, changed_fields(data, rules, before, PROFILE_CATALOG_PAIRS), rules),
                                       PROFILE_CATALOG_PAIRS)
            if 'rotation_schedule_id' in data and (data['rotation_schedule_id'] or None) != before.get('rotation_schedule_id'):
                schedule_id = uuid_value(data['rotation_schedule_id']) if data['rotation_schedule_id'] else None
                schedule = db.native('SELECT id,name FROM workforce_rotation_schedules WHERE id=%s AND active', (schedule_id,)).fetchone() if schedule_id else None
                if schedule_id and not schedule:
                    abort(400, description='Выберите действующий график вахтования.')
                clean.update(rotation_schedule_id=schedule_id, rotation_schedule=schedule['name'] if schedule else '')
            employer = None
            if 'employer_id' in data and data['employer_id'] != before['employer_id']:
                employer_id = uuid_value(data['employer_id'], 'Работодатель')
                employer = db.native('SELECT id,name FROM workforce_organizations WHERE id=%s AND active', (employer_id,)).fetchone()
                if not employer:
                    abort(400, description='Выберите действующего работодателя из справочника.')
                clean['employer_id'] = employer['id']
            if not clean:
                abort(400, description='Не переданы изменённые поля.')
            if 'employment_code' in clean:
                if str(worker['personnel_no']).isdigit() and not worker['personnel_is_internal'] and clean['employment_code'] != 'employment.staff':
                    abort(400, description='Сотрудник с табельным номером относится к штату.')
                if actor['role'] == 'recruitment' and clean['employment_code'] == 'employment.staff':
                    abort(403, description='Перевод в штат подтверждает перевахта или администратор.')
            if actor['role'] == 'recruitment' and set(clean) & {'arrival_date', 'forecast_departure_date', 'leave_start_date', 'leave_end_date'}:
                abort(403, description='Вахту и межвахтовый отпуск ведёт перевахта.')
            merged = {**before, **clean}
            if merged['leave_start_date'] and merged['leave_end_date'] and merged['leave_end_date'] < merged['leave_start_date']:
                abort(400, description='Окончание отпуска раньше его начала.')
            if 'full_name' in clean and not clean['full_name']:
                abort(400, description='ФИО не может быть пустым.')
            worker_changes = {key: clean.pop(key) for key in ('full_name', 'profession', 'profession_code') if key in clean}
            if worker_changes:
                db.native('UPDATE workers SET ' + ','.join(key+'=%s' for key in worker_changes) + ' WHERE id=%s',
                          [*worker_changes.values(), worker_id])
            if employer and employer['id'] != before['employer_id']:
                # Use the retained correction table so staffing and reimport keep
                # the same employer and the original source value.
                db.native('''INSERT INTO employee_employers(worker_id,employer,source_employer,edit_token,updated_by,updated_at)
                    VALUES (%s,%s,%s,gen_random_uuid()::text,%s,now()::text)
                    ON CONFLICT(worker_id) DO UPDATE SET employer=excluded.employer,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (worker_id, employer['name'], before['employer'], actor['id']))
            clean['edit_token'] = str(uuid4())
            sql = 'UPDATE workforce_profiles SET ' + ','.join(key + '=%s' for key in clean)
            db.native(sql + ',updated_by=%s,updated_at=now() WHERE worker_id=%s',
                      [*clean.values(), actor['id'], worker_id])
            row = profile_snapshot(db, worker_id)
            audit(db, actor, 'update', 'profile', worker_id, before, row, worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row)

    @app.put('/api/workforce/people/<int:worker_id>/checks/<check_code>')
    @roles_required('admin', 'recruitment')
    def workforce_check(worker_id, check_code):
        rules = {'state_code': ('catalog', 'checkstate'), 'planned_date': ('date',),
                 'completed_date': ('date',), 'notes': ('text', 10000)}
        data = payload(set(rules) | {'token', 'reason', 'request_key'}, {'token', 'reason', 'request_key', 'state_code'})
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'check')
            operation = f'check:{worker_id}:{check_code}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            catalog_value(db, check_code, 'check', True)
            before = plain(db.native('SELECT * FROM workforce_checks WHERE worker_id=%s AND check_code=%s',
                                     (worker_id, check_code)).fetchone())
            if data['token'] != (before['edit_token'] if before else 'new'):
                abort(409, description='Этап оформления изменён. Обновите карточку.')
            clean = validate_fields(db, {key: value for key, value in data.items() if key in rules}, rules, {'state_code'})
            merged = {**(before or {}), **clean}
            if merged['state_code'] == 'checkstate.ready' and not merged.get('completed_date'):
                abort(400, description='Укажите дату завершения этапа.')
            columns = ['worker_id', 'check_code', 'updated_by', *clean]
            sql = 'INSERT INTO workforce_checks (' + ','.join(columns) + ') VALUES (' + ','.join(['%s'] * len(columns)) + ')'
            sql += ' ON CONFLICT(worker_id,check_code) DO UPDATE SET ' + ','.join(key + '=excluded.' + key for key in clean)
            sql += ',updated_by=excluded.updated_by,updated_at=now(),edit_token=gen_random_uuid() RETURNING *'
            row = plain(db.native(sql, [worker_id, check_code, actor['id'], *clean.values()]).fetchone())
            audit(db, actor, 'update' if before else 'create', 'check', f'{worker_id}:{check_code}',
                  before, row, worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row)

    @app.patch('/api/workforce/conflicts/<uuid:conflict_id>')
    @roles_required('admin')
    def workforce_resolve(conflict_id):
        data = payload({'token', 'resolution', 'request_key'}, {'token', 'resolution', 'request_key'})
        resolution = text_value(data['resolution'], 'Решение', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _, _ = actor_scope(db, write=True)
            if actor['role'] not in ADMINS:
                abort(403)
            before = plain(db.native('SELECT * FROM workforce_conflicts WHERE id=%s', (conflict_id,)).fetchone())
            if not before:
                abort(404)
            if before['worker_id']:
                require_worker(db, before['worker_id'], 'resolve')
            else:
                from user_smu_access import require_all
                require_all(db)
            operation = f'resolve:{conflict_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            if data['token'] != before['edit_token'] or before['state'] != 'open':
                abort(409, description='Конфликт уже обработан. Обновите список.')
            # Resolution records the decision. Identity merging is a separate reviewed operation.
            if before['kind'] == 'identity':
                abort(409, description='Для конфликта личности сначала подтвердите сопоставление в импорте.')
            row = plain(db.native('''UPDATE workforce_conflicts SET state='resolved',resolution=%s,
                resolved_by=%s,resolved_at=now(),edit_token=gen_random_uuid() WHERE id=%s RETURNING *''',
                (resolution, actor['id'], conflict_id)).fetchone())
            audit(db, actor, 'resolve', 'conflict', conflict_id, before, row,
                  worker_id=before['worker_id'], reason=resolution)
            remember(db, actor, operation, data, row)
        return jsonify(row)

    @app.post('/api/workforce/catalog/<kind>')
    @app.patch('/api/workforce/catalog/<kind>/<code>')
    @roles_required('admin')
    def workforce_catalog(kind, code=None):
        if kind not in {'citizenship', 'employment', 'stage', 'basis', 'result', 'document', 'docstate',
                        'check', 'checkstate', 'project', 'organization', 'place', 'profession', 'specialty', 'travelpoint', 'direction', 'destination'}:
            abort(404)
        if kind in {'employment', 'stage', 'basis', 'result', 'docstate', 'checkstate', 'direction', 'destination'}:
            abort(400, description='Этот перечень закреплён правилами учёта и доступен только для просмотра.')
        data = payload({'label', 'active', 'address', 'capacity', 'specialty_code', 'grade', 'token', 'request_key', 'reason'},
                       {'request_key', 'reason'} | ({'token'} if code else {'label'}))
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _, _ = actor_scope(db, write=True)
            if actor['role'] not in ADMINS:
                abort(403)
            from user_smu_access import require_all
            require_all(db)
            operation = f'catalog:{kind}:{code or "new"}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            table, identity = ('workforce_organizations', 'id') if kind == 'organization' else (
                ('workforce_pvp_places', 'id') if kind == 'place' else ('workforce_catalog', 'code'))
            before = None
            if code:
                if identity == 'id':
                    uuid_value(code)
                elif not code.startswith(kind + '.'):
                    abort(404)
                before = plain(db.native(f'SELECT * FROM {table} WHERE {identity}=%s', (code,)).fetchone())
                if not before:
                    abort(404)
                if before['edit_token'] != data['token']:
                    abort(409, description='Справочник изменён. Обновите список.')
                if before.get('system_value'):
                    abort(400, description='Значение участвует в правилах учёта и не может быть изменено.')
            values = {}
            if 'label' in data:
                values['label' if identity == 'code' else 'name'] = text_value(data['label'], 'Название',
                    500 if kind in {'profession', 'specialty'} else 300 if kind == 'travelpoint' else 200, True)
            if 'active' in data:
                if type(data['active']) is not bool:
                    abort(400, description='Укажите активность значения.')
                values['active'] = data['active']
            if set(data) & {'specialty_code', 'grade'} and kind != 'profession':
                abort(400, description='Специальность и разряд доступны только для должностей и профессий.')
            if 'specialty_code' in data:
                specialty = data['specialty_code'] or None
                # Keep an existing archived parent; changing the relation requires an active one.
                if before is None or specialty != before.get('specialty_code'):
                    specialty = catalog_value(db, specialty, 'specialty', True)
                values['specialty_code'] = specialty
            if 'grade' in data:
                grade = data['grade']
                if grade is not None and (type(grade) is not int or not 1 <= grade <= 99):
                    abort(400, description='Разряд должен быть целым числом от 1 до 99 или не указан.')
                if grade is not None and not values.get('specialty_code', (before or {}).get('specialty_code')):
                    abort(400, description='Для разряда выберите специальность.')
                values['grade'] = grade
            if set(data) & {'address', 'capacity'} and kind != 'place':
                abort(400, description='Адрес и вместимость доступны только для ПВП.')
            if 'address' in data:
                values['address'] = text_value(data['address'], 'Адрес', 500)
            if 'capacity' in data:
                if data['capacity'] is not None and (type(data['capacity']) is not int or not 0 <= data['capacity'] <= 1000000):
                    abort(400, description='Вместимость должна быть целым неотрицательным числом.')
                values['capacity'] = data['capacity']
            if not values:
                abort(400, description='Нет изменений.')
            if code:
                sql = f'UPDATE {table} SET ' + ','.join(key + '=%s' for key in values)
                sql += f',updated_by=%s,updated_at=now(),edit_token=gen_random_uuid() WHERE {identity}=%s RETURNING *'
                row = plain(db.native(sql, [*values.values(), actor['id'], code]).fetchone())
            else:
                values['updated_by'] = actor['id']
                if identity == 'code':
                    values.update(code=kind + '.' + uuid4().hex, kind=kind)
                sql = f'INSERT INTO {table} (' + ','.join(values) + ') VALUES (' + ','.join(['%s'] * len(values)) + ') RETURNING *'
                row = plain(db.native(sql, list(values.values())).fetchone())
            audit(db, actor, 'update' if before else 'create', 'catalog.' + kind, row[identity], before, row, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row), 200 if code else 201
