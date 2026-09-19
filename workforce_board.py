"""Confirmed kanban transitions over the existing, append-only presence history."""
from uuid import uuid4

from flask import abort, jsonify, request

from workforce_core import (ADMINS, EDITORS, actor_scope, audit, catalog_value,
                            date_value, digest, plain, remember, replay, text_value)


STAGES = ('stage.leave', 'stage.inbound', 'stage.pvp', 'stage.onsite', 'stage.outbound')


def transition_targets(role, current, active=True):
    if not active:
        return []
    if role in ADMINS | {'rotation'}:
        allowed = STAGES
    elif role == 'recruitment':
        # The recruiting service records arrival at / departure from PVP.
        allowed = ('stage.pvp', 'stage.inbound') if current == 'stage.pvp' else ('stage.pvp',)
    else:
        allowed = ()
    return [code for code in allowed if code != current]


def stage_token(worker_id, day, revision):
    # Include all events, even corrections and future ones, to detect ABA changes.
    return digest({'worker_id': worker_id, 'date': day, 'revision': revision})


def prepare_ticket(db, ticket_values, stage_code, effective_date, previous_stage=None, worker_id=None):
    """Validate a board-purchased ticket without turning a plan into a fact."""
    values = dict(ticket_values)
    recognition = values.pop('recognition', None)
    if recognition is not None:
        from ticket_import import confirm_values
        recognition = confirm_values(recognition)
    if stage_code == 'stage.inbound':
        values.update(direction='arrival', destination_kind='site', actual_date=None,
                      basis_code='basis.ticket', result_code=None)
    elif stage_code == 'stage.pvp':
        values.update(direction='arrival', destination_kind='pvp', actual_date=None, basis_code='basis.ticket', result_code=None)
    elif stage_code == 'stage.outbound':
        values.update(direction='departure', destination_kind='home', actual_date=None,
                      basis_code='basis.ticket', result_code=None)
    elif stage_code == 'stage.onsite' and previous_stage == 'stage.leave':
        # Retain the original direct-arrival contract for existing board callers.
        values.update(direction='arrival', destination_kind='site', actual_date=effective_date,
                      basis_code='basis.ticket', result_code='result.happened')
    else:
        abort(400, description='Билет в этом окне доступен при переходе в «Заезд», «ПВП» или «Выезд».')
    from workforce_api import ENTITIES, validate_fields, validate_entity
    clean = validate_fields(db, values, ENTITIES['movement']['fields'],
                            {'planned_date', 'travel_details'})
    validate_entity('movement', clean)
    if recognition:
        expected = recognition['segments'][-1]['arrival_date'] if clean['direction'] == 'arrival' else recognition['segments'][0]['departure_date']
        if (worker_id is not None and recognition['worker_id'] != worker_id) or recognition['direction'] != clean['direction'] or clean['planned_date'] != expected:
            abort(400, description='Распознанный билет не соответствует переходу.')
        clean['_recognition'] = recognition
    return clean


def record_ticket(db, actor, worker_id, stage_event_id, clean, reason):
    movement = plain(db.native('''INSERT INTO workforce_movements
        (worker_id,direction,destination_kind,planned_date,actual_date,basis_code,result_code,
         origin_code,destination_code,travel_details,stage_event_id,created_by,updated_by,request_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *''', (worker_id, clean['direction'], clean['destination_kind'], clean['planned_date'],
            clean['actual_date'], clean['basis_code'], clean['result_code'], clean.get('origin_code'),
            clean.get('destination_code'), clean['travel_details'], stage_event_id, actor['id'], actor['id'],
            str(uuid4()))).fetchone())
    if clean.get('_recognition'):
        from ticket_import import link_recognition, recognition_values
        item, job, identity = recognition_values(db, actor, clean['_recognition'], worker_id, clean['direction'])
        movement = plain(db.native('UPDATE workforce_movements SET origin=%s,destination=%s WHERE id=%s RETURNING *', (item['segments'][0]['origin'], item['segments'][-1]['destination'], movement['id'])).fetchone())
        link_recognition(db, actor, item, job, identity, movement, reason)
    audit(db, actor, 'create', 'movement', movement['id'], None, movement, worker_id=worker_id, reason=reason)
    return movement


