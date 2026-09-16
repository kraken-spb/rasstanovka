"""Administrator-owned category catalog, independent of attendance text."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman
import secrets
import sqlite3
import unicodedata

from flask import abort, g, jsonify, request
from backup_api import create_backup


STAFFING_CATEGORY_NAMES = (
    'Арматурщик', 'Бетонщик', 'Монтажник СиЖБК', 'Монтажник ТТ',
    'Прочие монтажники', 'Прочие основные рабочие', 'Прочие сварщики и газорезчики',
    'Сварщик АиПАМ', 'Сварщик МК', 'Сварщик ТТ', 'Электромонтажник', 'Изолировщик',
)


def staffing_eligible_sql(worker='w'):
    """Current catalog binding is authoritative; source text never grants access."""
    return f'''EXISTS (SELECT 1 FROM employee_gdlr staffing_binding
        JOIN gdlr_categories staffing_category ON staffing_category.id=staffing_binding.category_id
        WHERE staffing_binding.worker_id={worker}.id
          AND staffing_category.active=1 AND staffing_category.staffing_allowed=1)'''


def require_staffing_workers(db, ids):
    count = db.execute(f'''SELECT COUNT(*) FROM workers w
        WHERE w.id IN ({','.join('?' for _ in ids)}) AND {staffing_eligible_sql()}''', ids).fetchone()[0]
    if count != len(set(ids)):
        abort(409, description='Категория ГДЛР сотрудника не допускается в расстановку. '
              'Проверьте категорию в списке сотрудников и обновите таблицу.')


def bind_import_category(db, worker_id, user_id, stamp):
    """Bind an exact catalog match only when the worker has no current binding."""
    row = db.execute('''SELECT w.category FROM workers w WHERE w.id=?
        AND NOT EXISTS (SELECT 1 FROM employee_gdlr e WHERE e.worker_id=w.id)''', (worker_id,)).fetchone()
    if row is None:
        return
    key = unicodedata.normalize('NFC', ' '.join((row['category'] or '').split())).casefold()
    category = db.execute('SELECT id FROM gdlr_categories WHERE name_key=? AND active=1', (key,)).fetchone()
    if category:
        db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
            VALUES (?,?,?,?,?)''', (worker_id, category['id'], secrets.token_hex(16), user_id, stamp))


def migrate_gdlr(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS gdlr_categories (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            edit_token TEXT NOT NULL, updated_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employee_gdlr (
            worker_id INTEGER PRIMARY KEY REFERENCES workers(id),
            category_id INTEGER NOT NULL REFERENCES gdlr_categories(id),
            edit_token TEXT NOT NULL, updated_by INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_employee_gdlr_category ON employee_gdlr(category_id);
    """)
    if 'staffing_allowed' not in {r['name'] for r in db.execute('PRAGMA table_info(gdlr_categories)')}:
        db.execute('''ALTER TABLE gdlr_categories ADD COLUMN staffing_allowed INTEGER
            NOT NULL DEFAULT 0 CHECK(staffing_allowed IN (0,1))''')
        keys = [name.casefold() for name in STAFFING_CATEGORY_NAMES]
        db.execute(f"UPDATE gdlr_categories SET staffing_allowed=1 WHERE name_key IN ({','.join('?' for _ in keys)})", keys)


