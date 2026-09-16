"""Permanent employer corrections, with a preserved source value and optimistic locking."""
import hashlib
import secrets
import unicodedata

from flask import abort, g, jsonify, request

from user_smu_access import can_worker


def employer_token(name, edit_token=None):
    return edit_token or 'source:' + hashlib.sha256((name or '').encode('utf-8')).hexdigest()


def migrate_employers(db):
    # workers.employer remains the effective value for all existing readers. The
    # correction is authoritative on reimport; assignments keep their own snapshot.
    db.executescript('''
        CREATE TABLE IF NOT EXISTS employee_employers (
            worker_id INTEGER PRIMARY KEY REFERENCES workers(id),
            employer TEXT NOT NULL, source_employer TEXT,
            edit_token TEXT NOT NULL, updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS employee_employer_insert AFTER INSERT ON employee_employers
        BEGIN UPDATE workers SET employer=NEW.employer WHERE id=NEW.worker_id; END;
        CREATE TRIGGER IF NOT EXISTS employee_employer_update AFTER UPDATE OF employer ON employee_employers
        BEGIN UPDATE workers SET employer=NEW.employer WHERE id=NEW.worker_id; END;
        CREATE TRIGGER IF NOT EXISTS employee_employer_delete AFTER DELETE ON employee_employers
        BEGIN UPDATE workers SET employer=OLD.source_employer WHERE id=OLD.worker_id; END;
        CREATE TRIGGER IF NOT EXISTS preserve_employee_employer AFTER UPDATE OF employer ON workers
        WHEN EXISTS(SELECT 1 FROM employee_employers e WHERE e.worker_id=NEW.id AND NEW.employer IS NOT e.employer)
        BEGIN UPDATE workers SET employer=(SELECT employer FROM employee_employers WHERE worker_id=NEW.id)
              WHERE id=NEW.id; END;
    ''')


def register_employer_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/groups/employer')
    @roles_required('admin', 'foreman')
    def bind_group_employer():
        from staffing_shifts import validate_group_snapshot

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Некорректные данные работодателя.')
        name = data.get('employer')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            abort(400, description='Введите организацию-работодателя: от 1 до 200 символов.')
        if any(unicodedata.category(char).startswith('C') for char in name):
            abort(400, description='Название не должно содержать служебных символов.')
        name = ' '.join(unicodedata.normalize('NFC', name).split())
        ids = data.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected, crews = data.get('expected_tokens'), data.get('expected_crews')
        if not isinstance(expected, dict) or not isinstance(crews, dict) or any(
                not isinstance(expected.get(str(i)), str) or str(i) not in crews or
                (crews[str(i)] is not None and type(crews[str(i)]) is not int) for i in ids):
            abort(400, description='Обновите расстановку перед изменением работодателя.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman'):
                abort(403, description='Нет права изменять расстановку.')
            if getattr(db, 'dialect', None) == 'postgres' and actor['role'] == 'foreman':
                if not db.native('SELECT id FROM workforce_organizations WHERE name_key=log_casefold(trim(%s)) AND active',
                                 (name,)).fetchone():
                    abort(400, description='Выберите работодателя из готового справочника. Новую организацию добавляет администратор.')
            workers = db.execute('''SELECT w.id,w.active,w.employer,m.crew_id,e.edit_token
                FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
                LEFT JOIN employee_employers e ON e.worker_id=w.id
                WHERE w.id IN (''' + ','.join('?' for _ in ids) + ')', ids).fetchall()
            if len(workers) != len(ids):
                abort(409, description='Состав сотрудников изменился. Обновите расстановку.')
            for worker in workers:
                key = str(worker['id'])
                if not can_worker(db, worker['id']):
                    abort(403, description='Нет доступа к сотруднику.')
                if not worker['active'] or crews[key] != worker['crew_id']:
                    abort(409, description='Состав бригады изменился. Обновите расстановку.')
                if expected[key] != employer_token(worker['employer'], worker['edit_token']):
                    abort(409, description='Работодатель сотрудника уже изменён. Обновите расстановку.')
            validate_group_snapshot(db, ids, data)
            updated, now = [], utc_now()
            for worker in workers:
                token = secrets.token_hex(16)
                db.execute('''INSERT INTO employee_employers(worker_id,employer,source_employer,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET employer=excluded.employer,
                    edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (worker['id'], name, worker['employer'], token, g.user['id'], now))
                updated.append({'id': worker['id'], 'employer': name, 'employer_token': token})
        return jsonify({'rows': updated})
