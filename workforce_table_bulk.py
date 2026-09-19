"""Atomic edits of the exact dated entities displayed in the workforce table."""
from datetime import date, timedelta
from uuid import uuid4

from flask import abort, jsonify, request
from psycopg.types.json import Jsonb

from workforce_bulk import FIELDS, apply_binding, check_ownership, ids_from, lock_people, references, snapshots
from workforce_core import ADMINS, EDITORS, audit, date_value, digest, plain, remember, replay, text_value, uuid_value
from workforce_rotations import planned_dates

DATES = {'stage_date': 'Дата начала статуса', 'arrival_date': 'Дата заезда',
         'forecast_departure_date': 'Прогноз окончания вахты', 'planned_date': 'Плановая дата поездки',
         'leave_start_date': 'Дата начала МО', 'leave_end_date': 'Дата окончания МО', 'next_arrival_date': 'Следующий заезд'}
MOVEMENT_FIELDS = {'movement_direction': 'direction', 'movement_basis': 'basis_code', 'planned_date': 'planned_date'}
ROTATION_FIELDS = {'rotation_schedule_id', 'leave_end_date', 'next_arrival_date'}
TABLE_FIELDS = set(FIELDS) | set(DATES) | set(MOVEMENT_FIELDS)


def table_snapshots(db, ids, day):
    profiles = snapshots(db, ids)
    movements = {r['worker_id']: plain(r) for r in db.native('''SELECT DISTINCT ON(m.worker_id) m.*
        FROM workforce_movements m WHERE m.worker_id=ANY(%s) AND m.actual_date IS NULL
        AND COALESCE(m.result_code,'') NOT IN ('result.cancelled','result.happened')
        AND NOT EXISTS(SELECT 1 FROM workforce_movements n WHERE n.rescheduled_from=m.id)
        ORDER BY m.worker_id,m.planned_date,m.id''', (ids,))}
    rotations = {r['worker_id']: plain(r) for r in db.native('''SELECT DISTINCT ON(r.worker_id) r.*
        FROM workforce_rotations r WHERE r.worker_id=ANY(%s) AND NOT r.cancelled AND r.actual_end_date IS NULL
        ORDER BY r.worker_id,r.start_date DESC,r.id''', (ids,))}
    forecast = {r['worker_id']: plain(r) for r in db.native('''SELECT DISTINCT ON(r.worker_id) r.*
        FROM workforce_rotations r WHERE r.worker_id=ANY(%s) AND NOT r.cancelled
        AND r.start_date<=%s AND COALESCE(r.actual_end_date,r.next_arrival_date)>=%s
        ORDER BY r.worker_id,r.start_date DESC,r.id''', (ids, day, day))}
    stages = {r['worker_id']: plain(r) for r in db.native('''SELECT DISTINCT ON(e.worker_id) e.*
        FROM workforce_stage_events e WHERE e.worker_id=ANY(%s) AND e.confirmed AND NOT e.retracted
        AND e.effective_date<=%s AND NOT EXISTS(SELECT 1 FROM workforce_stage_events n
          WHERE n.replaces_id=e.id AND (n.confirmed OR n.retracted) AND n.effective_date<=%s)
        ORDER BY e.worker_id,e.effective_date DESC,e.sequence DESC''', (ids, day, day))}
    revisions = {r['worker_id']: r['revision'] for r in db.native('''SELECT worker_id,max(sequence) revision
        FROM workforce_stage_events WHERE worker_id=ANY(%s) GROUP BY worker_id''', (ids,))}
    result = []
    for p in profiles:
        row = {'profile': p, 'movement': movements.get(p['id']), 'rotation': rotations.get(p['id']),
               'forecast': forecast.get(p['id']), 'stage': stages.get(p['id']), 'stage_revision': revisions.get(p['id'], 0)}
        result.append({**row, 'id': p['id'], 'name': p['full_name'], 'token': digest(row)})
    return result