def register_gdlr_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/groups/category')
    @roles_required('admin', 'foreman')
    def bind_selected_category():
        from staffing_shifts import validate_group_snapshot
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('category_id')) is not int or data['category_id'] <= 0:
            abort(400, description='Выберите категорию из справочника.')
        ids = data.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Отметьте сотрудников для изменения категории.')
        ids = sorted(set(ids))
        expected, crews = data.get('expected_tokens'), data.get('expected_crews')
        if not isinstance(expected, dict) or not isinstance(crews, dict) or any(
                str(i) not in expected or type(crews.get(str(i))) is not int for i in ids):
            abort(400, description='Обновите расстановку перед изменением категории.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman'):
                abort(403, description='Нет права изменять категории сотрудников.')
            category = db.execute('SELECT * FROM gdlr_categories WHERE id=?', (data['category_id'],)).fetchone()
            if not category or not category['active'] or not category['staffing_allowed']:
                abort(400, description='Категория отсутствует, отключена или не допускается в расстановку. Выберите категорию из списка.')
            if data.get('category_token') != category['edit_token']:
                abort(409, description='Категория изменена. Обновите справочник и повторите выбор.')
            workers = db.execute(f'''SELECT w.id,w.active,m.crew_id,c.owner_user_id,e.edit_token
                FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
                LEFT JOIN crews c ON c.id=m.crew_id LEFT JOIN employee_gdlr e ON e.worker_id=w.id
                WHERE w.id IN ({','.join('?' for _ in ids)})''', ids).fetchall()
            if len(workers) != len(ids) or any(not r['active'] for r in workers):
                abort(409, description='Список сотрудников изменился. Обновите расстановку.')
            for worker in workers:
                key = str(worker['id'])
                if not can_worker(db, worker['id']):
                    abort(403, description='Категорию изменяет ответственный прораб или администратор.')
                if crews[key] != worker['crew_id']:
                    abort(409, description='Бригада сотрудника изменилась. Обновите расстановку.')
                if expected[key] != worker['edit_token']:
                    abort(409, description='Категория сотрудника уже изменена. Обновите расстановку.')
            validate_group_snapshot(db, ids, data)
            try:
                create_backup(db, reason='category-correction')
            except (OSError, sqlite3.Error):
                abort(503, description='Не удалось создать резервную копию. Категории не изменены.')
            now = utc_now()
            for worker_id in ids:
                db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (worker_id, category['id'], secrets.token_hex(16), g.user['id'], now))
        return jsonify({'saved': len(ids), 'category_id': category['id'], 'category': category['name']})

    @app.put('/api/staffing/workers/<int:worker_id>/category')
    @roles_required('admin', 'foreman')
    def bind_staffing_category(worker_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('category_id')) is not int or data['category_id'] <= 0:
            abort(400, description='Выберите категорию из справочника.')
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
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman') or (
                    not can_worker(db, worker['id'])):
                abort(403, description='Категорию изменяет ответственный прораб или администратор.')
            if 'expected_crew_id' not in data or data['expected_crew_id'] != worker['crew_id']:
                abort(409, description='Бригада сотрудника изменилась. Обновите расстановку.')
            old = db.execute('SELECT edit_token FROM employee_gdlr WHERE worker_id=?', (worker_id,)).fetchone()
            if 'expected_token' not in data or data['expected_token'] != (old['edit_token'] if old else None):
                abort(409, description='Категория сотрудника уже изменена. Обновите расстановку.')
            category = db.execute('SELECT * FROM gdlr_categories WHERE id=?', (data['category_id'],)).fetchone()
            if not category or not category['active'] or not category['staffing_allowed']:
                abort(400, description='Категория отсутствует, отключена или не допускается в расстановку. Выберите категорию из списка.')
            if data.get('category_token') != category['edit_token']:
                abort(409, description='Категория изменена. Обновите справочник и повторите выбор.')
            token = secrets.token_hex(16)
            db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
                edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (worker_id, category['id'], token, g.user['id'], utc_now()))
        return jsonify({'worker_id': worker_id, 'category_id': category['id'], 'category': category['name'],
                        'category_binding_token': token})

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные категории.')
        return data

    def name_values(data):
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            abort(400, description='Введите название категории от 1 до 200 символов.')
        if any(unicodedata.category(char).startswith('C') for char in name):
            abort(400, description='Название не должно содержать служебные символы.')
        name = ' '.join(name.split())
        return name, unicodedata.normalize('NFC', name).casefold()

    def check_admin(db):
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not actor or not actor['active'] or actor['role'] != 'super_admin':
            abort(403, description='Справочник изменяет только супер-администратор.')

    @app.get('/api/gdlr-categories')
    @roles_required('admin', 'foreman')
    def categories():
        rows = [dict(row) for row in get_db().execute("""
            SELECT c.*,COUNT(e.worker_id) employee_count FROM gdlr_categories c
            LEFT JOIN employee_gdlr e ON e.category_id=c.id GROUP BY c.id
        """)]
        return jsonify({'rows': sorted(rows, key=lambda row: row['name_key'])})

    @app.post('/api/gdlr-categories')
    @roles_required('super_admin')
    def create_category():
        name, key = name_values(payload())
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                check_admin(db)
                category_id = db.execute('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?)''', (name, key, secrets.token_hex(16), g.user['id'], utc_now())).lastrowid
        except sqlite3.IntegrityError:
            abort(409, description='Категория с таким названием уже есть, в том числе среди отключённых.')
        return jsonify({'id': category_id}), 201

    @app.patch('/api/gdlr-categories/<int:category_id>')
    @roles_required('super_admin')
    def update_category(category_id):
        data = payload()
        name, key = name_values(data)
        if type(data.get('active')) is not bool:
            abort(400, description='Укажите, доступна ли категория для выбора.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                check_admin(db)
                row = db.execute('SELECT * FROM gdlr_categories WHERE id=?', (category_id,)).fetchone()
                if row is None:
                    abort(404, description='Категория не найдена.')
                if data.get('expected_token') != row['edit_token']:
                    abort(409, description='Категория изменена другим пользователем. Обновите справочник.')
                db.execute('''UPDATE gdlr_categories SET name=?,name_key=?,active=?,edit_token=?,updated_by=?,updated_at=?
                    WHERE id=?''', (name, key, data['active'], secrets.token_hex(16), g.user['id'], utc_now(), category_id))
        except sqlite3.IntegrityError:
            abort(409, description='Категория с таким названием уже есть, в том числе среди отключённых.')
        return jsonify({'updated': category_id})

    @app.delete('/api/gdlr-categories/<int:category_id>')
    @roles_required('super_admin')
    def delete_category(category_id):
        data = payload()
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            check_admin(db)
            row = db.execute('SELECT * FROM gdlr_categories WHERE id=?', (category_id,)).fetchone()
            if row is None:
                abort(404, description='Категория не найдена.')
            if data.get('expected_token') != row['edit_token']:
                abort(409, description='Категория уже изменена. Обновите справочник перед удалением.')
            if db.execute('SELECT 1 FROM employee_gdlr WHERE category_id=? LIMIT 1', (category_id,)).fetchone():
                abort(409, description='Категория связана с сотрудниками. Сначала измените их категорию или отключите её для выбора.')
            db.execute('DELETE FROM gdlr_categories WHERE id=?', (category_id,))
        return jsonify({'deleted': category_id})
