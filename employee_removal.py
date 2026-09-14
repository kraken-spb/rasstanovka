"""Dismiss employees while retaining their identity and historical placement."""
import hashlib
from user_smu_access import require_workers
import json
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import abort, g, jsonify, request


def migrate_employee_removals(db):
    db.execute("""CREATE TABLE IF NOT EXISTS employee_removals (
        worker_id INTEGER PRIMARY KEY REFERENCES workers(id), effective_date TEXT NOT NULL,
        reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
        changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL,
        worker_name TEXT NOT NULL, personnel_no TEXT NOT NULL,
        crew_id INTEGER REFERENCES crews(id), crew_name TEXT NOT NULL,
        actor_name TEXT NOT NULL, actor_username TEXT NOT NULL,
        removed_assignments_json TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS employee_restorations (
        id INTEGER PRIMARY KEY, worker_id INTEGER NOT NULL REFERENCES workers(id),
        restored_date TEXT NOT NULL, changed_at TEXT NOT NULL, changed_by INTEGER NOT NULL REFERENCES users(id),
        worker_name TEXT NOT NULL, personnel_no TEXT NOT NULL, crew_id INTEGER REFERENCES crews(id),
        crew_name TEXT NOT NULL, actor_name TEXT NOT NULL, actor_username TEXT NOT NULL,
        removal_json TEXT CHECK(removal_json IS NULL OR json_valid(removal_json)),
        edit_token TEXT NOT NULL UNIQUE)""")
    db.execute('CREATE INDEX IF NOT EXISTS idx_employee_restorations_worker ON employee_restorations(worker_id,id)')
    db.execute("""CREATE VIEW IF NOT EXISTS employee_inactive_periods AS
        SELECT worker_id,effective_date,NULL restored_date FROM employee_removals
        UNION ALL SELECT worker_id,json_extract(removal_json,'$.effective_date'),restored_date
        FROM employee_restorations WHERE removal_json IS NOT NULL""")


def restoration_token(worker, removal, last_restoration):
    return hashlib.sha256(json.dumps([dict(worker), dict(removal) if removal else None, last_restoration],
        ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def register_employee_removal_routes(app, get_db, roles_required, utc_now):
    def work_day(raw):
        try:
            day = date.fromisoformat(str(raw))
            today = datetime.fromisoformat(utc_now()).astimezone(timezone(timedelta(hours=3))).date()
            if day > today:
                raise ValueError()
            return day.isoformat()
        except (ValueError, TypeError):
            abort(400, description='Укажите дату увольнения не позже сегодняшней.')

    def snapshot(db, worker_id, day):
        worker = db.execute("""SELECT w.*,m.crew_id,c.name crew_name FROM workers w
            LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
            WHERE w.id=?""", (worker_id,)).fetchone()
        if worker is None:
            abort(404, description='Сотрудник не найден.')
        require_workers(db, [worker_id])
        if not worker['active'] or db.execute('SELECT 1 FROM employee_removals WHERE worker_id=?', (worker_id,)).fetchone():
            abort(409, description='Сотрудник уже удалён или отключён. Обновите список.')
        assignments = [dict(r) for r in db.execute('SELECT * FROM assignments WHERE worker_id=? AND work_date>=? ORDER BY id', (worker_id, day))]
        last = db.execute('SELECT edit_token FROM employee_restorations WHERE worker_id=? ORDER BY id DESC LIMIT 1', (worker_id,)).fetchone()
        token = hashlib.sha256(json.dumps([day, dict(worker), assignments, last[0] if last else None], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        return worker, assignments, token

    @app.post('/api/employees/<int:worker_id>/restore')
    @roles_required('admin')
    def restore_employee(worker_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('expected_token'), str):
            abort(400, description='Обновите список и выберите сотрудника для восстановления.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
                abort(403, description='Восстанавливать сотрудников может только администратор.')
            worker = db.execute('SELECT * FROM workers WHERE id=?', (worker_id,)).fetchone()
            if worker is None:
                abort(404, description='Сотрудник не найден.')
            require_workers(db, [worker_id], allow_unowned=True)
            if worker['active']:
                abort(409, description='Сотрудник уже действующий. Обновите список.')
            removal = db.execute('SELECT * FROM employee_removals WHERE worker_id=?', (worker_id,)).fetchone()
            last = db.execute('SELECT edit_token FROM employee_restorations WHERE worker_id=? ORDER BY id DESC LIMIT 1', (worker_id,)).fetchone()
            if payload['expected_token'] != restoration_token(worker, removal, last[0] if last else None):
                abort(409, description='Данные сотрудника изменились. Обновите список и повторите восстановление.')
            crew = db.execute('''SELECT c.id,c.name FROM crew_members m JOIN crews c ON c.id=m.crew_id
                WHERE m.worker_id=?''', (worker_id,)).fetchone()
            stamp = utc_now()
            day = datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=3))).date().isoformat()
            db.execute('''INSERT INTO employee_restorations(worker_id,restored_date,changed_at,changed_by,
                worker_name,personnel_no,crew_id,crew_name,actor_name,actor_username,removal_json,edit_token)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (worker_id, day, stamp, actor['id'], worker['full_name'],
                worker['personnel_no'], crew['id'] if crew else None, crew['name'] if crew else '',
                actor['full_name'], actor['username'], json.dumps(dict(removal), ensure_ascii=False) if removal else None,
                secrets.token_hex(16)))
            db.execute('DELETE FROM employee_removals WHERE worker_id=?', (worker_id,))
            db.execute('UPDATE workers SET active=1 WHERE id=?', (worker_id,))
        return jsonify({'worker_id': worker_id, 'restored': True, 'date': day})

    @app.get('/api/employees/<int:worker_id>/removal-preview')
    @roles_required('admin')
    def removal_preview(worker_id):
        day = work_day(request.args.get('date', ''))
        db = get_db()
        db.execute('BEGIN')
        worker, assignments, token = snapshot(db, worker_id, day)
        return jsonify({'worker_id': worker_id, 'full_name': worker['full_name'], 'personnel_no': worker['personnel_no'],
                        'date': day, 'assignment_count': len(assignments), 'expected_token': token})

    @app.delete('/api/employees/<int:worker_id>')
    @roles_required('admin')
    def remove_employee(worker_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description='Укажите дату и причину удаления.')
        reason = payload.get('reason')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            abort(400, description='Укажите причину удаления: от 1 до 1000 символов.')
        day = work_day(payload.get('date', ''))
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
                abort(403, description='Удалять сотрудников может только администратор.')
            worker, assignments, token = snapshot(db, worker_id, day)
            if payload.get('expected_token') != token:
                abort(409, description='Данные сотрудника или назначения изменились. Проверьте удаление заново.')
            stamp = utc_now()
            db.execute("""INSERT INTO employee_removals(worker_id,effective_date,reason,changed_by,changed_at,
                worker_name,personnel_no,crew_id,crew_name,actor_name,actor_username,removed_assignments_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (worker_id, day, reason.strip(), actor['id'], stamp,
                worker['full_name'], worker['personnel_no'], worker['crew_id'], worker['crew_name'] or '',
                actor['full_name'], actor['username'], json.dumps(assignments, ensure_ascii=False)))
            db.execute('DELETE FROM assignments WHERE worker_id=? AND work_date>=?', (worker_id, day))
            db.execute('UPDATE workers SET active=0 WHERE id=?', (worker_id,))
        return jsonify({'worker_id': worker_id, 'removed': True, 'date': day, 'cleared_assignments': len(assignments)})
