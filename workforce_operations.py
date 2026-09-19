"""Dated readiness, actionable exceptions and explicit workforce demand."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json

from flask import abort, jsonify, request

from workforce_core import (ADMINS, READERS, actor_scope, audit, catalog_value, date_value,
                            digest, plain, remember, replay, require_worker, text_value)


STAGE_SQL = """LEFT JOIN LATERAL (
    SELECT e.stage_code,e.effective_date,e.reason FROM workforce_stage_events e
    WHERE e.worker_id=w.id AND e.confirmed AND NOT e.retracted AND e.effective_date<=%s
    AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r WHERE r.replaces_id=e.id
        AND (r.confirmed OR r.retracted) AND r.effective_date<=%s)
    ORDER BY e.effective_date DESC,e.sequence DESC LIMIT 1) stage ON TRUE"""


def population(db, day, department='', worker_id=None):
    actor, scope, args = actor_scope(db)
    clauses = [scope]
    if worker_id is None:
        clauses.append('w.active=1')
    else:
        clauses.append('w.id=%s'); args.append(worker_id)
    if department:
        clauses.append('w.department=%s'); args.append(department)
    rows = db.native(f'''SELECT w.id,w.full_name,w.department,w.active,
        COALESCE(pc.label,NULLIF(w.profession,''),'Без профессии') profession,
        eg.category_id,(gc.active=1 AND gc.staffing_allowed=1) category_allowed,
        p.staffing_ready,p.forecast_departure_date,p.arrival_date,p.edit_token profile_token,
        stage.stage_code,stage.effective_date,stage.reason,
        COALESCE((SELECT max(e.sequence) FROM workforce_stage_events e WHERE e.worker_id=w.id),0) stage_revision,
        cycle.effective_date cycle_start,cycle.sequence cycle_sequence,
        EXISTS(SELECT 1 FROM assignments a WHERE a.worker_id=w.id AND a.work_date=%s) assigned
        FROM workers w JOIN workforce_profiles p ON p.worker_id=w.id
        LEFT JOIN workforce_catalog pc ON pc.code=w.profession_code
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id
        LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        {STAGE_SQL}
        LEFT JOIN LATERAL (SELECT e.effective_date,e.sequence FROM workforce_stage_events e
            WHERE e.worker_id=w.id AND e.stage_code='stage.onsite' AND e.confirmed AND NOT e.retracted
              AND e.effective_date<=%s AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r WHERE r.replaces_id=e.id
                  AND (r.confirmed OR r.retracted) AND r.effective_date<=%s)
            ORDER BY e.effective_date DESC,e.sequence DESC LIMIT 1) cycle ON TRUE
        WHERE {' AND '.join(clauses)} ORDER BY w.full_name,w.id''',
        [day, day, day, day, day, *args]).fetchall()
    return actor, [plain(row) for row in rows]


def allowed_departments(db, actor):
    access=db.native('SELECT mode,departments_json FROM user_smu_access WHERE user_id=%s',(actor['id'],)).fetchone()
    if actor['role'] in ('super_admin','hr_viewer') or (access and access['mode']=='all') or (not access and actor['role'] in ('admin','viewer')):
        return None
    return json.loads(access['departments_json']) if access else []


def related(db, ids):
    result = {}
    for key, table in [('movements','workforce_movements'), ('documents','workforce_documents'),
                       ('conflicts','workforce_conflicts'), ('rotations','workforce_rotations')]:
        grouped = defaultdict(list)
        if ids:
            query = ('''SELECT m.*,e.sequence event_sequence FROM workforce_movements m
                        LEFT JOIN workforce_stage_events e ON e.id=m.stage_event_id WHERE m.worker_id=ANY(%s)'''
                     if key == 'movements' else f'SELECT * FROM {table} WHERE worker_id=ANY(%s)')
            for row in db.native(query, (ids,)).fetchall():
                grouped[row['worker_id']].append(plain(row))
        result[key] = grouped
    return result


def visible_related(actor, data):
    if actor['role'] in ('foreman','viewer'):
        data['documents'] = defaultdict(list)
        data['conflicts'] = defaultdict(list)
    return data


def readiness(person, movements, documents, day):
    reasons = []
    if not person['active']:
        reasons.append('Сотрудник неактивен')
    if person['stage_code'] != 'stage.onsite':
        reasons.append('Нет подтверждённой явки на выбранную дату')
    departed = any(m['direction'] == 'departure' and m.get('result_code') == 'result.happened'
                   and m.get('actual_date') and (person.get('effective_date') or '0001-01-01') <= m['actual_date'] <= day
                   for m in movements)
    if departed:
        reasons.append('Зафиксирован выезд после последней явки')
    if not person['staffing_ready']:
        reasons.append('Не включён в состав для расстановки')
    if not person['category_id']:
        reasons.append('Не сопоставлена категория ГДЛР')
    elif not person['category_allowed']:
        reasons.append('Категория ГДЛР отключена или не допускается к расстановке')
    expired = [
        d for d in documents if d.get('expires_on') and d['expires_on'] < day
        and (not d.get('issued_on') or d['issued_on'] <= day)
        and d.get('state_code') != 'docstate.cancelled']
    if expired:
        reasons.append(f'Истёк срок документов: {len(expired)}')
    return {'ready': not reasons, 'reasons': reasons, 'assigned': person['assigned'],
            'stage_code': person['stage_code'], 'stage_date': person['effective_date'],
            'stage_reason': person['reason'],
            'note': 'Проверка носит справочный характер. Отсутствие замечаний не заменяет проверку обязательных допусков.'}


def departures_for(person, data, day):
    """Actionable plans for the current onsite cycle; never infer departure itself."""
    if not person['active'] or person['stage_code'] not in ('stage.onsite', 'stage.outbound'):
        return []
    wid = person['id']
    cycle = person.get('cycle_start') or person.get('arrival_date') or (
        person.get('effective_date') if person['stage_code'] == 'stage.onsite' else None)
    movements = data['movements'][wid]
    rotations = data['rotations'][wid]
    for movement in movements:
        actual = movement.get('actual_date')
        if movement['direction'] != 'departure' or movement.get('result_code') != 'result.happened' or not actual:
            continue
        if not cycle or actual > cycle or (actual == cycle and (
                not person.get('cycle_sequence') or not movement.get('event_sequence')
                or movement['event_sequence'] > person['cycle_sequence'])):
            return []
    open_rotations = [r for r in rotations if not r.get('cancelled') and not r.get('actual_end_date')
                      and r['start_date'] <= day and (not cycle or r['planned_end_date'] >= cycle)]
    rotation = max(open_rotations, key=lambda r: (r['start_date'], str(r['id'])), default=None)
    pending = [m for m in movements if m['direction'] == 'departure' and m.get('planned_date')
               and not m.get('actual_date') and m.get('result_code') not in ('result.cancelled', 'result.postponed', 'result.happened')
               and (not cycle or m['planned_date'] >= cycle)]
    plans = [('movement', m['id'], m['planned_date'], m) for m in pending]
    # A later or rescheduled trip takes precedence over an expired forecast.
    if not plans:
        if rotation:
            plans = [('rotation', rotation['id'], rotation['planned_end_date'], None)]
        elif person.get('forecast_departure_date'):
            plans = [('profile', wid, person['forecast_departure_date'], None)]
    minimum = max(filter(None, [person.get('effective_date'), cycle, (rotation or {}).get('start_date')]), default='')
    snapshot = {'worker': wid, 'day': day, 'stage': person['stage_code'], 'stage_date': person.get('effective_date'),
                'revision': person.get('stage_revision'), 'cycle': cycle, 'profile': person.get('profile_token'),
                'forecast': person.get('forecast_departure_date'),
                'movements': sorted(movements, key=lambda m: str(m.get('id', ''))),
                'rotations': sorted(rotations, key=lambda r: str(r.get('id', '')))}
    items = []
    for source, entity, planned, movement in plans:
        if planned > day or (cycle and planned < cycle):
            continue
        key = f'departure:{source}:{entity}'
        label = ('Дата по билету' if movement and movement.get('basis_code') == 'basis.ticket' else
                 'План поездки' if movement else 'График вахты' if rotation else 'Прогноз окончания вахты')
        details = {'source': source, 'source_label': label, 'planned_date': planned,
                   'movement_id': str(movement['id']) if movement else None,
                   'rotation_id': str(rotation['id']) if rotation else None,
                   'min_date': minimum, 'token': digest({**snapshot, 'key': key})}
        items.append({'key': key, 'kind': 'departure', 'worker_id': wid, 'full_name': person['full_name'],
                      'department': person['department'], 'title': 'Наступила дата выезда: подтвердить факт',
                      'due_date': planned, 'action': 'Подтвердить выезд', 'team': 'Перевахта', 'departure': details})
    return items


def issues_for(person, data, day):
    wid = person['id']; end = (date.fromisoformat(day) + timedelta(days=7)).isoformat()
    items = []
    def add(kind, entity, title, due, action, team):
        items.append({'key': f'{kind}:{entity}', 'kind': kind, 'worker_id': wid,
                      'full_name': person['full_name'], 'department': person['department'],
                      'title': title, 'due_date': due, 'action': action, 'team': team})
    ready = readiness(person, data['movements'][wid], data['documents'][wid], day)
    if person['stage_code'] == 'stage.onsite' and not person['assigned']:
        add('unassigned',f'{day}:{wid}','Явка без расстановки',day,'Открыть расстановку','Линейный ИТР')
    if person['assigned'] and ready['reasons']:
        add('readiness',f'{day}:{wid}','Расставлен: требуется проверка готовности',day,'Проверить карточку','Линейный ИТР')
    departures = departures_for(person, data, day)
    items.extend(departures)
    due_movements = {item['departure']['movement_id'] for item in departures}
    due_rotations = {item['departure']['rotation_id'] for item in departures}
    for m in data['movements'][wid]:
        if str(m['id']) in due_movements:
            continue
        if m['direction'] == 'departure':
            if person['stage_code'] not in ('stage.onsite', 'stage.outbound'):
                continue
            cycle = person.get('cycle_start') or person.get('arrival_date')
            if cycle and m.get('planned_date') and m['planned_date'] < cycle:
                continue
        if m.get('planned_date') and m['planned_date'] <= end and not m.get('actual_date') and m.get('result_code') not in ('result.cancelled','result.postponed'):
            add('movement',m['id'],('Заезд' if m['direction']=='arrival' else 'Выезд') + ': подтвердить поездку',m['planned_date'],'Проверить поездку','Перевахта')
    for d in data['documents'][wid]:
        if d.get('expires_on') and d['expires_on'] <= end and d.get('state_code') != 'docstate.cancelled' and (not d.get('issued_on') or d['issued_on'] <= day):
            add('document',d['id'],'Истекает срок документа',d['expires_on'],'Открыть документы','Комплектация')
    for c in data['conflicts'][wid]:
        if c['state'] == 'open':
            add('conflict',c['id'],'Не разрешён конфликт данных',day,'Разобрать конфликт','Администратор')
    for r in data['rotations'][wid]:
        if str(r['id']) in due_rotations:
            continue
        if not r['cancelled'] and not r.get('actual_end_date') and r['start_date'] <= day and r['planned_end_date'] <= end:
            add('rotation',r['id'],'Завершается вахта: проверить замену',r['planned_end_date'],'Открыть график вахты','Перевахта')
    return items


def forecast(rows, data, day, days, demands):
    """A scenario, never a claim that a ticket proves factual presence."""
    groups = defaultdict(list)
    for person in rows:
        groups[(person['department'],person['profession'])].append(person)
    for d in demands:
        groups.setdefault((d['department'],d['profession']), [])
    wanted = {(d['day'], d['department'], d['profession']): d for d in demands}
    output = []
    for (department, profession), people in sorted(groups.items()):
        points = []
        for delta in range(days):
            target = (date.fromisoformat(day) + timedelta(days=delta)).isoformat()
            confirmed = expected = arrivals = departures = 0
            for p in people:
                present = p['stage_code'] == 'stage.onsite'
                ms = data['movements'][p['id']]
                # Facts at or before the baseline can remove a departed worker,
                # but an arrival ticket/movement alone cannot establish onsite presence.
                if any(m['direction']=='departure' and m.get('result_code')=='result.happened'
                       and m.get('actual_date') and (p['effective_date'] or '0001-01-01') <= m['actual_date'] <= day for m in ms):
                    present = False
                confirmed += int(present)
                events = []
                for m in ms:
                    planned = m.get('planned_date')
                    if not planned or m.get('actual_date') or m.get('result_code') in ('result.cancelled','result.postponed'):
                        continue
                    # Travel to PVP/home is not a planned arrival at the site.
                    if m['direction']=='arrival' and m.get('destination_kind') != 'site':
                        continue
                    if day < planned <= target:
                        events.append((planned,m['direction']))
                # Equal-day contradictions stay out of a numerical forecast.
                by_date = defaultdict(set)
                for when, direction in events:
                    by_date[when].add(direction)
                for when, directions in sorted(by_date.items()):
                    if len(directions) != 1:
                        continue
                    direction = next(iter(directions))
                    new = direction == 'arrival'
                    if when == target and new != present:
                        arrivals += int(new); departures += int(not new)
                    present = new
                expected += int(present)
            demand = wanted.get((target,department,profession))
            required = demand['required'] if demand else None
            points.append({'date':target,'confirmed_base':confirmed,'expected':expected,
                           'planned_arrivals':arrivals,'planned_departures':departures,
                           'required':required,'shortage':max(0, required-expected) if required is not None else None,
                           'edit_token':demand['edit_token'] if demand else ''})
        output.append({'department':department,'profession':profession,'days':points})
    return output


def register_workforce_operations(app, get_db, roles_required):
    def db_get():
        db = get_db()
        if getattr(db,'dialect',None) != 'postgres':
            abort(404)
        return db

    def selected_day():
        return date_value(request.args.get('date'), required=True)

    @app.get('/api/workforce/readiness/<int:worker_id>')
    @roles_required(*READERS)
    def workforce_readiness(worker_id):
        db = db_get(); day = selected_day()
        with db:
            db.execute('BEGIN')
            require_worker(db,worker_id)
            actor, rows = population(db,day,worker_id=worker_id)
            if not rows:
                abort(404)
            data = visible_related(actor,related(db,[worker_id]))
            result = readiness(rows[0],data['movements'][worker_id],data['documents'][worker_id],day)
            if actor['role'] in ('foreman','viewer'):
                result['stage_reason'] = None
                result['note'] += ' Проверка документов доступна кадровой службе.'
            result['assignments'] = plain(db.native('''SELECT a.work_date date,a.shift,o.name object_name,s.name subobject_name
                FROM assignments a JOIN subobjects s ON s.id=a.subobject_id JOIN objects o ON o.id=s.object_id
                WHERE a.worker_id=%s AND a.work_date=%s ORDER BY a.shift''',(worker_id,day)).fetchall())
        return jsonify(date=day,worker_id=worker_id,**result)

    @app.get('/api/workforce/operations')
    @roles_required(*READERS)
    def workforce_operations():
        db = db_get(); day = selected_day()
        department = text_value(request.args.get('department',''),'СМУ',300)
        try:
            days = int(request.args.get('days',14)); offset = int(request.args.get('offset',0))
            group_offset=int(request.args.get('group_offset',0))
            group_limit=int(request.args['group_limit']) if 'group_limit' in request.args else None
            if days not in (7,14,30) or offset < 0 or offset > 100000:
                raise ValueError()
            if group_offset < 0 or (group_limit is not None and not 1 <= group_limit <= 50):
                raise ValueError()
        except ValueError:
            abort(400,description='Выберите период 7, 14 или 30 дней и допустимую страницу.')
        with db:
            db.execute('BEGIN')
            db.native("SET LOCAL statement_timeout='5000ms'")
            actor, rows = population(db,day,department)
            data = visible_related(actor,related(db,[r['id'] for r in rows]))
            items = [i for p in rows for i in issues_for(p,data,day)]
            keys = [i['key'] for i in items]
            ownership = {r['issue_key']:plain(r) for r in db.native('''SELECT a.*,u.full_name owner_name
                FROM workforce_attention_owners a JOIN users u ON u.id=a.owner_id
                WHERE a.issue_key=ANY(%s)''',(keys,)).fetchall()} if keys else {}
            for item in items:
                item.update(ownership.get(item['key'],{}))
                item['overdue'] = str(item['due_date']) < day
            counts = dict(Counter(i['kind'] for i in items))
            kind = request.args.get('kind','')
            if kind:
                items = [i for i in items if i['kind']==kind]
            if request.args.get('mine') == '1':
                items = [i for i in items if i.get('owner_id')==actor['id']]
            items.sort(key=lambda i:(not i['overdue'],i['due_date'],i['full_name'],i['key']))
            end = (date.fromisoformat(day)+timedelta(days=days-1)).isoformat()
            allowed = allowed_departments(db,actor)
            demands = [plain(r) for r in db.native('SELECT * FROM workforce_demand WHERE day BETWEEN %s AND %s',(day,end)).fetchall()
                       if (allowed is None or r['department'] in allowed) and (not department or r['department']==department)]
            departments = sorted({r['department'] for r in rows}|{r['department'] for r in demands})
            plan_departments = sorted({r[0] for r in db.native("SELECT name FROM smu_catalog UNION SELECT DISTINCT department FROM workers WHERE department<>'' UNION SELECT DISTINCT department FROM workforce_demand").fetchall()
                                       if allowed is None or r[0] in allowed})
            group_keys=sorted({(r['department'],r['profession']) for r in rows}|{(r['department'],r['profession']) for r in demands})
            group_total=len(group_keys)
            if group_limit is not None:
                group_offset=min(group_offset,max(0,(group_total-1)//group_limit)*group_limit)
                selected=set(group_keys[group_offset:group_offset+group_limit])
                forecast_rows=[r for r in rows if (r['department'],r['profession']) in selected]
                forecast_demands=[r for r in demands if (r['department'],r['profession']) in selected]
            else:
                forecast_rows,forecast_demands=rows,demands
            points = forecast(forecast_rows,data,day,days,forecast_demands) if request.args.get('forecast','1')=='1' else []
        return jsonify(date=day,days=days,rows=items[offset:offset+50],total=len(items),offset=offset,counts=counts,
                       forecast=points,forecast_total=group_total,forecast_offset=group_offset,
                       departments=departments,plan_departments=plan_departments,can_claim=actor['role'] in ADMINS|{'rotation','recruitment'},
                       can_confirm_departure=actor['role'] in ADMINS|{'rotation'},
                       can_plan=actor['role'] in ADMINS,actor_id=actor['id'],
                       note='Подтверждённый состав — явка на начальную дату. Прогноз учитывает запланированные поездки на объект и выезды. Билеты не подтверждают фактический заезд. Неоднозначные поездки в один день пропущены. Потребность задаётся отдельно; пустое значение не равно нулю.')

    @app.post('/api/workforce/operations/departure')
    @roles_required(*ADMINS, 'rotation')
    def workforce_departure_confirm():
        from workforce_api import payload
        from workforce_presence import sync_operation_presence
        fields = {'date', 'worker_id', 'key', 'token', 'actual_date', 'reason', 'request_key'}
        body = payload(fields, fields)
        wid = body['worker_id']
        if type(wid) is not int or not 0 < wid < 2**63:
            abort(400, description='Выберите сотрудника.')
        day = date_value(body['date'], required=True)
        actual = date_value(body['actual_date'], 'Фактическая дата выезда', True)
        today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        if actual > min(day, today):
            abort(400, description='Фактическая дата выезда не может быть позже отчётной даты или сегодняшнего дня.')
        key = text_value(body['key'], 'Ситуация', 100, True)
        token = text_value(body['token'], 'Версия данных', 64, True)
        if len(token) != 64:
            abort(400, description='Обновите список перед подтверждением выезда.')
        reason = text_value(body.get('reason', ''), 'Основание подтверждения', 10000)
        db = db_get()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, wid, 'movement')
            # Serialize confirmation with stage/ticket/profile edits for this worker.
            locked = db.native('''SELECT w.active FROM workers w JOIN workforce_profiles p ON p.worker_id=w.id
                WHERE w.id=%s FOR UPDATE OF w,p''', (wid,)).fetchone()
            if not locked or not locked['active']:
                abort(409, description='Сотрудник выведен из состава. Обновите список.')
            operation = f'confirm-departure:{wid}'
            previous = replay(db, actor, operation, body)
            if previous is not None:
                return jsonify(previous)
            for table in ('workforce_movements', 'workforce_rotations'):
                db.native(f'SELECT id FROM {table} WHERE worker_id=%s ORDER BY id FOR UPDATE', (wid,)).fetchall()
            _, people = population(db, day, worker_id=wid)
            data = related(db, [wid])
            item = next((item for item in departures_for(people[0], data, day) if item['key'] == key), None) if people else None
            if not item or item['departure']['token'] != token:
                abort(409, description='Дата выезда, вахта или статус уже изменены. Обновите «Мой день».')
            selected = item['departure']
            if actual < selected['min_date']:
                abort(400, description='Выезд не может быть раньше текущего статуса или начала вахты.')
            catalog_value(db, 'stage.leave', 'stage', True)
            catalog_value(db, 'result.happened', 'result', True)
            before = None
            if selected['movement_id']:
                before = plain(db.native('SELECT * FROM workforce_movements WHERE id=%s', (selected['movement_id'],)).fetchone())
                row = plain(db.native('''UPDATE workforce_movements SET actual_date=%s,result_code='result.happened',
                    edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''',
                    (actual, actor['id'], before['id'])).fetchone())
            else:
                basis = catalog_value(db, 'basis.schedule', 'basis', True) if selected['rotation_id'] else None
                row = plain(db.native('''INSERT INTO workforce_movements
                    (worker_id,direction,destination_kind,planned_date,actual_date,basis_code,result_code,created_by,updated_by,request_key)
                    VALUES (%s,'departure','home',%s,%s,%s,'result.happened',%s,%s,%s) RETURNING *''',
                    (wid, selected['planned_date'], actual, basis, actor['id'], actor['id'], body['request_key'])).fetchone())
            row = sync_operation_presence(db, actor, 'movement', before, row, reason)
            audit(db, actor, 'update' if before else 'create', 'movement', row['id'], before, row, worker_id=wid, reason=reason)
            if selected['rotation_id']:
                rotation = plain(db.native('SELECT * FROM workforce_rotations WHERE id=%s', (selected['rotation_id'],)).fetchone())
                closed = plain(db.native('''UPDATE workforce_rotations SET actual_end_date=%s,
                    edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''',
                    (actual, actor['id'], rotation['id'])).fetchone())
                audit(db, actor, 'close', 'rotation', rotation['id'], rotation, closed, worker_id=wid, reason=reason)
            result = {'worker_id': wid, 'actual_date': actual, 'stage_code': 'stage.leave',
                      'movement_id': row['id'], 'stage_event_id': row['stage_event_id']}
            remember(db, actor, operation, body, result)
        return jsonify(result)

    @app.post('/api/workforce/operations/claim')
    @roles_required(*READERS)
    def workforce_attention_claim():
        db = db_get(); body = request.get_json(silent=True) or {}
        if not isinstance(body,dict):
            abort(400)
        day = date_value(body.get('date'),required=True)
        due = date_value(body.get('due_date'),required=True)
        key = text_value(body.get('key'),'Ситуация',100,True)
        wid = body.get('worker_id')
        if type(wid) is not int or wid < 1:
            abort(400,description='Выберите сотрудника.')
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db,wid)
            actor_scope(db,write=True)
            _, rows = population(db,day,worker_id=wid)
            if not rows or key not in {i['key'] for i in issues_for(rows[0],related(db,[wid]),day)}:
                abort(409,description='Ситуация уже изменилась. Обновите список.')
            before = db.native('SELECT * FROM workforce_attention_owners WHERE issue_key=%s FOR UPDATE',(key,)).fetchone()
            if str(before['edit_token'] if before else '') != body.get('expected_token',''):
                abort(409,description='Ответственный уже изменён. Обновите список.')
            if before and before['owner_id'] != actor['id'] and actor['role'] not in ADMINS:
                abort(409,description='Ситуация уже закреплена за другим сотрудником.')
            after = db.native('''INSERT INTO workforce_attention_owners(issue_key,worker_id,owner_id,due_date)
                VALUES (%s,%s,%s,%s) ON CONFLICT(issue_key) DO UPDATE SET owner_id=EXCLUDED.owner_id,
                due_date=EXCLUDED.due_date,edit_token=gen_random_uuid(),updated_at=now() RETURNING *''',
                (key,wid,actor['id'],due)).fetchone()
            audit(db,actor,'claim','attention',key,before,after,worker_id=wid)
        return jsonify(plain(after))

    @app.post('/api/workforce/demand')
    @roles_required(*ADMINS)
    def workforce_demand_save():
        db=db_get(); body=request.get_json(silent=True) or {}
        if not isinstance(body,dict):
            abort(400)
        day=date_value(body.get('date'),required=True)
        department=text_value(body.get('department'),'СМУ',300,True)
        profession=text_value(body.get('profession'),'Профессия',300,True)
        required=body.get('required')
        if type(required) is not int or not 0 <= required <= 100000:
            abort(400,description='Потребность: целое число от 0 до 100000.')
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, rows=population(db,day,department)
            if actor['role'] not in ADMINS:
                abort(403)
            allowed=allowed_departments(db,actor)
            if allowed is not None and department not in allowed:
                abort(403,description='Нет доступа к этому СМУ.')
            known_department=db.native('''SELECT name FROM smu_catalog WHERE name=%s UNION
                SELECT department FROM workers WHERE department=%s UNION
                SELECT department FROM workforce_demand WHERE department=%s LIMIT 1''',(department,department,department)).fetchone()
            known_profession=db.native("SELECT 1 FROM workforce_catalog WHERE kind='profession' AND active AND label=%s",(profession,)).fetchone()
            if not known_department or (not known_profession and not any(r['profession']==profession for r in rows)):
                abort(400,description='Выберите СМУ и профессию из справочников.')
            before=db.native('SELECT * FROM workforce_demand WHERE day=%s AND department=%s AND profession=%s FOR UPDATE',(day,department,profession)).fetchone()
            if str(before['edit_token'] if before else '') != body.get('expected_token',''):
                abort(409,description='Потребность уже изменена. Обновите прогноз.')
            after=db.native('''INSERT INTO workforce_demand(day,department,profession,required,updated_by)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(day,department,profession) DO UPDATE SET
                required=EXCLUDED.required,updated_by=EXCLUDED.updated_by,edit_token=gen_random_uuid(),updated_at=now()
                RETURNING *''',(day,department,profession,required,actor['id'])).fetchone()
            audit(db,actor,'update','demand',f'{day}:{department}:{profession}',before,after)
        return jsonify(plain(after))
