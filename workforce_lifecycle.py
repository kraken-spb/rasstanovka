"""Date-aware source facts stay separate from plans and unresolved identity."""
from datetime import timedelta
import re

from workforce_sources import normalize, source_date


def key(record):
    return (record['filename'], record['sheet'], record['row'])


def residence(fields):
    place = str(fields.get('accommodation', '')).casefold()
    notes = place + ' ' + str(fields.get('notes', '')).casefold()
    if re.search(r'не прибыл|не приехал|не засел|съехал|отмен', notes):
        return 'negative'
    if re.search(r'засел|карла маркса|тимирязев|дальневосточ|сурнова|урицкого|революц|марата|бригантин|бриз|хостел', place):
        return 'positive'
    return 'unknown'


def classify(record, day):
    fields, sheet = record['fields'], normalize(record['sheet'])
    arrival, departure, leave = (source_date(fields.get(k)) for k in ('arrival', 'departure', 'leave_start'))
    ticket, transit = (source_date(fields.get(k)) for k in ('ticket_arrival', 'transit_arrival'))
    stage, confirmed, event_date, notes = 'stage.inbound', False, None, []
    if record['service'] == 'recruitment':
        presence = residence(fields)
        if presence == 'negative':
            notes.append('Есть отметка о неприбытии или выезде из ПВП; дата билета не подтверждает присутствие.')
        elif ticket and ticket > day:
            pass
        elif presence == 'positive':
            stage, confirmed, event_date = 'stage.pvp', True, ticket
        elif ticket and ticket <= day:
            stage = 'stage.pvp'
            notes.append('Дата билета наступила, но заселение в ПВП не подтверждено.')
        else:
            notes.append('Нет подтверждения прибытия в ПВП.')
    elif sheet == 'явка':
        stage, confirmed = 'stage.onsite', True
        event_date = arrival if arrival and arrival <= day else None
        if arrival and arrival > day:
            notes.append('В Явке будущая дата прибытия; дата перехода требует проверки.')
    elif sheet == 'неявка':
        stage, confirmed = 'stage.leave', True
        event_date = leave if leave and leave <= day else departure if departure and departure <= day else None
        if leave and leave > day:
            notes.append('В Неявке будущее начало отпуска; дата перехода требует проверки.')
    elif sheet == 'пвп':
        stage, confirmed = 'stage.pvp', residence(fields) != 'negative'
        event_date = transit if transit and transit <= day else None
        if transit and transit > day:
            stage, confirmed = 'stage.inbound', False
        if arrival and arrival <= day:
            notes.append('Дата прибытия на участок в строке ПВП не принята за самостоятельное подтверждение Явки.')
    elif sheet in ('аустаффинг', 'аутстаффинг'):
        stage = 'stage.onsite'
        confirmed = bool(arrival and arrival <= day and (not departure or departure > day))
        event_date = arrival if confirmed else None
    elif arrival and arrival <= day:
        notes.append('Наступившая дата в Заезде не подтверждает переход в Явку.')
    return {'stage': stage, 'confirmed': confirmed, 'event_date': event_date,
            'rank': {'stage.leave': 1, 'stage.inbound': 2, 'stage.pvp': 3, 'stage.onsite': 4}[stage], 'notes': notes}


