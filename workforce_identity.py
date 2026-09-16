"""Reuse durable personnel/outstaff keys; weak name matches require review."""
from collections import defaultdict
import hashlib
import json

from workforce_core import digest, plain


def normalized(value):
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def phone_key(value):
    digits = ''.join(char for char in str(value or '') if char.isdigit())
    return '7' + digits[1:] if len(digits) == 11 and digits.startswith('8') else digits


def legacy_outstaff_key(name, employer):
    return hashlib.sha256(json.dumps([normalized(name), normalized(employer)],
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_groups(records):
    """Group only corroborated identities within an upload, preserving conflicts."""
    groups = defaultdict(list)
    for index, record in enumerate(records):
        fields = record['fields']
        if fields.get('tab'):
            key = ('tab', fields['tab'])
        elif fields.get('dob'):
            key = ('dob', normalized(fields.get('name')), fields['dob'], normalized(fields.get('employer')))
        elif phone_key(fields.get('phone')):
            key = ('phone', normalized(fields.get('name')), phone_key(fields['phone']), normalized(fields.get('employer')))
        elif record['sheet'].casefold() in ('аустаффинг', 'аутстаффинг'):
            key = ('outstaff', legacy_outstaff_key(fields.get('name'), fields.get('employer')))
        else:
            # A name alone is not a durable identity. Cross-source links are reviewed.
            key = ('source', index)
        groups[key].append(index)
    return list(groups.values())


def worker_index(db):
    rows = plain(db.native('''SELECT w.id,w.uuid,w.full_name,w.personnel_no::text personnel_no,
        w.personnel_is_internal,w.active,w.employer,w.profession,w.department,w.contractor,w.category source_category,
        p.birth_date,p.phone,p.edit_token profile_token,p.employment_code,p.updated_at profile_updated_at,
        eg.category_id,eg.edit_token category_token,gc.name category,sm.smu_id,
        om.identity_key outstaff_identity_key,
        EXISTS(SELECT 1 FROM manual_employees me WHERE me.worker_id=w.id) manually_created,
        (SELECT max(a.id) FROM workforce_audit a WHERE a.worker_id=w.id) audit_revision
        FROM workers w LEFT JOIN workforce_profiles p ON p.worker_id=w.id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        LEFT JOIN employee_smu sm ON sm.worker_id=w.id LEFT JOIN outstaff_members om ON om.worker_id=w.id''').fetchall())
    by_id, by_tab, by_name, by_outstaff = {}, {}, defaultdict(list), {}
    for row in rows:
        row['token'] = digest(row)
        by_id[row['id']] = row
        if not row['personnel_is_internal'] and row['personnel_no'].isdigit():
            by_tab[row['personnel_no']] = row
        by_name[normalized(row['full_name'])].append(row)
        if row['outstaff_identity_key']:
            by_outstaff[row['outstaff_identity_key']] = row
    ambiguous_aliases = set()
    for source in db.native('''SELECT DISTINCT worker_id,mapped_json->>'outstaff_identity_key' identity_key
        FROM workforce_source_records WHERE mapped_json ? 'outstaff_identity_key' '''):
        if source['worker_id'] in by_id and source['identity_key'] not in ambiguous_aliases:
            existing = by_outstaff.get(source['identity_key'])
            if existing and existing['id'] != source['worker_id']:
                # An inconsistent alias cannot become an automatic identity match.
                by_outstaff.pop(source['identity_key'], None)
                ambiguous_aliases.add(source['identity_key'])
            else:
                by_outstaff[source['identity_key']] = by_id[source['worker_id']]
    return {'by_id': by_id, 'by_tab': by_tab, 'by_name': by_name, 'by_outstaff': by_outstaff}


def match_worker(records, index, *, reviewed=False):
    tabs = {record['fields'].get('tab') for record in records} - {None, ''}
    if len(tabs) > 1:
        return {'worker': None, 'candidates': [], 'issue': 'В связанных строках разные табельные номера.'}
    names = {normalized(record['fields'].get('name')) for record in records}
    births = {record['fields'].get('dob') for record in records} - {None, ''}
    if not reviewed and (len(names) > 1 or len(births) > 1):
        candidates = [index['by_tab'][tab] for tab in tabs if tab in index['by_tab']]
        return {'worker': None, 'candidates': candidates,
                'issue': 'Идентификационные данные связанных строк противоречат друг другу. Требуется подтверждение связи.'}
    if tabs:
        worker = index['by_tab'].get(next(iter(tabs)))
        if worker:
            if worker['birth_date'] and births and births != {worker['birth_date']}:
                return {'worker': None, 'candidates': [worker], 'issue': 'Табельный номер совпал, но дата рождения отличается от базы.'}
            return {'worker': worker, 'candidates': [], 'issue': None, 'basis': 'Табельный номер'}
    matched = {}
    candidates = {worker['id']: worker for name in names for worker in index['by_name'].get(name, [])}
    for record in records:
        fields = record['fields']
        persisted = index['by_outstaff'].get(legacy_outstaff_key(fields.get('name'), fields.get('employer')))
        if persisted and (not tabs or persisted['personnel_is_internal'] or not persisted['personnel_no'].isdigit()):
            matched[persisted['id']] = persisted
        for worker in candidates.values():
            same_employer = normalized(worker['employer']) == normalized(fields.get('employer'))
            same_birth = bool(fields.get('dob') and worker['birth_date'] == fields['dob'])
            same_phone = bool(phone_key(fields.get('phone')) and phone_key(worker['phone']) == phone_key(fields['phone']))
            compatible_tab = not tabs or worker['personnel_is_internal'] or not worker['personnel_no'].isdigit()
            if compatible_tab and same_employer and (same_birth or same_phone):
                matched[worker['id']] = worker
    if len(matched) == 1:
        worker = next(iter(matched.values()))
        if worker['birth_date'] and births and births != {worker['birth_date']}:
            return {'worker': None, 'candidates': [worker],
                    'issue': 'Идентификаторы совпали, но дата рождения отличается от базы. Подтвердите связь.'}
        return {'worker': worker, 'candidates': [], 'issue': None,
                'basis': 'Сохранённая связь аутстаффа или независимые идентификаторы'}
    if candidates or matched:
        combined = {**candidates, **matched}
        return {'worker': None, 'candidates': list(combined.values()),
                'issue': 'В базе есть похожие сотрудники. Подтвердите связь; новая запись пока не создаётся.'}
    return {'worker': None, 'candidates': [], 'issue': None, 'basis': 'Новый сотрудник'}
