"""SMU catalog with exact source aliases and persistent employee links."""
import json
import secrets
import sqlite3
import unicodedata

from flask import abort, g, jsonify, request
from pps_api import pps_value


def migrate_smu_catalog(db):
    first = not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='smu_catalog'").fetchone()
    if first and db.execute("SELECT 1 FROM workers WHERE trim(department)<>'' LIMIT 1").fetchone():
        from backup_api import create_backup
        create_backup(db, reason='smu-catalog-migration')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS smu_catalog (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            site_chief_user_id INTEGER REFERENCES users(id),
            edit_token TEXT NOT NULL DEFAULT (lower(hex(randomblob(16)))),
            updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS smu_aliases (
            name TEXT PRIMARY KEY, smu_id INTEGER NOT NULL REFERENCES smu_catalog(id)
        );
        CREATE TABLE IF NOT EXISTS employee_smu (
            worker_id INTEGER PRIMARY KEY REFERENCES workers(id) ON DELETE CASCADE,
            smu_id INTEGER NOT NULL REFERENCES smu_catalog(id), source_department TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_employee_smu_catalog ON employee_smu(smu_id);
        CREATE INDEX IF NOT EXISTS idx_smu_aliases_catalog ON smu_aliases(smu_id);
    ''')
    if 'site_chief_user_id' not in {r[1] for r in db.execute('PRAGMA table_info(smu_catalog)')}:
        with db:
            db.execute('BEGIN IMMEDIATE')
            # Another application process can finish the migration while we wait.
            if 'site_chief_user_id' not in {r[1] for r in db.execute('PRAGMA table_info(smu_catalog)')}:
                from backup_api import create_backup
                create_backup(db, reason='smu-site-chief-migration')
                db.execute('ALTER TABLE smu_catalog ADD COLUMN site_chief_user_id INTEGER REFERENCES users(id)')
    # Both imports and manual employee creation already write workers.department.
    # Resolve only exact aliases; preserve the source when a canonical name changes.
    body = '''
        INSERT INTO smu_catalog(name)
            SELECT NEW.department WHERE trim(NEW.department)<>''
            AND NOT EXISTS(SELECT 1 FROM smu_aliases WHERE name=NEW.department)
            ON CONFLICT(name) DO NOTHING;
        INSERT INTO smu_aliases(name,smu_id)
            SELECT name,id FROM smu_catalog WHERE name=NEW.department
            ON CONFLICT(name) DO NOTHING;
        DELETE FROM employee_smu WHERE worker_id=NEW.id AND trim(NEW.department)='';
        INSERT INTO employee_smu(worker_id,smu_id,source_department)
            SELECT NEW.id,smu_id,NEW.department FROM smu_aliases WHERE name=NEW.department
            ON CONFLICT(worker_id) DO UPDATE SET smu_id=excluded.smu_id,
            source_department=CASE WHEN employee_smu.smu_id=excluded.smu_id
                THEN employee_smu.source_department ELSE excluded.source_department END;
        UPDATE workers SET department=(SELECT c.name FROM employee_smu e
            JOIN smu_catalog c ON c.id=e.smu_id WHERE e.worker_id=NEW.id)
            WHERE id=NEW.id AND EXISTS(SELECT 1 FROM employee_smu e JOIN smu_catalog c ON c.id=e.smu_id
                WHERE e.worker_id=NEW.id AND c.name<>NEW.department);
    '''
    for event, suffix in [('INSERT', 'insert'), ('UPDATE OF department', 'update')]:
        db.execute(f'CREATE TRIGGER IF NOT EXISTS workers_smu_{suffix} AFTER {event} ON workers BEGIN {body} END')
    # Backfill adds links only; existing employee/source/history rows are untouched.
    db.execute('''INSERT INTO smu_catalog(name) SELECT DISTINCT w.department FROM workers w
        WHERE trim(w.department)<>'' AND NOT EXISTS(SELECT 1 FROM smu_aliases a WHERE a.name=w.department)
        ON CONFLICT(name) DO NOTHING''')
    db.execute('''INSERT INTO smu_aliases(name,smu_id) SELECT name,id FROM smu_catalog WHERE 1
        ON CONFLICT(name) DO NOTHING''')
    db.execute('''INSERT INTO employee_smu(worker_id,smu_id,source_department)
        SELECT w.id,a.smu_id,w.department FROM workers w JOIN smu_aliases a ON a.name=w.department
        WHERE trim(w.department)<>'' ON CONFLICT(worker_id) DO NOTHING''')


def active_smu_names(db):
    return [r[0] for r in db.execute('SELECT name FROM smu_catalog WHERE active=1 ORDER BY name')]


def register_smu_routes(app, get_db, roles_required, utc_now):
    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные СМУ.')
        return data

    def name_value(data):
        name = data.get('name')
        if (not isinstance(name, str) or not name.strip() or len(name) > 500
                or any(unicodedata.category(c).startswith('C') for c in name)):
            abort(400, description='Введите название СМУ от 1 до 500 символов без служебных символов.')
        return name.strip()

    def check_actor(db):
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not actor or not actor['active'] or actor['role'] != 'super_admin':
            abort(403, description='Справочник изменяет только супер-администратор.')

    def chief_value(db, data, current=None):
        if 'site_chief_user_id' not in data:
            return current
        value = data['site_chief_user_id']
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            abort(400, description='Выберите ответственное лицо из пользователей приложения.')
        user = db.execute('SELECT active FROM users WHERE id=?', (value,)).fetchone()
        if not user or (not user['active'] and value != current):
            abort(400, description='Для назначения выберите действующего пользователя приложения.')
        return value

    def checked_row(db, smu_id, data):
        check_actor(db)
        row = db.execute('SELECT * FROM smu_catalog WHERE id=?', (smu_id,)).fetchone()
        if not row:
            abort(404, description='СМУ не найдено.')
        if data.get('expected_token') != row['edit_token']:
            abort(409, description='СМУ уже изменено. Обновите справочник.')
        return row

    @app.get('/api/smu')
    @roles_required('admin', 'foreman')
    def list_smu():
        db = get_db()
        rows = [dict(r) for r in db.execute('''
            SELECT c.*,p.name pps_name,p.active pps_active,u.full_name site_chief_name,u.active site_chief_active,
                   COALESCE(e.employee_count,0) employee_count FROM smu_catalog c
            LEFT JOIN users u ON u.id=c.site_chief_user_id
            LEFT JOIN pps_catalog p ON p.id=c.pps_id
            LEFT JOIN (SELECT smu_id,COUNT(worker_id) employee_count
                       FROM employee_smu GROUP BY smu_id) e ON e.smu_id=c.id
            ORDER BY c.name''')]
        options = [dict(r) for r in db.execute('''SELECT id,full_name,username FROM users
            WHERE active=1 ORDER BY full_name,username,id''')] if g.user['role'] == 'super_admin' else []
        return jsonify({'rows': rows, 'chief_options': options,
                        'pps_options': [dict(r) for r in db.execute('SELECT id,name,active FROM pps_catalog ORDER BY name,id')]})

    @app.post('/api/smu')
    @roles_required('super_admin')
    def create_smu():
        data = payload()
        name = name_value(data)
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                check_actor(db)
                chief_id = chief_value(db, data)
                pps_id = pps_value(db, data)
                smu_id = db.execute('''INSERT INTO smu_catalog
                    (name,site_chief_user_id,pps_id,updated_by,updated_at) VALUES (?,?,?,?,?)''',
                    (name, chief_id, pps_id, g.user['id'], utc_now())).lastrowid
                db.execute('INSERT INTO smu_aliases VALUES (?,?)', (name, smu_id))
        except sqlite3.IntegrityError:
            abort(409, description='Такое название СМУ уже есть или сохранено как прежнее название.')
        return jsonify({'id': smu_id}), 201

    @app.patch('/api/smu/<int:smu_id>')
    @roles_required('super_admin')
    def update_smu(smu_id):
        from backup_api import create_backup
        from user_smu_access import access_view, allowed_workers
        data = payload()
        name = name_value(data)
        if type(data.get('active')) is not bool:
            abort(400, description='Укажите доступность СМУ для выбора.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                old = checked_row(db, smu_id, data)
                chief_id = chief_value(db, data, old['site_chief_user_id'])
                pps_id = pps_value(db, data, old['pps_id'])
                alias = db.execute('SELECT smu_id FROM smu_aliases WHERE name=?', (name,)).fetchone()
                if alias and alias[0] != smu_id:
                    abort(409, description='Такое название уже связано с другим СМУ.')
                stamp = utc_now()
                if name != old['name']:
                    create_backup(db, reason='smu-rename')
                    ids = [r[0] for r in db.execute('SELECT id FROM workers')]
                    users = db.execute('SELECT u.* FROM users u JOIN user_smu_access a ON a.user_id=u.id').fetchall()
                    rights = {u['id']: allowed_workers(db, ids, u) for u in users}
                    views = {u['id']: access_view(db, u) for u in users}
                db.execute('''UPDATE smu_catalog SET name=?,active=?,site_chief_user_id=?,pps_id=?,
                    edit_token=?,updated_by=?,updated_at=? WHERE id=?''',
                    (name, int(data['active']), chief_id, pps_id, secrets.token_hex(16), g.user['id'], stamp, smu_id))
                db.execute('INSERT INTO smu_aliases VALUES (?,?) ON CONFLICT(name) DO NOTHING', (name, smu_id))
                if name != old['name']:
                    db.execute('UPDATE workers SET department=? WHERE id IN (SELECT worker_id FROM employee_smu WHERE smu_id=?)',
                               (name, smu_id))
                    for user in users:
                        before = views[user['id']]
                        departments = sorted({name if d == old['name'] else d for d in before['departments']})
                        if departments != before['departments'] and user['role'] != 'super_admin':
                            db.execute('''UPDATE user_smu_access SET departments_json=?,edit_token=?,updated_by=?,updated_at=?
                                WHERE user_id=?''', (json.dumps(departments, ensure_ascii=False), secrets.token_hex(16),
                                                    g.user['id'], stamp, user['id']))
                            db.execute('''INSERT INTO user_smu_access_events
                                (user_id,before_json,after_json,changed_by,changed_at) VALUES (?,?,?,?,?)''',
                                (user['id'], json.dumps(before, ensure_ascii=False),
                                 json.dumps(access_view(db, user), ensure_ascii=False), g.user['id'], stamp))
                        if allowed_workers(db, ids, user) != rights[user['id']]:
                            abort(409, description='Переименование изменяет права доступа. Проверьте настройки СМУ пользователей.')
        except sqlite3.IntegrityError:
            abort(409, description='Такое название СМУ уже есть.')
        except (OSError, sqlite3.OperationalError):
            abort(503, description='Не удалось сохранить СМУ и резервную копию. Повторите позже.')
        return jsonify({'updated': smu_id})

    @app.delete('/api/smu/<int:smu_id>')
    @roles_required('super_admin')
    def delete_smu(smu_id):
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            checked_row(db, smu_id, payload())
            if db.execute('SELECT 1 FROM employee_smu WHERE smu_id=? LIMIT 1', (smu_id,)).fetchone():
                abort(409, description='СМУ связано с сотрудниками. Можно отключить его для выбора.')
            aliases = {r[0] for r in db.execute('SELECT name FROM smu_aliases WHERE smu_id=?', (smu_id,))}
            if any(aliases.intersection(json.loads(r[0])) for r in db.execute('SELECT departments_json FROM user_smu_access')):
                abort(409, description='СМУ используется в настройках доступа пользователей.')
            if getattr(db,'dialect',None) == 'postgres':
                if db.native('SELECT 1 FROM workforce_profiles p JOIN workforce_divisions d ON d.id=p.division_id WHERE d.smu_id=%s LIMIT 1',(smu_id,)).fetchone():
                    abort(409, description='СМУ используется как подразделение сотрудников. Можно отключить его для выбора.')
                db.native('DELETE FROM workforce_divisions WHERE smu_id=%s',(smu_id,))
            db.execute('DELETE FROM smu_aliases WHERE smu_id=?', (smu_id,))
            db.execute('DELETE FROM smu_catalog WHERE id=?', (smu_id,))
        return jsonify({'deleted': smu_id})
