"""Date-bound employee shifts and atomic placement across a mixed-shift brigade."""
import hashlib
import json
import secrets
import re
from user_smu_access import require_workers, require_crew, legacy_foreman
from datetime import date

from flask import abort, g, jsonify, request


def migrate_crewless_assignments(db):
    """Allow a genuine absent brigade in audit events, preserving all historic rows."""
    column = next(r for r in db.execute('PRAGMA table_info(assignment_events)') if r[1] == 'crew_id')
    if not column[3]:
        return
    from backup_api import create_backup
    db.commit()
    with db:
        db.execute('BEGIN IMMEDIATE')
        create_backup(db, reason='crewless-assignment-events')
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='assignment_events'").fetchone()[0]
        schema, count = re.subn(r'\bcrew_id\s+INTEGER\s+NOT\s+NULL\b', 'crew_id INTEGER', schema, flags=re.I)
        if count != 1:
            raise ValueError('Неизвестная структура журнала назначений.')
        schema, count = re.subn(r'^CREATE TABLE\s+["`\[]?assignment_events["`\]]?',
                               'CREATE TABLE assignment_events_crewless', schema, count=1, flags=re.I)
        if count != 1:
            raise ValueError('Неизвестное определение журнала назначений.')
        definitions = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='assignment_events' AND type IN ('index','trigger') AND sql IS NOT NULL")]
        db.execute(schema)
        db.execute('INSERT INTO assignment_events_crewless SELECT * FROM assignment_events')
        db.execute('DROP TABLE assignment_events')
        db.execute('ALTER TABLE assignment_events_crewless RENAME TO assignment_events')
        for statement in definitions:
            db.execute(statement)


def canonical_shift(value):
    return '2 смена' if value == 'Ночная смена' else value


def responsible_ref(row, field):
    """Public reference namespaces never confuse workforce IDs with file-person IDs."""
    worker = row[field + '_worker_id']
    return 'outstaff:' + str(worker) if worker is not None else row[field + '_person_id']


def resolve_responsible(db, reference, name):
    if reference is None:
        return None, None
    if isinstance(reference, str) and re.fullmatch(r'outstaff:[1-9][0-9]{0,17}', reference):
        worker = int(reference.split(':')[1])
        row = db.execute('''SELECT w.full_name FROM workers w JOIN outstaff_members o ON o.worker_id=w.id
            WHERE w.id=? AND w.active=1''', (worker,)).fetchone()
        if not row or row['full_name'] != name:
            abort(409, description='Сотрудник аутстаффа изменён или недоступен. Обновите список и выберите его заново.')
        return None, worker
    if type(reference) is not int:
        abort(400, description='Выберите ответственного из списка.')
    person = db.execute('SELECT full_name FROM staffing_people WHERE id=?', (reference,)).fetchone()
    if not person or person['full_name'] != name:
        abort(400, description='ФИО не соответствует выбранному сотруднику. Выберите его заново.')
    return reference, None


def responsibility_states(db, ids):
    if not ids:
        return {}
    result = {}
    from query_helpers import membership
    clause, parameters = membership(db, 'w.id', ids)
    for row in db.execute(f'''SELECT w.id,m.crew_id,c.owner_user_id,c.details_token,
            c.linear_itr,c.linear_itr_person_id,c.brigadier,c.brigadier_person_id,
            d.linear_itr_override,d.linear_itr_person_id row_itr_id,d.brigadier_override,
            d.brigadier_person_id row_brigadier_id,d.edit_token,
            p.personnel_no itr_number,p.full_name itr_person_name,p.id itr_person_id,
            c.linear_itr_worker_id,c.brigadier_worker_id,d.linear_itr_worker_id row_itr_worker_id,
            d.brigadier_worker_id row_brigadier_worker_id, ow.id itr_worker_id,ow.personnel_no itr_worker_number
        FROM workers w LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        LEFT JOIN staffing_row_details d ON d.worker_id=w.id
        LEFT JOIN staffing_people p ON p.id=CASE WHEN d.linear_itr_override IS NULL
            THEN c.linear_itr_person_id ELSE d.linear_itr_person_id END
        LEFT JOIN workers ow ON ow.id=CASE WHEN d.linear_itr_override IS NULL
            THEN c.linear_itr_worker_id ELSE d.linear_itr_worker_id END
        WHERE {clause}''', parameters):
        name = row['linear_itr_override'] if row['linear_itr_override'] is not None else row['linear_itr'] or ''
        if not name:
            identity, label = ['none'], 'Линейный ИТР не указан'
        elif row['itr_worker_id'] is not None:
            identity = ['outstaff', row['itr_worker_id']]
            label = name + ' · Аутстафф · ' + row['itr_worker_number']
        elif row['itr_person_id'] is not None:
            identity = ['person', row['itr_number'] or row['itr_person_id'], row['itr_person_name'].strip().casefold()]
            label = name + (' · таб. № ' + row['itr_number'] if row['itr_number'] else '')
        else:
            identity, label = ['manual', name.strip().casefold()], name + ' · вручную'
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        result[row['id']] = {'itr_group_key': 'itr-' + digest(identity), 'itr_group_label': label,
                              'group_token': digest(dict(row))}
    return result


