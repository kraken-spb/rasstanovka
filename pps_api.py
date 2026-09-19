"""Construction project catalog. SMU membership is explicit, never inferred."""
import re
import secrets
import sqlite3
import unicodedata

from flask import abort, g, jsonify, request


def pps_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(
            unicodedata.category(c).startswith('C') for c in value):
        abort(400, description='Введите название ППС от 1 до 200 символов.')
    name = ' '.join(value.split())
    match = re.fullmatch(r'ППС[\s\-–—]*(\d+)', name, re.IGNORECASE)
    return 'ППС-' + match[1] if match else name


def migrate_pps_catalog(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pps_catalog'").fetchone():
        return
    db.commit()
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pps_catalog'").fetchone():
            return
        from backup_api import create_backup
        create_backup(db, reason='pps-catalog-migration')
        db.execute('''CREATE TABLE pps_catalog (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            edit_token TEXT NOT NULL, updated_by INTEGER REFERENCES users(id),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        db.execute('ALTER TABLE smu_catalog ADD COLUMN pps_id INTEGER REFERENCES pps_catalog(id)')
        db.execute('CREATE INDEX idx_smu_pps ON smu_catalog(pps_id)')
        for row in db.execute("SELECT DISTINCT pps FROM workers WHERE trim(COALESCE(pps,''))<>''").fetchall():
            name = pps_name(row[0])
            db.execute('INSERT INTO pps_catalog(name,name_key,edit_token) VALUES (?,?,?) ON CONFLICT(name_key) DO NOTHING',
                       (name, name.casefold(), secrets.token_hex(16)))


def pps_value(db, data, current=None):
    if 'pps_id' not in data:
        return current
    value = data['pps_id']
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        abort(400, description='Выберите ППС из справочника.')
    row = db.execute('SELECT active FROM pps_catalog WHERE id=?', (value,)).fetchone()
    if not row or (not row['active'] and value != current):
        abort(400, description='Для привязки выберите действующий ППС.')
    return value


def register_pps_routes(app, get_db, roles_required, utc_now):
    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400, description='Некорректные данные ППС.')
        return value

    def actor(db):
        row = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not row or not row['active'] or row['role'] != 'super_admin':
            abort(403, description='Справочник изменяет только супер-администратор.')

    def checked(db, pps_id, body):
        actor(db)
        row = db.execute('SELECT * FROM pps_catalog WHERE id=?', (pps_id,)).fetchone()
        if not row:
            abort(404, description='ППС не найден.')
        if body.get('expected_token') != row['edit_token']:
            abort(409, description='ППС уже изменён. Обновите справочник.')
        return row

    @app.get('/api/pps')
    @roles_required('admin', 'foreman')
    def list_pps():
        db = get_db()
        rows = [dict(r) for r in db.execute('SELECT * FROM pps_catalog ORDER BY name,id')]
        smu = [dict(r) for r in db.execute('''SELECT c.id,c.name,c.pps_id,c.active,COUNT(e.worker_id) employee_count
            FROM smu_catalog c LEFT JOIN employee_smu e ON e.smu_id=c.id
            GROUP BY c.id,c.name,c.pps_id,c.active ORDER BY c.name''')]
        grouped = {row['id']: [] for row in rows}
        for row in smu:
            if row['pps_id'] in grouped:
                grouped[row['pps_id']].append(row)
        divisions = {}
        if getattr(db,'dialect',None) == 'postgres':
            for item in db.native('SELECT id,name,pps_id,active FROM workforce_divisions WHERE smu_id IS NULL ORDER BY name'):
                divisions.setdefault(item['pps_id'], []).append(dict(item))
        return jsonify(rows=[{**r, 'divisions': divisions.get(r['id'], []), 'smu': grouped[r['id']],
                              'employee_count': sum(s['employee_count'] for s in grouped[r['id']])} for r in rows],
                       unbound_smu_count=sum(r['pps_id'] is None for r in smu))

    @app.post('/api/pps')
    @roles_required('super_admin')
    def create_pps():
        body = data()
        name = pps_name(body.get('name'))
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor(db)
                new_id = db.execute('''INSERT INTO pps_catalog(name,name_key,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?)''', (name, name.casefold(), secrets.token_hex(16), g.user['id'], utc_now())).lastrowid
        except sqlite3.IntegrityError:
            abort(409, description='Такой ППС уже есть в справочнике.')
        return jsonify(id=new_id), 201

    @app.patch('/api/pps/<int:pps_id>')
    @roles_required('super_admin')
    def update_pps(pps_id):
        body = data()
        name = pps_name(body.get('name'))
        if type(body.get('active')) is not bool:
            abort(400, description='Укажите доступность ППС для выбора.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                checked(db, pps_id, body)
                db.execute('''UPDATE pps_catalog SET name=?,name_key=?,active=?,edit_token=?,updated_by=?,updated_at=?
                    WHERE id=?''', (name, name.casefold(), int(body['active']), secrets.token_hex(16), g.user['id'], utc_now(), pps_id))
        except sqlite3.IntegrityError:
            abort(409, description='Такой ППС уже есть в справочнике.')
        return jsonify(updated=pps_id)

    @app.delete('/api/pps/<int:pps_id>')
    @roles_required('super_admin')
    def delete_pps(pps_id):
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            checked(db, pps_id, data())
            if db.execute('SELECT 1 FROM smu_catalog WHERE pps_id=? LIMIT 1', (pps_id,)).fetchone():
                abort(409, description='К ППС привязаны СМУ. Можно отключить ППС для выбора.')
            if getattr(db,'dialect',None) == 'postgres' and db.native('SELECT 1 FROM workforce_divisions WHERE pps_id=%s LIMIT 1',(pps_id,)).fetchone():
                abort(409, description='К ППС привязаны подразделения. Можно отключить ППС для выбора.')
            db.execute('DELETE FROM pps_catalog WHERE id=?', (pps_id,))
        return jsonify(deleted=pps_id)