def register_board_routes(app, database, roles_required):
    @app.post('/api/workforce/transitions')
    @roles_required(*EDITORS)
    def workforce_transition():
        data = request.get_json(silent=True)
        fields = {'date', 'effective_date', 'stage_code', 'reason', 'people', 'request_key'}
        if not isinstance(data, dict) or set(data) - fields - {'tickets'} or (fields - {'reason'}) - set(data):
            abort(400, description='Неверный состав полей перемещения.')
        day = date_value(data['date'], required=True)
        effective = date_value(data['effective_date'], required=True)
        reason = text_value(data.get('reason', ''), 'Основание изменения', 10000)
        people = data['people']
        if not isinstance(people, list) or not 1 <= len(people) <= 100:
            abort(400, description='Выберите от 1 до 100 сотрудников.')
        expected = {}
        for person in people:
            if (not isinstance(person, dict) or set(person) != {'id', 'token'}
                    or type(person['id']) is not int or not 0 < person['id'] < 2**63
                    or not isinstance(person['token'], str) or len(person['token']) != 64
                    or person['id'] in expected):
                abort(400, description='Неверный или повторяющийся сотрудник в выборе. Обновите доску.')
            expected[person['id']] = person['token']
        tickets = data.get('tickets', [])
        ticket_fields = {'worker_id', 'planned_date', 'travel_details', 'origin_code', 'destination_code', 'recognition'}
        tickets_by_worker = {}
        if not isinstance(tickets, list) or len(tickets) > len(expected):
            abort(400, description='Некорректный список билетов.')
        for ticket in tickets:
            if (not isinstance(ticket, dict) or set(ticket) - ticket_fields
                    or {'worker_id', 'planned_date', 'travel_details'} - set(ticket)
                    or type(ticket['worker_id']) is not int or ticket['worker_id'] not in expected
                    or ticket['worker_id'] in tickets_by_worker):
                abort(400, description='Укажите один билет на выбранного сотрудника.')
            tickets_by_worker[ticket['worker_id']] = ticket
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, scope, args = actor_scope(db, write=True)
            code = catalog_value(db, data['stage_code'], 'stage', True)
            if code not in STAGES:
                abort(400, description='Выберите доступный этап доски.')
            if actor['role'] == 'recruitment' and code not in ('stage.pvp', 'stage.inbound'):
                abort(403, description='Комплектация ведёт прибытие в ПВП и выезд из ПВП. Остальные переходы ведёт перевахта.')
            # Deterministic locks and complete authorization precede both replay and writes.
            workers = db.native(f'''SELECT w.id,w.active FROM workers w
                JOIN workforce_profiles p ON p.worker_id=w.id
                WHERE w.id=ANY(%s) AND ({scope}) ORDER BY w.id FOR UPDATE OF w,p''',
                [list(expected), *args]).fetchall()
            if len(workers) != len(expected):
                abort(404, description='Один из сотрудников не найден в доступных СМУ. Обновите доску.')
            if any(not row['active'] for row in workers):
                abort(409, description='В выборе есть сотрудник, выведенный из состава. Обновите доску.')
            previous = replay(db, actor, 'board-transition', data)
            if previous is not None:
                return jsonify(previous)
            states = plain(db.native('''SELECT w.id worker_id,st.id,st.stage_code,st.effective_date,
                COALESCE((SELECT max(v.sequence) FROM workforce_stage_events v WHERE v.worker_id=w.id),0) revision
                FROM workers w LEFT JOIN LATERAL (
                    SELECT e.id,e.stage_code,e.effective_date FROM workforce_stage_events e
                    WHERE e.worker_id=w.id AND e.confirmed AND NOT e.retracted AND e.effective_date<=%s
                    AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r WHERE r.replaces_id=e.id
                        AND (r.confirmed OR r.retracted) AND r.effective_date<=%s)
                    ORDER BY e.effective_date DESC,e.sequence DESC LIMIT 1
                ) st ON TRUE WHERE w.id=ANY(%s) ORDER BY w.id''', (day, day, list(expected))).fetchall())
            # Check every row before changing any row: a stale bulk selection is atomic.
            for state in states:
                if stage_token(state['worker_id'], day, state['revision']) != expected[state['worker_id']]:
                    abort(409, description='Этап одного из сотрудников уже изменён. Обновите доску и повторите выбор.')
                if code not in transition_targets(actor['role'], state['stage_code']):
                    abort(403, description='Этот переход недоступен вашей службе или сотрудник уже находится на выбранном этапе.')
                if state['effective_date'] and effective < state['effective_date']:
                    abort(400, description='Дата перехода раньше текущего этапа. Для исправления истории откройте карточку сотрудника.')
            # Validate every ticket before any event is inserted. A failed ticket must
            # not leave a partially applied bulk transition behind.
            clean_tickets = {}
            for state in states:
                ticket = tickets_by_worker.get(state['worker_id'])
                if ticket is None:
                    continue
                values = {key: value for key, value in ticket.items() if key != 'worker_id'}
                clean_tickets[state['worker_id']] = prepare_ticket(
                    db, values, code, effective, state['stage_code'], worker_id=state['worker_id'])
            for before in states:
                worker_id = before['worker_id']
                after = plain(db.native('''INSERT INTO workforce_stage_events
                    (worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
                    VALUES (%s,%s,%s,TRUE,%s,%s,%s) RETURNING *''',
                    (worker_id, code, effective, reason, actor['id'], str(uuid4()))).fetchone())
                audit(db, actor, 'transition', 'stage', after['id'], before, after,
                      worker_id=worker_id, reason=reason)
                if worker_id in clean_tickets:
                    record_ticket(db, actor, worker_id, after['id'], clean_tickets[worker_id], reason)
            if code == 'stage.onsite':
                db.native('''UPDATE workforce_profiles SET staffing_ready=TRUE
                    WHERE worker_id=ANY(%s) AND NOT staffing_ready''', (list(expected),))
            result = {'changed': len(states), 'stage_code': code, 'effective_date': effective}
            remember(db, actor, 'board-transition', data, result)
        return jsonify(result), 201

