"""Explicit, shared SMU editing rights; unchanged accounts keep their existing crew rights."""
import hashlib
import json
import secrets

from flask import abort, g, jsonify, request
from user_roles import HR_VIEWER


def migrate_smu_access(db):
    db.execute('''CREATE TABLE IF NOT EXISTS user_smu_access (
        user_id INTEGER PRIMARY KEY REFERENCES users(id),
        mode TEXT NOT NULL CHECK(mode IN ('all','selected')),
        departments_json TEXT NOT NULL, edit_token TEXT NOT NULL,
        updated_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS user_smu_access_events (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL)''')


def profile(db, user=None):
    user = user if user is not None else g.user
    actor = db.execute('SELECT id,role,active FROM users WHERE id=?', (user['id'],)).fetchone()
    if not actor or not actor['active'] or actor['role'] == 'viewer':
        return actor, {'mode': 'selected', 'departments_json': '[]'}
    if actor['role'] in ('super_admin', HR_VIEWER):
        return actor, {'mode': 'all', 'departments_json': '[]'}
    return actor, db.execute('SELECT * FROM user_smu_access WHERE user_id=?', (actor['id'],)).fetchone()


def explicit_scope(db, user=None):
    return profile(db, user)[1] is not None


def require_all(db, user=None):
    actor, scope = profile(db, user)
    if actor and actor['role'] == HR_VIEWER:
        abort(403, description='Для этой роли доступен только просмотр данных.')
    if scope is not None and scope['mode'] != 'all':
        abort(403, description='Для этой операции нужен доступ ко всем СМУ.')
    if scope is None and actor['role'] not in ('admin', 'super_admin'):
        abort(403, description='Для этой операции нужен доступ ко всем СМУ.')


def legacy_foreman(db, user=None):
    actor, scope = profile(db, user)
    return bool(actor and actor['role'] == 'foreman' and scope is None)


def worker_clause(db, user=None, worker='w', crew='c', allow_unowned=False):
    actor, scope = profile(db, user)
    if scope is not None:
        if scope['mode'] == 'all':
            return '1', []
        departments = json.loads(scope['departments_json'])
        if not departments:
            return '0', []
        return f"{worker}.department IN ({','.join('?' for _ in departments)})", departments
    if actor['role'] in ('admin', 'super_admin'):
        return '1', []
    return f"({crew}.owner_user_id=?" + (f" OR {crew}.owner_user_id IS NULL)" if allow_unowned else ')'), [actor['id']]


def allowed_workers(db, ids, user=None, allow_unowned=False):
    ids = list(set(ids))
    if not ids:
        return set()
    clause, params = worker_clause(db, user, allow_unowned=allow_unowned)
    return {r[0] for r in db.execute(f'''SELECT w.id FROM workers w
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        WHERE w.id IN ({','.join('?' for _ in ids)}) AND ({clause})''', [*ids, *params])}


def require_workers(db, ids, user=None, allow_unowned=False):
    actor, _ = profile(db, user)
    if actor and actor['role'] == HR_VIEWER:
        abort(403, description='Для этой роли доступен только просмотр данных.')
    if set(ids) - allowed_workers(db, ids, user, allow_unowned):
        abort(403, description='Нет права редактировать сотрудников этого СМУ.')


def can_worker(db, worker_id, user=None, allow_unowned=False):
    return worker_id in allowed_workers(db, [worker_id], user, allow_unowned)


def can_crew(db, crew_id, user=None, whole=False):
    actor, scope = profile(db, user)
    crew = db.execute('SELECT owner_user_id FROM crews WHERE id=?', (crew_id,)).fetchone()
    if not crew:
        return False
    if scope is None:
        return actor['role'] in ('admin', 'super_admin') or crew['owner_user_id'] == actor['id']
    if scope['mode'] == 'all':
        return True
    ids = [r[0] for r in db.execute('SELECT worker_id FROM crew_members WHERE crew_id=?', (crew_id,))]
    allowed = allowed_workers(db, ids, user)
    if not ids:
        return crew['owner_user_id'] == actor['id'] and bool(json.loads(scope['departments_json']))
    return len(allowed) == len(ids) if whole else bool(allowed)


