"""Brigade ownership, persistent rosters and date-bound workplace editing."""
from user_smu_access import can_worker, can_crew, require_workers, require_crew, allowed_workers, worker_clause, explicit_scope, legacy_foreman, require_all
import secrets
import sqlite3
import hashlib
import json
import re
from contextlib import closing
from datetime import date
from pathlib import Path
from functools import lru_cache

from flask import abort, g, jsonify, request
from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer

from staffing_shifts import responsible_ref, resolve_responsible

SHIFTS = {"1 смена", "2 смена"}


@lru_cache(maxsize=12000)
def signed_employee_snapshot(secret_key, snapshot):
    """Cache immutable signed snapshots, never permissions or live assignments."""
    fingerprint = hashlib.sha256(json.dumps(dict(snapshot), sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return URLSafeSerializer(secret_key, salt='employee-membership').dumps(fingerprint)


def register_crew_routes(app, get_db, roles_required, utc_now):
    from manual_employees import register_manual_employees
    def authorize_manual(db, worker, crew):
        require_workers(db, [worker], allow_unowned=True)
        if crew is not None:
            require_crew(db, crew, whole=True)
    register_manual_employees(app, get_db, roles_required, utc_now, authorize_manual)
    from employee_removal import register_employee_removal_routes, restoration_token
    register_employee_removal_routes(app, get_db, roles_required, utc_now)
    def employee_rows(worker_id=None):
        return get_db().execute("""
            SELECT w.*, m.crew_id, c.name crew_name, c.owner_user_id,
                u.full_name owner_name, ec.category_id, ec.edit_token category_edit_token,
                gc.name category_name, gc.active category_active,
                COALESCE(d.linear_itr_override, c.linear_itr, '') linear_itr_name,
                COALESCE(d.brigadier_override, c.brigadier, '') brigadier_name
            FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
            LEFT JOIN crews c ON c.id=m.crew_id LEFT JOIN users u ON u.id=c.owner_user_id
            LEFT JOIN staffing_row_details d ON d.worker_id=w.id
            LEFT JOIN employee_gdlr ec ON ec.worker_id=w.id
            LEFT JOIN gdlr_categories gc ON gc.id=ec.category_id
        """ + (' WHERE w.id=?' if worker_id is not None else '')
            + ' ORDER BY w.full_name, w.personnel_no, w.id',
            (worker_id,) if worker_id is not None else ()).fetchall()

    def employee_token(row):
        return signed_employee_snapshot(app.secret_key, tuple(sorted(dict(row).items())))

    @app.get('/api/employees')
    @roles_required('admin', 'foreman')
    def employees():
        try:
            report_date = date.fromisoformat(request.args.get('date', date.today().isoformat())).isoformat()
        except ValueError:
            abort(400, description='Укажите корректную отчётную дату.')
        db = get_db()
        db.execute('BEGIN')
        assigned = {row['worker_id'] for row in db.execute(
            'SELECT DISTINCT worker_id FROM assignments WHERE work_date=?', (report_date,))}
        # Read source qualifications independently of the editable GDLR category.
        # Conflicting entries within the latest import of an identity remain unknown.
        manual = {r['worker_id']: dict(r) for r in db.execute('SELECT worker_id,qualification,created_at FROM manual_employees')}
        qualifications = {}
        for person in db.execute('''SELECT import_id,personnel_no,full_name,qualification
                FROM staffing_people ORDER BY import_id DESC,id DESC'''):
            key = (person['personnel_no'].strip().casefold(), person['full_name'].strip().casefold())
            latest = qualifications.setdefault(key, {'import_id': person['import_id'], 'values': set()})
            if latest['import_id'] == person['import_id']:
                latest['values'].add(person['qualification'].strip().casefold())
        from outstaff_api import reference_data
        outstaff = {r['worker_id']: dict(r) for r in db.execute('SELECT * FROM outstaff_members')}
        removals = {r['worker_id']: dict(r) for r in db.execute(
            'SELECT * FROM employee_removals')}
        restoration_workers = {r['id']: r for r in db.execute('SELECT * FROM workers WHERE active=0')}
        restorations = {r['worker_id']: r['edit_token'] for r in db.execute(
            'SELECT worker_id,edit_token FROM employee_restorations ORDER BY id')}
        rows = []
        employees = employee_rows()
        editable = set() if g.user['role'] == 'hr_viewer' else allowed_workers(db, [r['id'] for r in employees], allow_unowned=True)
        for row in employees:
            if request.args.get('scope') == 'outstaff' and row['id'] not in outstaff:
                continue
            item = dict(row)
            removal = removals.get(row['id'])
            item['removal'] = {key: removal[key] for key in ('worker_id', 'effective_date', 'reason', 'changed_at', 'actor_name')} if removal else None
            item['can_remove'] = bool(row['active'] and row['id'] in editable and g.user['role'] in ('admin', 'super_admin'))
            item['can_restore'] = bool(not row['active'] and row['id'] in editable and g.user['role'] in ('admin', 'super_admin'))
            item['restoration_token'] = restoration_token(restoration_workers[row['id']], removal, restorations.get(row['id'])) if item['can_restore'] else None
            item['outstaff'] = outstaff.get(row['id'])
            source = qualifications.get((row['personnel_no'].strip().casefold(), row['full_name'].strip().casefold()))
            qualification = next(iter(source['values'])) if source and len(source['values']) == 1 else ''
            if source is None and row['id'] in manual:
                qualification = manual[row['id']]['qualification'].casefold()
            item['manual_registration'] = manual.get(row['id'])
            item['qualification'] = qualification
            item['is_worker'] = qualification == 'рабочие'
            item['assigned_on_date'] = row['id'] in assigned
            item['source_category'] = row['category']
            item['category'] = row['category_name'] if row['category_id'] is not None else row['category']
            item['can_edit'] = bool(row['active'] and row['id'] in editable)
            item['membership_token'] = employee_token(row)
            rows.append(item)
        return jsonify({'rows': rows, 'date': report_date, 'departments': reference_data(db)[1] if request.args.get('scope') == 'outstaff' else [], 'summary': {
            'total': len(rows), 'workers': sum(row['is_worker'] for row in rows),
            'assigned': sum(row['assigned_on_date'] for row in rows),
            'unknown_qualification': sum(not row['qualification'] for row in rows)}})

    @app.put('/api/employees/<int:worker_id>/pps')
    @roles_required('admin', 'foreman')
    def employee_pps(worker_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('pps'), str) or payload['pps'] not in ('', 'ППС15', 'ППС19'):
            abort(400, description='Выберите ППС15, ППС19 или «Не указана».')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = next(iter(employee_rows(worker_id)), None)
            if row is None:
                abort(404, description='Сотрудник не найден.')
            if not row['active']:
                abort(409, description='Сотрудник отключён.')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman') or not can_worker(db, worker_id, allow_unowned=True):
                abort(403, description='Нет права изменять ППС этого сотрудника.')
            if payload.get('expected_token') != employee_token(row):
                abort(409, description='Данные сотрудника изменились. Обновите список и повторите выбор.')
            db.execute('UPDATE workers SET pps=? WHERE id=?', (payload['pps'], worker_id))
        return jsonify({'worker_id': worker_id, 'pps': payload['pps']})

    @app.put('/api/employees/<int:worker_id>/category')
    @roles_required('admin', 'foreman')
    def employee_category(worker_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get('category_id')) is not int or payload['category_id'] <= 0:
            abort(400, description='Выберите категорию из справочника.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = next(iter(employee_rows(worker_id)), None)
            if row is None:
                abort(404, description='Сотрудник не найден.')
            if not row['active']:
                abort(409, description='Сотрудник отключён.')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman') or (
                    not can_worker(db, worker_id, allow_unowned=True)):
                abort(403, description='Категорию изменяет ответственный прораб или администратор.')
            if payload.get('expected_token') != employee_token(row):
                abort(409, description='Данные сотрудника изменились. Обновите список и повторите выбор.')
            category = db.execute('SELECT * FROM gdlr_categories WHERE id=?', (payload['category_id'],)).fetchone()
            if not category or not category['active']:
                abort(400, description='Категория отсутствует или отключена. Обновите справочник.')
            if payload.get('category_token') != category['edit_token']:
                abort(409, description='Категория изменена. Обновите справочник и повторите выбор.')
            db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
                edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (worker_id, category['id'], secrets.token_hex(16), g.user['id'], utc_now()))
        return jsonify({'worker_id': worker_id, 'category_id': category['id']})

    def transfer_targets():
        return get_db().execute('''SELECT c.id,c.name,c.owner_user_id,c.details_token,
            u.full_name owner_name,u.active owner_active,u.role owner_role
            FROM crews c JOIN users u ON u.id=c.owner_user_id ORDER BY c.name,c.id''').fetchall()

    def transfer_target_token(row):
        return URLSafeSerializer(app.secret_key, salt='crew-transfer-target').dumps(dict(row))

    @app.get('/api/staffing/crew-options')
    @roles_required('admin', 'foreman')
    def crew_transfer_options():
        rows = [{'id': row['id'], 'name': row['name'], 'owner_name': row['owner_name'],
                 'target_token': transfer_target_token(row)} for row in transfer_targets()
                if row['owner_active'] and row['owner_role'] in ('super_admin', 'admin', 'foreman')
                and can_crew(get_db(), row['id'])]
        return jsonify({'rows': rows})

    @app.put('/api/staffing/groups/crew')
    @roles_required('admin', 'foreman')
    def transfer_group_crew():
        from staffing_shifts import validate_group_snapshot

        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('crew_id')) is not int or data['crew_id'] <= 0:
            abort(400, description='Выберите новую бригаду из списка.')
        ids = data.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников для перевода.')
        ids = sorted(set(ids))
        expected = data.get('expected_crews')
        if not isinstance(expected, dict) or any(type(expected.get(str(i))) is not int or expected[str(i)] <= 0 for i in ids):
            abort(400, description='Обновите расстановку перед изменением бригады.')
        if not isinstance(data.get('target_token'), str):
            abort(400, description='Обновите список бригад перед переводом.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman'):
                abort(403, description='Нет права изменять состав бригад.')
            target = next((row for row in transfer_targets() if row['id'] == data['crew_id']), None)
            if target is None:
                abort(404, description='Бригада не найдена. Обновите список.')
            if not can_crew(db, target['id']):
                abort(403, description='Новая бригада закреплена за другим прорабом.')
            if not target['owner_active'] or target['owner_role'] not in ('super_admin', 'admin', 'foreman'):
                abort(409, description='Ответственный новой бригады отключён или не может управлять бригадой.')
            if data['target_token'] != transfer_target_token(target):
                abort(409, description='Новая бригада или её ответственный изменились. Обновите список.')
            marks = ','.join('?' for _ in ids)
            workers = db.execute(f'''SELECT w.id,w.active,m.crew_id,c.owner_user_id
                FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
                LEFT JOIN crews c ON c.id=m.crew_id WHERE w.id IN ({marks})''', ids).fetchall()
            if len(workers) != len(ids):
                abort(409, description='Состав сотрудников изменился. Обновите расстановку.')
            for worker in workers:
                if not can_worker(db, worker['id']):
                    abort(403, description='Сотрудник не входит в вашу бригаду.')
                if not worker['active'] or expected[str(worker['id'])] != worker['crew_id']:
                    abort(409, description='Состав бригады изменился. Обновите расстановку.')
            validate_group_snapshot(db, ids, data)
            changed = db.execute(f'''UPDATE crew_members SET crew_id=?
                WHERE worker_id IN ({marks}) AND crew_id<>?''', [target['id'], *ids, target['id']]).rowcount
        return jsonify({'changed': changed, 'worker_ids': ids, 'crew_id': target['id'], 'crew_name': target['name']})

    @app.put('/api/employees/<int:worker_id>/crew')
    @roles_required('admin', 'foreman')
    def employee_crew(worker_id):
        payload = request.get_json(silent=True) or {}
        target = payload.get('crew_id')
        if 'crew_id' not in payload or (target is not None and (type(target) is not int or target <= 0)):
            abort(400, description='Выберите бригаду из списка.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = next(iter(employee_rows(worker_id)), None)
            if row is None:
                abort(404, description='Сотрудник не найден.')
            if not row['active']:
                abort(409, description='Сотрудник отключён. Изменение бригады недоступно.')
            require_workers(db, [worker_id], allow_unowned=True)
            if row['crew_id'] is not None:
                crew_access(row['crew_id'])
            if target is not None:
                crew_access(target)
            token = payload.get('expected_token')
            if not isinstance(token, str) or token != employee_token(row):
                abort(409, description='Данные сотрудника изменились. Обновите список и повторите выбор.')
            if target != row['crew_id']:
                db.execute('DELETE FROM crew_members WHERE worker_id=?', (worker_id,))
                if target is not None:
                    db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (target, worker_id))
        return jsonify({'worker_id': worker_id, 'crew_id': target})

    def crew_access(crew_id):
        crew = get_db().execute("SELECT * FROM crews WHERE id = ?", (crew_id,)).fetchone()
        if crew is None:
            abort(404, description="Бригада не найдена.")
        if not can_crew(get_db(), crew_id):
            abort(403, description="Эта бригада закреплена за другим прорабом.")
        return crew

    def period(values):
        try:
            work_date = date.fromisoformat(str(values.get("date", ""))).isoformat()
        except ValueError:
            abort(400, description="Укажите дату расстановки.")
        shift = values.get("shift")
        if shift not in SHIFTS:
            abort(400, description="Выберите смену из списка.")
        return work_date, shift

    def worker_ids(values):
        raw = values.get("worker_ids")
        if not isinstance(raw, list) or not raw or len(raw) > 1000:
            abort(400, description="Выберите сотрудников.")
        if any(type(item) is not int or item <= 0 for item in raw):
            abort(400, description="Некорректный список сотрудников.")
        return sorted(set(raw))

    def owner_id(values):
        value = values.get("owner_user_id")
        if type(value) is not int:
            abort(400, description="Выберите прораба.")
        owner = get_db().execute(
            "SELECT id FROM users WHERE id = ? AND active = 1 AND role IN ('super_admin', 'admin', 'foreman')", (value,)
        ).fetchone()
        if owner is None:
            abort(400, description="Выберите действующую учётную запись прораба.")
        return value

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def api_error(error):
        return jsonify({"error": error.description}), error.code

    def crew_catalog_token(crew):
        fields = ('id', 'name', 'owner_user_id', 'linear_itr', 'brigadier', 'details_token',
                  'linear_itr_person_id', 'brigadier_person_id', 'linear_itr_worker_id', 'brigadier_worker_id')
        return hashlib.sha256(json.dumps({key: crew[key] for key in fields},
            sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def catalog_members(db, crew_id=None):
        clause, params = worker_clause(db)
        extra = '' if crew_id is None else ' AND c.id=?'
        if crew_id is not None:
            params = [*params, crew_id]
        return db.execute(f"""SELECT w.id,w.full_name,w.personnel_no,w.department,w.employer,
            COALESCE(gc.name,w.category) category,w.active,m.crew_id
            FROM workers w JOIN crew_members m ON m.worker_id=w.id JOIN crews c ON c.id=m.crew_id
            LEFT JOIN employee_gdlr ec ON ec.worker_id=w.id
            LEFT JOIN gdlr_categories gc ON gc.id=ec.category_id
            WHERE ({clause}){extra} ORDER BY w.full_name,w.id""", params).fetchall()

    def catalog_crew_history(db):
        # Undo/redo guards also retain brigade IDs after a roster transfer.
        history_sql = '''
            SELECT crew_id FROM assignments WHERE crew_id IS NOT NULL
            UNION SELECT crew_id FROM assignment_events WHERE crew_id IS NOT NULL
            UNION SELECT crew_id FROM employee_removals WHERE crew_id IS NOT NULL
            UNION SELECT crew_id FROM employee_restorations WHERE crew_id IS NOT NULL
        '''
        if getattr(db, 'dialect', None) == 'postgres':
            # Match existing IDs as text so unrelated/non-numeric JSON values
            # cannot break the catalog through an integer cast.
            return {row[0] for row in db.native(history_sql + '''
                UNION SELECT c.id FROM staffing_action_history h
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    COALESCE(NULLIF(h.guards_json::jsonb->'crew_ids', 'null'::jsonb), '[]'::jsonb)
                ) AS guarded(crew_id)
                JOIN crews c ON c.id::text=guarded.crew_id
            ''')}
        return {row[0] for row in db.execute(history_sql + '''
            UNION SELECT value FROM staffing_action_history,
                json_each(staffing_action_history.guards_json, '$.crew_ids')
        ''')}

    def catalog_delete_reason(member_count, has_history):
        if member_count:
            return 'В бригаде есть сотрудники. Сначала переведите их в другую бригаду.'
        if has_history:
            return 'Бригада используется в расстановке или истории изменений. Удаление недоступно.'
        return ''

    @app.get('/api/crew-catalog')
    @roles_required('admin')
    def list_crew_catalog():
        db = get_db()
        members = {}
        for member in catalog_members(db):
            members.setdefault(member['crew_id'], []).append(member)
        totals = dict(db.execute('SELECT crew_id,COUNT(*) FROM crew_members GROUP BY crew_id').fetchall())
        history = catalog_crew_history(db) if g.user['role'] == 'super_admin' else set()
        result = []
        for crew in db.execute('SELECT * FROM crews ORDER BY name,id'):
            visible = members.get(crew['id'], [])
            total = totals.get(crew['id'], 0)
            if not visible and (total or not can_crew(db, crew['id'])):
                continue
            result.append({**{key: crew[key] for key in ('id','name','linear_itr','brigadier',
                'linear_itr_person_id','brigadier_person_id')},
                'linear_itr_person_id': responsible_ref(crew, 'linear_itr'),
                'brigadier_person_id': responsible_ref(crew, 'brigadier'),
                'expected_token': crew_catalog_token(crew), 'member_count': len(visible),
                'active_count': sum(bool(row['active']) for row in visible),
                'departments': sorted({row['department'] for row in visible if row['department']}),
                'can_edit': g.user['role'] != 'hr_viewer' and len(visible) == total,
                'can_delete': g.user['role'] == 'super_admin' and not total and crew['id'] not in history,
                'delete_reason': catalog_delete_reason(total, crew['id'] in history) if g.user['role'] == 'super_admin' else '',
                'imported': bool(crew['import_key'])})
        return jsonify({'rows': result})

    @app.delete('/api/crew-catalog/<int:crew_id>')
    @roles_required('super_admin')
    def delete_crew_catalog(crew_id):
        from backup_api import create_backup

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'expected_token'} or not isinstance(payload['expected_token'], str):
            abort(400, description='Обновите справочник перед удалением бригады.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
                if not actor or not actor['active'] or actor['role'] != 'super_admin':
                    abort(403, description='Удалять бригады из справочника может только супер-администратор.')
                crew = crew_access(crew_id)
                require_crew(db, crew_id, whole=True)
                if payload['expected_token'] != crew_catalog_token(crew):
                    abort(409, description='Бригада изменена. Закройте окно и обновите справочник перед удалением.')
                count = db.execute('SELECT COUNT(*) FROM crew_members WHERE crew_id=?', (crew_id,)).fetchone()[0]
                reason = catalog_delete_reason(count, crew_id in catalog_crew_history(db))
                if reason:
                    abort(409, description=reason)
                try:
                    create_backup(db, reason='before-crew-deletion')
                except (OSError, sqlite3.Error):
                    return jsonify({'error': 'Не удалось создать резервную копию. Бригада не удалена.'}), 503
                db.execute('DELETE FROM crews WHERE id=?', (crew_id,))
        except sqlite3.IntegrityError:
            abort(409, description='Бригада связана с другими данными. Удаление недоступно.')
        return jsonify({'deleted': crew_id})

    @app.get('/api/crew-catalog/<int:crew_id>/members')
    @roles_required('admin')
    def crew_catalog_members(crew_id):
        crew_access(crew_id)
        return jsonify({'rows': [dict(row) for row in catalog_members(get_db(), crew_id)]})

    @app.patch('/api/crew-catalog/<int:crew_id>')
    @roles_required('admin')
    def save_crew_catalog(crew_id):
        payload = request.get_json(silent=True)
        allowed = {'name','linear_itr','brigadier','linear_itr_person_id','brigadier_person_id','expected_token'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            abort(400, description='Некорректные данные бригады.')
        values = {}
        for field, limit in (('name',120),('linear_itr',200),('brigadier',200)):
            value = payload.get(field)
            if not isinstance(value, str) or len(value.strip()) > limit or (field == 'name' and not value.strip()):
                abort(400, description='Проверьте название бригады и ФИО ответственных.')
            values[field] = value.strip()
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                crew = crew_access(crew_id)
                require_crew(db, crew_id, whole=True)
                if payload.get('expected_token') != crew_catalog_token(crew):
                    abort(409, description='Бригада изменена в другом окне. Закройте форму и обновите справочник.')
                bindings = []
                for field in ('linear_itr','brigadier'):
                    reference = payload.get(field + '_person_id', responsible_ref(crew, field) if values[field] == crew[field] else None)
                    # Renaming a brigade must preserve a historical, now inactive responsible.
                    if values[field] == crew[field] and reference == responsible_ref(crew, field):
                        bindings.extend((crew[field + '_person_id'], crew[field + '_worker_id']))
                    else:
                        bindings.extend(resolve_responsible(db, reference, values[field]))
                db.execute('UPDATE crews SET name=?,linear_itr=?,brigadier=?,linear_itr_person_id=?,linear_itr_worker_id=?,brigadier_person_id=?,brigadier_worker_id=?,details_token=? WHERE id=?',
                    (values['name'],values['linear_itr'],values['brigadier'],*bindings,secrets.token_urlsafe(16),crew_id))
        except sqlite3.IntegrityError:
            abort(409, description='Бригада с таким названием уже существует. Укажите другое название.')
        return jsonify({'id': crew_id, **values})

    @app.get("/api/crews")
    @roles_required("admin", "foreman")
    def list_crews():
        clause, params = '', ()
        rows = get_db().execute(
            """SELECT c.id, c.name, c.owner_user_id, u.full_name owner_name, COUNT(m.worker_id) member_count
               FROM crews c JOIN users u ON u.id = c.owner_user_id
               LEFT JOIN crew_members m ON m.crew_id = c.id""" + clause
            + " GROUP BY c.id,u.full_name ORDER BY u.full_name, c.name", params,
        )
        return jsonify({"rows": [dict(row) for row in rows if can_crew(get_db(), row['id'])]})

    @app.post("/api/crews")
    @roles_required("admin")
    def create_crew():
        from backup_api import create_backup
        from staffing_shifts import validate_group_snapshot

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400, description='Укажите данные новой бригады.')
        name = payload.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            abort(400, description='Укажите название бригады, не более 120 символов.')
        name = name.strip()
        responsible = {}
        for field in ('linear_itr', 'brigadier'):
            value = payload.get(field, '')
            if not isinstance(value, str) or len(value) > 200:
                abort(400, description='ФИО должно быть текстом не длиннее 200 символов.')
            responsible[field] = value.strip()
        ids = payload.get('worker_ids', [])
        if not isinstance(ids, list) or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Некорректный список сотрудников.')
        ids = sorted(set(ids))
        expected = payload.get('expected_crews')
        if ids and (not isinstance(expected, dict) or any(str(i) not in expected or
                (expected[str(i)] is not None and (type(expected[str(i)]) is not int or expected[str(i)] <= 0)) for i in ids)):
            abort(400, description='Обновите состав бригад перед созданием.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
                if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
                    abort(403, description='Создавать бригады может администратор.')
                owner = owner_id({'owner_user_id': g.user['id'], **payload})
                if db.execute('SELECT id FROM crews WHERE owner_user_id=? AND name=?', (owner, name)).fetchone():
                    abort(409, description='У этого прораба уже есть бригада с таким названием.')
                if ids:
                    marks = ','.join('?' for _ in ids)
                    require_workers(db, ids)
                    workers = db.execute(f'''SELECT w.id,w.active,m.crew_id FROM workers w
                        LEFT JOIN crew_members m ON m.worker_id=w.id WHERE w.id IN ({marks})''', ids).fetchall()
                    if len(workers) != len(ids) or any(not row['active'] or expected[str(row['id'])] != row['crew_id'] for row in workers):
                        abort(409, description='Состав сотрудников изменился. Закройте форму и обновите расстановку.')
                    validate_group_snapshot(db, ids, payload)
                    create_backup(db, reason='before-crew-creation')
                bindings = []
                for field in ('linear_itr', 'brigadier'):
                    bindings.extend(resolve_responsible(db, payload.get(field + '_person_id'), responsible[field]))
                crew_id = db.execute('''INSERT INTO crews(name,owner_user_id,created_at,linear_itr,brigadier,details_token,
                    linear_itr_person_id,linear_itr_worker_id,brigadier_person_id,brigadier_worker_id) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (name, owner, utc_now(), responsible['linear_itr'], responsible['brigadier'], secrets.token_urlsafe(16),
                     *bindings)).lastrowid
                if ids:
                    db.executemany('''INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)
                        ON CONFLICT(worker_id) DO UPDATE SET crew_id=excluded.crew_id''', [(crew_id, i) for i in ids])
        except sqlite3.IntegrityError:
            abort(409, description='Не удалось создать бригаду. Обновите данные и проверьте название и состав.')
        return jsonify({'id': crew_id, 'name': name, 'member_count': len(ids), 'worker_ids': ids}), 201


    @app.patch("/api/crews/<int:crew_id>")
    @roles_required("admin")
    def update_crew(crew_id):
        get_db().execute('BEGIN IMMEDIATE')
        require_crew(get_db(), crew_id, whole=True)
        crew_access(crew_id)
        owner = owner_id(request.get_json(silent=True) or {})
        try:
            get_db().execute("UPDATE crews SET owner_user_id = ? WHERE id = ?", (owner, crew_id))
            get_db().commit()
        except sqlite3.IntegrityError:
            get_db().rollback()
            abort(409, description="У прораба уже есть бригада с таким названием.")
        return jsonify({"id": crew_id})

    def department_roster():
        return [dict(row) for row in get_db().execute('''
            SELECT w.id worker_id,w.department,m.crew_id,c.name crew_name,c.owner_user_id,u.full_name owner_name
            FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id
            LEFT JOIN crews c ON c.id=m.crew_id LEFT JOIN users u ON u.id=c.owner_user_id
            WHERE w.active=1 ORDER BY w.id''')]

    def construction_department(name):
        return bool(get_db().execute('SELECT 1 FROM smu_catalog WHERE name=?', (name,)).fetchone())

    @app.get('/api/crew-departments')
    @roles_required('admin')
    def crew_departments():
        departments = {}
        known = {r[0] for r in get_db().execute('SELECT name FROM smu_catalog')}
        for row in department_roster():
            name = row['department']
            if name not in known:
                continue
            entry = departments.setdefault(name, {'name': name, 'worker_count': 0, 'crews': set()})
            entry['worker_count'] += 1
            if row['crew_id'] is not None:
                entry['crews'].add(row['crew_id'])
        return jsonify({'rows': [{'name': name, 'worker_count': entry['worker_count'], 'crew_count': len(entry['crews'])}
                                 for name, entry in sorted(departments.items())]})

    def department_proposal(values):
        department = values.get('department')
        if not isinstance(department, str) or not construction_department(department):
            abort(400, description='Выберите строительно-монтажный участок.')
        include_mixed = values.get('include_mixed', False)
        if type(include_mixed) is not bool:
            abort(400, description='Укажите, включать ли смешанные бригады.')
        owner = owner_id(values)
        roster = department_roster()
        matching = [row for row in roster if row['department'] == department]
        if not matching:
            abort(400, description='Участок не найден в текущем составе сотрудников.')
        crew_ids = {row['crew_id'] for row in matching if row['crew_id'] is not None}
        groups = {}
        for row in roster:
            if row['crew_id'] not in crew_ids:
                continue
            entry = groups.setdefault(row['crew_id'], {'id': row['crew_id'], 'name': row['crew_name'],
                'owner_user_id': row['owner_user_id'], 'owner_name': row['owner_name'],
                'worker_count': 0, 'department_count': 0, 'departments': set()})
            entry['worker_count'] += 1
            entry['department_count'] += int(row['department'] == department)
            entry['departments'].add(row['department'] or 'Подразделение не указано')
        crews, excluded = [], []
        for entry in sorted(groups.values(), key=lambda item: item['name']):
            entry['departments'] = sorted(entry['departments'])
            entry['mixed'] = len(entry['departments']) > 1
            (excluded if entry['mixed'] and not include_mixed else crews).append(entry)
        target = dict(get_db().execute('SELECT id,full_name,username,role,active FROM users WHERE id=?', (owner,)).fetchone())
        # Membership and ownership changes invalidate the whole reviewed batch.
        snapshot = {'roster': [row for row in roster if row['crew_id'] in crew_ids or row['department'] == department], 'target': target}
        fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        names = {}
        chosen = {crew['id'] for crew in crews}
        for crew in crews:
            names[crew['name']] = names.get(crew['name'], 0) + 1
        for row in get_db().execute('SELECT id,name FROM crews WHERE owner_user_id=?', (owner,)):
            if row['id'] not in chosen:
                names[row['name']] = names.get(row['name'], 0) + 1
        conflicts = sorted(name for name, count in names.items() if count > 1)
        return {'department': department, 'owner_user_id': owner, 'owner_name': target['full_name'],
                'include_mixed': include_mixed, 'crews': crews, 'excluded': excluded, 'conflicts': conflicts,
                'unassigned_count': sum(row['crew_id'] is None for row in matching),
                'changed_count': sum(crew['owner_user_id'] != owner for crew in crews), 'fingerprint': fingerprint}

    @app.post('/api/crews/assign-department/preview')
    @roles_required('admin')
    def preview_department_assignment():
        values = request.get_json(silent=True) or {}
        if not isinstance(values, dict):
            abort(400, description='Некорректные данные назначения.')
        proposal = department_proposal(values)
        signed = {key: proposal[key] for key in ('department', 'owner_user_id', 'include_mixed', 'fingerprint')}
        signed['actor_id'] = g.user['id']
        token = URLSafeTimedSerializer(app.secret_key, salt='department-ownership').dumps(signed)
        return jsonify({**{key: value for key, value in proposal.items() if key != 'fingerprint'}, 'preview_token': token})

    @app.post('/api/crews/assign-department/apply')
    @roles_required('admin')
    def apply_department_assignment():
        require_all(get_db())
        values = request.get_json(silent=True) or {}
        if not isinstance(values, dict) or not isinstance(values.get('preview_token'), str):
            abort(400, description='Сначала проверьте список бригад участка.')
        try:
            signed = URLSafeTimedSerializer(app.secret_key, salt='department-ownership').loads(values['preview_token'], max_age=900)
        except BadSignature:
            abort(409, description='Предпросмотр устарел. Проверьте список заново.')
        if signed['actor_id'] != g.user['id']:
            abort(403, description='Предпросмотр подготовлен другим администратором.')
        db = get_db()
        checked = department_proposal(signed)
        if checked['fingerprint'] != signed['fingerprint']:
            abort(409, description='Состав участка, ответственные или пользователь изменились. Проверьте список заново.')
        if checked['conflicts'] or not checked['crews']:
            abort(409, description='Нет подходящих бригад или совпадают их названия. Проверьте список заново.')
        from backup_api import create_backup
        backup = create_backup(db, reason='before-smu-transfer')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
                if not actor or actor['role'] not in ('admin', 'super_admin') or not actor['active']:
                    abort(403, description='Для назначения необходима роль администратора.')
                proposal = department_proposal(signed)
                require_all(db)
                if proposal['fingerprint'] != signed['fingerprint']:
                    abort(409, description='Состав участка, ответственные или пользователь изменились. Проверьте список заново.')
                if proposal['conflicts']:
                    abort(409, description='У пользователя совпадают названия бригад: ' + ', '.join(proposal['conflicts']))
                if not proposal['crews']:
                    abort(400, description='Нет бригад для назначения. Проверьте смешанные группы.')
                for crew in proposal['crews']:
                    db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (proposal['owner_user_id'], crew['id']))
        except sqlite3.IntegrityError:
            abort(409, description='Названия бригад конфликтуют. Ни одна бригада не передана.')
        return jsonify({'updated': proposal['changed_count'], 'crew_count': len(proposal['crews']), 'backup': backup.name})

    @app.get("/api/crews/<int:crew_id>/board")
    @roles_required("admin", "foreman")
    def crew_board(crew_id):
        crew = crew_access(crew_id)
        work_date, shift = period(request.args)
        rows = get_db().execute(
            """SELECT w.id, w.full_name, w.personnel_no, w.employer, w.profession,
                      a.id assignment_id, a.subobject_id, a.crew_id assignment_crew_id,
                      a.foreman_user_id, a.edit_token, s.name subobject_name, o.name object_name
               FROM crew_members m JOIN workers w ON w.id = m.worker_id
               LEFT JOIN assignments a ON a.worker_id = w.id AND a.work_date = ?
                   AND (CASE WHEN a.shift = 'Ночная смена' THEN '2 смена' ELSE a.shift END) = ?
               LEFT JOIN subobjects s ON s.id = a.subobject_id
               LEFT JOIN objects o ON o.id = s.object_id
               WHERE m.crew_id = ? AND w.active = 1 ORDER BY w.full_name""",
            (work_date, shift, crew_id),
        )
        members = []
        for row in rows:
            if explicit_scope(get_db()) and not can_worker(get_db(), row['id']):
                continue
            item = dict(row)
            item["locked"] = bool(row["assignment_id"] and (
                row["assignment_crew_id"] not in (None, crew_id)
                or (row["assignment_crew_id"] is None and legacy_foreman(get_db())
                    and row["foreman_user_id"] != g.user["id"])
            ))
            members.append(item)
        return jsonify({"crew": dict(crew), "date": work_date, "shift": shift, "members": members})

    @app.get("/api/crews/<int:crew_id>/candidates")
    @roles_required("admin", "foreman")
    def crew_candidates(crew_id):
        crew_access(crew_id)
        rows = get_db().execute(
            """SELECT w.id, w.full_name, w.personnel_no, w.employer, w.profession,
                      m.crew_id, c.name crew_name
               FROM workers w LEFT JOIN crew_members m ON m.worker_id = w.id
               LEFT JOIN crews c ON c.id = m.crew_id
               WHERE w.active = 1 ORDER BY w.full_name"""
        )
        return jsonify({"rows": [dict(row) for row in rows if not explicit_scope(get_db()) or can_worker(get_db(), row['id'])]})

    @app.post("/api/crews/<int:crew_id>/members")
    @roles_required("admin", "foreman")
    def add_members(crew_id):
        ids = worker_ids(request.get_json(silent=True) or {})
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            crew_access(crew_id)
            placeholders = ",".join("?" for _ in ids)
            active = db.execute(f"SELECT id FROM workers WHERE active = 1 AND id IN ({placeholders})", ids).fetchall()
            if len(active) != len(ids):
                abort(400, description="Сотрудник не найден или отключён.")
            if explicit_scope(db):
                require_workers(db, ids)
            conflict = db.execute(
                f"SELECT worker_id FROM crew_members WHERE worker_id IN ({placeholders}) AND crew_id != ?",
                [*ids, crew_id],
            ).fetchone()
            if conflict:
                abort(409, description="Сотрудник уже состоит в другой бригаде. Обновите список.")
            db.executemany("INSERT OR IGNORE INTO crew_members(crew_id, worker_id) VALUES (?, ?)", [(crew_id, item) for item in ids])
        return jsonify({"added": len(ids)})

    @app.delete("/api/crews/<int:crew_id>/members/<int:worker_id>")
    @roles_required("admin", "foreman")
    def remove_member(crew_id, worker_id):
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            crew_access(crew_id)
            if explicit_scope(db):
                require_workers(db, [worker_id])
            db.execute("DELETE FROM crew_members WHERE crew_id = ? AND worker_id = ?", (crew_id, worker_id))
        # Assignments keep their own brigade/date attribution after roster changes.
        return jsonify({"removed": worker_id})

    @app.put("/api/crews/<int:crew_id>/assignments")
    @roles_required("admin", "foreman")
    def set_workplace(crew_id):
        payload = request.get_json(silent=True) or {}
        work_date, shift = period(payload)
        ids = worker_ids(payload)
        site = payload.get("subobject_id")
        if site is not None and (type(site) is not int or site <= 0):
            abort(400, description="Выберите место работы из списка.")
        expected = payload.get("expected_tokens")
        if not isinstance(expected, dict) or any(str(item) not in expected for item in ids):
            abort(400, description="Обновите расстановку перед изменением.")
        db = get_db()
        changed = 0
        with db:
            db.execute("BEGIN IMMEDIATE")
            crew = crew_access(crew_id)
            if site is not None and not db.execute("SELECT id FROM subobjects WHERE id = ?", (site,)).fetchone():
                abort(400, description="Подобъект не найден.")
            placeholders = ",".join("?" for _ in ids)
            members = db.execute(
                f"""SELECT w.id, w.employer FROM crew_members m JOIN workers w ON w.id = m.worker_id
                    WHERE m.crew_id = ? AND w.active = 1 AND w.id IN ({placeholders})""", [crew_id, *ids]
            ).fetchall()
            if len(members) != len(ids):
                abort(409, description="Состав бригады изменился. Обновите список.")
            require_workers(db, ids)
            if site is not None:
                from gdlr_api import require_staffing_workers
                require_staffing_workers(db, ids)
            current_rows = db.execute(
                f"""SELECT * FROM assignments WHERE work_date = ?
                    AND (CASE WHEN shift = 'Ночная смена' THEN '2 смена' ELSE shift END) = ?
                    AND worker_id IN ({placeholders})""",
                [work_date, shift, *ids],
            ).fetchall()
            current = {row["worker_id"]: row for row in current_rows}
            if len(current) != len(current_rows):
                abort(409, description="Обнаружены старые дубли ночной смены. Обратитесь к администратору.")
            for member in members:
                old = current.get(member["id"])
                if old and (old["crew_id"] not in (None, crew_id) or (
                    old["crew_id"] is None and legacy_foreman(db) and old["foreman_user_id"] != g.user["id"]
                )):
                    abort(409, description="Сотрудник уже назначен другой бригадой в эту смену.")
                if (not old and site is None) or (old and old["crew_id"] == crew_id and old["subobject_id"] == site):
                    continue  # An identical retry is harmless, and does not double-count.
                token = old["edit_token"] if old else None
                if expected[str(member["id"])] != token:
                    abort(409, description="Расстановка уже изменена в другом окне. Обновите список и повторите выбор.")
                if site is None:
                    db.execute("DELETE FROM assignments WHERE id = ?", (old["id"],))
                elif old:
                    db.execute(
                        """UPDATE assignments SET subobject_id = ?, crew_id = ?, foreman_user_id = ?,
                           edit_token = ? WHERE id = ?""",
                        (site, crew_id, crew["owner_user_id"], secrets.token_urlsafe(16), old["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO assignments(work_date, shift, subobject_id, worker_id, employer,
                               foreman_user_id, created_at, crew_id, edit_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(work_date, shift, worker_id) DO UPDATE SET
                               subobject_id = excluded.subobject_id, crew_id = excluded.crew_id,
                               foreman_user_id = excluded.foreman_user_id, edit_token = excluded.edit_token""",
                        (work_date, shift, site, member["id"], member["employer"], crew["owner_user_id"],
                         utc_now(), crew_id, secrets.token_urlsafe(16)),
                    )
                db.execute(
                    """INSERT INTO assignment_events(crew_id, worker_id, work_date, shift, before_subobject_id,
                           after_subobject_id, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (crew_id, member["id"], work_date, shift, old["subobject_id"] if old else None, site, g.user["id"], utc_now()),
                )
                changed += 1
        return jsonify({"saved": len(ids), "changed": changed, "date": work_date, "shift": shift})
