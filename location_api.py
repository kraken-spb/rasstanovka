"""Editable location catalogs; IDs keep existing plans and assignments attached."""
import hashlib
import json
import secrets
import sqlite3
import unicodedata

from flask import abort, g, jsonify, request


def migrate_locations(db):
    db.execute('''CREATE TABLE IF NOT EXISTS location_stages (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
    )''')
    if 'edit_token' not in {row[1] for row in db.execute('PRAGMA table_info(location_stages)')}:
        db.execute("ALTER TABLE location_stages ADD COLUMN edit_token TEXT NOT NULL DEFAULT ''")
    if 'stage_id' not in {row[1] for row in db.execute('PRAGMA table_info(objects)')}:
        db.execute('ALTER TABLE objects ADD COLUMN stage_id INTEGER REFERENCES location_stages(id)')
    db.execute('''CREATE TABLE IF NOT EXISTS location_revisions (
        kind TEXT NOT NULL CHECK(kind IN ('objects', 'subobjects')),
        location_id INTEGER NOT NULL,
        edit_token TEXT NOT NULL,
        updated_by INTEGER NOT NULL REFERENCES users(id),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(kind, location_id)
    )''')


def name_key(name):
    return unicodedata.normalize('NFC', ' '.join(name.split())).casefold()


def location_token(row):
    # Include the fields as well as the revision to detect edits outside this API.
    values = [row['id'], row['name'], row.get('object_id'), row.get('stage_id'), row.get('revision')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()


def register_location_routes(app, get_db, roles_required, utc_now):
    def rows(db, kind):
        if kind == 'stages':
            return [dict(row) for row in db.execute('''SELECT id,name,edit_token revision
                FROM location_stages ORDER BY id''')]
        return [dict(row) for row in db.execute(f'''
            SELECT l.*, r.edit_token revision FROM {kind} l
            LEFT JOIN location_revisions r ON r.kind=? AND r.location_id=l.id
        ''', (kind,))]

    @app.get('/api/locations')
    @roles_required('admin')
    def location_catalogs():
        db = get_db()
        result = {'stages': [dict(row) for row in db.execute('SELECT id,name FROM location_stages ORDER BY id')]}
        result['stage_details'] = [dict(id=row['id'], edit_token=location_token(row)) for row in rows(db, 'stages')]
        for kind in ('objects', 'subobjects'):
            items = rows(db, kind)
            for row in items:
                row['edit_token'] = location_token(row)
                row.pop('revision', None)
            result[kind] = sorted(items, key=lambda row: (name_key(row['name']), row['id']))
        return jsonify(result)

    def save(kind, location_id=None):
        if kind not in ('stages', 'objects', 'subobjects'):
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные справочника.')
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 300:
            abort(400, description='Введите название от 1 до 300 символов.')
        if any(unicodedata.category(char).startswith('C') for char in name):
            abort(400, description='Название не должно содержать служебные символы.')
        name = unicodedata.normalize('NFC', ' '.join(name.split()))
        object_id = data.get('object_id')
        if kind == 'subobjects' and (type(object_id) is not int or object_id < 1):
            abort(400, description='Выберите группу подобъектов.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('SELECT role, active FROM users WHERE id=?', (g.user['id'],)).fetchone()
                if not actor or not actor['active'] or actor['role'] != 'super_admin':
                    abort(403, description='Справочники изменяет только супер-администратор.')
                items = rows(db, kind)
                if location_id is not None:
                    current = next((row for row in items if row['id'] == location_id), None)
                    if current is None:
                        abort(404, description='Запись справочника не найдена.')
                    if data.get('expected_token') != location_token(current):
                        abort(409, description='Запись уже изменена. Закройте редактор и обновите справочник.')
                if kind == 'subobjects' and not db.execute('SELECT 1 FROM objects WHERE id=?', (object_id,)).fetchone():
                    abort(400, description='Группа не найдена. Обновите справочник.')
                stage_id = data.get('stage_id', current.get('stage_id') if location_id is not None and kind == 'objects' else None)
                if kind == 'objects' and stage_id is not None and (
                        type(stage_id) is not int or not db.execute('SELECT 1 FROM location_stages WHERE id=?', (stage_id,)).fetchone()):
                    abort(400, description='Этап не найден. Обновите справочник.')
                if any(row['id'] != location_id and name_key(row['name']) == name_key(name)
                       and (kind != 'subobjects' or row['object_id'] == object_id) for row in items):
                    abort(409, description='Такое название уже есть в справочнике.' if kind != 'subobjects'
                          else 'Подобъект с таким названием уже есть в выбранной группе.')
                if location_id is None:
                    if kind == 'stages':
                        location_id = db.execute('INSERT INTO location_stages(name) VALUES (?)', (name,)).lastrowid
                    elif kind == 'objects':
                        location_id = db.execute('INSERT INTO objects(name,stage_id) VALUES (?,?)', (name, stage_id)).lastrowid
                    else:
                        location_id = db.execute('INSERT INTO subobjects(name,object_id) VALUES (?,?)', (name, object_id)).lastrowid
                elif kind == 'stages':
                    db.execute('UPDATE location_stages SET name=? WHERE id=?', (name, location_id))
                elif kind == 'objects':
                    db.execute('UPDATE objects SET name=?,stage_id=? WHERE id=?', (name, stage_id, location_id))
                else:
                    db.execute('UPDATE subobjects SET name=?,object_id=? WHERE id=?', (name, object_id, location_id))
                if kind == 'stages':
                    db.execute('UPDATE location_stages SET edit_token=? WHERE id=?', (secrets.token_hex(16), location_id))
                else:
                    db.execute('''INSERT INTO location_revisions(kind,location_id,edit_token,updated_by,updated_at)
                        VALUES (?,?,?,?,?) ON CONFLICT(kind,location_id) DO UPDATE SET
                        edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                        (kind, location_id, secrets.token_hex(16), g.user['id'], utc_now()))
        except sqlite3.IntegrityError:
            abort(409, description='Не удалось сохранить запись: проверьте название и выбранную группу.')
        return jsonify({'id': location_id})

    @app.post('/api/locations/<kind>')
    @roles_required('super_admin')
    def create_location(kind):
        return save(kind), 201

    @app.patch('/api/locations/<kind>/<int:location_id>')
    @roles_required('super_admin')
    def update_location(kind, location_id):
        return save(kind, location_id)

    @app.delete('/api/locations/<kind>/<int:location_id>')
    @roles_required('super_admin')
    def delete_location(kind, location_id):
        if kind not in ('stages', 'objects', 'subobjects'):
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные справочника.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role, active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] != 'super_admin':
                abort(403, description='Справочники изменяет только супер-администратор.')
            current = next((row for row in rows(db, kind) if row['id'] == location_id), None)
            if current is None:
                abort(404, description='Запись справочника не найдена.')
            if data.get('expected_token') != location_token(current):
                abort(409, description='Запись уже изменена. Обновите справочник перед удалением.')
            if kind == 'stages':
                if db.execute('SELECT 1 FROM objects WHERE stage_id=? LIMIT 1', (location_id,)).fetchone():
                    abort(409, description='В этапе есть группы подобъектов. Сначала перенесите их в другой этап.')
            elif kind == 'objects':
                if db.execute('SELECT 1 FROM subobjects WHERE object_id=? LIMIT 1', (location_id,)).fetchone():
                    abort(409, description='В группе есть подобъекты. Сначала перенесите их в другую группу или удалите неиспользуемые.')
            else:
                for table, column in (('assignments', 'subobject_id'), ('daily_staffing_plans', 'subobject_id'),
                                      ('staffing_plans', 'subobject_id'), ('assignment_events', 'before_subobject_id'),
                                      ('assignment_events', 'after_subobject_id')):
                    if db.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1', (location_id,)).fetchone():
                        abort(409, description='Подобъект используется в расстановке, планах или истории изменений. Удаление недоступно.')
            db.execute('DELETE FROM location_revisions WHERE kind=? AND location_id=?', (kind, location_id))
            table = 'location_stages' if kind == 'stages' else kind
            db.execute(f'DELETE FROM {table} WHERE id=?', (location_id,))
        return jsonify({'deleted': location_id})
