"""Apply a server-prepared, reviewed source bundle to the isolated master registry.

No worksheet cell is an instruction. Durable worker matches and their snapshot
tokens must be supplied by the preview builder, never by an unreviewed upload.
"""
from datetime import date, datetime, timezone
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from gdlr_api import STAFFING_CATEGORY_NAMES
from workforce_core import audit, digest, plain
from workforce_identity import legacy_outstaff_key, normalized, worker_index
from workforce_lifecycle import classify
from workforce_sources import SourceProblem, source_date
from workforce_rotations import planned_dates


def smu_name(fields):
    value = fields.get('smu') or fields.get('department') or ''
    match = re.fullmatch(r'\s*(?:(?:СМУ\s*[-№]?\s*|Строительно-монтажный участок\s*№?\s*))?(\d+(?:\.\d+)*)\s*', value, re.I)
    return 'Строительно-монтажный участок № ' + match[1] if match else None


def seed_references(db, groups, actor):
    stamp = datetime.now(timezone.utc).isoformat()
    categories = {normalized(row['name']): row['id'] for row in db.native('SELECT id,name FROM gdlr_categories')}
    smus = {normalized(row['name']): row['id'] for row in db.native('SELECT id,name FROM smu_catalog')}
    schedules = {normalized(row['name']): plain(row) for row in db.native('SELECT * FROM workforce_rotation_schedules')}
    citizenship = {normalized(row['label']): row['code'] for row in db.native("SELECT * FROM workforce_catalog WHERE kind='citizenship'")}
    organizations = {normalized(row['name']): row['id'] for row in db.native('SELECT id,name FROM workforce_organizations')}
    allowed = {normalized(name) for name in STAFFING_CATEGORY_NAMES}
    for group in groups:
        fields = group['merged']['fields']
        category = group.get('category') or fields.get('category') or ''
        if category and normalized(category) not in categories:
            row = db.native('''INSERT INTO gdlr_categories(name,name_key,active,edit_token,updated_by,updated_at,staffing_allowed)
                VALUES (%s,%s,1,%s,%s,%s,%s) RETURNING id''',
                (category, normalized(category), uuid4().hex, actor['id'], stamp, int(normalized(category) in allowed))).fetchone()
            categories[normalized(category)] = row['id']
            audit(db, actor, 'create', 'gdlr_category', row['id'], None, {'name': category}, reason='Подтверждённый импорт УРП')
        name = smu_name(fields)
        if name and normalized(name) not in smus:
            # Match the existing SMU catalog's explicit text/key schema.
            row = db.native('''INSERT INTO smu_catalog(name,active,edit_token,updated_by,updated_at)
                VALUES (%s,1,%s,%s,%s) RETURNING id''',
                (name, uuid4().hex, actor['id'], stamp)).fetchone()
            smus[normalized(name)] = row['id']
            audit(db, actor, 'create', 'smu', row['id'], None, {'name': name}, reason='СМУ из исходного файла')
        if name:
            code = name.rsplit('№ ', 1)[-1].split('.')
            project = 'project.ukpg45' if code[0] == '15' else {
                ('19', '1'): 'project.stage15', ('19', '2'): 'project.ukpg45line', ('19', '3'): 'project.stage5'}.get(tuple(code[:2]))
            if project:
                db.native('''INSERT INTO workforce_smu_projects(smu_id,project_code,updated_by) VALUES (%s,%s,%s)
                    ON CONFLICT(smu_id) DO NOTHING''', (smus[normalized(name)], project, actor['id']))
        country = (fields.get('citizenship') or '').upper().strip()
        if country and normalized(country) not in citizenship:
            code = 'citizenship.' + uuid4().hex
            db.native('INSERT INTO workforce_catalog(code,kind,label,updated_by) VALUES (%s,\'citizenship\',%s,%s)',
                      (code, country, actor['id']))
            citizenship[normalized(country)] = code
            audit(db, actor, 'create', 'citizenship', code, None, {'label': country}, reason='Справочник из исходного файла')
        employer = (fields.get('employer') or '').strip()
        if employer and normalized(employer) not in organizations:
            row = db.native('INSERT INTO workforce_organizations(name,updated_by) VALUES (%s,%s) RETURNING id',
                            (employer, actor['id'])).fetchone()
            organizations[normalized(employer)] = row['id']
            audit(db, actor, 'create', 'organization', row['id'], None, {'name': employer}, reason='Работодатель из исходного файла')
        schedule = (fields.get('schedule') or '').strip()
        if schedule and normalized(schedule) not in schedules:
            explicit = re.search(r'\((\d+)\s*/\s*(\d+)\s+по\s+\d+\s+час', schedule, re.I)
            work_days, leave_days = (int(explicit[1]), int(explicit[2])) if explicit else (None, None)
            row = plain(db.native('''INSERT INTO workforce_rotation_schedules(name,onsite_days,leave_days,travel_days,needs_review,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
                (schedule, work_days, leave_days, 2 if explicit else None, not bool(explicit), actor['id'])).fetchone())
            schedules[normalized(schedule)] = row
            audit(db, actor, 'create', 'rotation_schedule', row['id'], None, row,
                  reason='График из Excel. Следующий заезд после МО + 2 дня; неполные графики требуют уточнения.')
    return {'categories': categories, 'smus': smus, 'citizenship': citizenship, 'organizations': organizations, 'schedules': schedules}


def seed_related_records(db, worker_id, records, source_ids, merged, schedule, day, actor_id):
    """Keep source details usable without inventing completion or date meanings."""
    seen = set()
    chosen_date = source_date(merged.get('event_date')) or day
    for record in records:
        source_id = source_ids[(record['filename'], record['sheet'], record['row'])]
        fields = record['fields']
        fact = classify(record, day)
        identity = (fact['stage'], fact['event_date'])
        if (fact['confirmed'] and fact['event_date'] and fact['event_date'] < chosen_date
                and identity not in seen and fact['stage'] != 'stage.pvp'):
            # PVP presence can be vetoed by another source. Its reviewed final fact
            # is inserted separately; raw contradictory observations remain attached.
            seen.add(identity)
            db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
                source_record_id,reason,created_by,request_key) VALUES (%s,%s,%s,TRUE,%s,%s,%s,%s)''',
                (worker_id, fact['stage'], fact['event_date'], source_id,
                 'Предыдущий датированный этап из исходного листа «' + record['sheet'] + '».', actor_id, str(uuid4())))
    recruitment = [record for record in records if record['service'] == 'recruitment']
    selected = next((record for record in records if list((record['filename'], record['sheet'], record['row']))
                     == list(merged['chosen'])), records[0])
    pvp_record = selected if selected['service'] == 'recruitment' or selected['sheet'].casefold() == 'пвп' else None
    if pvp_record:
        fields = pvp_record['fields']
        actual = source_date(merged.get('event_date')) if merged['stage'] == 'stage.pvp' and merged['confirmed'] else None
        observed = day if merged['stage'] == 'stage.pvp' and merged['confirmed'] else None
        db.native('''INSERT INTO workforce_pvp_stays(worker_id,planned_arrival,arrived_on,observed_on,
            notes,source_record_id,request_key,created_by,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (worker_id, source_date(fields.get('ticket_arrival')), actual, observed,
             fields.get('accommodation', '') + ('\nПрисутствие подтверждено на отчётную дату; дата заселения неизвестна.' if observed and not actual else ''),
             source_ids[(pvp_record['filename'], pvp_record['sheet'], pvp_record['row'])], str(uuid4()), actor_id, actor_id))
    documents = set()
    checks = {}
    for record in recruitment:
        source_id = source_ids[(record['filename'], record['sheet'], record['row'])]
        fields = record['fields']
        if 'патенты' in record['sheet'].casefold():
            documents.add(('document.patent', 'Лист «' + record['sheet'] + '». Реквизиты и готовность патента требуют заполнения.', source_id))
        if fields.get('migration'):
            documents.add(('document.migration', 'МУ в источнике: ' + fields['migration'] + '. Назначение даты требует уточнения.', source_id))
        for field, code in [('ok', 'check.hr'), ('medical', 'check.medical'), ('safety', 'check.safety'),
                            ('attestation', 'check.attestation'), ('hangar', 'check.hangar')]:
            if fields.get(field):
                checks.setdefault(code, []).append(record['filename'] + ' / ' + record['sheet'] + ': ' + fields[field])
    for code, notes, source_id in sorted(documents, key=lambda item: (item[0], item[1], str(item[2]))):
        db.native('''INSERT INTO workforce_documents(worker_id,document_code,state_code,notes,source_record_id,
            request_key,created_by,updated_by) VALUES (%s,%s,'docstate.pending',%s,%s,%s,%s,%s)''',
            (worker_id, code, notes, source_id, str(uuid4()), actor_id, actor_id))
    for code, values in checks.items():
        db.native('''INSERT INTO workforce_checks(worker_id,check_code,state_code,notes,updated_by)
            VALUES (%s,%s,'checkstate.pending',%s,%s) ON CONFLICT(worker_id,check_code) DO NOTHING''',
            (worker_id, code, '\n'.join(dict.fromkeys(values)), actor_id))
    start, end = source_date(merged['fields'].get('arrival')), source_date(merged.get('forecast_departure_date'))
    if schedule and not schedule['needs_review'] and start and end and start <= end:
        dates = planned_dates(start.isoformat(), schedule, end.isoformat())
        db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,start_date,
            planned_end_date,leave_end_date,next_arrival_date,notes,request_key,created_by,updated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (worker_id, schedule['id'], Jsonb(schedule), start, end, dates['leave_end_date'], dates['next_arrival_date'],
             'Начало и прогноз окончания из Явки. Дата фактического выезда не подтверждена.', str(uuid4()), actor_id, actor_id))


def apply_reviewed_bundle(db, bundle, actor_id):
    """Caller holds SERIALIZABLE transaction and creates a backup before calling."""
    from user_smu_access import require_all
    actor = db.native('SELECT id,role,active FROM users WHERE id=%s', (actor_id,)).fetchone()
    if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
        raise SourceProblem('Начальную загрузку согласованного набора выполняет администратор.')
    require_all(db, actor)
    day = source_date(bundle.get('report_date'))
    if not day:
        raise SourceProblem('В наборе не указана отчётная дата.')
    fingerprint = digest(bundle)
    already = db.native('SELECT id,state FROM workforce_import_batches WHERE source_key=%s AND file_sha256=%s',
                        (bundle['source_key'], fingerprint)).fetchone()
    if already:
        return {'batch_id': already['id'], 'already_imported': True}
    current = worker_index(db)
    existing_ids = set()
    personnel_numbers = set()
    for group in bundle['groups']:
        worker_id = group.get('worker_id')
        if group.get('issue'):
            raise SourceProblem('Набор содержит нерешённые конфликты сопоставления.')
        if worker_id:
            if worker_id in existing_ids:
                raise SourceProblem('Один сотрудник сопоставлен нескольким группам.')
            existing_ids.add(worker_id)
            if worker_id not in current['by_id'] or current['by_id'][worker_id]['token'] != group['worker_token']:
                raise SourceProblem('База изменилась после предпросмотра. Повторите сверку.')
        fields = group['merged']['fields']
        tab = fields.get('tab')
        if tab and tab in personnel_numbers:
            raise SourceProblem('Один табельный номер указан в нескольких исходных группах. Требуется сверка личности.')
        if tab:
            personnel_numbers.add(tab)
        if not fields.get('name') or len(fields['name']) > 300:
            raise SourceProblem('Некорректное ФИО в исходной группе.')
    refs = seed_references(db, bundle['groups'], actor)
    batch = db.native('''INSERT INTO workforce_import_batches(request_key,file_sha256,filename,service,source_key,
        report_date,state,preview_json,created_by,applied_at) VALUES (%s,%s,%s,'reviewed',%s,%s,'applied',%s,%s,now()) RETURNING id''',
        (str(uuid4()), fingerprint, bundle['filename'], bundle['source_key'], day,
         Jsonb({'groups': len(bundle['groups']), 'records': len(bundle['records']), 'source_hashes': bundle['source_hashes']}), actor_id)).fetchone()['id']
    records = bundle['records']
    stamp = datetime.now(timezone.utc).isoformat()
    counts = {'added': 0, 'matched': 0, 'inactive_preserved': 0, 'source_rows': 0, 'category_bindings_preserved': 0}
    for group in bundle['groups']:
        merged, worker_id = group['merged'], group.get('worker_id')
        fields = merged['fields']
        before = current['by_id'].get(worker_id)
        category = group.get('category') or fields.get('category') or ''
        name = smu_name(fields)
        department = before['department'] if before and before['smu_id'] else name or fields.get('department', '')
        tab = fields.get('tab') or (before['personnel_no'] if before and not before['personnel_is_internal']
                                   and before['personnel_no'].isdigit() else '')
        if worker_id:
            # Explicit employer/SMU corrections and existing category bindings stay authoritative.
            employer_override = db.native('SELECT employer FROM employee_employers WHERE worker_id=%s', (worker_id,)).fetchone()
            db.native('''UPDATE workers SET full_name=%s,personnel_no=%s,personnel_is_internal=%s,
                profession=%s,employer=%s,department=%s WHERE id=%s''',
                (fields['name'], tab or before['personnel_no'], not bool(tab), fields.get('profession', ''),
                 employer_override['employer'] if employer_override else fields.get('employer', ''), department, worker_id))
            counts['matched'] += 1
            counts['inactive_preserved'] += int(not before['active'])
        else:
            worker_id = db.native('''INSERT INTO workers(full_name,personnel_no,personnel_is_internal,contractor,
                employer,profession,category,department) VALUES (%s,%s,%s,'ЛГСС',%s,%s,%s,%s) RETURNING id''',
                (fields['name'], tab or 'WF-' + uuid4().hex, not bool(tab), fields.get('employer', ''),
                 fields.get('profession', ''), category, department)).fetchone()['id']
            counts['added'] += 1
        category_id = refs['categories'].get(normalized(category))
        if before and before['category_id']:
            counts['category_bindings_preserved'] += 1
        elif category_id:
            db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(worker_id) DO NOTHING''',
                (worker_id, category_id, uuid4().hex, actor_id, stamp))
        smu_id = before['smu_id'] if before and before['smu_id'] else refs['smus'].get(normalized(name))
        if smu_id:
            db.native('''INSERT INTO employee_smu(worker_id,smu_id,source_department)
                VALUES (%s,%s,%s) ON CONFLICT(worker_id) DO NOTHING''', (worker_id, smu_id, department))
        schedule = refs['schedules'].get(normalized(fields.get('schedule')))
        ready = merged['stage'] == 'stage.onsite' or merged['pure_outstaff'] or bool(before and db.native('''SELECT 1 FROM crew_members WHERE worker_id=%s''', (worker_id,)).fetchone())
        notes = group.get('notes') or '\n'.join(merged['warnings'])
        if before and before['category'] and normalized(before['category']) != normalized(category):
            notes += '\nКатегория сохранена по текущей базе: ' + before['category'] + '.'
        if before and before['smu_id'] and name and name != before['department']:
            notes += '\nСМУ сохранён по текущей привязке в базе: ' + before['department'] + '; в источнике: ' + name + '.'
        notes = notes.strip()
        if len(notes) > 30000:
            raise SourceProblem('Примечания превышают допустимую длину; данные не обрезаны.')
        db.native('''UPDATE workforce_profiles SET citizenship_code=%s,employment_code=%s,birth_date=%s,phone=%s,
            messenger=%s,origin_city=%s,rotation_schedule=%s,rotation_schedule_id=%s,arrival_date=%s,
            forecast_departure_date=%s,leave_start_date=%s,leave_end_date=%s,notes=%s,staffing_ready=%s,
            pure_outstaff=%s,workforce_managed=TRUE,updated_by=%s,updated_at=now(),edit_token=gen_random_uuid()
            WHERE worker_id=%s''',
            (refs['citizenship'].get(normalized(fields.get('citizenship'))), 'employment.staff' if tab else merged['employment_code'],
             source_date(fields.get('dob')), fields.get('phone', ''), fields.get('messenger', ''), fields.get('origin') or fields.get('city') or '',
             fields.get('schedule', ''), schedule['id'] if schedule else None, source_date(fields.get('arrival')),
             source_date(merged.get('forecast_departure_date')), source_date(fields.get('leave_start')), source_date(fields.get('leave_end')),
             notes, ready, merged['pure_outstaff'], actor_id, worker_id))
        source_ids = {}
        selected = [records[index] for index in group['record_indexes']]
        for record in selected:
            role = 'outstaff' if record['sheet'].casefold() in ('аустаффинг', 'аутстаффинг') else record['service']
            mapped = {'fields': record['fields'], 'section': record.get('section', '')}
            if role == 'outstaff':
                mapped['outstaff_identity_key'] = legacy_outstaff_key(record['fields'].get('name'), record['fields'].get('employer'))
            source_id = db.native('''INSERT INTO workforce_source_records(batch_id,worker_id,filename,sheet,source_row,
                source_role,raw_json,mapped_json,mapping_notes,source_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                (batch, worker_id, record['filename'], record['sheet'], record['row'], role, Jsonb(record['raw']), Jsonb(mapped),
                 '\n'.join(record.get('mapping_notes', [])), record['source_key'])).fetchone()['id']
            source_ids[(record['filename'], record['sheet'], record['row'])] = source_id
            counts['source_rows'] += 1
        chosen_id = source_ids[tuple(merged['chosen'])]
        seed_related_records(db, worker_id, selected, source_ids, merged, schedule, day, actor_id)
        db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,source_record_id,
            reason,created_by,request_key,date_basis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (worker_id, merged['stage'], source_date(merged['event_date']) or day, merged['confirmed'], chosen_id,
             'Состояние из согласованных источников на ' + day.isoformat() + ('. Дата перехода неизвестна.' if not merged['event_date'] else '.'),
             actor_id, str(uuid4()), 'event' if merged['event_date'] else 'observed'))
        if merged.get('planned_date') or merged.get('basis_code'):
            db.native('''INSERT INTO workforce_movements(worker_id,direction,planned_date,basis_code,source_record_id,
                notes,created_by,updated_by,request_key) VALUES (%s,'arrival',%s,%s,%s,%s,%s,%s,%s)''',
                (worker_id, source_date(merged['planned_date']), merged['basis_code'], chosen_id,
                 'План из исходных файлов; факт заезда не подтверждён автоматически.', actor_id, actor_id, str(uuid4())))
        after = {'worker_id': worker_id, 'full_name': fields['name'], 'source_group': group['source_group'],
                 'stage': merged['stage'], 'confirmed': merged['confirmed'], 'sources': len(selected),
                 'category': before['category'] if before and before['category_id'] else category,
                 'department': department, 'staffing_ready': ready}
        audit(db, actor, 'source_import', 'profile', worker_id, before, after,
              worker_id=worker_id, reason='Подтверждённый перенос согласованных данных УРП; исходные строки сохранены.')
    return {'batch_id': batch, 'already_imported': False, **counts}