def table_references(db, actor, rows, selected=None):
    # Keep the same reference tokens as bulk editing, loading only a cell's dependencies.
    needed = None if selected is None else {selected}
    if needed is not None:
        if selected in ('smu_id', 'project_code'):
            needed.update(('smu_id', 'project_code'))
        if selected in DATES or selected in ROTATION_FIELDS or selected in MOVEMENT_FIELDS:
            needed.update(('rotation_schedule_id', 'movement_direction', 'movement_destination'))
    fields = references(db, actor, needed)
    if 'smu_id' in fields:
        fields['smu_id']['label'] = 'СМУ'
    for field, label in DATES.items():
        if needed is not None and field not in needed:
            continue
        fields[field] = {'label': label, 'type': 'date', 'token': digest([field, 1]), 'options': []}
    for field, kind, label in [('movement_direction', 'direction', 'Заезд / выезд'), ('movement_basis', 'basis', 'Тип заезда/выезда'), ('movement_destination', 'destination', 'Пункт назначения')]:
        if needed is not None and field not in needed:
            continue
        choices = plain(db.native('SELECT code,label,edit_token FROM workforce_catalog WHERE kind=%s AND active ORDER BY sort_order,label', (kind,)).fetchall())
        fields[field] = {'label': label, 'token': digest(choices), 'options': [
            {'value': r['code'].split('.', 1)[1] if kind in ('direction','destination') else r['code'], 'label': r['label']} for r in choices]}
    # A project is derived from SMU. Offer an explicit destination SMU, never rewrite the shared mapping.
    if 'smu_id' in fields:
        allowed = {int(r['value']) for r in fields['smu_id']['options']}
        links = plain(db.native('''SELECT sp.smu_id,c.code,c.label,c.edit_token FROM workforce_smu_projects sp
        JOIN workforce_catalog c ON c.code=sp.project_code WHERE c.active AND sp.smu_id=ANY(%s) ORDER BY c.label,sp.smu_id''', (sorted(allowed),)).fetchall())
        fields['smu_id']['token'] = digest([fields['smu_id']['token'], links])
        fields['project_code'] = {'label': 'Проект', 'type': 'project', 'options': list({r['code']:{'value':r['code'],'label':r['label']} for r in links}.values()),
                              'smus': {r['value']: [str(link['smu_id']) for link in links if link['code']==r['value']]
                                       for r in [{'value':x['code']} for x in links]}}
    context_keys = ('rotation_schedule_id', 'movement_direction', 'movement_destination')
    context_token = digest([fields[k]['token'] for k in context_keys]) if all(k in fields for k in context_keys) else None
    for key, field in fields.items():
        field['enabled'] = True
        field['note'] = field.get('note', '')
        if key in DATES or key in ROTATION_FIELDS or key in MOVEMENT_FIELDS:
            field['token'] = digest([field['token'], context_token])
        movement = key in MOVEMENT_FIELDS or key in ROTATION_FIELDS or key in ('stage_date', 'leave_start_date') or (
            key == 'forecast_departure_date' and any(r['forecast'] for r in rows))
        if movement and actor['role'] not in ADMINS | {'rotation'}:
            field.update(enabled=False, note='Изменение движения и вахт доступно перевахте и администраторам.')
        if key == 'stage_date' and any(not r['stage'] for r in rows):
            field.update(enabled=False, note='Сначала задайте статус всем выбранным сотрудникам.')
        if key == 'forecast_departure_date' and any(r['forecast'] and r['forecast']['actual_end_date'] for r in rows):
            field.update(enabled=False, note='В выборе есть завершённая вахта. Её история здесь не изменяется.')
        if key == 'forecast_departure_date':
            field['recalculate_enabled'] = all(r['forecast'] for r in rows)
            if not field['recalculate_enabled']:
                field['note'] = 'Для части выбранных сотрудников нет вахты на отчётную дату: меняется только прогноз. Для пересчёта сначала задайте им вахту и график.'
        if key in MOVEMENT_FIELDS:
            field['create_movement'] = sum(not r['movement'] for r in rows)
            field['note'] = field['note'] or 'Изменяется ближайшая незавершённая поездка из таблицы. Перенос даты сохраняет предыдущий план в истории.'
        if key in ROTATION_FIELDS:
            field['create_rotation'] = sum(not r['rotation'] for r in rows)
        if key == 'rotation_schedule_id':
            field['note'] = 'График назначается открытой вахте; даты пересчитываются только при включённом переключателе. Для новой вахты укажите начало.'
        if key == 'stage_date':
            field['note'] = 'Исправляет дату показанного статуса с сохранением истории. Даты билетов и заезда не изменяются.'
    return fields


