"""Persisted Excel preview/confirm; worker identity and source ownership are explicit."""
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import re
from pathlib import PurePath
from uuid import uuid4

from flask import abort, jsonify, request
from psycopg.types.json import Jsonb

from workforce_core import ADMINS, actor_scope, audit, date_value, digest, plain, text_value, uuid_value
from workforce_identity import match_worker, normalized, source_groups, worker_index
from workforce_lifecycle import merge_sources
from workforce_seed import smu_name
from workforce_sources import SourceProblem, parse_workbook, source_date


SOURCES = {'urp:П15': ('rotation', 'Перевахтовка ППС-15'), 'urp:П19': ('rotation', 'Перевахтовка ППС-19'),
           'urp:К15': ('recruitment', 'Комплектация ППС-15'), 'urp:К19': ('recruitment', 'Комплектация ППС-19')}


def import_actor(db, source_key):
    actor, scope, params = actor_scope(db, write=True)
    if source_key not in SOURCES:
        abort(400, description='Выберите источник импорта.')
    if actor['role'] not in ADMINS and actor['role'] != SOURCES[source_key][0]:
        abort(403, description='Этот источник ведёт другая служба.')
    ids = {row['id'] for row in db.native('SELECT w.id FROM workers w WHERE ' + scope, params)}
    access = db.native('SELECT mode,departments_json FROM user_smu_access WHERE user_id=%s', (actor['id'],)).fetchone()
    departments = None if actor['role'] == 'super_admin' or (access and access['mode'] == 'all') or (
        not access and actor['role'] in ADMINS) else set(json.loads(access['departments_json'])) if access else set()
    return actor, ids, departments


def visible_index(index, ids):
    return {'by_id': {key: row for key, row in index['by_id'].items() if key in ids},
            'by_tab': {key: row for key, row in index['by_tab'].items() if row['id'] in ids},
            'by_outstaff': {key: row for key, row in index['by_outstaff'].items() if row['id'] in ids},
            'by_name': {key: [row for row in rows if row['id'] in ids] for key, rows in index['by_name'].items()}}


def restricted_identity(records, hidden_index):
    match = match_worker(records, hidden_index)
    return bool(match['worker'] or match['candidates'])


def require_preview_scope(db, batch, ids, departments):
    data = batch['preview_json']
    referenced = {row['worker_id'] for row in db.native(
        'SELECT DISTINCT worker_id FROM workforce_source_records WHERE batch_id=%s', (batch['id'],))}
    for item in data['items']:
        if item['worker_id']:
            referenced.add(item['worker_id'])
        referenced.update(row['id'] for row in item['candidates'])
        if departments is not None and item['department'] not in departments:
            abort(403, description='Права по СМУ изменились. Прежняя сверка недоступна.')
    referenced.update(row['id'] for row in data['missing'])
    if not referenced <= ids:
        abort(403, description='Права по СМУ изменились. Прежняя сверка недоступна.')


def source_snapshot(db, source_key, ids):
    return digest(plain(db.native('''SELECT id,worker_id,active FROM workforce_source_records
        WHERE source_key=%s AND worker_id=ANY(%s) ORDER BY id''', (source_key, list(ids))).fetchall()))


def other_sources(db, ids, source_key):
    result = {}
    for row in db.native('''SELECT worker_id,filename,sheet,source_row,source_role,mapped_json,mapping_notes
        FROM workforce_source_records WHERE worker_id=ANY(%s) AND source_key<>%s AND active''', (list(ids), source_key)):
        result.setdefault(row['worker_id'], []).append({'filename':row['filename'],'sheet':row['sheet'],'row':row['source_row'],
            'service':'rotation' if row['source_role']=='outstaff' else row['source_role'],
            'fields':row['mapped_json']['fields'],'section':row['mapped_json'].get('section',''),
            'mapping_notes':[row['mapping_notes']] if row['mapping_notes'] else []})
    return result


