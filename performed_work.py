"""Employee work descriptions scoped to a date and the employee's shift."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman
import secrets
import unicodedata
from datetime import date

from flask import abort, g, jsonify, request
from staffing_shifts import day_states, validate_group_snapshot


def migrate_performed_work(db):
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_performed_work (
        work_date TEXT NOT NULL, worker_id INTEGER NOT NULL REFERENCES workers(id),
        shift TEXT NOT NULL CHECK(shift IN ('1 смена','2 смена')), description TEXT NOT NULL,
        edit_token TEXT NOT NULL, updated_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL,
        PRIMARY KEY(work_date,worker_id,shift))''')


def performed_work_states(db, day, ids):
    if not ids:
        return {}
    return {(r['worker_id'], r['shift']): {'performed_work': r['description'], 'performed_work_token': r['edit_token']}
            for r in db.execute(f"SELECT * FROM staffing_performed_work WHERE work_date=? AND worker_id IN ({','.join('?' for _ in ids)})", [day, *ids])}


def register_performed_work_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/performed-work')
    @roles_required('admin', 'foreman')
    def save_performed_work():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные выполняемых работ.')
        try:
            day = date.fromisoformat(str(data.get('date', ''))).isoformat()
        except ValueError:
            abort(400, description='Укажите дату выполнения работ.')
        description = data.get('description')
        if not isinstance(description, str) or len(description) > 2000 or any(
                unicodedata.category(c).startswith('C') and c not in '\n\r\t' for c in description):
            abort(400, description='Введите работы текстом до 2000 символов.')
        description = description.replace('\r\n', '\n').replace('\r', '\n').strip()
        ids = data.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected, expected_days = data.get('expected_tokens'), data.get('expected_day_tokens')
        if any(not isinstance(value, dict) or any(str(i) not in value for i in ids) for value in (expected, expected_days)):
            abort(400, description='Обновите расстановку перед изменением работ.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman'):
                abort(403, description='Нет права изменять выполняемые работы.')
            workers = db.execute(f'''SELECT w.id,w.active,c.owner_user_id FROM workers w
                LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
                WHERE w.id IN ({','.join('?' for _ in ids)})''', ids).fetchall()
            if len(workers) != len(ids) or any(not r['active'] for r in workers):
                abort(409, description='Список сотрудников изменился. Обновите расстановку.')
            if set(ids) - allowed_workers(db, ids):
                abort(403, description='Можно менять работы только сотрудников своих бригад.')
            validate_group_snapshot(db, ids, data)
            daily = day_states(db, day, ids)
            if any(daily[i]['employee_shift'] not in ('1 смена', '2 смена') or
                   expected_days[str(i)] != daily[i]['day_token'] for i in ids):
                abort(409, description='Смена или расстановка сотрудника изменилась. Обновите таблицу.')
            current = performed_work_states(db, day, ids)
            if any(expected[str(i)] != current.get((i, daily[i]['employee_shift']), {}).get('performed_work_token') for i in ids):
                abort(409, description='Выполняемые работы уже изменены. Обновите таблицу.')
            now, result = utc_now(), []
            for i in ids:
                shift, token = daily[i]['employee_shift'], secrets.token_hex(16)
                # Keep an empty record's token so stale editors cannot recreate a cleared value.
                db.execute('''INSERT INTO staffing_performed_work VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(work_date,worker_id,shift) DO UPDATE SET description=excluded.description,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (day, i, shift, description, token, g.user['id'], now))
                result.append({'id': i, 'performed_work': description, 'performed_work_token': token})
        from day_inheritance import freshness_states
        freshness = freshness_states(db, day, ids)
        for row in result:
            row['freshness'] = freshness[row['id']]
        return jsonify({'rows': result, 'saved': len(ids)})
