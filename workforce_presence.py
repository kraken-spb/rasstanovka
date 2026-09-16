"""Confirmed operational records publish facts; corrections retain old events."""
from uuid import uuid4

from workforce_core import audit, plain


def replace_fact(db, actor, worker_id, old_id, stage, day, reason):
    old = plain(db.native('SELECT * FROM workforce_stage_events WHERE id=%s', (old_id,)).fetchone()) if old_id else None
    if old and old['stage_code'] == stage and old['effective_date'] == day and not old['retracted']:
        return old_id
    if old and not db.native('SELECT 1 FROM workforce_stage_events WHERE replaces_id=%s', (old_id,)).fetchone():
        withdrawn = db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
            retracted,replaces_id,reason,created_by,request_key) VALUES (%s,%s,%s,FALSE,TRUE,%s,%s,%s,%s) RETURNING *''',
            (worker_id, old['stage_code'], old['effective_date'], old_id,
             'Исправлена связанная операция. ' + reason, actor['id'], str(uuid4()))).fetchone()
        audit(db, actor, 'retract', 'stage', withdrawn['id'], old, withdrawn, worker_id=worker_id, reason=reason)
    if not day or not stage:
        return None
    event = db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,
        reason,created_by,request_key) VALUES (%s,%s,%s,TRUE,%s,%s,%s) RETURNING *''',
        (worker_id, stage, day, reason, actor['id'], str(uuid4()))).fetchone()
    audit(db, actor, 'confirmed_operation', 'stage', event['id'], None, event, worker_id=worker_id, reason=reason)
    if stage == 'stage.onsite':
        db.native('UPDATE workforce_profiles SET staffing_ready=TRUE WHERE worker_id=%s', (worker_id,))
    return event['id']


def sync_operation_presence(db, actor, kind, before, row, reason):
    if kind == 'movement':
        stage, day = None, None
        if row['result_code'] == 'result.happened' and row['actual_date']:
            day = row['actual_date']
            stage = 'stage.leave' if row['direction'] == 'departure' else {
                'site': 'stage.onsite', 'pvp': 'stage.pvp', 'home': 'stage.leave'}.get(row['destination_kind'])
        event = replace_fact(db, actor, row['worker_id'], (before or {}).get('stage_event_id'), stage, day, reason)
        db.native('UPDATE workforce_movements SET stage_event_id=%s WHERE id=%s', (event, row['id']))
        row['stage_event_id'] = event
    elif kind == 'pvp':
        arrival = replace_fact(db, actor, row['worker_id'], (before or {}).get('arrival_event_id'),
                               'stage.pvp' if row['arrived_on'] else None, row['arrived_on'], reason)
        departure = replace_fact(db, actor, row['worker_id'], (before or {}).get('departure_event_id'),
                                 'stage.inbound' if row['departed_on'] else None, row['departed_on'], reason)
        db.native('UPDATE workforce_pvp_stays SET arrival_event_id=%s,departure_event_id=%s WHERE id=%s',
                  (arrival, departure, row['id']))
        row.update(arrival_event_id=arrival, departure_event_id=departure)
    return row
