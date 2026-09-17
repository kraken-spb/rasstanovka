"""Shared workforce authorization, dated reads and transactional audit contracts."""
import hashlib
import json
from datetime import date, datetime
from uuid import UUID

from flask import abort, g
from psycopg.types.json import Jsonb


ADMINS = {'admin', 'super_admin'}
EDITORS = ADMINS | {'rotation', 'recruitment'}
READERS = EDITORS | {'foreman', 'viewer', 'hr_viewer'}


def plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, 'keys'):
        return {key: plain(value[key]) for key in value.keys()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def uuid_value(value, label='Идентификатор'):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        abort(400, description=label + ': неверный формат.')


def date_value(value, label='Дата', required=False):
    if value in (None, '') and not required:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        abort(400, description=label + ': укажите дату в формате ГГГГ-ММ-ДД.')


def text_value(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or any(
            ord(char) < 32 and char not in '\n\t' or ord(char) == 127 for char in value):
        abort(400, description=f'{label}: допустимо до {maximum} символов.')
    value = value.strip()
    if required and not value:
        abort(400, description=label + ': заполните значение.')
    return value


def actor_scope(db, write=False):
    """Reload privileges in the transaction; never trust a posted role or SMU."""
    actor = db.native('SELECT id,role,active FROM users WHERE id=%s', (g.user['id'],)).fetchone()
    if not actor or not actor['active'] or actor['role'] not in READERS:
        abort(403, description='Доступ к учёту персонала отсутствует.')
    if write and actor['role'] not in EDITORS:
        abort(403, description='Эта роль работает с готовой базой персонала.')
    if actor['role'] in {'super_admin', 'hr_viewer'}:
        return actor, 'TRUE', []
    scope = db.native('SELECT mode,departments_json FROM user_smu_access WHERE user_id=%s',
                      (actor['id'],)).fetchone()
    if scope:
        return (actor, 'TRUE', []) if scope['mode'] == 'all' else (
            actor, 'w.department = ANY(%s)', [json.loads(scope['departments_json'])])
    if actor['role'] in ADMINS or actor['role'] == 'viewer' and not write:
        return actor, 'TRUE', []
    if actor['role'] == 'foreman':
        return actor, ('EXISTS(SELECT 1 FROM crew_members cm JOIN crews cr ON cr.id=cm.crew_id '
                       'WHERE cm.worker_id=w.id AND cr.owner_user_id=%s)'), [actor['id']]
    # A newly created service account needs explicit SMU access, never all by default.
    return actor, 'FALSE', []


def require_worker(db, worker_id, operation=None):
    actor, scope, params = actor_scope(db, write=operation is not None)
    row = db.native(f'''SELECT w.id,w.full_name,w.personnel_no,w.personnel_is_internal,
        w.active,p.employment_code FROM workers w LEFT JOIN workforce_profiles p ON p.worker_id=w.id
        WHERE w.id=%s AND ({scope})''', [worker_id, *params]).fetchone()
    if not row:
        abort(404, description='Сотрудник не найден в доступных СМУ.')
    role = actor['role']
    if operation and role not in ADMINS:
        if operation in {'movement', 'stage'} and role != 'rotation':
            abort(403, description='Движение работников ведёт перевахта.')
        if operation in {'document', 'pvp', 'check'} and role != 'recruitment':
            abort(403, description='ПВП, документы и оформление ведёт комплектующая служба.')
        if operation == 'profile':
            staff = row['employment_code'] == 'employment.staff'
            if (role == 'rotation' and not staff) or (role == 'recruitment' and staff):
                abort(403, description='Карточку этого сотрудника ведёт другая служба.')
        if operation == 'resolve':
            abort(403, description='Конфликты сопоставления разрешает администратор.')
    return actor, row


def catalog_value(db, value, kind, required=False):
    if value in (None, '') and not required:
        return None
    if not isinstance(value, str) or not value.startswith(kind + '.'):
        abort(400, description='Выберите значение справочника: ' + kind)
    row = db.native('SELECT code FROM workforce_catalog WHERE code=%s AND kind=%s AND active',
                    (value, kind)).fetchone()
    if not row:
        abort(400, description='Значение справочника недоступно. Обновите список.')
    return value


def audit(db, actor, action, entity_type, entity_id, before, after, *, worker_id=None, reason=''):
    db.native('''INSERT INTO workforce_audit(worker_id,actor_id,action,entity_type,entity_id,
        before_json,after_json,reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (worker_id, actor['id'], action, entity_type, str(entity_id),
         Jsonb(plain(before)) if before is not None else None,
         Jsonb(plain(after)) if after is not None else None, reason))


def replay(db, actor, operation, payload):
    key = uuid_value(payload.get('request_key'), 'Ключ операции')
    row = db.native('SELECT * FROM workforce_requests WHERE request_key=%s', (key,)).fetchone()
    if row:
        if row['actor_id'] != actor['id'] or row['operation'] != operation or row['payload_hash'] != digest(payload):
            abort(409, description='Ключ операции уже использован для других данных.')
        return row['response_json']
    return None


def remember(db, actor, operation, payload, response):
    db.native('''INSERT INTO workforce_requests(request_key,actor_id,operation,payload_hash,response_json)
        VALUES (%s,%s,%s,%s,%s)''', (payload['request_key'], actor['id'], operation,
                                   digest(payload), Jsonb(plain(response))))


PROFILE_SELECT = '''SELECT w.id,w.uuid,w.full_name,
    CASE WHEN w.personnel_is_internal THEN '' ELSE w.personnel_no::text END personnel_no,
    w.profession,w.profession_code,w.department,w.active,w.contractor,p.employer_id,o.name employer,
    p.citizenship_code,ct.label citizenship,p.employment_code,et.label employment,
    p.birth_date,p.phone,p.messenger,p.origin_city,p.origin_code,p.rotation_schedule,p.rotation_schedule_id,p.arrival_date,
    p.forecast_departure_date,p.leave_start_date,p.leave_end_date,p.notes,p.edit_token,
    eg.category_id,COALESCE(gc.name,w.category) category,es.smu_id,pr.label project
    FROM workers w JOIN workforce_profiles p ON p.worker_id=w.id
    LEFT JOIN workforce_organizations o ON o.id=p.employer_id
    LEFT JOIN workforce_catalog ct ON ct.code=p.citizenship_code
    LEFT JOIN workforce_catalog et ON et.code=p.employment_code
    LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
    LEFT JOIN employee_smu es ON es.worker_id=w.id
    LEFT JOIN workforce_smu_projects sp ON sp.smu_id=es.smu_id
    LEFT JOIN workforce_catalog pr ON pr.code=sp.project_code'''


def profile_snapshot(db, worker_id):
    row = plain(db.native(PROFILE_SELECT + ' WHERE w.id=%s', (worker_id,)).fetchone())
    if not row:
        abort(404, description='Карточка сотрудника не найдена.')
    row['token'] = digest(row)
    return row


def departure_warnings(db, day, worker_ids):
    """Warnings only: confirmed departure never invalidates a staffing save."""
    if getattr(db, 'dialect', None) != 'postgres' or not worker_ids:
        return {}
    rows = db.native('''SELECT DISTINCT ON(worker_id) worker_id,direction,actual_date
        FROM workforce_movements WHERE worker_id=ANY(%s) AND result_code='result.happened'
        AND (direction='departure' OR destination_kind='site')
        AND actual_date<=%s ORDER BY worker_id,actual_date DESC,updated_at DESC,id''',
        (list(worker_ids), day))
    departures = {row['worker_id']: row['actual_date'] for row in rows if row['direction'] == 'departure'}
    result = {worker_id: 'Подтверждён выезд ' + actual.strftime('%d.%m.%Y') +
              '. Назначение можно сохранить.' for worker_id, actual in departures.items()}
    stages = db.native('''SELECT DISTINCT ON(e.worker_id) e.worker_id,e.stage_code,e.effective_date
        FROM workforce_stage_events e WHERE e.worker_id=ANY(%s) AND e.confirmed AND NOT e.retracted
        AND e.effective_date<=%s AND NOT EXISTS(SELECT 1 FROM workforce_stage_events r
            WHERE r.replaces_id=e.id AND (r.confirmed OR r.retracted) AND r.effective_date<=%s)
        ORDER BY e.worker_id,e.effective_date DESC,e.sequence DESC''', (list(worker_ids), day, day))
    for stage in stages:
        worker_id = stage['worker_id']
        if stage['stage_code'] == 'stage.leave':
            result[worker_id] = ('Подтверждена неявка с ' + stage['effective_date'].strftime('%d.%m.%Y') +
                                 '. Назначение можно сохранить.')
        elif stage['stage_code'] == 'stage.onsite' and (
                worker_id not in departures or stage['effective_date'] >= departures[worker_id]):
            result.pop(worker_id, None)
    return result
