"""Append-only daily report checks and closed versions; source operations stay editable.

The integration layer supplies authorization for the complete requested scope.
Authorization of saved versions always uses their immutable scope, never today's
worker membership. Every capture requires one consistent database transaction.
"""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from flask import abort, g, jsonify, request, send_file

from gdlr_api import staffing_eligible_sql
from query_helpers import membership
from staffing_import import active_members_sql


SCHEMA_VERSION = 1
SNAPSHOT_NOTE = (
    'Сохранённый снимок отчёта. Каждый сотрудник учитывается один раз за дату; '
    'назначения обеих смен сохранены. Расставлен — сотрудник со статусом «Явка» '
    'и назначением; не расставлен — «Явка» без назначения; остальные статусы — «Неявка». '
    'Состав и значения справочников зафиксированы в момент проверки отчёта. '
    'Они не восстанавливают неизвестную историю справочников на отчётную дату.'
)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def migrate_report_closure(db):
    """SQLite test bootstrap; PostgreSQL uses reviewed offline migration 022."""
    if getattr(db, 'dialect', None) == 'postgres':
        raise RuntimeError('PostgreSQL report closure requires the offline migration.')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS report_closure_checks (
            id INTEGER PRIMARY KEY, scope_key TEXT NOT NULL, scope_json TEXT NOT NULL,
            work_date TEXT NOT NULL, base_version_id INTEGER REFERENCES report_closure_versions(id),
            fingerprint TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id), actor_json TEXT NOT NULL,
            created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
            UNIQUE(created_by,idempotency_key));
        CREATE INDEX IF NOT EXISTS idx_report_checks_scope_date
            ON report_closure_checks(scope_key,work_date,id);
        CREATE TABLE IF NOT EXISTS report_closure_versions (
            id INTEGER PRIMARY KEY, scope_key TEXT NOT NULL, scope_json TEXT NOT NULL,
            work_date TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
            check_id INTEGER NOT NULL UNIQUE REFERENCES report_closure_checks(id),
            previous_version_id INTEGER REFERENCES report_closure_versions(id),
            correction_reason TEXT NOT NULL,
            fingerprint TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id), actor_json TEXT NOT NULL,
            created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
            UNIQUE(scope_key,work_date,version), UNIQUE(created_by,idempotency_key),
            CHECK((version=1 AND previous_version_id IS NULL AND correction_reason='') OR
                  (version>1 AND previous_version_id IS NOT NULL AND length(trim(correction_reason))>0)));
        CREATE INDEX IF NOT EXISTS idx_report_versions_scope_date
            ON report_closure_versions(scope_key,work_date,id);
        CREATE TRIGGER IF NOT EXISTS immutable_report_checks_update
            BEFORE UPDATE ON report_closure_checks BEGIN SELECT RAISE(ABORT,'Report checks are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_report_checks_delete
            BEFORE DELETE ON report_closure_checks BEGIN SELECT RAISE(ABORT,'Report checks are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_report_versions_update
            BEFORE UPDATE ON report_closure_versions BEGIN SELECT RAISE(ABORT,'Report versions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_report_versions_delete
            BEFORE DELETE ON report_closure_versions BEGIN SELECT RAISE(ABORT,'Report versions are immutable'); END;
    ''')


def normalize_scope(value):
    if value == {'kind': 'all'}:
        return {'kind': 'all'}
    if (isinstance(value, dict) and set(value) == {'kind', 'department'}
            and value['kind'] == 'department' and isinstance(value['department'], str)
            and value['department'].strip() and len(value['department']) <= 500):
        return {'kind': 'department', 'department': value['department']}
    abort(400, description='Выберите полный отчёт или одно СМУ.')


def report_day(value):
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value or day > datetime.now(timezone(timedelta(hours=3))).date():
            raise ValueError
    except (ValueError, TypeError):
        abort(400, description='Выберите существующую дату не позже сегодняшней даты по Москве.')
    return day.isoformat()


def capture_report(db, day, scope):
    """Capture server-derived facts in the caller's repeatable/serializable snapshot."""
    if not db.in_transaction:
        raise RuntimeError('A report snapshot requires an explicit transaction.')
    scope = normalize_scope(scope)
    day = report_day(day)
    clause, params = ('1', []) if scope['kind'] == 'all' else ('w.department=?', [scope['department']])
    identity = 'w.uuid' if getattr(db, 'dialect', None) == 'postgres' else 'NULL'
    rows = db.execute(f'''
        WITH roster AS (
            SELECT worker_id FROM ({active_members_sql()})
            UNION SELECT worker_id FROM outstaff_members
            UNION SELECT worker_id FROM manual_employees
            UNION SELECT worker_id FROM employee_restorations
        ), population AS (
            SELECT r.worker_id FROM roster r JOIN workers w ON w.id=r.worker_id
            WHERE w.active=1 AND {staffing_eligible_sql()}
            UNION SELECT worker_id FROM assignments WHERE work_date=?
        )
        SELECT w.id,{identity} worker_uuid,w.full_name,w.personnel_no,w.profession,w.gsp_profession,
            w.active,w.department,COALESCE(w.pps,'') pps,
            COALESCE(gc.name,w.category,'') category,eg.category_id,eg.edit_token category_token,
            COALESCE(ct.name,w.contractor,'') contractor,ec.contractor_id,ec.edit_token contractor_token,
            COALESCE(ee.employer,w.employer,'') employer,ee.edit_token employer_token,
            m.crew_id,COALESCE(c.name,'') crew_name,c.details_token crew_token,
            COALESCE(d.linear_itr_override,c.linear_itr,'') linear_itr,
            COALESCE(d.brigadier_override,c.brigadier,'') brigadier,d.edit_token responsible_token,
            COALESCE(att.status,'Явка') attendance_status,att.edit_token attendance_token,
            ss.shift employee_shift,ss.edit_token shift_token
        FROM population p JOIN workers w ON w.id=p.worker_id
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        LEFT JOIN staffing_row_details d ON d.worker_id=w.id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        LEFT JOIN employee_contractors ec ON ec.worker_id=w.id LEFT JOIN contractors ct ON ct.id=ec.contractor_id
        LEFT JOIN employee_employers ee ON ee.worker_id=w.id
        LEFT JOIN staffing_attendance att ON att.worker_id=w.id AND att.work_date=?
        LEFT JOIN staffing_shifts ss ON ss.worker_id=w.id AND ss.work_date=?
        WHERE {clause} ORDER BY w.id
    ''', [day, day, day, *params]).fetchall()
    people = {r['id']: {**dict(r), 'assignments': []} for r in rows}
    selected, parameters = membership(db, 'a.worker_id', people)
    assignments = db.execute(f'''
        SELECT a.worker_id id,a.id assignment_id,a.work_date,a.shift assignment_shift,
            a.subobject_id,a.crew_id assignment_crew_id,a.created_at assignment_created_at,
            a.edit_token assignment_token,a.employer assignment_employer,a.foreman_user_id,
            s.object_id,o.name object_name,s.name subobject_name,COALESCE(c.name,'') assignment_crew_name,
            pw.description performed_work,pw.edit_token performed_work_token
        FROM assignments a JOIN subobjects s ON s.id=a.subobject_id JOIN objects o ON o.id=s.object_id
        LEFT JOIN crews c ON c.id=a.crew_id
        LEFT JOIN staffing_performed_work pw ON pw.worker_id=a.worker_id AND pw.work_date=a.work_date
            AND pw.shift=CASE WHEN a.shift='Ночная смена' THEN '2 смена' ELSE a.shift END
        WHERE a.work_date=? AND {selected} ORDER BY a.id
    ''', [day, *parameters]).fetchall()
    from staffing_api import assignment_authors
    authors = assignment_authors(db, day, assignments)
    author_labels = {}
    for row in assignments:
        actor = authors.get(row['assignment_id'])
        key = str(actor['user_id']) if actor else 'unknown'
        author_labels[key] = (actor['full_name'] or 'Пользователь №' + key) if actor else 'Автор не определён'
        people[row['id']]['assignments'].append({**dict(row), 'author': actor,
            'shift': 'Ночь' if row['assignment_shift'] in ('2 смена', 'Ночная смена') else 'День'})
    groups, totals = {}, dict(total=len(people), assigned=0, unassigned=0, absent=0)
    for person in people.values():
        person['assigned'] = bool(person['assignments'])
        status = 'absent' if person['attendance_status'] != 'Явка' else 'assigned' if person['assigned'] else 'unassigned'
        person['status'] = status
        totals[status] += 1
        group = groups.setdefault((person['pps'], person['category']), {
            'pps': person['pps'], 'category': person['category'], 'total': 0,
            'assigned': 0, 'unassigned': 0, 'absent': 0, 'people': [], 'companies': {}})
        group['people'].append(person)
        company = group['companies'].setdefault(person['contractor'], dict(total=0, assigned=0, unassigned=0, absent=0))
        for item in (group, company):
            item['total'] += 1
            item[status] += 1
    return {'schema_version': SCHEMA_VERSION, 'date': day, 'scope': scope,
        'filters': dict(pps=None, category=None, author=None),
        'options': {'pps': sorted({p['pps'] for p in people.values()}),
                    'categories': sorted({p['category'] for p in people.values()}),
                    'authors': sorted(author_labels), 'author_labels': author_labels},
        'contractors': sorted({p['contractor'] for p in people.values()}, key=str.casefold),
        'totals': totals, 'groups': sorted(groups.values(), key=lambda r: (r['pps'], r['category'].casefold())),
        'note': SNAPSHOT_NOTE}


def _metadata(row, kind):
    if row is None:
        return None
    keys = ('id', 'work_date', 'fingerprint', 'created_at')
    result = {key: row[key] for key in keys}
    result.update(kind=kind, scope=json.loads(row['scope_json']), actor=json.loads(row['actor_json']))
    if kind == 'closed':
        result.update(version=row['version'], previous_version_id=row['previous_version_id'],
                      correction_reason=row['correction_reason'], check_id=row['check_id'])
    else:
        result['base_version_id'] = row['base_version_id']
    return result


def closure_state(db, day, scope, actor_id):
    """Read current and immutable report facts within one caller-owned transaction."""
    current = capture_report(db, day, scope)
    key = digest(scope)
    versions = db.execute('''SELECT * FROM report_closure_versions
        WHERE scope_key=? AND work_date=? ORDER BY version DESC''', (key, day)).fetchall()
    latest = versions[0] if versions else None
    checked = db.execute('''SELECT * FROM report_closure_checks
        WHERE scope_key=? AND work_date=? ORDER BY id DESC LIMIT 1''', (key, day)).fetchone()
    fingerprint = digest(current)
    ready = bool(checked and checked['fingerprint'] == fingerprint
                 and checked['base_version_id'] == (latest['id'] if latest else None))
    divergent = bool(latest and latest['fingerprint'] != fingerprint)
    return {'date': day, 'scope': scope, 'current': current, 'fingerprint': fingerprint,
        'status': 'closed' if latest else 'checked' if ready else 'draft',
        'draft_status': 'checked' if ready else 'draft', 'ready_to_close': ready,
        'divergent': divergent,
        'expected_token': digest({'fingerprint': fingerprint, 'scope': scope, 'date': day,
            'checked': checked['id'] if checked else None, 'closed': latest['id'] if latest else None,
            'actor_id': actor_id}),
        'latest_check': _metadata(checked, 'checked'), 'latest_version': _metadata(latest, 'closed'),
        'versions': [_metadata(row, 'closed') for row in versions]}


def _actor(db):
    actor = db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
    if not actor or not actor['active']:
        abort(403, description='Учётная запись недоступна.')
    return dict(actor)


def _payload():
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or set(data) - {'date', 'scope', 'expected_token', 'idempotency_key', 'reason'}
            or not isinstance(data.get('expected_token'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', data['expected_token'])
            or not isinstance(data.get('idempotency_key'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', data['idempotency_key'])):
        abort(400, description='Обновите предпросмотр и повторите действие.')
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 2000:
        abort(400, description='Причина исправления должна содержать не более 2000 символов.')
    return {**data, 'date': report_day(data.get('date')), 'scope': normalize_scope(data.get('scope')), 'reason': reason.strip()}


def _idempotent(db, table, actor_id, payload):
    row = db.execute(f'SELECT * FROM {table} WHERE created_by=? AND idempotency_key=?',
                     (actor_id, payload['idempotency_key'])).fetchone()
    if row and row['request_hash'] != digest(payload):
        abort(409, description='Ключ операции уже использован для других данных.')
    return row


def _save_check(db, state, actor, payload, stamp):
    latest = state['latest_version']
    row_id = db.execute('''INSERT INTO report_closure_checks
        (scope_key,scope_json,work_date,base_version_id,fingerprint,snapshot_json,
         created_by,actor_json,created_at,idempotency_key,request_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (digest(payload['scope']), packed(payload['scope']), payload['date'], latest['id'] if latest else None,
         state['fingerprint'], packed(state['current']), actor['id'], packed(actor), stamp,
         payload['idempotency_key'], digest(payload))).lastrowid
    return db.execute('SELECT * FROM report_closure_checks WHERE id=?', (row_id,)).fetchone()


def _save_version(db, state, actor, payload, stamp):
    latest = state['latest_version']
    if latest and not payload['reason']:
        abort(400, description='Укажите причину новой исправленной версии.')
    if not latest and payload['reason']:
        abort(400, description='Причина исправления указывается при создании следующей версии.')
    if not state['ready_to_close']:
        abort(409, description='Сначала проверьте текущий снимок отчёта.')
    if latest and not state['divergent']:
        abort(409, description='Текущий отчёт совпадает с закрытой версией.')
    check = db.execute('SELECT * FROM report_closure_checks WHERE id=?', (state['latest_check']['id'],)).fetchone()
    row_id = db.execute('''INSERT INTO report_closure_versions
        (scope_key,scope_json,work_date,version,check_id,previous_version_id,correction_reason,
         fingerprint,snapshot_json,created_by,actor_json,created_at,idempotency_key,request_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (digest(payload['scope']), packed(payload['scope']), payload['date'], latest['version'] + 1 if latest else 1,
         check['id'], latest['id'] if latest else None, payload['reason'], check['fingerprint'], check['snapshot_json'],
         actor['id'], packed(actor), stamp, payload['idempotency_key'], digest(payload))).lastrowid
    return db.execute('SELECT * FROM report_closure_versions WHERE id=?', (row_id,)).fetchone()


def register_report_closure(app, get_db, roles_required, utc_now, *, authorize_scope):
    """authorize_scope(db, scope, *, write) must abort on unauthorized complete scopes.

    It is intentionally required: integration must explicitly choose the closure
    policy instead of accidentally inheriting worker/brigade-based read rights.
    """
    @app.get('/api/placement-report/closure')
    @roles_required('admin', 'foreman', 'viewer')
    def placement_report_closure():
        day = report_day(request.args.get('date'))
        scope = normalize_scope({'kind': request.args.get('scope', 'all'),
            **({'department': request.args['department']} if 'department' in request.args else {})})
        db = get_db()
        with db:
            db.execute('BEGIN')
            actor = _actor(db)
            authorize_scope(db, scope, write=False)
            result = closure_state(db, day, scope, actor['id'])
        response = jsonify(result)
        response.headers['Cache-Control'] = 'no-store'
        return response

    def save(kind):
        payload = _payload()
        if kind == 'checked' and payload['reason']:
            abort(400, description='Причина исправления указывается при закрытии версии.')
        table = 'report_closure_checks' if kind == 'checked' else 'report_closure_versions'
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = _actor(db)
                authorize_scope(db, payload['scope'], write=True)
                row = _idempotent(db, table, actor['id'], payload)
                if row is None:
                    state = closure_state(db, payload['date'], payload['scope'], actor['id'])
                    if state['expected_token'] != payload['expected_token']:
                        abort(409, description='Отчёт или его версия изменились. Обновите предпросмотр и проверьте данные заново.')
                    row = (_save_check if kind == 'checked' else _save_version)(db, state, actor, payload, utc_now())
                result = _metadata(row, kind)
        except sqlite3.IntegrityError:
            abort(409, description='Версия отчёта уже изменилась. Обновите предпросмотр.')
        response = jsonify(result)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/placement-report/closure/check')
    @roles_required('admin')
    def check_placement_report_closure():
        return save('checked')

    @app.post('/api/placement-report/closure/close')
    @roles_required('admin')
    def close_placement_report_closure():
        return save('closed')

    @app.get('/api/placement-report/closure/versions/<int:version_id>')
    @app.get('/api/placement-report/closure/versions/<int:version_id>/pdf')
    @app.get('/api/placement-report/closure/versions/<int:version_id>/export')
    @roles_required('admin', 'foreman', 'viewer')
    def placement_report_closure_version(version_id):
        db = get_db()
        with db:
            db.execute('BEGIN')
            _actor(db)
            row = db.execute('SELECT * FROM report_closure_versions WHERE id=?', (version_id,)).fetchone()
            if row is None:
                abort(404, description='Сохранённая версия не найдена.')
            authorize_scope(db, json.loads(row['scope_json']), write=False)
            result = _metadata(row, 'closed')
            result['report'] = json.loads(row['snapshot_json'])
        filename = f"placement-report-{result['work_date']}-v{result['version']}"
        if request.path.endswith('/pdf'):
            from placement_report_pdf import build_pdf
            report = result['report']
            report['note'] += (f" Версия {result['version']}; закрыта {result['created_at']}; "
                f"{result['actor']['full_name']}." + (f" Исправление: {result['correction_reason']}." if result['correction_reason'] else ''))
            response = send_file(BytesIO(build_pdf(report, include_people=True)), mimetype='application/pdf',
                as_attachment=True, download_name=filename + '.pdf')
        elif request.path.endswith('/export'):
            response = send_file(BytesIO(packed(result).encode('utf-8')), mimetype='application/json',
                as_attachment=True, download_name=filename + '.json')
        else:
            response = jsonify(result)
        response.headers['Cache-Control'] = 'no-store'
        return response