def validate_group_snapshot(db, ids, payload):
    expected = payload.get('expected_group_tokens')
    if not isinstance(expected, dict) or any(not isinstance(expected.get(str(i)), str) for i in ids):
        abort(400, description='Обновите группы ответственных перед назначением.')
    current = responsibility_states(db, ids)
    if any(i not in current or expected[str(i)] != current[i]['group_token'] for i in ids):
        abort(409, description='Состав группы или ответственные изменились. Обновите таблицу.')


def day_states(db, day, ids, records=None, *, summary=False):
    if not ids:
        return {}
    from query_helpers import dated_records
    if records is None:
        records = {table: dated_records(db, table, day, ids) for table in ('staffing_shifts', 'assignments')}
    schedules = {r['worker_id']: dict(r) for r in records['staffing_shifts']}
    assignments = {worker_id: [] for worker_id in ids}
    for row in records['assignments']:
        assignments[row['worker_id']].append(dict(row))
    result = {}
    for worker_id in ids:
        records, schedule = assignments[worker_id], schedules.get(worker_id)
        if len(records) == 1:
            shift = canonical_shift(records[0]['shift'])
        elif not records:
            shift = schedule['shift'] if schedule else '1 смена'
        else:
            shift = None
        result[worker_id] = {'employee_shift': shift, 'shift_conflict': len(records) > 1, 'assignments': records}
        if not summary:
            result[worker_id]['day_token'] = hashlib.sha256(json.dumps([schedule, records], sort_keys=True).encode()).hexdigest()
    return result


