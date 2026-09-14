"""Contractor catalog and persistent employee corrections."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone

from flask import abort, g, jsonify, request


def contractor_name(value):
    name = ' '.join(unicodedata.normalize('NFC', value or '').split())
    return name, name.casefold()


def placement_company(employer, contractor, contractor_key):
    """Outside LGSS, display the selected contractor; retain the source employer separately."""
    return contractor if contractor_key is not None and contractor_key != 'лгсс' else employer


def placement_company_sql(employer='a.employer'):
    # The caller supplies an internal SQL expression; ct is the joined contractor catalog.
    return f"CASE WHEN ct.name_key IS NOT NULL AND ct.name_key <> 'лгсс' THEN ct.name ELSE {employer} END"


def sync_contractors(db, user_id=None):
    """Populate only missing bindings; preserve every existing correction."""
    names = {row['name_key']: row['id'] for row in db.execute('SELECT id,name_key FROM contractors')}
    rows = db.execute('''SELECT w.id,w.contractor FROM workers w
        LEFT JOIN employee_contractors e ON e.worker_id=w.id WHERE e.worker_id IS NULL''').fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        name, key = contractor_name(row['contractor'])
        if not name:
            continue
        if key not in names:
            names[key] = db.execute('''INSERT INTO contractors(name,name_key,edit_token,updated_by,updated_at)
                VALUES (?,?,?,?,?)''', (name, key, secrets.token_hex(16), user_id, now)).lastrowid
        db.execute('''INSERT INTO employee_contractors(worker_id,contractor_id,edit_token,updated_by,updated_at)
            VALUES (?,?,?,?,?)''', (row['id'], names[key], secrets.token_hex(16), user_id, now))


def populate_contractors(db):
    if not any(contractor_name(row[0])[0] for row in db.execute('''SELECT w.contractor FROM workers w
            LEFT JOIN employee_contractors e ON e.worker_id=w.id WHERE e.worker_id IS NULL''')):
        return
    from staffing_import import backup_database
    backup_database(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        sync_contractors(db)


def migrate_contractors(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS contractors (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            edit_token TEXT NOT NULL, updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employee_contractors (
            worker_id INTEGER PRIMARY KEY REFERENCES workers(id),
            contractor_id INTEGER NOT NULL REFERENCES contractors(id),
            edit_token TEXT NOT NULL, updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_employee_contractors_contractor ON employee_contractors(contractor_id);
    """)


