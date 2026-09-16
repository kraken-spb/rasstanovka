"""Date/shift-bound human verification of an immutable placement snapshot."""
import hashlib
import json
import secrets
from datetime import date

from flask import abort, g, jsonify, request
from user_smu_access import worker_clause


def migrate_verification(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS placement_verifications (
            assignment_id INTEGER PRIMARY KEY, work_date TEXT NOT NULL,
            fingerprint TEXT NOT NULL, verified_by INTEGER NOT NULL REFERENCES users(id),
            verified_at TEXT NOT NULL, edit_token TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_placement_verifications_date ON placement_verifications(work_date);
        CREATE TABLE IF NOT EXISTS placement_verification_events (
            id INTEGER PRIMARY KEY, assignment_id INTEGER NOT NULL, work_date TEXT NOT NULL,
            action TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL);
    ''')


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def verification_rows(db, day, shift):
    access, params = worker_clause(db)
    rows = db.execute('''
        SELECT a.id assignment_id,a.worker_id,a.work_date,a.shift source_shift,
            CASE WHEN a.shift='Ночная смена' THEN '2 смена' ELSE a.shift END shift,
            a.subobject_id,a.crew_id assignment_crew_id,a.foreman_user_id,
            a.employer assignment_employer,a.created_at assignment_created_at,a.edit_token assignment_token,
            w.full_name,w.personnel_no,w.active,w.department,w.pps,w.profession,w.gsp_profession,
            COALESCE(ct.name,w.contractor) contractor,ec.edit_token contractor_token,
            COALESCE(gc.name,w.category) category,eg.edit_token category_token,
            s.object_id,s.name subobject_name,o.name object_name,
            m.crew_id,c.name crew_name,c.details_token crew_token,
            COALESCE(d.linear_itr_override,c.linear_itr,'') linear_itr,
            COALESCE(d.brigadier_override,c.brigadier,'') brigadier,d.edit_token responsible_token,
            COALESCE(att.status,'Явка') attendance_status,att.edit_token attendance_token,
            ss.shift employee_shift,ss.edit_token shift_token,
            pw.description performed_work,pw.edit_token work_token
        FROM assignments a JOIN workers w ON w.id=a.worker_id
        JOIN subobjects s ON s.id=a.subobject_id JOIN objects o ON o.id=s.object_id
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        LEFT JOIN staffing_row_details d ON d.worker_id=w.id
        LEFT JOIN employee_contractors ec ON ec.worker_id=w.id LEFT JOIN contractors ct ON ct.id=ec.contractor_id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        LEFT JOIN staffing_attendance att ON att.worker_id=w.id AND att.work_date=a.work_date
        LEFT JOIN staffing_shifts ss ON ss.worker_id=w.id AND ss.work_date=a.work_date
        LEFT JOIN staffing_performed_work pw ON pw.worker_id=w.id AND pw.work_date=a.work_date
            AND pw.shift=CASE WHEN a.shift='Ночная смена' THEN '2 смена' ELSE a.shift END
        WHERE a.work_date=? AND (?='all' OR (CASE WHEN a.shift='Ночная смена' THEN '2 смена' ELSE a.shift END)=?)
            AND (''' + access + ''')
        ORDER BY o.name,s.name,a.shift,w.full_name,w.personnel_no,a.id
    ''', [day, shift, shift, *params]).fetchall()
    marks = {r['assignment_id']: dict(r) for r in db.execute('''
        SELECT v.*,u.full_name verifier_name FROM placement_verifications v
        JOIN users u ON u.id=v.verified_by WHERE v.work_date=?''', (day,))}
    result = []
    for row in rows:
        snapshot = dict(row)
        fingerprint = digest(snapshot)
        mark = marks.get(row['assignment_id'])
        result.append({**snapshot, 'fingerprint':fingerprint, 'expected_token':digest([snapshot,mark]),
            'verification_status':'unverified' if not mark else 'verified' if mark['fingerprint']==fingerprint else 'changed',
            'verified_by':mark['verifier_name'] if mark else None, 'verified_at':mark['verified_at'] if mark else None})
    return result


def register_verification(app, get_db, roles_required, utc_now):
    def period(data):
        try: day = date.fromisoformat(data.get('date','')).isoformat()
        except (ValueError, TypeError): abort(400, description='Укажите дату проверки.')
        shift = data.get('shift','all')
        if shift not in ('all','1 смена','2 смена'): abort(400, description='Выберите смену проверки.')
        return day, shift

    @app.get('/api/staffing/verification')
    @roles_required('admin','foreman')
    def get_verification():
        day, shift = period(request.args)
        db = get_db()
        with db:
            db.execute('BEGIN')
            rows = verification_rows(db, day, shift)
        return jsonify({'date':day,'shift':shift,'rows':rows})

    @app.post('/api/staffing/verification')
    @roles_required('admin','foreman')
    def set_verification():
        data = request.get_json(silent=True)
        if not isinstance(data,dict): abort(400, description='Некорректные данные проверки.')
        day, shift = period(data)
        ids, expected, action = data.get('assignment_ids'), data.get('expected_tokens'), data.get('action')
        if (action not in ('verify','clear') or not isinstance(ids,list) or not ids or len(ids)>2000
                or any(type(i) is not int or i<=0 for i in ids) or len(set(ids))!=len(ids)
                or not isinstance(expected,dict) or any(not isinstance(expected.get(str(i)),str) for i in ids)):
            abort(400, description='Выберите назначения и обновите данные проверки.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?',(g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin','super_admin','foreman'): abort(403)
            current = {r['assignment_id']:r for r in verification_rows(db,day,shift)}
            if any(i not in current or current[i]['expected_token']!=expected[str(i)] for i in ids):
                abort(409, description='Расстановка, права доступа или отметки проверки изменились. Обновите список и проверьте его заново.')
            now = utc_now()
            for i in ids:
                row = current[i]
                if action=='verify':
                    db.execute('''INSERT INTO placement_verifications VALUES (?,?,?,?,?,?)
                        ON CONFLICT(assignment_id) DO UPDATE SET work_date=excluded.work_date,
                        fingerprint=excluded.fingerprint,verified_by=excluded.verified_by,
                        verified_at=excluded.verified_at,edit_token=excluded.edit_token''',
                        (i,day,row['fingerprint'],g.user['id'],now,secrets.token_hex(16)))
                else:
                    db.execute('DELETE FROM placement_verifications WHERE assignment_id=?',(i,))
                db.execute('''INSERT INTO placement_verification_events
                    (assignment_id,work_date,action,snapshot_json,changed_by,changed_at) VALUES (?,?,?,?,?,?)''',
                    (i,day,action,packed(row),g.user['id'],now))
        return jsonify({'updated':len(ids)})