def master_editable(worker, actor, source_key):
    return not worker or actor['role'] in ADMINS or (SOURCES[source_key][0]=='rotation' and worker['employment_code']=='employment.staff') or (
        SOURCES[source_key][0]=='recruitment' and worker['employment_code']!='employment.staff')


def preview_data(db, parsed, source_key, day, actor, ids, departments):
    full_index = worker_index(db)
    index = visible_index(full_index, ids)
    hidden_index = visible_index(full_index, set(full_index['by_id']) - ids)
    categories = {normalized(row['name']) for row in db.native('SELECT name FROM gdlr_categories WHERE active=1')}
    records = parsed['records']
    groups, skipped = [], []
    for positions in source_groups(records):
        members = [records[position] for position in positions]
        match = match_worker(members, index)
        worker = match['worker']
        merged = merge_sources(members, date.fromisoformat(day))
        department = worker['department'] if worker else smu_name(merged['fields']) or merged['fields'].get('department', '')
        if departments is not None and department not in departments:
            skipped.extend(positions)
            continue
        issue = match['issue']
        restricted = not worker and restricted_identity(members, hidden_index)
        if restricted:
            issue = 'Возможна связь с записью вне доступных СМУ. Сопоставление должен проверить администратор с полным доступом.'
        if not worker and normalized(merged['fields'].get('category')) not in categories:
            issue = (issue + ' ' if issue else '') + 'Для новой записи выберите категорию из справочника администратора.'
        item = {'record_indexes': positions, 'worker_id': worker['id'] if worker else None,
                'worker_token': worker['token'] if worker else None, 'issue': issue,
                'candidates': [{'id': row['id'], 'name': row['full_name'], 'tab': '' if row['personnel_is_internal'] else row['personnel_no'], 'token': row['token']}
                               for row in match['candidates']], 'merged': merged, 'department': department,
                'basis': match.get('basis'), 'changes': []}
        item['restricted_identity'] = restricted
        if worker:
            for field, old in [('name', worker['full_name']), ('profession', worker['profession']), ('employer', worker['employer'])]:
                new = merged['fields'].get(field, '')
                if new and new != old:
                    item['changes'].append({'field': field, 'before': old, 'after': new})
            item['category_preserved'] = worker['category']
        groups.append(item)
    # Different source lines can match the same durable employee. Merge those
    # lines before rendering, so one employee is never updated twice per import.
    combined = {}
    for item in groups:
        key = ('worker', item['worker_id']) if item['worker_id'] and not item['issue'] else ('row', item['record_indexes'][0])
        if key in combined:
            combined[key]['record_indexes'].extend(item['record_indexes'])
            combined[key]['merged'] = merge_sources([records[i] for i in combined[key]['record_indexes']], date.fromisoformat(day))
        else:
            combined[key] = item
    groups = list(combined.values())
    others = other_sources(db, ids, source_key)
    overrides = {row['worker_id']:row['employer'] for row in db.native('SELECT worker_id,employer FROM employee_employers WHERE worker_id=ANY(%s)', (list(ids),))}
    for item in groups:
        incoming_rows = [records[i] for i in item['record_indexes']]
        item['variants'] = {}
        targets = {row['id'] for row in item['candidates']} | ({item['worker_id']} if item['worker_id'] else set()) | {None}
        for target in targets:
            worker = index['by_id'].get(target)
            merged = merge_sources([*others.get(target, []), *incoming_rows], date.fromisoformat(day))
            changes = []
            if worker and master_editable(worker, actor, source_key):
                for field, old in [('name',worker['full_name']),('profession',worker['profession']),('employer',worker['employer'])]:
                    new = overrides[target] if field=='employer' and target in overrides else merged['fields'].get(field)
                    if new and new != old:changes.append({'field':field,'before':old,'after':new})
            item['variants'][str(target) if target else 'new'] = {'merged':merged,'changes':changes}
        variant = item['variants'][str(item['worker_id']) if item['worker_id'] else 'new']
        item.update(merged=variant['merged'],changes=variant['changes'])
    weak_names = Counter(normalized(item['merged']['fields'].get('name')) for item in groups
                         if not item['worker_id'] and not item['merged']['fields'].get('tab'))
    for item in groups:
        if not item['worker_id'] and not item['merged']['fields'].get('tab') and weak_names[normalized(item['merged']['fields'].get('name'))] > 1:
            item['issue'] = 'В файле несколько строк с таким ФИО без общего надёжного идентификатора. Подтвердите, что это разные сотрудники, либо исправьте идентификаторы в Excel.'
    incoming = {item['worker_id'] for item in groups if item['worker_id']}
    missing = plain(db.native('''SELECT DISTINCT w.id,w.full_name,w.personnel_no::text personnel_no,
        EXISTS(SELECT 1 FROM manual_employees me WHERE me.worker_id=w.id) manually_created
        FROM workforce_source_records sr JOIN workers w ON w.id=sr.worker_id
        WHERE sr.source_key=%s AND sr.active AND w.id=ANY(%s) AND NOT(w.id=ANY(%s)) ORDER BY w.full_name,w.id''',
        (source_key, list(ids), list(incoming))).fetchall())
    for row in missing:
        row['token'] = index['by_id'][row['id']]['token']
    return {'format_version':2, 'records': records, 'items': groups, 'missing': missing, 'skipped_count': len(skipped),
            'excluded_count': len(parsed['excluded']), 'mappings': parsed['mappings'],
            'source_snapshot': source_snapshot(db, source_key, ids), 'result': None}


