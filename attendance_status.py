from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Date-bound attendance, independent of retained workplace assignments."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman
import secrets
from datetime import date

from flask import abort, g, jsonify, request

from staffing_shifts import validate_group_snapshot


STATUSES = ('Явка', 'Вых', 'Без сод', 'Больн', 'МО')


def migrate_attendance_status(db):
    db.execute("""CREATE TABLE IF NOT EXISTS staffing_attendance (
        work_date TEXT NOT NULL, worker_id INTEGER NOT NULL REFERENCES workers(id),
        status TEXT NOT NULL CHECK(status IN ('Явка','Вых','Без сод','Больн','МО')),
        edit_token TEXT NOT NULL, updated_by INTEGER NOT NULL REFERENCES users(id),
        updated_at TEXT NOT NULL, PRIMARY KEY(work_date,worker_id))""")


def attendance_states(db, day, ids, records=None):
    result = {i: {'attendance_status': 'Явка', 'attendance_token': None} for i in ids}
    if ids:
        from query_helpers import dated_records
        rows = records['staffing_attendance'] if records is not None else dated_records(db, 'staffing_attendance', day, ids)
        for row in rows:
            result[row['worker_id']] = {'attendance_status': row['status'], 'attendance_token': row['edit_token']}
    return result


def calendar_absences(db, start, end, category):
    params = [start, end]
    category_filter = ''
    if category is not None:
        category_filter = " AND COALESCE(gc.name,w.category,'') IN (" + ','.join('?' for _ in filter_values(category)) + ')'
        params.extend(filter_values(category))
    # One person per date, including people without a workplace assignment.
    return [dict(row) for row in db.execute("""SELECT t.work_date,t.worker_id,t.status,
        w.full_name,w.personnel_no,w.employer,c.name crew_name,
        COALESCE(gc.name,w.category,'') category,
        GROUP_CONCAT(DISTINCT o.name) object_names,
        GROUP_CONCAT(DISTINCT s.name) subobject_names
        FROM staffing_attendance t JOIN workers w ON w.id=t.worker_id
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        LEFT JOIN assignments a ON a.worker_id=t.worker_id AND a.work_date=t.work_date
        LEFT JOIN subobjects s ON s.id=a.subobject_id LEFT JOIN objects o ON o.id=s.object_id
        WHERE t.work_date BETWEEN ? AND ? AND t.status<>'Явка'
        AND NOT EXISTS (SELECT 1 FROM employee_inactive_periods er WHERE er.worker_id=t.worker_id
            AND er.effective_date<=t.work_date AND (er.restored_date IS NULL OR er.restored_date>t.work_date))""" + category_filter +
        " GROUP BY t.work_date,t.worker_id,t.status,w.id,c.name,gc.name ORDER BY t.work_date,t.status,w.full_name,w.id", params)]


def register_attendance_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/status')
    @roles_required('admin', 'foreman')
    def save_attendance_status():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description='Некорректные данные статуса.')
        try:
            day = date.fromisoformat(str(payload.get('date', ''))).isoformat()
        except ValueError:
            abort(400, description='Укажите дату статуса.')
        status = payload.get('status')
        if not isinstance(status, str) or status not in STATUSES:
            abort(400, description='Выберите статус из списка.')
        ids = payload.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected = payload.get('expected_tokens')
        if not isinstance(expected, dict) or any(str(i) not in expected for i in ids):
            abort(400, description='Обновите статусы перед изменением.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman'):
                abort(403, description='Нет права изменять статусы.')
            validate_group_snapshot(db, ids, payload)
            rows = db.execute(f"""SELECT w.id,w.active,c.owner_user_id FROM workers w
                LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
                WHERE w.id IN ({','.join('?' for _ in ids)})""", ids).fetchall()
            if len(rows) != len(ids) or any(not r['active'] for r in rows):
                abort(409, description='Список сотрудников изменился. Обновите таблицу.')
            if set(ids) - allowed_workers(db, ids):
                abort(403, description='Можно менять статусы только своих бригад.')
            current = attendance_states(db, day, ids)
            if any(expected[str(i)] != current[i]['attendance_token'] for i in ids):
                abort(409, description='Статус уже изменён в другом окне. Обновите таблицу.')
            for i in ids:
                db.execute("""INSERT INTO staffing_attendance VALUES (?,?,?,?,?,?)
                    ON CONFLICT(work_date,worker_id) DO UPDATE SET status=excluded.status,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (day, i, status, secrets.token_urlsafe(16), g.user['id'], utc_now()))
        return jsonify({'saved': len(ids), 'date': day, 'status': status})
