"""Scoped manual recruitment registration with duplicate checks and durable provenance."""
from uuid import uuid4

from flask import abort, jsonify, request

from workforce_bulk import references
from workforce_core import (ADMINS, actor_scope, audit, date_value, digest, plain,
                            profile_snapshot, remember, replay, text_value, email_value)


FIELDS = ('smu_id', 'employer_id', 'contractor_id', 'profession_code', 'category_id',
          'citizenship_code', 'employment_code')


def creator(db):
    actor, _, _ = actor_scope(db, write=True)
    if actor['role'] not in ADMINS | {'recruitment'}:
        abort(403, description='Добавлять сотрудников в комплектацию может комплектующая служба или администратор.')
    return actor


def register_create_routes(app, database, roles_required):
    @app.get('/api/workforce/people/create-options')
    @roles_required('admin', 'recruitment')
    def workforce_create_options():
        db = database()
        actor = creator(db)
        return jsonify({'fields': {key: value for key, value in references(db, actor).items() if key in FIELDS},
                        'can_create_staff': actor['role'] in ADMINS})

    @app.post('/api/workforce/people')
    @roles_required('admin', 'recruitment')
    def workforce_create_person():
        data = request.get_json(silent=True)
        allowed = set(FIELDS) | {'full_name', 'personnel_no', 'birth_date', 'phone', 'email', 'notes', 'request_key', 'tokens'}
        if not isinstance(data, dict) or set(data) - allowed or not {'full_name', 'smu_id', 'employment_code', 'request_key', 'tokens'} <= set(data):
            abort(400, description='Проверьте поля формы добавления сотрудника.')
        full_name = ' '.join(text_value(data['full_name'], 'ФИО', 200, True).split())
        number = text_value(data.get('personnel_no', ''), 'Табельный номер', 100)
        if any(c.isspace() for c in number):
            abort(400, description='В табельном номере не должно быть пробелов.')
        birth = date_value(data.get('birth_date'), 'Дата рождения')
        phone = text_value(data.get('phone', ''), 'Контакт', 300)
        email = email_value(data.get('email', ''))
        notes = text_value(data.get('notes', ''), 'Примечание', 10000)
        if not isinstance(data['tokens'], dict):
            abort(400, description='Обновите справочники формы.')
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = creator(db)
            # Serialise retries and concurrent manual registrations before checking names.
            db.native("SELECT pg_advisory_xact_lock(hashtext('workforce-manual-create'))")
            previous = replay(db, actor, 'create_person', data)
            if previous:
                return jsonify(previous), 200
            options = references(db, actor)
            selected = {}
            for field in FIELDS:
                value = data.get(field)
                if value in ('', None):
                    if field in ('smu_id', 'employment_code'):
                        abort(400, description='Выберите СМУ и статус сотрудника.')
                    continue
                row = next((row for row in options[field]['options'] if str(row['value']) == str(value)), None)
                if not row:
                    abort(403 if field == 'smu_id' else 400, description='Недоступное значение: ' + options[field]['label'])
                if str(data['tokens'].get(field, '')) != str(options[field]['token']):
                    abort(409, description='Справочник изменился. Обновите значения в форме.')
                selected[field] = row
            employment = selected['employment_code']['value']
            if number and number.isdecimal() and employment != 'employment.staff':
                abort(400, description='Сотрудник с цифровым табельным номером относится к штату. Штат оформляет администратор или перевахта.')
            if number and db.native('SELECT 1 FROM workers WHERE personnel_no=%s', (number,)).fetchone():
                abort(409, description='Табельный номер уже занят. Он должен быть уникальным для всех сотрудников комплектации и перевахты, включая отключённых. Новая запись не создана.')
            duplicate = db.native('''SELECT id FROM workers WHERE
                replace(log_casefold(regexp_replace(trim(full_name),'\\s+',' ','g')),'ё','е')=
                replace(log_casefold(%s),'ё','е') LIMIT 1''', (full_name,)).fetchone()
            if duplicate:
                abort(409, description='Возможный дубль: сотрудник с таким ФИО или табельным номером уже есть, в том числе среди отключённых. Проверьте существующую карточку; новая запись не создана.')
            def value(field, key='value', default=None):
                return selected.get(field, {}).get(key, default)
            worker_id = db.native('''INSERT INTO workers(full_name,personnel_no,personnel_is_internal,
                department,employer,contractor,profession,profession_code,category)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                (full_name, number or 'MAN-' + uuid4().hex.upper(), not bool(number),
                 value('smu_id', 'label'), value('employer_id', 'label', ''), value('contractor_id', 'label', ''),
                 value('profession_code', 'label', ''), value('profession_code'), value('category_id', 'label', ''))).fetchone()['id']
            db.native('''UPDATE workforce_profiles SET employer_id=%s,employment_code=%s,citizenship_code=%s,
                birth_date=%s,phone=%s,email=%s,notes=%s,workforce_managed=TRUE,staffing_ready=FALSE,
                created_by=%s,updated_by=%s WHERE worker_id=%s''',
                (value('employer_id'), employment, value('citizenship_code'), birth, phone, email, notes,
                 actor['id'], actor['id'], worker_id))
            db.native('INSERT INTO employee_smu(worker_id,smu_id,source_department) VALUES (%s,%s,%s)',
                      (worker_id, value('smu_id'), value('smu_id', 'label')))
            for field, table in [('category_id', 'employee_gdlr'), ('contractor_id', 'employee_contractors')]:
                if field in selected:
                    db.native(f'''INSERT INTO {table}(worker_id,{field},edit_token,updated_by,updated_at)
                        VALUES (%s,%s,%s,%s,now()::text)''', (worker_id, value(field), uuid4().hex, actor['id']))
            db.native('''INSERT INTO manual_employees(worker_id,created_by,created_at,qualification,request_key,payload_hash)
                VALUES (%s,%s,now()::text,'',%s,%s)''',
                (worker_id, actor['id'], data['request_key'], digest(data)))
            db.native("INSERT INTO workforce_registry_memberships(worker_id,service,created_by) VALUES (%s,'recruitment',%s)",
                      (worker_id, actor['id']))
            result = profile_snapshot(db, worker_id)
            audit(db, actor, 'create', 'profile', worker_id, None, {**result, 'service': 'recruitment'},
                  worker_id=worker_id, reason='Ручное добавление в комплектации')
            remember(db, actor, 'create_person', data, result)
        return jsonify(plain(result)), 201