def table_values(row, fields):
    """Raw identities/dates of the displayed entities; never match labels to IDs."""
    values = {}
    for field in fields:
        source, key = row['profile'], field
        if field in MOVEMENT_FIELDS:
            source, key = row['movement'] or {}, MOVEMENT_FIELDS[field]
        elif field in ROTATION_FIELDS:
            source, key = row['rotation'] or {}, 'schedule_id' if field == 'rotation_schedule_id' else field
        elif field == 'stage_date':
            source, key = row['stage'] or {}, 'effective_date'
        elif field == 'forecast_departure_date' and row['forecast']:
            source, key = row['forecast'], 'planned_end_date'
        if field == 'project_code':
            smu = str(row['profile'].get('smu_id') or '')
            values[field] = next((code for code, ids in fields[field]['smus'].items() if smu in ids), '')
        else:
            value = source.get(key)
            values[field] = str(value) if value is not None else ''
    return values


def schedule(db, value):
    options = plain(db.native('SELECT * FROM workforce_rotation_schedules WHERE id=%s AND active', (uuid_value(value),)).fetchone()) if value else None
    if not options:
        abort(400, description='Для новой вахты выберите действующий график.')
    return options


def rotation_plan(db, row, field, value, extra):
    old = row['forecast'] if field == 'forecast_departure_date' else row['rotation']
    target = 'planned_end_date' if field == 'forecast_departure_date' else field
    if old and old['actual_end_date']:
        abort(409, description='Вахта уже завершена. Обновите список.')
    selected = schedule(db, value if field == 'rotation_schedule_id' else extra.get('schedule_id')) if not old or field == 'rotation_schedule_id' else old['schedule_snapshot']
    if not old:
        start = date_value(extra.get('start_date'), 'Начало новой вахты', True)
        current = {**planned_dates(start, selected), 'schedule_id': selected['id'], 'schedule_snapshot': selected}
    else:
        current = {k: old[k] for k in ('start_date','planned_end_date','leave_end_date','next_arrival_date','schedule_id','schedule_snapshot')}
    if field == 'rotation_schedule_id':
        current.update(schedule_id=value, schedule_snapshot=selected)
        if extra.get('recalculate'):
            current.update(planned_dates(current['start_date'], selected))
    else:
        current[target] = value
        if extra.get('recalculate') and field == 'forecast_departure_date':
            current.update(planned_dates(current['start_date'], selected, value))
        elif extra.get('recalculate') and field == 'leave_end_date':
            if selected.get('needs_review') or selected.get('travel_days') is None:
                abort(400, description='Уточните длительность дороги в графике перед пересчётом.')
            current['next_arrival_date'] = (date.fromisoformat(value)+timedelta(days=selected['travel_days'])).isoformat()
    if not (current['start_date'] <= current['planned_end_date'] <= current['leave_end_date'] < current['next_arrival_date']):
        abort(400, description='Даты должны идти по порядку: начало вахты → окончание вахты → окончание МО → следующий заезд. Включите пересчёт или уточните даты.')
    if db.native('''SELECT 1 FROM workforce_rotations WHERE worker_id=%s AND NOT cancelled AND (%s::uuid IS NULL OR id<>%s::uuid)
        AND start_date<=%s AND planned_end_date>=%s LIMIT 1''', (row['id'], old['id'] if old else None, old['id'] if old else None,
                                                             current['planned_end_date'], current['start_date'])).fetchone():
        abort(409, description='Новая дата пересекается с другой вахтой выбранного сотрудника.')
    return {'kind':'rotation','before':old,'values':current}