def public_preview(batch):
    data = batch['preview_json']
    return {'id': batch['id'], 'token': batch['preview_token'], 'state': batch['state'], 'date': batch['report_date'],
            'filename': batch['filename'], 'source': SOURCES[batch['source_key']][1], 'expires_at': batch['expires_at'],
            'items': [{'index': i, 'name': item['merged']['fields'].get('name'), 'tab': item['merged']['fields'].get('tab'),
                       'worker_id': item['worker_id'], 'issue': item['issue'],
                       'restricted_identity': item.get('restricted_identity', False),
                       'candidates': [{**{k: row[k] for k in ('id', 'name', 'tab')},
                           'changes':item.get('variants',{}).get(str(row['id']),{}).get('changes',[]),
                           'warnings':item.get('variants',{}).get(str(row['id']),{}).get('merged',{}).get('warnings',[])} for row in item['candidates']],
                       'warnings': item['merged']['warnings'],
                       'changes': item['changes'], 'category_preserved': item.get('category_preserved'),
                       'department': item['department'], 'basis': item['basis']} for i, item in enumerate(data['items'])],
            'missing': [{k: row[k] for k in ('id', 'full_name', 'manually_created')} for row in data['missing']],
            'counts': dict(Counter('review' if item['issue'] else 'matched' if item['worker_id'] else 'added' for item in data['items'])),
            'skipped_count': data['skipped_count'], 'excluded_count': data['excluded_count'],
            'mappings': data['mappings'], 'result': data.get('result')}


