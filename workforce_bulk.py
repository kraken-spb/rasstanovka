"""Atomic, scoped edits of workforce reference bindings, with preview revisions."""
from uuid import uuid4

from flask import abort, jsonify, request

from workforce_core import (ADMINS, EDITORS, PROFILE_SELECT, actor_scope, audit,
                            digest, plain, remember, replay, text_value)


FIELDS = {
    'smu_id': ('СМУ / проект', 'smu_catalog'),
    'division_id': ('Подразделение', 'workforce_divisions'),
    'employer_id': ('Организация-работодатель', 'workforce_organizations'),
    'profession_code': ('Должность / профессия', 'profession'),
    'category_id': ('Категория ГДЛР', 'gdlr_categories'),
    'employment_code': ('Статус сотрудника', 'employment'),
    'accommodation_code': ('Проживание', 'accommodation'),
    'citizenship_code': ('Гражданство', 'citizenship'),
    'contractor_id': ('Компания-подрядчик', 'contractors'),
    'origin_code': ('Город отправления', 'travelpoint'),
    'rotation_schedule_id': ('График вахты', 'workforce_rotation_schedules'),
}
CATALOG_KINDS = {'profession', 'employment', 'citizenship', 'travelpoint', 'accommodation'}


def ids_from(data):
    if (not isinstance(data, list) or not 1 <= len(data) <= 100
            or any(type(value) is not int or not 0 < value < 2**63 for value in data)
            or len(set(data)) != len(data)):
        abort(400, description='Выберите от 1 до 100 разных сотрудников.')
    return sorted(data)


def allowed_smus(db, actor):
    if actor['role'] == 'super_admin':
        return None
    access = db.native('SELECT mode,departments_json FROM user_smu_access WHERE user_id=%s', (actor['id'],)).fetchone()
    if access and access['mode'] != 'all':
        import json
        return json.loads(access['departments_json'])
    return None if access or actor['role'] in ADMINS else []


def references(db, actor, selected=None):
    result = {}
    departments = allowed_smus(db, actor) if selected is None or 'smu_id' in selected else None
    for key, (label, source) in FIELDS.items():
        if selected is not None and key not in selected:
            continue
        if source == 'workforce_divisions':
            rows = db.native("""SELECT d.id value,d.name || ' · ' || COALESCE(p.name,'ППС не указан') label,d.edit_token
                FROM workforce_divisions d LEFT JOIN pps_catalog p ON p.id=d.pps_id
                WHERE d.active AND (p.id IS NULL OR p.active=1) ORDER BY p.name,d.name,d.id""").fetchall()
        elif source in CATALOG_KINDS:
            rows = db.native('''SELECT c.code value,c.label,c.edit_token FROM workforce_catalog c
                LEFT JOIN workforce_catalog parent ON parent.code=c.specialty_code
                WHERE c.kind=%s AND c.active AND (c.specialty_code IS NULL OR parent.active)
                ORDER BY c.sort_order,c.label,c.code''', (source,)).fetchall()
        else:
            # All identifiers come from FIELDS, never from request data.
            active = 'active' if source.startswith('workforce_') else 'active=1'
            rows = db.native(f'SELECT id value,name label,edit_token FROM {source} WHERE {active} ORDER BY name,id').fetchall()
        rows = plain(rows)
        if key == 'smu_id' and departments is not None:
            rows = [row for row in rows if row['label'] in departments]
        if key == 'employment_code' and actor['role'] == 'recruitment':
            rows = [row for row in rows if row['value'] != 'employment.staff']
        result[key] = {'label': label, 'token': digest(rows),
                       'options': [{'value': str(row['value']), 'label': row['label']} for row in rows]}
    if 'smu_id' in result:
        result['smu_id']['note'] = 'Проект определяется привязкой выбранного СМУ. Состав бригады не меняется.'
    return result


def snapshots(db, ids):
    rows = plain(db.native(PROFILE_SELECT + ' WHERE w.id=ANY(%s) ORDER BY w.id', (ids,)).fetchall())
    bindings = {row['id']: plain(row) for row in db.native('''SELECT w.id,w.personnel_is_internal,
        ec.contractor_id,ct.name contractor_name,ec.edit_token contractor_token,
        eg.edit_token category_token,ee.edit_token employer_token
        FROM workers w LEFT JOIN employee_contractors ec ON ec.worker_id=w.id
        LEFT JOIN contractors ct ON ct.id=ec.contractor_id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id
        LEFT JOIN employee_employers ee ON ee.worker_id=w.id WHERE w.id=ANY(%s)''', (ids,)).fetchall()}
    for row in rows:
        row.update(bindings[row['id']])
        row['token'] = digest(row)
    return rows


def check_ownership(actor, rows):
    if actor['role'] not in ADMINS and any(
            (actor['role'] == 'rotation') != (row['employment_code'] == 'employment.staff') for row in rows):
        abort(403, description='Среди выбранных есть карточки другой службы. Перевахта редактирует штат, комплектация — остальных сотрудников.')


def lock_people(db, ids, ownership=True):
    actor, scope, args = actor_scope(db, write=True)
    rows = db.native(f'''SELECT w.id,w.active,p.employment_code FROM workers w
        JOIN workforce_profiles p ON p.worker_id=w.id WHERE w.id=ANY(%s) AND ({scope})
        ORDER BY w.id FOR UPDATE OF w,p''', [ids, *args]).fetchall()
    if len(rows) != len(ids):
        abort(404, description='Один из выбранных сотрудников недоступен в ваших СМУ. Обновите список.')
    if any(not row['active'] for row in rows):
        abort(409, description='В выборе есть сотрудник, выведенный из состава. Обновите список.')
    if ownership:
        check_ownership(actor, rows)
    return actor