def make_plan(db, row, field, value, extra):
    if field == 'leave_start_date' and row['profile'].get('leave_end_date') and value > str(row['profile']['leave_end_date']):
        abort(400, description='Начало межвахтового отпуска не может быть позже его окончания в карточке сотрудника.')
    if field == 'forecast_departure_date' and extra.get('recalculate') and not row['forecast']:
        abort(400, description='Для пересчёта прогноза у всех выбранных сотрудников должна быть вахта на отчётную дату.')
    if field in MOVEMENT_FIELDS:
        old = row['movement']
        current = {k: old.get(k) for k in ('direction','basis_code','planned_date','destination_kind')} if old else {
            'direction': extra.get('direction'), 'planned_date': extra.get('planned_date'), 'basis_code': None,
            'destination_kind': extra.get('destination_kind')}
        current[MOVEMENT_FIELDS[field]] = value
        if not old:
            from workforce_api import validate_fields, ENTITIES
            current = validate_fields(db, current, ENTITIES['movement']['fields'], {'direction','destination_kind'})
        date_value(current['planned_date'], 'Плановая дата новой поездки', True)
        return {'kind':'movement','before':old,'values':current}
    if field in ROTATION_FIELDS or field == 'forecast_departure_date' and row['forecast']:
        return rotation_plan(db, row, field, value, extra)
    if field == 'stage_date':
        old = row['stage']
        if db.native('SELECT 1 FROM workforce_stage_events WHERE replaces_id=%s', (old['id'],)).fetchone():
            abort(409, description='Статус уже исправлен. Обновите список.')
        return {'kind':'stage','before':old,'values':{'effective_date':value}}
    return {'kind':'profile','before':row['profile'],'values':{field:value}}


def apply_plan(db, actor, row, plan, field, value, label, reason):
    old, values, kind = plan['before'], plan['values'], plan['kind']
    worker = row['id']
    if kind == 'profile':
        if field in FIELDS:
            apply_binding(db, actor, old, field, value, label)
        else:
            db.native(f'''UPDATE workforce_profiles SET {field}=%s,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                WHERE worker_id=%s''', (value, actor['id'], worker))
        after = snapshots(db, [worker])[0]
        audit(db, actor, 'bulk_update', 'profile', worker, old, after, worker_id=worker, reason=reason)
        return
    if kind == 'stage':
        after = plain(db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,
            replaces_id,created_by,request_key,date_basis) VALUES (%s,%s,%s,TRUE,%s,%s,%s,%s,%s) RETURNING *''',
            (worker,old['stage_code'],value,reason,old['id'],actor['id'],str(uuid4()),old['date_basis'])).fetchone())
        audit(db, actor, 'correct_date', 'stage', after['id'], old, after, worker_id=worker, reason=reason)
        return
    if kind == 'movement':
        if old and field == 'planned_date':
            previous = plain(db.native('''UPDATE workforce_movements SET result_code='result.postponed',edit_token=gen_random_uuid(),
                updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''', (actor['id'],old['id'])).fetchone())
            audit(db, actor, 'reschedule', 'movement', old['id'], old, previous, worker_id=worker, reason=reason)
        if not old or field == 'planned_date':
            carried = old or {}
            columns = ('origin','destination','travel_details','notes','source_record_id','destination_kind','origin_code','destination_code')
            carried = {**carried, 'destination_kind': values['destination_kind']}
            after = plain(db.native('''INSERT INTO workforce_movements(worker_id,direction,planned_date,basis_code,
                origin,destination,travel_details,notes,source_record_id,destination_kind,origin_code,destination_code,
                rescheduled_from,created_by,updated_by,request_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (worker,values['direction'],values['planned_date'],values['basis_code'],
                 *[carried.get(k, '' if k in columns[:4] else None) for k in columns],
                 old['id'] if old else None, actor['id'],actor['id'],str(uuid4()))).fetchone())
        else:
            column = MOVEMENT_FIELDS[field]
            after = plain(db.native(f'''UPDATE workforce_movements SET {column}=%s,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                WHERE id=%s RETURNING *''', (value,actor['id'],old['id'])).fetchone())
    else:
        if old:
            after = plain(db.native('''UPDATE workforce_rotations SET schedule_id=%s,schedule_snapshot=%s,planned_end_date=%s,
                leave_end_date=%s,next_arrival_date=%s,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                WHERE id=%s RETURNING *''', (values['schedule_id'],Jsonb(values['schedule_snapshot']),values['planned_end_date'],
                                          values['leave_end_date'],values['next_arrival_date'],actor['id'],old['id'])).fetchone())
        else:
            after = plain(db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,start_date,
                planned_end_date,leave_end_date,next_arrival_date,notes,created_by,updated_by,request_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (worker,values['schedule_id'],Jsonb(values['schedule_snapshot']),values['start_date'],values['planned_end_date'],
                 values['leave_end_date'],values['next_arrival_date'],reason,actor['id'],actor['id'],str(uuid4()))).fetchone())
    audit(db, actor, 'bulk_update' if old else 'bulk_create', kind, after['id'], old, after, worker_id=worker, reason=reason)


