"""Read-only position cards from the same authorized snapshot as the XLSX export."""
import io
import re
from datetime import date

from flask import abort, request, send_file

from staffing_export import _rows, SHIFT_LABELS
from position_cards_pdf import build_position_cards_pdf


def _filter(name, *, keep_empty=False):
    values = request.args.getlist(name)
    if len(values) > 100 or any(len(value) > 500 for value in values):
        abort(400, description='Слишком длинный фильтр отчёта.')
    return set(values if keep_empty else (value for value in values if value))


def _cards(db, rows):
    """Enrich only already-authorized assignment IDs; never widen the population."""
    details = {}
    ids = [row['id'] for row in rows]
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        for row in db.execute('''
            SELECT a.id, a.worker_id, a.crew_id, c.name crew_name,
                   st.name stage, es.smu_id, sc.name smu_name,
                   u.full_name responsible_name
            FROM assignments a
            JOIN subobjects s ON s.id=a.subobject_id
            JOIN objects o ON o.id=s.object_id
            LEFT JOIN location_stages st ON st.id=o.stage_id
            LEFT JOIN crews c ON c.id=a.crew_id
            LEFT JOIN employee_smu es ON es.worker_id=a.worker_id
            LEFT JOIN smu_catalog sc ON sc.id=es.smu_id
            LEFT JOIN users u ON u.id=sc.site_chief_user_id
            WHERE a.id IN (''' + ','.join('?' for _ in batch) + ')', batch):
            details[row['id']] = dict(row)
    cards = {}
    for row in rows:
        detail = details[row['id']]
        department = detail['smu_name'] or row['department'] or ''
        key = (row['subobject_id'], detail['smu_id'], department)
        card = cards.setdefault(key, {
            'stage': detail['stage'] or '', 'object_name': row['object_name'],
            'subobject_name': row['subobject_name'], 'department': department,
            'responsible_name': detail['responsible_name'] or '', 'shifts': {},
        })
        shift = card['shifts'].setdefault(row['normalized_shift'], {
            'label': SHIFT_LABELS[row['normalized_shift']], 'groups': {},
        })
        itr = row['linear_itr_override'] if row['linear_itr_override'] is not None else row['crew_linear_itr']
        brigadier = row['brigadier_override'] if row['brigadier_override'] is not None else row['crew_brigadier']
        group = shift['groups'].setdefault((detail['crew_id'], itr or '', brigadier or ''), {
            'linear_itr': itr or '', 'brigadier': brigadier or '',
            'crew_name': detail['crew_name'] or '', 'works': {},
        })
        description = row['performed_work'] or ''
        work = group['works'].setdefault(description, {'description': description, 'workers': []})
        work['workers'].append({
            'full_name': row['full_name'], 'profession': row['profession'] or row['gsp_profession'] or '',
            'personnel_no': row['personnel_no'] or '',
        })
    result = sorted(cards.values(), key=lambda card: tuple(card[key].casefold() for key in
                    ('stage', 'object_name', 'subobject_name', 'department')))
    for card in result:
        card['shifts'] = [card['shifts'][shift] for shift in SHIFT_LABELS if shift in card['shifts']]
        for shift in card['shifts']:
            shift['groups'] = list(shift['groups'].values())
            for group in shift['groups']:
                group['works'] = list(group['works'].values())
    return result


def register_position_cards_route(app, get_db, roles_required):
    @app.get('/api/staffing/position-cards/pdf')
    @roles_required('admin', 'foreman')
    def position_cards_pdf():
        raw_day = request.args.get('date', '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_day):
            abort(400, description='Укажите дату расстановки в формате ГГГГ-ММ-ДД.')
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            abort(400, description='Укажите существующую дату расстановки.')
        shifts = _filter('shift') or {'all'}
        if not shifts <= {'all', *SHIFT_LABELS}:
            abort(400, description='Выберите смену или все смены.')
        shifts = tuple(SHIFT_LABELS) if 'all' in shifts else tuple(s for s in SHIFT_LABELS if s in shifts)
        departments, contractors = _filter('department'), _filter('contractor')
        categories = _filter('category', keep_empty=True)
        query = request.args.get('query', '')
        if len(query) > 500:
            abort(400, description='Слишком длинный фильтр отчёта.')
        words = query.casefold().replace('ё', 'е').split()
        db = get_db()
        db.execute('BEGIN')
        try:
            rows = [row for row in _rows(db, day.isoformat(), shifts)
                    if (not departments or row['department'] in departments)
                    and (not contractors or row['contractor'] in contractors)
                    and (not categories or (row['category'] or '') in categories)
                    and all(word in (row['object_name'] + ' ' + row['subobject_name']).casefold().replace('ё', 'е')
                            for word in words)]
            cards = _cards(db, rows)
        finally:
            db.commit()
        if not rows:
            return {'error': 'По выбранным дате и фильтрам нет расставленных сотрудников.'}, 404
        summary = []
        if contractors:
            summary.append('Фильтр подрядчиков: ' + str(len(contractors)))
        if categories:
            summary.append('Фильтр ГДЛР: ' + str(len(categories)))
        if query:
            summary.append('Поиск по позициям включён')
        output = build_position_cards_pdf(cards, day_label=day.strftime('%d.%m.%Y'),
                                          filter_summary=' · '.join(summary))
        response = send_file(io.BytesIO(output), as_attachment=True,
                             download_name=f'Карточки позиций {day.isoformat()}.pdf', mimetype='application/pdf')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Export-Count'] = str(len(rows))
        response.headers['X-Export-Card-Count'] = str(len(cards))
        return response