def display_value(row, field):
    text = {'division_id': 'division', 'smu_id': 'department', 'employer_id': 'employer', 'profession_code': 'profession',
            'category_id': 'category', 'employment_code': 'employment', 'citizenship_code': 'citizenship',
            'accommodation_code': 'accommodation',
            'contractor_id': 'contractor_name', 'origin_code': 'origin_city', 'rotation_schedule_id': 'rotation_schedule'}
    return row.get(text[field]) or 'Не указано'


def apply_binding(db, actor, before, field, value, label):
    worker = before['id']
    if field in ('category_id', 'contractor_id'):
        table = 'employee_gdlr' if field == 'category_id' else 'employee_contractors'
        db.native(f'''INSERT INTO {table}(worker_id,{field},edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,now()::text) ON CONFLICT(worker_id) DO UPDATE SET
            {field}=excluded.{field},edit_token=excluded.edit_token,
            updated_by=excluded.updated_by,updated_at=excluded.updated_at''', (worker, int(value), uuid4().hex, actor['id']))
    elif field == 'smu_id':
        db.native('UPDATE workers SET department=%s WHERE id=%s', (label, worker))
        db.native('''INSERT INTO employee_smu(worker_id,smu_id,source_department) VALUES (%s,%s,%s)
            ON CONFLICT(worker_id) DO UPDATE SET smu_id=excluded.smu_id''', (worker, int(value), before['department']))
    elif field == 'profession_code':
        db.native('UPDATE workers SET profession_code=%s WHERE id=%s', (value, worker))
    elif field == 'employer_id':
        db.native('''INSERT INTO employee_employers(worker_id,employer,source_employer,edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,%s,now()::text) ON CONFLICT(worker_id) DO UPDATE SET
            employer=excluded.employer,edit_token=excluded.edit_token,
            updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (worker, label, before['employer'] or '', uuid4().hex, actor['id']))
        db.native('UPDATE workforce_profiles SET employer_id=%s WHERE worker_id=%s', (value, worker))
    elif field == 'rotation_schedule_id':
        db.native('UPDATE workforce_profiles SET rotation_schedule_id=%s,rotation_schedule=%s WHERE worker_id=%s', (value, label, worker))
    else:
        db.native(f'UPDATE workforce_profiles SET {field}=%s WHERE worker_id=%s', (value, worker))
    db.native('''UPDATE workforce_profiles SET edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
        WHERE worker_id=%s''', (actor['id'], worker))


def register_bulk_routes(app, database, roles_required):
    from workforce_auto_category import register_auto_category_routes
    register_auto_category_routes(app, database, roles_required)

    @app.post('/api/workforce/bulk/prepare')
    @roles_required(*EDITORS)
    def workforce_bulk_prepare():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'ids'}:
            abort(400, description='Передайте список выбранных сотрудников.')
        ids = ids_from(data['ids'])
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = lock_people(db, ids)
            fields = references(db, actor)
            rows = snapshots(db, ids)
            people = [{'id': row['id'], 'name': row['full_name'], 'token': row['token'],
                       'values': {key: display_value(row, key) for key in FIELDS}} for row in rows]
        return jsonify(fields=fields, people=people)

    @app.post('/api/workforce/bulk/apply')
    @roles_required(*EDITORS)
    def workforce_bulk_apply():
        data = request.get_json(silent=True)
        required = {'field', 'value', 'reference_token', 'people', 'reason', 'request_key'}
        if not isinstance(data, dict) or set(data) - required or (required - {'reason'}) - set(data) or not isinstance(data['field'], str) or data['field'] not in FIELDS:
            abort(400, description='Выберите поле из справочников.')
        if not isinstance(data['people'], list) or any(not isinstance(row, dict) or set(row) != {'id', 'token'} for row in data['people']):
            abort(400, description='Неверный список выбранных сотрудников.')
        ids = ids_from([row['id'] for row in data['people']])
        expected = {row['id']: row['token'] for row in data['people']}
        if any(not isinstance(token, str) or len(token) != 64 for token in expected.values()):
            abort(400, description='Обновите выбранные строки.')
        reason = text_value(data.get('reason', ''), 'Причина изменения', 10000)
        field = data['field']
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = lock_people(db, ids, ownership=False)
            previous = replay(db, actor, 'bulk-reference-edit', data)
            if previous is not None:
                return jsonify(previous)
            catalog = references(db, actor)[field]
            if data['reference_token'] != catalog['token']:
                abort(409, description='Справочник изменился. Откройте массовое редактирование заново.')
            option = next((row for row in catalog['options'] if row['value'] == data['value']), None)
            if option is None:
                abort(400, description='Выберите действующее значение справочника в пределах ваших прав.')
            before = snapshots(db, ids)
            check_ownership(actor, before)
            if any(row['token'] != expected[row['id']] for row in before):
                abort(409, description='Данные одного из сотрудников изменены. Ничего не сохранено: обновите список и повторите выбор.')
            if field == 'employment_code' and data['value'] != 'employment.staff' and any(
                    str(row['personnel_no']).isdigit() and not row['personnel_is_internal'] for row in before):
                abort(400, description='Сотрудник с цифровым табельным номером относится к штату.')
            changed = [row for row in before if str(row.get(field) or '') != data['value']]
            # Every selected row is validated before the first update.
            for row in changed:
                apply_binding(db, actor, row, field, data['value'], option['label'])
            after = {row['id']: row for row in snapshots(db, [row['id'] for row in changed])} if changed else {}
            for row in changed:
                audit(db, actor, 'update', 'profile', row['id'], row, after[row['id']], worker_id=row['id'], reason=reason)
            result = {'changed': len(changed), 'unchanged': len(before) - len(changed), 'field': field}
            remember(db, actor, 'bulk-reference-edit', data, result)
        return jsonify(result)