def register_table_bulk_routes(app, database, roles_required):
    @app.post('/api/workforce/bulk/table/prepare')
    @roles_required(*EDITORS)
    def workforce_table_prepare():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {'ids','date','field'} or {'ids','date'} - set(data):
            abort(400, description='Передайте выбранных сотрудников и отчётную дату.')
        selected = data.get('field')
        if 'field' in data and (not isinstance(selected, str) or selected not in TABLE_FIELDS | {'project_code'}):
            abort(400, description='Выберите редактируемый столбец.')
        ids, day = ids_from(data['ids']), date_value(data['date'], required=True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = lock_people(db, ids)
            rows = table_snapshots(db, ids, day)
            fields = table_references(db, actor, rows, selected)
        return jsonify(fields=fields, people=[{**{k:r[k] for k in ('id','name','token')},
                       **({'values': table_values(r, fields)} if selected else {})} for r in rows], date=day)

    @app.post('/api/workforce/bulk/table/apply')
    @roles_required(*EDITORS)
    def workforce_table_apply():
        data = request.get_json(silent=True)
        fields = {'people','date','field','value','reference_token','reason','request_key','extra'}
        if not isinstance(data, dict) or set(data) - fields or (fields - {'reason'}) - set(data):
            abort(400, description='Неверный состав полей массового изменения.')
        field = data['field']
        if not isinstance(field, str) or field not in TABLE_FIELDS:
            abort(400, description='Выберите редактируемый столбец.')
        if not isinstance(data['people'], list) or any(not isinstance(r,dict) or set(r)!={'id','token'} for r in data['people']):
            abort(400, description='Неверный список сотрудников.')
        ids = ids_from([r['id'] for r in data['people']])
        expected = {r['id']:r['token'] for r in data['people']}
        day = date_value(data['date'], required=True)
        reason = text_value(data.get('reason', ''), 'Причина изменения', 10000)
        extra = data['extra']
        if not isinstance(extra, dict) or set(extra)-{'recalculate','start_date','schedule_id','direction','planned_date','destination_kind'} or type(extra.get('recalculate',False)) is not bool:
            abort(400, description='Неверные дополнительные параметры.')
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = lock_people(db, ids, ownership=False)
            previous = replay(db, actor, 'bulk-table-edit', data)
            if previous is not None:
                return jsonify(previous)
            rows = table_snapshots(db, ids, day)
            check_ownership(actor, [r['profile'] for r in rows])
            if any(r['token'] != expected[r['id']] for r in rows):
                abort(409, description='Одна из карточек, поездок или вахт изменена. Ничего не сохранено; обновите список.')
            catalog = table_references(db, actor, rows, field)[field]
            if not catalog['enabled']:
                abort(403, description=catalog['note'])
            if data['reference_token'] != catalog['token']:
                abort(409, description='Справочник изменился. Повторите выбор.')
            value = date_value(data['value'], catalog['label'], True) if field in DATES else data['value']
            option = {'label':value} if field in DATES else next((o for o in catalog['options'] if o['value']==value), None)
            if not option:
                abort(400, description='Выберите действующее значение справочника.')
            if field == 'employment_code' and value != 'employment.staff' and any(
                    str(r['profile']['personnel_no']).isdigit() and not r['profile']['personnel_is_internal'] for r in rows):
                abort(400, description='Сотрудник с цифровым табельным номером относится к штату.')
            plans = [(r,make_plan(db,r,field,value,extra)) for r in rows]
            def differs(before, key, value):
                previous = before.get(key)
                if isinstance(value, dict):
                    return previous != value
                return str(previous if previous is not None else '') != str(value if value is not None else '')

            changed = [(r,p) for r,p in plans if not p['before'] or any(differs(p['before'],k,v) for k,v in p['values'].items())]
            for row, plan in changed:
                apply_plan(db,actor,row,plan,field,value,option['label'],reason)
            result = {'changed':len(changed),'unchanged':len(rows)-len(changed),'field':field}
            remember(db,actor,'bulk-table-edit',data,result)
        return jsonify(result)