def require_crew(db, crew_id, user=None, whole=False):
    actor, _ = profile(db, user)
    if actor and actor['role'] == HR_VIEWER:
        abort(403, description='Для этой роли доступен только просмотр данных.')
    if not can_crew(db, crew_id, user, whole):
        abort(403, description='Нет права изменять эту бригаду целиком. Выберите сотрудников доступных СМУ.')


def department_options(db):
    return [dict(r) for r in db.execute('''SELECT c.id,c.name,c.active,COUNT(w.id) worker_count
        FROM smu_catalog c LEFT JOIN employee_smu e ON e.smu_id=c.id
        LEFT JOIN workers w ON w.id=e.worker_id AND w.active=1
        GROUP BY c.id ORDER BY c.name''')]


def access_view(db, user):
    stored = db.execute('SELECT * FROM user_smu_access WHERE user_id=?', (user['id'],)).fetchone()
    departments = json.loads(stored['departments_json']) if stored else [r[0] for r in db.execute('''
        SELECT DISTINCT w.department FROM workers w JOIN crew_members m ON m.worker_id=w.id
        JOIN crews c ON c.id=m.crew_id WHERE c.owner_user_id=? ORDER BY w.department''', (user['id'],))]
    if not stored:
        known = {r[0] for r in db.execute('SELECT name FROM smu_catalog')}
        departments = [name for name in departments if name in known]
    mode = stored['mode'] if stored else ('all' if user['role'] in ('admin', 'super_admin') else 'selected')
    if user['role'] in ('super_admin', HR_VIEWER):
        mode, departments = 'all', []
    snapshot = {'user_id': user['id'], 'role': user['role'], 'active': user['active'],
                'mode': mode, 'departments': departments, 'configured': bool(stored),
                'edit_token': stored['edit_token'] if stored else None}
    token = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {**snapshot, 'expected_token': token}


def register_smu_access(app, get_db, roles_required, utc_now):
    @app.get('/api/user-smu-access')
    @roles_required('admin')
    def list_access():
        db = get_db()
        return jsonify({'departments': department_options(db), 'rows': [access_view(db, user)
            for user in db.execute('SELECT id,role,active FROM users ORDER BY id')]})

    @app.put('/api/users/<int:user_id>/smu-access')
    @roles_required('super_admin')
    def save_access(user_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('mode') not in ('all', 'selected'):
            abort(400, description='Выберите все СМУ или конкретные СМУ.')
        departments = data.get('departments')
        if not isinstance(departments, list) or len(departments) > 500 or any(not isinstance(d, str) or not d or len(d) > 500 for d in departments):
            abort(400, description='Отметьте СМУ в списке.')
        departments = sorted(set(departments))
        if data['mode'] == 'all' and departments:
            abort(400, description='Для всех СМУ отдельный список не нужен.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT id,role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            user = db.execute('SELECT id,role,active FROM users WHERE id=?', (user_id,)).fetchone()
            if not actor or not actor['active'] or actor['role'] != 'super_admin':
                abort(403, description='Выдавать доступ к СМУ может только супер-администратор.')
            if not user:
                abort(404, description='Пользователь не найден.')
            if user['role'] in ('viewer', 'super_admin', HR_VIEWER):
                abort(400, description='Доступ этой учётной записи определяется её ролью.')
            before = access_view(db, user)
            if data.get('expected_token') != before['expected_token']:
                abort(409, description='Права или роль уже изменились. Обновите учётные записи.')
            known = {r['name'] for r in department_options(db)} | set(before['departments'])
            if set(departments) - known:
                abort(400, description='СМУ отсутствует в списке. Обновите учётные записи.')
            stamp = utc_now()
            db.execute('''INSERT INTO user_smu_access VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                mode=excluded.mode,departments_json=excluded.departments_json,edit_token=excluded.edit_token,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (user_id, data['mode'], json.dumps(departments, ensure_ascii=False), secrets.token_hex(16), actor['id'], stamp))
            after = access_view(db, user)
            db.execute('INSERT INTO user_smu_access_events(user_id,before_json,after_json,changed_by,changed_at) VALUES (?,?,?,?,?)',
                       (user_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), actor['id'], stamp))
        return jsonify(after)