def apply_item(db, item, records, batch, actor, index):
    """Update permitted master fields; always preserve current GDLR and overrides."""
    source_key, day = batch['source_key'], batch['report_date']
    service = SOURCES[source_key][0]
    worker_id = item['worker_id']
    before = index['by_id'].get(worker_id)
    incoming = [records[i] for i in item['record_indexes']]
    others = other_sources(db, [worker_id], source_key).get(worker_id, []) if worker_id else []
    merged = item['merged']
    fields = merged['fields']
    # A service owns only its part of the master record. Recruitment may append
    # PVP/doc information for a staff employee, but cannot rewrite staff identity.
    editable = master_editable(before, actor, source_key)
    tab = fields.get('tab') or (before['personnel_no'] if before and not before['personnel_is_internal'] and before['personnel_no'].isdigit() else '')
    category = db.native('SELECT id,name FROM gdlr_categories WHERE name_key=%s AND active=1', (normalized(fields.get('category')),)).fetchone()
    if worker_id:
        if editable:
            override = db.native('SELECT employer FROM employee_employers WHERE worker_id=%s', (worker_id,)).fetchone()
            db.native('''UPDATE workers SET full_name=%s,personnel_no=%s,personnel_is_internal=%s,profession=%s,employer=%s WHERE id=%s''',
                (fields['name'], tab or before['personnel_no'], not bool(tab), fields.get('profession') or before['profession'],
                 override['employer'] if override else fields.get('employer') or before['employer'], worker_id))
    else:
        if not category:
            abort(409, description='Категория новой записи отсутствует в справочнике. Повторите сверку после её добавления.')
        worker_id = db.native('''INSERT INTO workers(full_name,personnel_no,personnel_is_internal,contractor,employer,
            profession,category,department) VALUES (%s,%s,%s,'ЛГСС',%s,%s,%s,%s) RETURNING id''',
            (fields['name'], tab or 'WF-' + uuid4().hex, not bool(tab), fields.get('employer', ''),
             fields.get('profession', ''), category['name'], item['department'])).fetchone()['id']
        db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s)''',
            (worker_id, category['id'], uuid4().hex, actor['id'], datetime.now(timezone.utc).isoformat()))
        smu = db.native('SELECT id FROM smu_catalog WHERE name=%s AND active=1', (item['department'],)).fetchone()
        if smu:
            db.native('INSERT INTO employee_smu(worker_id,smu_id,source_department) VALUES (%s,%s,%s)', (worker_id, smu['id'], item['department']))
    for warning in merged['warnings']:
        if warning.startswith('Разные значения «'):
            field = warning.split('«', 1)[1].split('»', 1)[0]
            db.native('''INSERT INTO workforce_conflicts(worker_id,batch_id,kind,field_name,description,candidates_json)
                VALUES (%s,%s,'field',%s,%s,%s)''', (worker_id, batch['id'], field, warning,
                Jsonb({'sources': [record['fields'].get(field) for record in [*others, *incoming] if record['fields'].get(field)]})))
    db.native('UPDATE workforce_source_records SET active=FALSE WHERE source_key=%s AND worker_id=%s AND active', (source_key, worker_id))
    chosen_id, source_ids = None, {}
    for record in incoming:
        role = 'outstaff' if record['sheet'].casefold() in ('аустаффинг', 'аутстаффинг') else service
        mapped = {'fields': record['fields'], 'section': record.get('section', '')}
        if role == 'outstaff':
            from workforce_identity import legacy_outstaff_key
            mapped['outstaff_identity_key'] = legacy_outstaff_key(record['fields'].get('name'), record['fields'].get('employer'))
        source_id = db.native('''INSERT INTO workforce_source_records(batch_id,worker_id,filename,sheet,source_row,
            source_role,raw_json,mapped_json,mapping_notes,source_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (batch['id'], worker_id, record['filename'], record['sheet'], record['row'], role,
             Jsonb(record['raw']), Jsonb(mapped), '\n'.join(record['mapping_notes']), source_key)).fetchone()['id']
        source_ids[(record['filename'], record['sheet'], record['row'])] = source_id
        if list(merged['chosen']) == [record['filename'], record['sheet'], record['row']]:
            chosen_id = source_id
    if editable:
        country = db.native("SELECT code FROM workforce_catalog WHERE kind='citizenship' AND lower(label)=lower(%s) AND active",
                            (fields.get('citizenship', ''),)).fetchone()
        clean = {'employment_code': 'employment.staff' if tab else merged['employment_code']}
        for name, source in [('birth_date', 'dob'), ('phone', 'phone'), ('messenger', 'messenger'), ('origin_city', 'origin')]:
            if fields.get(source):
                clean[name] = source_date(fields[source]) if name == 'birth_date' else fields[source]
        if country:
            clean['citizenship_code'] = country['code']
        if service == 'rotation':
            for name, value in [('arrival_date', fields.get('arrival')), ('forecast_departure_date', merged['forecast_departure_date']),
                                ('leave_start_date', fields.get('leave_start')), ('leave_end_date', fields.get('leave_end'))]:
                if source_date(value):
                    clean[name] = source_date(value)
        db.native('UPDATE workforce_profiles SET ' + ','.join(name + '=%s' for name in clean) +
                  ',updated_by=%s,updated_at=now(),edit_token=gen_random_uuid() WHERE worker_id=%s', [*clean.values(), actor['id'], worker_id])
    ready = merged['stage'] == 'stage.onsite' or merged['pure_outstaff']
    db.native('''UPDATE workforce_profiles SET workforce_managed=TRUE,staffing_ready=staffing_ready OR %s,pure_outstaff=%s
        WHERE worker_id=%s''', (ready, merged['pure_outstaff'], worker_id))
    # Import records an observation at the report date, not a retrospectively
    # invented event. Earlier confirmed/manual history remains immutable.
    if chosen_id:
        db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,source_record_id,
            reason,created_by,request_key,date_basis) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'observed')''',
            (worker_id, merged['stage'], day, merged['confirmed'], chosen_id,
             'Сверка подтверждённого импорта на отчётную дату. ' + '\n'.join(merged['warnings']), actor['id'], str(uuid4())))
    from workforce_import_details import sync_plan, sync_recruitment
    sync_plan(db, actor, batch, worker_id, chosen_id, merged)
    sync_recruitment(db, actor, batch, worker_id, incoming, source_ids, merged)
    audit(db, actor, 'source_import', 'profile', worker_id, before,
          {'worker_id': worker_id, 'source_key': source_key, 'source_rows': len(incoming), 'fields': fields,
           'master_fields_updated': editable, 'category_preserved': before['category'] if before else category['name']},
          worker_id=worker_id, reason='Пользователь подтвердил сверку Excel; категории базы сохранены.')
    return worker_id


def register_import_routes(app, database, roles_required):
    from workforce_api import payload

    @app.get('/api/workforce/imports/sources')
    @roles_required('admin', 'rotation', 'recruitment')
    def workforce_import_sources():
        actor, _, _ = actor_scope(database(), write=True)
        return jsonify([{'key': key, 'label': label, 'service': service} for key, (service, label) in SOURCES.items()
                        if actor['role'] in ADMINS or actor['role'] == service])

    @app.post('/api/workforce/imports/preview')
    @roles_required('admin', 'rotation', 'recruitment')
    def workforce_import_preview():
        source_key = request.form.get('source_key')
        day = date_value(request.form.get('date'), required=True)
        file = request.files.get('file')
        if not file:
            abort(400, description='Выберите шаблон перевахтовки или файл комплектации.')
        db = database()
        actor, ids, departments = import_actor(db, source_key)
        content = file.read(20 * 1024 * 1024 + 1)
        filename = PurePath(file.filename.replace('\\', '/')).name
        text_value(filename, 'Название файла', 250, True)
        pps = re.search(r'ППС\s*[-–]?\s*(15|19)\b', filename, re.I)
        if pps and not source_key.endswith(pps[1]):
            abort(400, description='ППС в названии файла не совпадает с выбранным источником.')
        if ('комплектац' in filename.casefold() and SOURCES[source_key][0] != 'recruitment') or (
                'перевахт' in filename.casefold() and SOURCES[source_key][0] != 'rotation'):
            abort(400, description='Тип книги не совпадает с выбранной службой.')
        try:
            parsed = parse_workbook(content, filename, SOURCES[source_key][0])
        except SourceProblem as error:
            abort(400, description=str(error))
        fingerprint = digest([hashlib.sha256(content).hexdigest(), day, actor['id']])
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, ids, departments = import_actor(db, source_key)
            old = plain(db.native('SELECT * FROM workforce_import_batches WHERE source_key=%s AND file_sha256=%s', (source_key, fingerprint)).fetchone())
            if old and old['state'] == 'applied':
                require_preview_scope(db, old, ids, departments)
                return jsonify(public_preview(old))
            preview = preview_data(db, parsed, source_key, day, actor, ids, departments)
            if old:
                batch = db.native('''UPDATE workforce_import_batches SET preview_json=%s,state='preview',preview_token=gen_random_uuid(),
                    expires_at=now()+interval '30 minutes' WHERE id=%s RETURNING *''', (Jsonb(preview), old['id'])).fetchone()
            else:
                batch = db.native('''INSERT INTO workforce_import_batches(request_key,file_sha256,filename,service,
                    source_key,report_date,state,preview_json,created_by) VALUES (%s,%s,%s,%s,%s,%s,'preview',%s,%s) RETURNING *''',
                    (str(uuid4()), fingerprint, filename, SOURCES[source_key][0], source_key, day, Jsonb(preview), actor['id'])).fetchone()
        return jsonify(public_preview(batch))

    @app.post('/api/workforce/imports/<uuid:batch_id>/apply')
    @roles_required('admin', 'rotation', 'recruitment')
    def workforce_import_apply(batch_id):
        data = payload({'token', 'confirmed', 'decisions'}, {'token', 'confirmed', 'decisions'})
        uuid_value(data['token'])
        if data['confirmed'] is not True or not isinstance(data['decisions'], list):
            abort(400, description='Подтвердите итог сверки.')
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            batch = db.native('SELECT * FROM workforce_import_batches WHERE id=%s FOR UPDATE', (batch_id,)).fetchone()
            if not batch:
                abort(404)
            actor, ids, departments = import_actor(db, batch['source_key'])
            if batch['created_by'] != actor['id']:
                abort(403, description='Подтвердить сверку может загрузивший её пользователь.')
            if batch['state'] == 'applied':
                require_preview_scope(db, batch, ids, departments)
                return jsonify(batch['preview_json']['result'])
            if str(batch['preview_token']) != data['token'] or batch['expires_at'] < datetime.now(timezone.utc):
                abort(409, description='Сверка устарела. Повторно загрузите файл.')
            preview = batch['preview_json']
            if preview.get('format_version') != 2:
                abort(409, description='Правила сверки обновлены. Повторно загрузите файл для проверки итоговых изменений.')
            if source_snapshot(db, batch['source_key'], ids) != preview['source_snapshot']:
                abort(409, description='Состав источника изменён после сверки. Загрузите файл повторно.')
            index = worker_index(db)
            hidden_index = visible_index(index, set(index['by_id']) - ids)
            for decision in data['decisions']:
                if not isinstance(decision, dict) or set(decision) != {'index', 'worker_id', 'reason'}:
                    abort(400, description='Некорректное решение по сопоставлению.')
                number = decision['index']
                if type(number) is not int or not 0 <= number < len(preview['items']):
                    abort(400)
                item = preview['items'][number]
                if not item['issue']:
                    abort(400, description='Эта строка не требует решения по сопоставлению.')
                if item.get('restricted_identity'):
                    abort(403, description='Сопоставление должен проверить администратор с полным доступом.')
                reason = text_value(decision['reason'], 'Основание сопоставления', 10000, True)
                target = decision['worker_id']
                if target == 'new':
                    worker = None
                elif type(target) is int and target in ids and target in {row['id'] for row in item['candidates']}:
                    worker = index['by_id'][target]
                else:
                    abort(403, description='Сопоставление недоступно.')
                candidate_token = next((row['token'] for row in item['candidates'] if worker and row['id'] == worker['id']), None)
                item.update(worker_id=worker['id'] if worker else None, worker_token=candidate_token,
                            issue=None, decision_reason=reason)
                variant = item.get('variants', {}).get(str(worker['id']) if worker else 'new')
                if variant is None:
                    abort(409, description='Формат сверки обновлён. Повторно загрузите файл.')
                item.update(merged=variant['merged'], changes=variant['changes'])
            used, tabs = set(), set()
            for item in preview['items']:
                if item['issue']:
                    abort(409, description='Сначала разрешите все конфликты сверки.')
                worker_id = item['worker_id']
                if not worker_id and restricted_identity([preview['records'][i] for i in item['record_indexes']], hidden_index):
                    abort(403, description='Возможна связь вне доступных СМУ. Повторите сверку с администратором.')
                if worker_id:
                    if worker_id not in ids or index['by_id'][worker_id]['token'] != item['worker_token'] or worker_id in used:
                        abort(409, description='Карточка изменена или связана дважды. Повторите сверку.')
                    used.add(worker_id)
                elif departments is not None and item['department'] not in departments:
                    abort(403, description='Нет права создавать сотрудника в этом СМУ.')
                elif not item.get('decision_reason'):
                    current_match = match_worker([preview['records'][i] for i in item['record_indexes']], visible_index(index, ids))
                    if current_match['worker'] or current_match['issue']:
                        abort(409, description='После сверки появился сотрудник с совпадающими данными. Повторите сопоставление.')
                tab = item['merged']['fields'].get('tab')
                if tab and tab in tabs:
                    abort(409, description='Один табельный указан в нескольких группах. Исправьте сопоставление.')
                if tab:
                    tabs.add(tab)
                if tab and tab in index['by_tab'] and index['by_tab'][tab]['id'] != worker_id:
                    abort(409, description='Табельный номер уже принадлежит сотруднику. Повторите сверку.')
            for row in preview['missing']:
                if row['id'] not in ids or index['by_id'][row['id']]['token'] != row['token']:
                    abort(409, description='Отсутствующая в Excel карточка изменилась. Повторите сверку.')
            # All validation precedes mutation; a failure rolls back the whole import.
            from postgres_backup import create
            backup = create(batch['report_date'].isoformat(), 'before-workforce-reimport')
            result = {'added': 0, 'matched': 0, 'removed_from_source': 0, 'manual_preserved': 0}
            for item in preview['items']:
                worker_id = apply_item(db, item, preview['records'], batch, actor, index)
                result['matched' if item['worker_id'] else 'added'] += 1
                if item.get('decision_reason'):
                    audit(db, actor, 'identity_confirmed', 'source', batch_id, None,
                          {'worker_id': worker_id, 'source_rows': item['record_indexes']}, worker_id=worker_id, reason=item['decision_reason'])
            for row in preview['missing']:
                if row['id'] in used:
                    continue
                if row['manually_created']:
                    result['manual_preserved'] += 1
                    continue
                db.native('UPDATE workforce_source_records SET active=FALSE WHERE worker_id=%s AND source_key=%s AND active', (row['id'], batch['source_key']))
                remaining = db.native('SELECT 1 FROM workforce_source_records WHERE worker_id=%s AND active LIMIT 1', (row['id'],)).fetchone()
                if not remaining:
                    db.native('UPDATE workforce_profiles SET staffing_ready=FALSE WHERE worker_id=%s', (row['id'],))
                audit(db, actor, 'source_absence', 'source', batch_id, {'active': True}, {'active': False}, worker_id=row['id'],
                      reason='Не найден в повторном Excel. Отсутствие подтверждено; карточка и история сохранены.')
                result['removed_from_source'] += 1
            preview['result'] = result
            preview['backup_name'] = backup.name
            db.native("UPDATE workforce_import_batches SET state='applied',applied_at=now(),preview_json=%s WHERE id=%s", (Jsonb(preview), batch_id))
        return jsonify(result)
