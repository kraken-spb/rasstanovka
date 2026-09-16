"""Initialize an untouched working date from the previous calendar day."""
import secrets
from user_smu_access import explicit_scope, require_all
import sqlite3
import hashlib
import json
from datetime import date, timedelta

from flask import abort, g, jsonify, request

from backup_api import create_backup
from staffing_shifts import canonical_shift


def migrate_day_inheritance(db):
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_day_inheritance (
        work_date TEXT NOT NULL, scope_key TEXT NOT NULL, source_date TEXT NOT NULL,
        created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
        PRIMARY KEY(work_date,scope_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_inherited_rows (
        work_date TEXT NOT NULL, worker_id INTEGER NOT NULL REFERENCES workers(id),
        source_date TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        PRIMARY KEY(work_date,worker_id))''')


def day_snapshots(db, day, ids):
    """Fingerprint individual dated fields, including edit tokens and explicit empty values."""
    result = {i: {} for i in ids}
    if not ids:
        return result
    for table, kind in (('assignments', 'assignment'), ('staffing_shifts', 'shift'),
                        ('staffing_attendance', 'attendance'), ('staffing_performed_work', 'work')):
        for row in db.execute(f"SELECT * FROM {table} WHERE work_date=? AND worker_id IN ({','.join('?' for _ in ids)})", [day, *ids]):
            key = kind + (':' + canonical_shift(row['shift']) if kind in ('assignment', 'work') else '')
            digest = hashlib.sha256(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            # Keep legacy duplicate shift spellings distinct; never hide one in the fingerprint.
            result[row['worker_id']].setdefault(key, []).append(digest)
    for values in result.values():
        for digests in values.values():
            digests.sort()
    return result


def freshness_states(db, day, ids):
    current = day_snapshots(db, day, ids)
    if not ids:
        return {}
    baselines = {r['worker_id']: r for r in db.execute(
        f"SELECT * FROM staffing_inherited_rows WHERE work_date=? AND worker_id IN ({','.join('?' for _ in ids)})", [day, *ids])}
    legacy = db.execute('SELECT 1 FROM staffing_day_inheritance WHERE work_date=? LIMIT 1', (day,)).fetchone() is not None
    labels = {'assignment': 'Назначение', 'shift': 'Смена', 'attendance': 'Статус явки', 'work': 'Выполняемые работы'}
    def label(key):
        kind, _, shift = key.partition(':')
        return labels[kind] + (' · ' + shift if shift else '')
    result = {}
    for worker_id, values in current.items():
        baseline = baselines.get(worker_id)
        inherited, updated = [], []
        if baseline:
            previous = json.loads(baseline['snapshot_json'])
            for key in sorted(previous.keys() | values.keys()):
                (inherited if key in previous and previous[key] == values.get(key) else updated).append(label(key))
            state = 'mixed' if inherited and updated else 'inherited' if inherited else 'current'
        else:
            # Old day-level markers cannot establish which individual rows were copied.
            state = ('unknown' if legacy else 'current') if values else 'empty'
        result[worker_id] = {'status': state, 'source_date': baseline['source_date'] if baseline else None,
                             'inherited_fields': inherited, 'updated_fields': updated}
    return result


def register_day_inheritance(app, get_db, roles_required, utc_now):
    @app.post('/api/staffing/inherit-previous-day')
    @roles_required('admin', 'foreman')
    def inherit_previous_day():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description='Укажите дату расстановки.')
        try:
            target = date.fromisoformat(str(payload.get('date', '')))
            source = (target - timedelta(days=1)).isoformat()
        except (ValueError, OverflowError):
            abort(400, description='Укажите дату с существующим предыдущим днём.')
        day = target.isoformat()
        db = get_db()
        with db:
            # Serialize initialization and ordinary edits; never overwrite a partially filled date.
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT id,role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman'):
                abort(403, description='Нет права изменять расстановку.')
            admin = actor['role'] in ('super_admin', 'admin')
            if explicit_scope(db, actor):
                require_all(db, actor)
                admin = True
            scope = 'all' if admin else 'foreman:' + str(actor['id'])
            result = {'date': day, 'source_date': source, 'copied': 0, 'assignments': 0,
                      'shifts': 0, 'statuses': 0, 'work_descriptions': 0}
            marker_clause = '' if admin else " AND scope_key IN ('all',?)"
            marker_params = [day] if admin else [day, scope]
            if db.execute('SELECT 1 FROM staffing_day_inheritance WHERE work_date=?' + marker_clause,
                          marker_params).fetchone():
                return jsonify({**result, 'status': 'existing'})
            for table in ('assignments', 'staffing_shifts', 'staffing_attendance', 'staffing_performed_work', 'assignment_events'):
                clause = '' if admin else ''' AND EXISTS (
                    SELECT 1 FROM crew_members m JOIN crews c ON c.id=m.crew_id
                    WHERE m.worker_id=t.worker_id AND c.owner_user_id=?)'''
                params = [day] if admin else [day, actor['id']]
                if db.execute(f'SELECT 1 FROM {table} t WHERE t.work_date=?' + clause + ' LIMIT 1', params).fetchone():
                    return jsonify({**result, 'status': 'existing'})

            records = {}
            from gdlr_api import staffing_eligible_sql
            for table in ('assignments', 'staffing_shifts', 'staffing_attendance', 'staffing_performed_work'):
                clause = '' if admin else ' AND c.owner_user_id=?'
                params = [source, day, day] if admin else [source, day, day, actor['id']]
                records[table] = db.execute(f'''SELECT t.*,m.crew_id current_crew_id,c.owner_user_id
                    FROM {table} t JOIN workers w ON w.id=t.worker_id
                    LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
                    WHERE t.work_date=? AND w.active=1 AND {staffing_eligible_sql()} AND NOT EXISTS (
                        SELECT 1 FROM employee_inactive_periods er WHERE er.worker_id=w.id AND er.effective_date<=?
                        AND (er.restored_date IS NULL OR er.restored_date>?))'''
                    + clause + ' ORDER BY t.worker_id', params).fetchall()
            assignments = records['assignments']
            seen = set()
            for row in assignments:
                key = (row['worker_id'], canonical_shift(row['shift']))
                if key in seen or key[1] not in ('1 смена', '2 смена'):
                    abort(409, description='На предыдущую дату есть неоднозначные смены. Уточните расстановку перед переносом.')
                seen.add(key)
                if (not row['current_crew_id'] or row['crew_id'] not in (None, row['current_crew_id'])
                        or (not admin and row['crew_id'] is None and row['foreman_user_id'] != actor['id'])):
                    abort(409, description='Состав бригад отличается от предыдущего дня. Уточните назначения перед переносом.')
            ids = {row['worker_id'] for rows in records.values() for row in rows}
            if not ids:
                return jsonify({**result, 'status': 'no_source'})
            # A separate reader backs up committed data while this transaction holds the write lock.
            try:
                backup = create_backup(db, day, reason='automatic')
            except (OSError, sqlite3.Error):
                abort(503, description='Не удалось создать резервную копию. Расстановка не перенесена. Повторите попытку позже.')
            stamp = utc_now()
            for row in assignments:
                shift = canonical_shift(row['shift'])
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,
                    foreman_user_id,created_at,crew_id,edit_token) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (day, shift, row['subobject_id'], row['worker_id'], row['employer'],
                     row['owner_user_id'], stamp, row['current_crew_id'], secrets.token_urlsafe(16)))
                db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,
                    before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,?,?,NULL,?,?,?)''',
                    (row['current_crew_id'], row['worker_id'], day, shift, row['subobject_id'], actor['id'], stamp))
            for row in records['staffing_shifts']:
                db.execute('''INSERT INTO staffing_shifts(work_date,worker_id,shift,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?)''', (day, row['worker_id'], canonical_shift(row['shift']),
                    secrets.token_urlsafe(16), actor['id'], stamp))
            for row in records['staffing_attendance']:
                db.execute('''INSERT INTO staffing_attendance(work_date,worker_id,status,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?)''', (day, row['worker_id'], row['status'],
                    secrets.token_urlsafe(16), actor['id'], stamp))
            for row in records['staffing_performed_work']:
                db.execute('''INSERT INTO staffing_performed_work(work_date,worker_id,shift,description,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?,?)''', (day, row['worker_id'], row['shift'], row['description'],
                    secrets.token_urlsafe(16), actor['id'], stamp))
            for worker_id, snapshot in day_snapshots(db, day, sorted(ids)).items():
                db.execute('INSERT INTO staffing_inherited_rows VALUES (?,?,?,?)',
                    (day, worker_id, source, json.dumps(snapshot, sort_keys=True, ensure_ascii=False)))
            db.execute('''INSERT INTO staffing_day_inheritance VALUES (?,?,?,?,?)''',
                       (day, scope, source, actor['id'], stamp))
            result.update(status='inherited', copied=len(ids), assignments=len(assignments),
                          shifts=len(records['staffing_shifts']), statuses=len(records['staffing_attendance']),
                          work_descriptions=len(records['staffing_performed_work']),
                          backup=backup.name)
        return jsonify(result)