def register_contractor_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/groups/contractor')
    @roles_required('admin', 'foreman')
    def bind_group_contractor():
        from staffing_shifts import validate_group_snapshot

        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('contractor_id')) is not int:
            abort(400, description='Выберите подрядчика из справочника.')
        ids = data.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected, crews = data.get('expected_tokens'), data.get('expected_crews')
        if not isinstance(expected, dict) or not isinstance(crews, dict) or any(
                str(i) not in expected or type(crews.get(str(i))) is not int for i in ids):
            abort(400, description='Обновите расстановку перед изменением подрядчика.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman'):
                abort(403, description='Нет права изменять расстановку.')
            contractor = db.execute('SELECT * FROM contractors WHERE id=?', (data['contractor_id'],)).fetchone()
            if contractor is None or not contractor['active']:
                abort(400, description='Выберите действующего подрядчика из справочника.')
            workers = db.execute(f'''SELECT w.id,w.active,m.crew_id,c.owner_user_id,e.edit_token
                FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
                LEFT JOIN crews c ON c.id=m.crew_id
                LEFT JOIN employee_contractors e ON e.worker_id=w.id
                WHERE w.id IN ({','.join('?' for _ in ids)})''', ids).fetchall()
            if len(workers) != len(ids):
                abort(409, description='Состав сотрудников изменился. Обновите расстановку.')
            for worker in workers:
                key = str(worker['id'])
                if not can_worker(db, worker['id']):
                    abort(403, description='Сотрудник не входит в вашу бригаду.')
                if not worker['active'] or crews[key] != worker['crew_id']:
                    abort(409, description='Состав бригады изменился. Обновите расстановку.')
                if expected[key] != worker['edit_token']:
                    abort(409, description='Подрядчик сотрудника уже изменён. Обновите расстановку.')
            validate_group_snapshot(db, ids, data)
            updated, now = [], utc_now()
            for worker_id in ids:
                token = secrets.token_hex(16)
                db.execute('''INSERT INTO employee_contractors(worker_id,contractor_id,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET contractor_id=excluded.contractor_id,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (worker_id, contractor['id'], token, g.user['id'], now))
                updated.append({'id': worker_id, 'contractor_id': contractor['id'],
                                'contractor': contractor['name'], 'contractor_token': token})
        return jsonify({'rows': updated})

    @app.put('/api/staffing/workers/<int:worker_id>/contractor')
    @roles_required('admin', 'foreman')
    def bind_contractor(worker_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('contractor_id')) is not int:
            abort(400, description='Выберите подрядчика из справочника.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            worker = db.execute('''SELECT w.id,w.active,m.crew_id,c.owner_user_id FROM workers w
                LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
                WHERE w.id=?''', (worker_id,)).fetchone()
            if worker is None:
                abort(404, description='Сотрудник не найден.')
            if not worker['active']:
                abort(409, description='Сотрудник отключён. Обновите расстановку.')
            if not can_worker(db, worker['id']):
                abort(403, description='Сотрудник не входит в вашу бригаду.')
            if 'expected_crew_id' not in data or data['expected_crew_id'] != worker['crew_id']:
                abort(409, description='Бригада сотрудника изменилась. Обновите расстановку.')
            old = db.execute('SELECT edit_token FROM employee_contractors WHERE worker_id=?', (worker_id,)).fetchone()
            if 'expected_token' not in data or data['expected_token'] != (old[0] if old else None):
                abort(409, description='Подрядчик сотрудника уже изменён. Обновите расстановку.')
            contractor = db.execute('SELECT * FROM contractors WHERE id=?', (data['contractor_id'],)).fetchone()
            if contractor is None or not contractor['active']:
                abort(400, description='Выберите действующего подрядчика из справочника.')
            token = secrets.token_hex(16)
            db.execute('''INSERT INTO employee_contractors(worker_id,contractor_id,edit_token,updated_by,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET contractor_id=excluded.contractor_id,
                edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (worker_id, contractor['id'], token, g.user['id'], utc_now()))
        return jsonify({'contractor_id': contractor['id'], 'contractor': contractor['name'], 'contractor_token': token})

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные подрядчика.')
        return data

    def name_values(data):
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            abort(400, description='Введите название подрядчика от 1 до 200 символов.')
        if any(unicodedata.category(char).startswith('C') for char in name):
            abort(400, description='Название не должно содержать служебные символы.')
        return contractor_name(name)

    def check_admin(db):
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not actor or not actor['active'] or actor['role'] != 'super_admin':
            abort(403, description='Справочник изменяет только супер-администратор.')

    @app.get('/api/contractors')
    @roles_required('admin', 'foreman')
    def contractors():
        rows = [dict(row) for row in get_db().execute("""
            SELECT c.*,COUNT(e.worker_id) employee_count FROM contractors c
            LEFT JOIN employee_contractors e ON e.contractor_id=c.id GROUP BY c.id
        """)]
        return jsonify({'rows': sorted(rows, key=lambda row: row['name_key'])})

    @app.post('/api/contractors')
    @roles_required('super_admin')
    def create_contractor():
        name, key = name_values(payload())
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                check_admin(db)
                contractor_id = db.execute('''INSERT INTO contractors(name,name_key,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?)''', (name, key, secrets.token_hex(16), g.user['id'], utc_now())).lastrowid
        except sqlite3.IntegrityError:
            abort(409, description='Подрядчик с таким названием уже есть, в том числе среди отключённых.')
        return jsonify({'id': contractor_id}), 201

    @app.patch('/api/contractors/<int:contractor_id>')
    @roles_required('super_admin')
    def update_contractor(contractor_id):
        data = payload()
        name, key = name_values(data)
        if type(data.get('active')) is not bool:
            abort(400, description='Укажите, доступен ли подрядчик для выбора.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                check_admin(db)
                row = db.execute('SELECT * FROM contractors WHERE id=?', (contractor_id,)).fetchone()
                if row is None:
                    abort(404, description='Подрядчик не найден.')
                if data.get('expected_token') != row['edit_token']:
                    abort(409, description='Подрядчик изменён другим пользователем. Обновите справочник.')
                db.execute('''UPDATE contractors SET name=?,name_key=?,active=?,edit_token=?,updated_by=?,updated_at=?
                    WHERE id=?''', (name, key, data['active'], secrets.token_hex(16), g.user['id'], utc_now(), contractor_id))
        except sqlite3.IntegrityError:
            abort(409, description='Подрядчик с таким названием уже есть, в том числе среди отключённых.')
        return jsonify({'updated': contractor_id})

    @app.delete('/api/contractors/<int:contractor_id>')
    @roles_required('super_admin')
    def delete_contractor(contractor_id):
        data = payload()
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            check_admin(db)
            row = db.execute('SELECT * FROM contractors WHERE id=?', (contractor_id,)).fetchone()
            if row is None:
                abort(404, description='Подрядчик не найден.')
            if data.get('expected_token') != row['edit_token']:
                abort(409, description='Подрядчик уже изменён. Обновите справочник перед удалением.')
            if db.execute('SELECT 1 FROM employee_contractors WHERE contractor_id=? LIMIT 1', (contractor_id,)).fetchone():
                abort(409, description='Подрядчик связан с сотрудниками. Сначала выберите им другого подрядчика или отключите его для выбора.')
            db.execute('DELETE FROM contractors WHERE id=?', (contractor_id,))
        return jsonify({'deleted': contractor_id})
