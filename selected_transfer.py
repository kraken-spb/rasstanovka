"""Preview and copy the previous day's placement for explicitly selected workers."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman
import hashlib
import json
import secrets
import sqlite3
from datetime import date, timedelta

from flask import abort, g, jsonify, request
from backup_api import create_backup
from staffing_shifts import canonical_shift, responsibility_states


TABLES = ('assignments', 'staffing_shifts', 'staffing_attendance', 'staffing_performed_work')


def migrate_selected_transfer(db):
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_inherited_rows (
        work_date TEXT NOT NULL, worker_id INTEGER NOT NULL REFERENCES workers(id),
        source_date TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        PRIMARY KEY(work_date,worker_id))''')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def records_for(db, day, ids):
    return {table: [dict(r) for r in db.execute(
        f"SELECT * FROM {table} WHERE work_date=? AND worker_id IN ({','.join('?' for _ in ids)}) ORDER BY worker_id",
        [day, *ids])] for table in (*TABLES, 'assignment_events', 'staffing_inherited_rows')}


def transfer_plan(db, data, actor):
    if not isinstance(data, dict):
        abort(400, description='Выберите дату и сотрудников для переноса.')
    try:
        target = date.fromisoformat(str(data.get('date', '')))
        source = (target - timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        abort(400, description='Укажите дату с существующим предыдущим днём.')
    day, ids = target.isoformat(), data.get('worker_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
        abort(400, description='Отметьте сотрудников для переноса.')
    ids = sorted(set(ids))
    from gdlr_api import staffing_eligible_sql
    workers = [dict(r) for r in db.execute(f'''SELECT w.id,w.full_name,w.personnel_no,w.active,
        {staffing_eligible_sql()} staffing_eligible,
        m.crew_id,c.owner_user_id,EXISTS(SELECT 1 FROM employee_inactive_periods er
            WHERE er.worker_id=w.id AND er.effective_date<=? AND (er.restored_date IS NULL OR er.restored_date>?)) removed
        FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        WHERE w.id IN ({','.join('?' for _ in ids)}) ORDER BY w.id''', [day, day, *ids])]
    if len(workers) != len(ids):
        abort(409, description='Список сотрудников изменился. Обновите расстановку.')
    if set(ids) - allowed_workers(db, ids, actor):
        abort(403, description='Можно переносить только сотрудников своих бригад.')
    previous, current = records_for(db, source, ids), records_for(db, day, ids)
    by_worker = {i: [] for i in ids}
    for row in previous['assignments']:
        by_worker[row['worker_id']].append(row)
    occupied = {r['worker_id'] for rows in current.values() for r in rows}
    sites = {r['id']: dict(r) for r in db.execute('''SELECT s.id,s.name,o.name object_name
        FROM subobjects s JOIN objects o ON o.id=s.object_id''')}
    result = []
    for worker in workers:
        assignments, reason = by_worker[worker['id']], ''
        shifts = [canonical_shift(r['shift']) for r in assignments]
        if not worker['active'] or worker['removed']:
            reason = 'Сотрудник отключён или снят с учёта'
        elif not worker['staffing_eligible']:
            reason = 'Категория ГДЛР не допускается в расстановку'
        elif worker['id'] in occupied:
            reason = 'На выбранную дату уже есть данные или изменения'
        elif not assignments:
            reason = 'На предыдущую дату нет расстановки'
        elif len(shifts) != len(set(shifts)) or any(s not in ('1 смена', '2 смена') for s in shifts):
            reason = 'Неоднозначная смена на предыдущую дату'
        elif any(r['crew_id'] not in (None, worker['crew_id']) or
                (legacy_foreman(db, actor) and r['crew_id'] is None and r['foreman_user_id'] != actor['id']) for r in assignments):
            reason = 'Бригада отличается от предыдущего дня'
        elif any(r['subobject_id'] not in sites for r in assignments):
            reason = 'Подобъект предыдущего дня недоступен'
        result.append({'id': worker['id'], 'full_name': worker['full_name'], 'personnel_no': worker['personnel_no'],
            'ready': not reason, 'reason': reason, 'assignments': [
                {'shift': canonical_shift(r['shift']), 'object': sites.get(r['subobject_id'], {}).get('object_name', ''),
                 'subobject': sites.get(r['subobject_id'], {}).get('name', '')} for r in assignments]})
    token = digest([day, source, actor['id'], actor['role'], workers, previous, current,
                    responsibility_states(db, ids), sites])
    public = {'date': day, 'source_date': source, 'rows': result, 'ready': sum(r['ready'] for r in result),
              'skipped': sum(not r['ready'] for r in result), 'expected_token': token}
    return public, previous, {r['id']: r for r in workers}


def register_selected_transfer(app, get_db, roles_required, utc_now):
    def actor(db):
        user = db.execute('SELECT id,role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not user or not user['active'] or user['role'] not in ('admin', 'super_admin', 'foreman'):
            abort(403, description='Нет права переносить расстановку.')
        return user

    @app.post('/api/staffing/transfer-selected/preview')
    @roles_required('admin', 'foreman')
    def preview_selected_transfer():
        db = get_db()
        with db:
            db.execute('BEGIN')
            plan, _, _ = transfer_plan(db, request.get_json(silent=True), actor(db))
        return jsonify(plan)

    @app.post('/api/staffing/transfer-selected')
    @roles_required('admin', 'foreman')
    def apply_selected_transfer():
        data, db = request.get_json(silent=True), get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            user = actor(db)
            plan, previous, workers = transfer_plan(db, data, user)
            if data.get('expected_token') != plan['expected_token']:
                abort(409, description='Расстановка или состав сотрудников изменились. Заново откройте форму переноса.')
            ids = {r['id'] for r in plan['rows'] if r['ready']}
            if not ids:
                return jsonify({'copied': 0, 'skipped': plan['skipped'], 'date': plan['date']})
            try:
                create_backup(db, plan['date'], reason='selected-transfer')
            except (OSError, sqlite3.Error):
                abort(503, description='Не удалось создать резервную копию. Перенос не выполнен.')
            day, stamp = plan['date'], utc_now()
            for row in previous['assignments']:
                if row['worker_id'] not in ids: continue
                worker, shift = workers[row['worker_id']], canonical_shift(row['shift'])
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,
                    foreman_user_id,created_at,crew_id,edit_token) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (day, shift, row['subobject_id'], worker['id'], row['employer'], worker['owner_user_id'] if worker['crew_id'] is not None else user['id'],
                     stamp, worker['crew_id'], secrets.token_hex(16)))
                db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,
                    before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,?,?,NULL,?,?,?)''',
                    (worker['crew_id'], worker['id'], day, shift, row['subobject_id'], user['id'], stamp))
            for table, field in [('staffing_shifts', 'shift'), ('staffing_attendance', 'status')]:
                for row in previous[table]:
                    if row['worker_id'] not in ids: continue
                    value = canonical_shift(row[field]) if field == 'shift' else row[field]
                    db.execute(f'INSERT INTO {table}(work_date,worker_id,{field},edit_token,updated_by,updated_at) VALUES (?,?,?,?,?,?)',
                               (day, row['worker_id'], value, secrets.token_hex(16), user['id'], stamp))
            for row in previous['staffing_performed_work']:
                if row['worker_id'] not in ids: continue
                db.execute('''INSERT INTO staffing_performed_work(work_date,worker_id,shift,description,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?,?)''', (day, row['worker_id'], row['shift'], row['description'], secrets.token_hex(16), user['id'], stamp))
            snapshots = {i: {} for i in ids}
            kinds = {'assignments': 'assignment', 'staffing_shifts': 'shift', 'staffing_attendance': 'attendance', 'staffing_performed_work': 'work'}
            copied = records_for(db, day, sorted(ids))
            for table in TABLES:
                for row in copied[table]:
                    kind = kinds[table]
                    key = kind + (':' + canonical_shift(row['shift']) if kind in ('assignment', 'work') else '')
                    snapshots[row['worker_id']].setdefault(key, []).append(digest(row))
            for i, snapshot in snapshots.items():
                for values in snapshot.values(): values.sort()
                db.execute('INSERT INTO staffing_inherited_rows VALUES (?,?,?,?)',
                           (day, i, plan['source_date'], json.dumps(snapshot, sort_keys=True, ensure_ascii=False)))
        return jsonify({'copied': len(ids), 'skipped': plan['skipped'], 'date': day, 'source_date': plan['source_date']})
