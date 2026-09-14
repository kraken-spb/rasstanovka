"""Manual employee registration with durable provenance and retry protection."""
import hashlib
import json
import secrets
import sqlite3
import unicodedata
from uuid import UUID, uuid4

from smu_api import active_smu_names

from flask import abort, g, jsonify, request


def migrate_manual_employees(db):
    db.execute('''CREATE TABLE IF NOT EXISTS manual_employees (
        worker_id INTEGER PRIMARY KEY REFERENCES workers(id),
        created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
        qualification TEXT NOT NULL DEFAULT '',
        request_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL)''')


def register_manual_employees(app, get_db, roles_required, utc_now, authorize=None):
    @app.get('/api/employees/create-options')
    @roles_required('admin')
    def options():
        db = get_db()
        return jsonify({'departments': active_smu_names(db),
                        'contractors': [dict(r) for r in db.execute('SELECT id,name,edit_token FROM contractors WHERE active=1 ORDER BY name')],
                        'categories': [dict(r) for r in db.execute('SELECT id,name,edit_token FROM gdlr_categories WHERE active=1 ORDER BY name')]})

    @app.post('/api/employees')
    @roles_required('admin')
    def create_employee():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Укажите данные сотрудника.')
        try:
            key = str(UUID(data.get('request_key', '')))
        except (ValueError, TypeError, AttributeError):
            abort(400, description='Обновите форму добавления сотрудника.')
        values = {}
        for field, limit in {'full_name': 200, 'personnel_no': 100, 'profession': 300,
                             'gsp_profession': 300, 'employer': 200, 'department': 500}.items():
            value = data.get(field, '')
            if not isinstance(value, str) or len(value) > limit or any(unicodedata.category(c).startswith('C') for c in value):
                abort(400, description='Проверьте длину полей и отсутствие служебных символов.')
            values[field] = unicodedata.normalize('NFC', value.strip())
        automatic = data.get('internal_number', False)
        if type(automatic) is not bool or not values['full_name'] or (not automatic and not values['personnel_no']) or (automatic and values['personnel_no']):
            abort(400, description='Укажите ФИО и табельный номер либо выберите внутренний код.')
        qualification = data.get('qualification', '')
        pps = data.get('pps', '')
        if qualification not in ('', 'Рабочие', 'ИТР', 'Другое') or pps not in ('', 'ППС15', 'ППС19'):
            abort(400, description='Выберите квалификацию и ППС из списка.')
        for field in ('crew_id', 'category_id', 'contractor_id'):
            value = data.get(field)
            if value is not None and (type(value) is not int or value <= 0):
                abort(400, description='Выберите бригаду, ГДЛР и подрядчика из справочников.')
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
                if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
                    abort(403, description='Добавлять сотрудников может администратор.')
                previous = db.execute('SELECT * FROM manual_employees WHERE request_key=?', (key,)).fetchone()
                if previous:
                    if previous['created_by'] != g.user['id'] or previous['payload_hash'] != digest:
                        abort(409, description='Этот запрос уже выполнен с другими данными. Закройте форму и обновите список.')
                    worker = db.execute('SELECT id,full_name,personnel_no FROM workers WHERE id=?', (previous['worker_id'],)).fetchone()
                    return jsonify({**dict(worker), 'already_created': True}), 200
                if automatic:
                    values['personnel_no'] = 'MAN-' + uuid4().hex[:16].upper()
                if db.execute('SELECT id FROM workers WHERE personnel_no=? COLLATE NOCASE', (values['personnel_no'],)).fetchone():
                    abort(409, description='Сотрудник с таким табельным номером уже существует, в том числе среди отключённых. Найдите его в списке.')
                crew = None
                if data.get('crew_id'):
                    crew = db.execute('''SELECT c.*,u.active owner_active,u.role owner_role FROM crews c
                        JOIN users u ON u.id=c.owner_user_id WHERE c.id=?''', (data['crew_id'],)).fetchone()
                    if not crew or not crew['owner_active'] or crew['owner_role'] not in ('admin', 'super_admin', 'foreman'):
                        abort(409, description='Бригада или её ответственный недоступны. Обновите форму.')
                bindings = {}
                for field, table in [('category', 'gdlr_categories'), ('contractor', 'contractors')]:
                    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (data.get(field + '_id'),)).fetchone()
                    if data.get(field + '_id') and (not row or not row['active'] or row['edit_token'] != data.get(field + '_token')):
                        abort(409, description='Справочник изменился. Закройте форму и обновите данные.')
                    bindings[field] = row
                category = bindings['category']['name'] if bindings['category'] else ''
                contractor = bindings['contractor']['name'] if bindings['contractor'] else ''
                worker = db.execute('''INSERT INTO workers(full_name,personnel_no,profession,gsp_profession,employer,department,pps,category,contractor)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (*[values[k] for k in ('full_name', 'personnel_no', 'profession', 'gsp_profession', 'employer', 'department')], pps, category, contractor)).lastrowid
                if crew:
                    db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (crew['id'], worker))
                if authorize:
                    authorize(db, worker, crew['id'] if crew else None)
                stamp = utc_now()
                db.execute('INSERT INTO manual_employees VALUES (?,?,?,?,?,?)', (worker, g.user['id'], stamp, qualification, key, digest))
                for field, table in [('category', 'employee_gdlr'), ('contractor', 'employee_contractors')]:
                    if bindings[field]:
                        db.execute(f'INSERT INTO {table}(worker_id,{field}_id,edit_token,updated_by,updated_at) VALUES (?,?,?,?,?)',
                                   (worker, bindings[field]['id'], secrets.token_hex(16), g.user['id'], stamp))
        except sqlite3.IntegrityError:
            abort(409, description='Данные сотрудника конфликтуют с существующей записью. Обновите список.')
        return jsonify({'id': worker, 'full_name': values['full_name'], 'personnel_no': values['personnel_no'], 'already_created': False}), 201
