"""Rotation schedules and explicit extensions, with immutable audit snapshots."""
from datetime import date, timedelta

from flask import abort, jsonify
from psycopg.types.json import Jsonb

from workforce_core import (ADMINS, actor_scope, audit, date_value, plain, remember, replay,
                            require_worker, text_value, uuid_value)


def planned_dates(start, schedule, end=None):
    if schedule.get('needs_review') or any(schedule.get(key) is None for key in ('onsite_days', 'leave_days', 'travel_days')):
        abort(400, description='В источнике не указаны параметры этого графика. Сначала уточните его в справочнике.')
    start = date.fromisoformat(start)
    end = date.fromisoformat(end) if end else start + timedelta(days=schedule['onsite_days'] - 1)
    if end < start:
        abort(400, description='Окончание вахты раньше её начала.')
    leave_end = end + timedelta(days=schedule['leave_days'])
    return {'start_date': start.isoformat(), 'planned_end_date': end.isoformat(),
            'leave_end_date': leave_end.isoformat(),
            'next_arrival_date': (leave_end + timedelta(days=schedule['travel_days'])).isoformat()}


def register_rotation_routes(app, database, roles_required):
    from workforce_api import payload

    @app.post('/api/workforce/people/<int:worker_id>/rotations/<uuid:rotation_id>/finish')
    @roles_required('admin', 'rotation')
    def workforce_finish_rotation(worker_id, rotation_id):
        data = payload({'action', 'actual_end_date', 'token', 'reason', 'request_key'},
                       {'action', 'token', 'reason', 'request_key'})
        if data['action'] not in ('close', 'cancel'):
            abort(400, description='Выберите завершение или отмену вахты.')
        end = date_value(data.get('actual_end_date'), 'Фактическое окончание', data['action'] == 'close')
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'movement')
            operation = f'rotation-finish:{worker_id}:{rotation_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = plain(db.native('SELECT * FROM workforce_rotations WHERE id=%s AND worker_id=%s', (rotation_id, worker_id)).fetchone())
            if not before:
                abort(404)
            if before['edit_token'] != data['token'] or before['cancelled'] or before['actual_end_date']:
                abort(409, description='Вахта изменена, завершена или отменена. Обновите карточку.')
            if end and end < before['start_date']:
                abort(400, description='Окончание вахты раньше её начала.')
            row = plain(db.native('''UPDATE workforce_rotations SET actual_end_date=%s,cancelled=%s,
                edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''',
                (end if data['action'] == 'close' else None, data['action'] == 'cancel', actor['id'], rotation_id)).fetchone())
            audit(db, actor, data['action'], 'rotation', rotation_id, before, row, worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row)

    @app.post('/api/workforce/rotation-schedules')
    @app.patch('/api/workforce/rotation-schedules/<uuid:schedule_id>')
    @roles_required('admin')
    def workforce_schedule(schedule_id=None):
        data = payload({'name', 'onsite_days', 'leave_days', 'travel_days', 'active', 'token', 'reason', 'request_key'},
                       {'name', 'onsite_days', 'leave_days', 'travel_days', 'reason', 'request_key'} | ({'token'} if schedule_id else set()))
        name = text_value(data['name'], 'Название графика', 200, True)
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        for key, minimum, maximum in [('onsite_days', 1, 366), ('leave_days', 0, 366), ('travel_days', 1, 30)]:
            if type(data[key]) is not int or not minimum <= data[key] <= maximum:
                abort(400, description=f'{key}: укажите целое число от {minimum} до {maximum}.')
        if 'active' in data and type(data['active']) is not bool:
            abort(400, description='Укажите активность графика.')
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _, _ = actor_scope(db, write=True)
            if actor['role'] not in ADMINS:
                abort(403)
            from user_smu_access import require_all
            require_all(db)
            operation = f'rotation-schedule:{schedule_id or "new"}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = None
            if schedule_id:
                before = plain(db.native('SELECT * FROM workforce_rotation_schedules WHERE id=%s', (schedule_id,)).fetchone())
                if not before:
                    abort(404)
                if before['edit_token'] != data['token']:
                    abort(409, description='График изменён. Обновите справочник.')
                row = db.native('''UPDATE workforce_rotation_schedules SET name=%s,onsite_days=%s,leave_days=%s,
                    travel_days=%s,active=%s,needs_review=FALSE,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                    WHERE id=%s RETURNING *''', (name, data['onsite_days'], data['leave_days'], data['travel_days'],
                    data.get('active', before['active']), actor['id'], schedule_id)).fetchone()
            else:
                row = db.native('''INSERT INTO workforce_rotation_schedules(name,onsite_days,leave_days,travel_days,updated_by)
                    VALUES (%s,%s,%s,%s,%s) RETURNING *''',
                    (name, data['onsite_days'], data['leave_days'], data['travel_days'], actor['id'])).fetchone()
            row = plain(row)
            audit(db, actor, 'update' if before else 'create', 'rotation_schedule', row['id'], before, row, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row)

    @app.post('/api/workforce/people/<int:worker_id>/rotations')
    @roles_required('admin', 'rotation')
    def workforce_rotation(worker_id):
        data = payload({'schedule_id', 'start_date', 'planned_end_date', 'notes', 'reason', 'request_key'},
                       {'schedule_id', 'start_date', 'reason', 'request_key'})
        schedule_id = uuid_value(data['schedule_id'])
        start = date_value(data['start_date'], 'Начало вахты', True)
        end = date_value(data.get('planned_end_date'), 'Окончание вахты')
        reason = text_value(data['reason'], 'Основание изменения', 10000, True)
        notes = text_value(data.get('notes', ''), 'Примечания', 10000)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'movement')
            operation = f'rotation:{worker_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            schedule = plain(db.native('SELECT * FROM workforce_rotation_schedules WHERE id=%s AND active', (schedule_id,)).fetchone())
            if not schedule:
                abort(400, description='Выберите действующий график вахтования.')
            dates = planned_dates(start, schedule, end)
            row = plain(db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,
                start_date,planned_end_date,leave_end_date,next_arrival_date,notes,request_key,created_by,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (worker_id, schedule_id, Jsonb(schedule), dates['start_date'], dates['planned_end_date'],
                 dates['leave_end_date'], dates['next_arrival_date'], notes, data['request_key'], actor['id'], actor['id'])).fetchone())
            audit(db, actor, 'create', 'rotation', row['id'], None, row, worker_id=worker_id, reason=reason)
            remember(db, actor, operation, data, row)
        return jsonify(row), 201

    @app.post('/api/workforce/people/<int:worker_id>/rotations/<uuid:rotation_id>/extend')
    @roles_required('admin', 'rotation')
    def workforce_extend(worker_id, rotation_id):
        data = payload({'new_end_date', 'token', 'reason', 'request_key'}, {'new_end_date', 'token', 'reason', 'request_key'})
        end = date_value(data['new_end_date'], 'Новая дата окончания', True)
        reason = text_value(data['reason'], 'Причина продления', 10000, True)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, _ = require_worker(db, worker_id, 'movement')
            operation = f'extend:{worker_id}:{rotation_id}'
            previous = replay(db, actor, operation, data)
            if previous is not None:
                return jsonify(previous)
            before = plain(db.native('SELECT * FROM workforce_rotations WHERE id=%s AND worker_id=%s',
                                     (rotation_id, worker_id)).fetchone())
            if not before:
                abort(404, description='Вахта сотрудника не найдена.')
            if before['edit_token'] != data['token']:
                abort(409, description='Вахта изменена другим пользователем. Обновите карточку.')
            if before['cancelled'] or before['actual_end_date']:
                abort(400, description='Завершённую или отменённую вахту продлить нельзя.')
            if end <= before['planned_end_date']:
                abort(400, description='Новая дата должна быть позже текущего окончания вахты.')
            dates = planned_dates(before['start_date'], before['schedule_snapshot'], end)
            row = plain(db.native('''UPDATE workforce_rotations SET planned_end_date=%s,leave_end_date=%s,
                next_arrival_date=%s,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                WHERE id=%s RETURNING *''', (dates['planned_end_date'], dates['leave_end_date'], dates['next_arrival_date'],
                                           actor['id'], rotation_id)).fetchone())
            audit(db, actor, 'extend', 'rotation', rotation_id, before, row, worker_id=worker_id, reason=reason)
            # Explicit tickets/requests are independent and retain their own reschedule history.
            conflicts = plain(db.native('''SELECT id,direction,planned_date FROM workforce_movements m
                WHERE worker_id=%s AND result_code IS NULL AND planned_date>=%s
                AND NOT EXISTS(SELECT 1 FROM workforce_movements n WHERE n.rescheduled_from=m.id)
                AND ((direction='departure' AND planned_date<%s) OR (direction='arrival' AND planned_date<%s))''',
                (worker_id, before['start_date'], end, dates['next_arrival_date'])).fetchall())
            result = {'rotation': row, 'trip_plans_to_review': conflicts}
            remember(db, actor, operation, data, result)
        return jsonify(result)
