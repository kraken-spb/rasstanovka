"""Source-owned plans and recruitment details preserve manually edited records."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from workforce_core import audit, plain
from workforce_sources import source_date


def conflict(db, actor, batch, worker_id, field, old, new):
    description = f'Импорт: «{field}» отличается от вручную изменённой записи. Текущее значение сохранено.'
    row = db.native('''INSERT INTO workforce_conflicts(worker_id,batch_id,kind,field_name,description,candidates_json)
        VALUES (%s,%s,'field',%s,%s,%s) RETURNING id''',
        (worker_id, batch['id'], field, description, Jsonb(plain({'current': old, 'excel': new})))).fetchone()
    audit(db, actor, 'import_conflict', 'conflict', row['id'], old, new, worker_id=worker_id, reason=description)


def manually_edited(db, kind, identifier):
    return bool(db.native('''SELECT 1 FROM workforce_audit WHERE entity_type=%s AND entity_id=%s
        AND action IN ('update','reschedule','extend','cancel','close') LIMIT 1''', (kind, str(identifier))).fetchone())


def sync_plan(db, actor, batch, worker_id, source_id, merged):
    if not source_id or not merged.get('planned_date'):
        return
    planned = source_date(merged['planned_date'])
    own = plain(db.native('''SELECT m.* FROM workforce_movements m JOIN workforce_source_records sr ON sr.id=m.source_record_id
        WHERE m.worker_id=%s AND sr.source_key=%s AND m.direction='arrival'
        AND NOT EXISTS(SELECT 1 FROM workforce_movements next WHERE next.rescheduled_from=m.id)
        ORDER BY m.created_at DESC,m.id LIMIT 1''', (worker_id, batch['source_key'])).fetchone())
    if own and own['planned_date'] == planned.isoformat() and own['basis_code'] == merged.get('basis_code'):
        return
    if own and manually_edited(db, 'movement', own['id']):
        conflict(db, actor, batch, worker_id, 'Плановая дата заезда', own, {'planned_date': planned.isoformat()})
        return
    predecessor = None
    reason = 'Плановая дата из подтверждённой сверки Excel. Факт заезда не устанавливается.'
    if own and not own['actual_date'] and own['result_code'] not in ('result.happened', 'result.cancelled'):
        predecessor = own['id']
        db.native("UPDATE workforce_movements SET result_code='result.postponed',updated_by=%s,updated_at=now(),edit_token=gen_random_uuid() WHERE id=%s",
                  (actor['id'], predecessor))
        audit(db, actor, 'source_reschedule', 'movement', predecessor, own, {'result_code': 'result.postponed'}, worker_id=worker_id,
              reason='Пользователь подтвердил изменённую плановую дату в повторном Excel.')
    row = plain(db.native('''INSERT INTO workforce_movements(worker_id,direction,planned_date,basis_code,source_record_id,
        rescheduled_from,notes,request_key,created_by,updated_by) VALUES (%s,'arrival',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (worker_id, planned, merged.get('basis_code'), source_id, predecessor, reason, str(uuid4()), actor['id'], actor['id'])).fetchone())
    audit(db, actor, 'source_plan', 'movement', row['id'], None, row, worker_id=worker_id, reason=reason)


def sync_recruitment(db, actor, batch, worker_id, records, source_ids, merged):
    docs = {}
    checks = {}
    for record in records:
        if record['service'] != 'recruitment':
            continue
        fields = record['fields']
        source_id = source_ids[(record['filename'], record['sheet'], record['row'])]
        if 'патенты' in record['sheet'].casefold():
            docs.setdefault('document.patent', []).append(('Лист «' + record['sheet'] + '». Реквизиты и готовность требуют проверки.', source_id))
        if fields.get('migration'):
            docs.setdefault('document.migration', []).append(('МУ: ' + fields['migration'] + '. Назначение даты требует уточнения.', source_id))
        for field, code in [('ok', 'check.hr'), ('medical', 'check.medical'), ('safety', 'check.safety'), ('attestation', 'check.attestation'), ('hangar', 'check.hangar')]:
            if fields.get(field):
                checks.setdefault(code, []).append(record['sheet'] + ': ' + fields[field])
    for code, values in docs.items():
        notes = '\n'.join(dict.fromkeys(value[0] for value in values))
        source_id = values[0][1]
        old = plain(db.native('''SELECT d.* FROM workforce_documents d JOIN workforce_source_records sr ON sr.id=d.source_record_id
            WHERE d.worker_id=%s AND d.document_code=%s AND sr.source_key=%s ORDER BY d.updated_at DESC LIMIT 1''',
            (worker_id, code, batch['source_key'])).fetchone())
        if old and manually_edited(db, 'document', old['id']):
            if old['notes'] != notes:
                conflict(db, actor, batch, worker_id, code, old['notes'], notes)
            continue
        if old:
            row = db.native('''UPDATE workforce_documents SET notes=%s,source_record_id=%s,updated_by=%s,updated_at=now(),
                edit_token=gen_random_uuid() WHERE id=%s RETURNING *''', (notes, source_id, actor['id'], old['id'])).fetchone()
        else:
            row = db.native('''INSERT INTO workforce_documents(worker_id,document_code,state_code,notes,source_record_id,
                request_key,created_by,updated_by) VALUES (%s,%s,'docstate.pending',%s,%s,%s,%s,%s) RETURNING *''',
                (worker_id, code, notes, source_id, str(uuid4()), actor['id'], actor['id'])).fetchone()
        audit(db, actor, 'source_detail', 'document', row['id'], old, row, worker_id=worker_id, reason='Сведения из подтверждённого импорта')
    for code, values in checks.items():
        old = db.native('SELECT * FROM workforce_checks WHERE worker_id=%s AND check_code=%s', (worker_id, code)).fetchone()
        notes = '\n'.join(dict.fromkeys(values))
        if old:
            if old['notes'] != notes:
                conflict(db, actor, batch, worker_id, code, old['notes'], notes)
            continue
        row = db.native('''INSERT INTO workforce_checks(worker_id,check_code,state_code,notes,updated_by)
            VALUES (%s,%s,'checkstate.pending',%s,%s) RETURNING *''', (worker_id, code, notes, actor['id'])).fetchone()
        audit(db, actor, 'source_detail', 'check', f'{worker_id}:{code}', None, row, worker_id=worker_id, reason='Исходная отметка без автоматического подтверждения завершения')
    chosen = next((record for record in records if [record['filename'],record['sheet'],record['row']] == list(merged['chosen'])
                   and record['service'] == 'recruitment'), None)
    if not chosen:
        return
    old = plain(db.native('''SELECT p.* FROM workforce_pvp_stays p JOIN workforce_source_records sr ON sr.id=p.source_record_id
        WHERE p.worker_id=%s AND sr.source_key=%s AND p.departed_on IS NULL ORDER BY p.updated_at DESC LIMIT 1''',
        (worker_id, batch['source_key'])).fetchone())
    fields = chosen['fields']
    notes = fields.get('accommodation', '')
    if old and manually_edited(db, 'pvp', old['id']):
        if old['notes'] != notes:
            conflict(db, actor, batch, worker_id, 'Пребывание в ПВП', old, notes)
        return
    planned = source_date(fields.get('ticket_arrival'))
    confirmed = merged['stage'] == 'stage.pvp' and merged['confirmed']
    arrived = source_date(merged['event_date']) if confirmed else None
    observed = batch['report_date'] if confirmed else None
    source_id = source_ids[(chosen['filename'], chosen['sheet'], chosen['row'])]
    if old:
        row = db.native('''UPDATE workforce_pvp_stays SET planned_arrival=%s,arrived_on=%s,observed_on=%s,notes=%s,
            source_record_id=%s,updated_by=%s,updated_at=now(),edit_token=gen_random_uuid() WHERE id=%s RETURNING *''',
            (planned, arrived, observed, notes, source_id, actor['id'], old['id'])).fetchone()
    else:
        existing = db.native('SELECT id FROM workforce_pvp_stays WHERE worker_id=%s AND arrived_on IS NOT NULL AND departed_on IS NULL', (worker_id,)).fetchone()
        if existing:
            conflict(db, actor, batch, worker_id, 'Пребывание в ПВП', {'id': existing['id']}, fields)
            return
        row = db.native('''INSERT INTO workforce_pvp_stays(worker_id,planned_arrival,arrived_on,observed_on,notes,source_record_id,
            request_key,created_by,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (worker_id, planned, arrived, observed, notes, source_id, str(uuid4()), actor['id'], actor['id'])).fetchone()
    audit(db, actor, 'source_detail', 'pvp', row['id'], old, row, worker_id=worker_id, reason='Подтверждённая сверка источника ПВП')