def register_shift_routes(app, get_db, roles_required, utc_now):
    @app.put('/api/staffing/assignments/clear', defaults={'crew_id': None, 'operation': 'clear'})
    @app.put('/api/staffing/groups/assignments', defaults={'crew_id': None, 'operation': 'place'})
    @app.put('/api/staffing/groups/shifts', defaults={'crew_id': None, 'operation': 'shift'})
    @app.put('/api/staffing/crews/<int:crew_id>/shifts', defaults={'operation': 'shift'})
    @app.put('/api/staffing/crews/<int:crew_id>/assignments', defaults={'operation': 'place'})
    @roles_required('admin', 'foreman')
    def update_day(crew_id, operation):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            abort(400, description='Некорректные данные назначения.')
        try:
            day = date.fromisoformat(str(payload.get('date', ''))).isoformat()
        except ValueError:
            abort(400, description='Укажите дату расстановки.')
        ids = payload.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > (10000 if crew_id is None else 1000) or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected = payload.get('expected_tokens')
        if not isinstance(expected, dict) or any(str(i) not in expected for i in ids):
            abort(400, description='Обновите таблицу перед изменением.')
        expected_crews = payload.get('expected_crews')
        if crew_id is None and (not isinstance(expected_crews, dict) or any(
                str(i) not in expected_crews or (expected_crews[str(i)] is not None and
                (type(expected_crews[str(i)]) is not int or expected_crews[str(i)] <= 0)) for i in ids)):
            abort(400, description='Обновите состав сотрудников перед изменением.')
        shift, site = payload.get('shift'), payload.get('subobject_id')
        if operation == 'clear':
            site = None
        if operation == 'shift' and shift not in ('1 смена', '2 смена'):
            abort(400, description='Выберите дневную или ночную смену.')
        if operation == 'place' and ('subobject_id' not in payload or (site is not None and (type(site) is not int or site <= 0))):
            abort(400, description='Выберите подобъект.')
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman'):
                abort(403, description='Нет права изменять расстановку.')
            if crew_id is None and operation != 'clear':
                validate_group_snapshot(db, ids, payload)
            if crew_id is not None:
                crew = db.execute('SELECT * FROM crews WHERE id=?', (crew_id,)).fetchone()
                if not crew:
                    abort(404, description='Бригада не найдена.')
                require_crew(db, crew_id)
            members = db.execute(f"""SELECT w.id,w.employer,m.crew_id,c.owner_user_id FROM workers w
                LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
                WHERE w.active=1 AND w.id IN ({','.join('?' for _ in ids)})""", ids).fetchall()
            if len(members) != len(ids):
                abort(409, description='Состав бригады изменился. Обновите таблицу.')
            for member in members:
                expected_crew = expected_crews[str(member['id'])] if crew_id is None else crew_id
                if member['crew_id'] != expected_crew:
                    abort(409, description='Состав бригады изменился. Обновите таблицу.')
            require_workers(db, ids)
            if operation == 'shift' or (operation == 'place' and site is not None):
                from gdlr_api import require_staffing_workers
                require_staffing_workers(db, ids)
            if operation == 'place' and site is not None and not db.execute('SELECT id FROM subobjects WHERE id=?', (site,)).fetchone():
                abort(400, description='Подобъект не найден в текущем справочнике. Обновите страницу и выберите подобъект заново.')
            states = day_states(db, day, ids)
            for member in members:
                worker_id, member_crew_id = member['id'], member['crew_id']
                current = states[worker_id]
                if current['shift_conflict']:
                    abort(409, description='У сотрудника несколько назначений за день. Уточните их в разделе «Состав бригад».')
                if expected[str(worker_id)] != current['day_token']:
                    abort(409, description='Смена или назначение уже изменены. Обновите таблицу.')
                for old in current['assignments']:
                    if old['crew_id'] not in (None, member_crew_id) or (old['crew_id'] is None and legacy_foreman(db) and old['foreman_user_id'] != g.user['id']):
                        abort(409, description='Сотрудник назначен другой бригадой. Сначала уточните его назначение.')
                if operation != 'shift' and not current['employee_shift']:
                    abort(409, description='Сначала выберите смену каждому сотруднику или примените смену всей бригаде.')
            cleared = 0
            for member in members:
                member_crew_id = member['crew_id']
                assignment_owner = member['owner_user_id'] if member_crew_id is not None else g.user['id']
                current = states[member['id']]
                old = current['assignments'][0] if current['assignments'] else None
                if operation == 'clear' and old is None:
                    continue
                target_shift = shift if operation == 'shift' else current['employee_shift']
                target_site = (old['subobject_id'] if old else None) if operation == 'shift' else site
                db.execute('''INSERT INTO staffing_shifts(work_date,worker_id,shift,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(work_date,worker_id) DO UPDATE SET
                    shift=excluded.shift,edit_token=excluded.edit_token,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (day, member['id'], target_shift, secrets.token_urlsafe(16), g.user['id'], utc_now()))
                if old and target_site is None:
                    db.execute('DELETE FROM assignments WHERE id=?', (old['id'],))
                    cleared += 1
                elif old:
                    db.execute('UPDATE assignments SET shift=?,subobject_id=?,crew_id=?,foreman_user_id=?,edit_token=? WHERE id=?',
                        (target_shift, target_site, member_crew_id, assignment_owner, secrets.token_urlsafe(16), old['id']))
                elif target_site is not None:
                    db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token)
                        VALUES (?,?,?,?,?,?,?,?,?)''', (day, target_shift, target_site, member['id'], member['employer'], assignment_owner, utc_now(), member_crew_id, secrets.token_urlsafe(16)))
                def event(event_shift, before, after):
                    db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at)
                        VALUES (?,?,?,?,?,?,?,?)''', (member_crew_id, member['id'], day, event_shift, before, after, g.user['id'], utc_now()))
                if old and canonical_shift(old['shift']) != target_shift:
                    event(canonical_shift(old['shift']), old['subobject_id'], None)
                    event(target_shift, None, target_site)
                elif (old['subobject_id'] if old else None) != target_site:
                    event(target_shift, old['subobject_id'] if old else None, target_site)
        return jsonify({'saved': len(ids), 'date': day, **({'cleared': cleared} if operation == 'clear' else {})})
