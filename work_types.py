"""Administrator-maintained work types; daily bindings share the work record."""
import secrets
import sqlite3
import unicodedata
from flask import abort, g, jsonify, request

DEFAULTS=('Монтаж и сварка МК','Электромонтаж','Благоустройство')


def migrate_work_types(db):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_types'").fetchone()
    if exists:return
    db.execute('''CREATE TABLE work_types (
        id INTEGER PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),edit_token TEXT NOT NULL,
        updated_by INTEGER REFERENCES users(id),updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('ALTER TABLE staffing_performed_work ADD COLUMN work_type_id INTEGER REFERENCES work_types(id)')
    db.execute('CREATE INDEX idx_staffing_work_type ON staffing_performed_work(work_type_id,work_date)')
    for name in DEFAULTS:
        db.execute('INSERT INTO work_types(name,name_key,edit_token) VALUES (?,?,?)',(name,name.casefold(),secrets.token_hex(16)))


def register_work_types(app,get_db,roles_required,utc_now):
    @app.get('/api/work-types')
    @roles_required('admin','foreman','viewer')
    def work_types_list():
        return jsonify(rows=[dict(row) for row in get_db().execute('SELECT * FROM work_types ORDER BY name_key,id')])

    def save(key=None):
        data=request.get_json(silent=True)
        if not isinstance(data,dict):abort(400,description='Некорректные данные вида работ.')
        name=data.get('name')
        if not isinstance(name,str) or not name.strip() or len(name)>200 or any(unicodedata.category(c).startswith('C') for c in name):
            abort(400,description='Введите название вида работ от 1 до 200 символов.')
        name=unicodedata.normalize('NFC',' '.join(name.split()))
        if key is not None and type(data.get('active')) is not bool:abort(400,description='Укажите доступность вида работ.')
        db=get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor=db.execute('SELECT role,active FROM users WHERE id=?',(g.user['id'],)).fetchone()
                if not actor or not actor['active'] or actor['role'] not in ('admin','super_admin'):abort(403)
                if key is None:
                    key=db.execute('INSERT INTO work_types(name,name_key,edit_token,updated_by,updated_at) VALUES (?,?,?,?,?)',
                        (name,name.casefold(),secrets.token_hex(16),g.user['id'],utc_now())).lastrowid
                else:
                    old=db.execute('SELECT * FROM work_types WHERE id=?',(key,)).fetchone()
                    if not old:abort(404,description='Вид работ не найден.')
                    if data.get('expected_token')!=old['edit_token']:abort(409,description='Вид работ уже изменён. Обновите справочник.')
                    db.execute('UPDATE work_types SET name=?,name_key=?,active=?,edit_token=?,updated_by=?,updated_at=? WHERE id=?',
                        (name,name.casefold(),int(data['active']),secrets.token_hex(16),g.user['id'],utc_now(),key))
        except sqlite3.IntegrityError:abort(409,description='Такой вид работ уже есть, в том числе среди отключённых.')
        return jsonify(id=key)

    @app.post('/api/work-types')
    @roles_required('admin')
    def work_types_create():return save(),201

    @app.patch('/api/work-types/<int:key>')
    @roles_required('admin')
    def work_types_update(key):return save(key)