def merge_sources(records, day, preferred=None):
    """Call only after identity has been matched or explicitly confirmed."""
    candidates = [(record, classify(record, day)) for record in records]
    warnings = []
    negatives = []
    for record in records:
        if residence(record['fields']) != 'negative':
            continue
        text = str(record['fields'].get('accommodation', '')) + ' ' + str(record['fields'].get('notes', ''))
        match = re.search(r'(?:не прибыл|не приехал|не засел|съехал|отмен)[^\n;]{0,100}?(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})', text, re.I)
        negative_day = source_date(match[1]) if match else None
        if negative_day is None or negative_day <= day:
            negatives.append(negative_day)
    for _, fact in candidates:
        if fact['stage'] == 'stage.pvp' and fact['confirmed'] and any(
                negative is None or fact['event_date'] is None or negative >= fact['event_date'] for negative in negatives):
            fact['confirmed'] = False
            fact['notes'].append('Есть противоречащее сообщение о неприбытии или выезде из ПВП. Более позднее возвращение не подтверждено.')
    actual = [candidate for candidate in candidates if candidate[1]['confirmed']]
    pool = actual or candidates
    if not actual and any(normalize(record['sheet']) == 'заезд' and record['service'] == 'rotation' for record, _ in pool):
        # User's explicit source priority for an unconfirmed PVP / inbound conflict.
        pool = [candidate for candidate in pool if candidate[1]['stage'] != 'stage.pvp'] or pool
    def priority(candidate):
        record, fact = candidate
        pvp_comp = fact['stage'] == 'stage.pvp' and record['service'] == 'recruitment'
        return (fact['rank'], fact['event_date'].isoformat() if fact['event_date'] else '',
                key(record) == preferred, pvp_comp, record['service'] == 'rotation',
                sum(bool(value) for value in record['fields'].values()), key(record))
    chosen, fact = max(pool, key=priority)
    if actual:
        dated = [candidate for candidate in actual if candidate[1]['event_date']]
        if fact['event_date'] and dated:
            latest = max(dated, key=lambda candidate: (candidate[1]['event_date'], candidate[1]['rank']))
            if latest[1]['event_date'] > fact['event_date']:
                chosen, fact = latest
                warnings.append('Выбран более поздний фактический переход; предыдущий этап относится к прошлому циклу.')
        elif len({item[1]['stage'] for item in actual}) > 1:
            warnings.append('Один из этапов без даты. Применён порядок этапов; цикл вахты требует проверки.')
    fields = dict(chosen['fields'])
    # Only an unambiguous value from an already matched person fills a missing cell.
    for field in ('tab', 'citizenship', 'dob', 'phone', 'messenger', 'schedule'):
        values = {record['fields'].get(field) for record in records} - {None, ''}
        if field == 'phone':
            from workforce_identity import phone_key
            comparable = {phone_key(value) for value in values}
        elif field == 'citizenship':
            comparable = {str(value).casefold().strip() for value in values}
        else:
            comparable = values
        if not fields.get(field) and len(comparable) == 1:
            fields[field] = next(iter(values))
        if len(comparable) > 1:
            warnings.append('Разные значения «' + field + '»: ' + ' / '.join(sorted(values)) + '.')
            if field in ('dob', 'phone', 'messenger'):
                fields.pop(field, None)
    markers = ' '.join(str(record['fields'].get(field, '')).casefold() for record in records for field in ('action', 'employment_type'))
    if fields.get('tab'):
        employment = 'employment.staff'
    elif 'аутстафф' in markers and 'внешн' in markers:
        employment = 'employment.external'
    elif 'аутстафф' in markers and 'внутрен' in markers:
        employment = 'employment.internal'
    elif any('патенты' in record['sheet'].casefold() for record in records):
        employment = 'employment.irs'
    elif chosen['service'] == 'recruitment':
        employment = 'employment.recruitment'
    elif normalize(chosen['sheet']) in ('явка', 'неявка'):
        employment = 'employment.staff'
    else:
        employment = None
    basis = 'basis.request' if employment in ('employment.external', 'employment.internal') else (
        'basis.ticket' if any(record['service'] == 'recruitment' and normalize(record.get('section')) == 'покупкабилетов' for record in records)
        else 'basis.schedule' if any(record['fields'].get('schedule') for record in records) else None)
    plan = None
    if fact['stage'] == 'stage.leave':
        end = source_date(fields.get('leave_end'))
        if end:
            plan = end + timedelta(days=2)
    elif chosen['service'] == 'recruitment':
        plan = source_date(fields.get('planned_arrival'))
    else:
        plans = [source_date(record['fields'].get('planned_arrival')) for record in records if record['service'] == 'recruitment']
        future = [value for value in plans if value and value >= day]
        plan = min(future) if future else None
    for record, candidate in candidates:
        warnings.extend(record.get('mapping_notes', []))
        warnings.extend(candidate['notes'])
    warnings = list(dict.fromkeys(warnings))
    return {'fields': fields, 'chosen': key(chosen), 'stage': fact['stage'], 'confirmed': fact['confirmed'],
            'event_date': fact['event_date'].isoformat() if fact['event_date'] else None,
            'employment_code': employment, 'basis_code': basis, 'planned_date': plan.isoformat() if plan else None,
            'warnings': warnings, 'forecast_departure_date': fields.get('departure') if normalize(chosen['sheet']) == 'явка' else None,
            'pure_outstaff': all(normalize(record['sheet']) in ('аустаффинг', 'аутстаффинг') for record in records)}
