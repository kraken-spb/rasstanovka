from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Read-only placement coverage: distinct workers by PPS and effective GDLR."""
from datetime import date, timedelta
from io import BytesIO

from flask import abort, g, jsonify, request, send_file

from staffing_import import active_members_sql
from gdlr_api import staffing_eligible_sql
from user_smu_access import legacy_foreman, worker_clause


REPORT_NOTE = ('Каждый сотрудник учитывается один раз за дату независимо от числа смен. '
               'Среди сотрудников со статусом «Явка»: расставлен — есть назначение на подобъект, '
               'включая перенесённое с предыдущего дня; не расставлен — назначения нет. '
               'Все остальные статусы показаны отдельно в строке «Неявка». '
               'Состав: текущие сотрудники и все назначения выбранной даты; '
               'ППС и ГДЛР — текущие значения справочника.')


def report_author_argument(key='author'):
    selection = filter_argument(key, limit=24, ignore_empty=True)
    for value in filter_values(selection):
        if value != 'unknown' and (not value.isascii() or not value.isdecimal() or
                not 0 < int(value) < 2**63 or str(int(value)) != value):
            abort(400, description='Выберите автора расстановки из списка.')
    return selection


def report_data(db, day, pps=None, category=None, author=None):
    access, params = ('1', []) if g.user['role'] == 'viewer' else worker_clause(db)
    if legacy_foreman(db):
        access = '''(''' + access + ''' OR EXISTS (
            SELECT 1 FROM assignments own LEFT JOIN crews oc ON oc.id=own.crew_id
            WHERE own.worker_id=w.id AND own.work_date=? AND (own.foreman_user_id=? OR oc.owner_user_id=?)))'''
        params.extend([day, g.user['id'], g.user['id']])
    rows = db.execute(f'''
        WITH roster AS (
            SELECT worker_id FROM ({active_members_sql()})
            UNION SELECT worker_id FROM outstaff_members
            UNION SELECT worker_id FROM manual_employees
            UNION SELECT worker_id FROM employee_restorations
        ), population AS (
            SELECT r.worker_id FROM roster r JOIN workers w ON w.id=r.worker_id
            WHERE w.active=1 AND {staffing_eligible_sql()}
            UNION SELECT worker_id FROM assignments WHERE work_date=?
        )
        SELECT w.id,w.full_name,w.personnel_no,w.profession,w.department,
               COALESCE(w.pps,'') pps,COALESCE(gc.name,w.category,'') category,
               COALESCE(ct.name,w.contractor,'') contractor,
               COALESCE(att.status,'Явка') attendance_status
        FROM population p JOIN workers w ON w.id=p.worker_id
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
        LEFT JOIN employee_contractors ec ON ec.worker_id=w.id
        LEFT JOIN contractors ct ON ct.id=ec.contractor_id
        LEFT JOIN staffing_attendance att ON att.worker_id=w.id AND att.work_date=?
        WHERE {access} ORDER BY w.full_name COLLATE NOCASE,w.personnel_no,w.id
    ''', [day, day, *params]).fetchall()
    options = {'pps': sorted({r['pps'] for r in rows}),
               'categories': sorted({r['category'] for r in rows}, key=str.casefold)}
    people = {r['id']: {**dict(r), 'assignments': []} for r in rows
              if filter_matches(pps,r['pps']) and filter_matches(category,r['category'])}
    from staffing_api import assignment_authors
    authorized = {row['id'] for row in rows}
    assignments = [row for row in db.execute('''
        SELECT a.worker_id id,a.worker_id,a.id assignment_id,a.shift,a.shift assignment_shift,
               a.subobject_id,a.crew_id assignment_crew_id,a.created_at assignment_created_at,
               o.name object_name,s.name subobject_name
        FROM assignments a JOIN subobjects s ON s.id=a.subobject_id JOIN objects o ON o.id=s.object_id
        WHERE a.work_date=? ORDER BY a.shift,o.name,s.name,a.id
    ''', (day,)) if row['worker_id'] in authorized]
    authors = assignment_authors(db, day, assignments)
    author_labels = {}
    for assignment in assignments:
        actor = authors.get(assignment['assignment_id'])
        key = str(actor['user_id']) if actor else 'unknown'
        author_labels[key] = (actor['full_name'] or 'Пользователь №' + key) if actor else 'Автор не определён'
    # Account IDs remain distinct even when two users share the same full name.
    duplicate_names = {name for name in author_labels.values() if list(author_labels.values()).count(name) > 1}
    author_labels = {key: name + (' · №' + key if name in duplicate_names else '') for key,name in author_labels.items()}
    options.update(authors=sorted(author_labels, key=lambda key: author_labels[key].casefold()), author_labels=author_labels)
    for assignment in assignments:
        actor = authors.get(assignment['assignment_id'])
        if not filter_matches(author, str(actor['user_id']) if actor else 'unknown'):
            continue
        if assignment['worker_id'] in people:
            people[assignment['worker_id']]['assignments'].append({
                'shift': 'Ночь' if assignment['shift'] in ('2 смена', 'Ночная смена') else 'День',
                'object_name': assignment['object_name'], 'subobject_name': assignment['subobject_name']})
    if author is not None:
        people = {key: person for key,person in people.items() if person['assignments']}
    groups = {}
    totals = {'total': len(people), 'assigned': 0, 'unassigned': 0, 'absent': 0}
    for person in people.values():
        person['assigned'] = bool(person['assignments'])
        status = 'absent' if person['attendance_status'] != 'Явка' else 'assigned' if person['assigned'] else 'unassigned'
        person['status'] = status
        totals[status] += 1
        group = groups.setdefault((person['pps'], person['category']), {
            'pps': person['pps'], 'category': person['category'], 'total': 0,
            'assigned': 0, 'unassigned': 0, 'absent': 0, 'people': [], 'companies': {}})
        group['total'] += 1
        group[status] += 1
        group['people'].append(person)
        company = group['companies'].setdefault(person['contractor'], {'total': 0, 'assigned': 0, 'unassigned': 0, 'absent': 0})
        company['total'] += 1
        company[status] += 1
    return {'date': day, 'filters': {'pps': pps, 'category': category, 'author': author}, 'options': options,
            'contractors': sorted({p['contractor'] for p in people.values()}, key=str.casefold),
            'totals': totals, 'groups': sorted(groups.values(), key=lambda r: (r['pps'], r['category'].casefold())),
            'note': REPORT_NOTE + (' При выборе автора учитываются только сотрудники с назначениями этого автора; автор определяется по последнему подтверждённому событию расстановки.' if author is not None else '')}


def report_period_data(db, start, end, pps=None, category=None, author=None):
    """Daily coverage using the same scope and exclusive statuses as the daily report."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    length = (last - first).days + 1
    if not 1 <= length <= 92:
        raise ValueError('Период должен содержать от 1 до 92 дней.')
    dates = [(first + timedelta(days=i)).isoformat() for i in range(length)]
    comparison_date = (last - timedelta(days=1)).isoformat()
    calculation_dates = [comparison_date, *dates] if length == 1 else dates
    keys = ('total', 'assigned', 'unassigned', 'absent')
    def series():
        return {key: [0] * len(calculation_dates) for key in keys}
    totals, categories = series(), {}
    options = {'pps': set(), 'categories': set(), 'authors': set()}
    author_labels = {}
    for index, day in enumerate(calculation_dates):
        data = report_data(db, day, pps=pps, category=category, author=author)
        author_labels.update(data['options']['author_labels'])
        for key in options:
            options[key].update(data['options'][key])
        for key in keys:
            totals[key][index] = data['totals'][key]
        for group in data['groups']:
            parent = categories.setdefault(group['category'], {
                'category': group['category'], 'counts': series(), 'pps': {}})
            child = parent['pps'].setdefault(group['pps'], {'pps': group['pps'], 'counts': series()})
            for key in keys:
                parent['counts'][key][index] += group[key]
                child['counts'][key][index] += group[key]
    groups = sorted(categories.values(), key=lambda group: group['category'].casefold())
    def changes(counts):
        result = {key: counts[key][-1] - counts[key][-2] for key in keys}
        if length == 1:
            for key in keys:
                counts[key] = counts[key][1:]
        return result
    total_changes = changes(totals)
    for group in groups:
        group['changes'] = changes(group['counts'])
        group['pps'] = sorted(group['pps'].values(), key=lambda child: child['pps'])
        for child in group['pps']:
            child['changes'] = changes(child['counts'])
    data['options'] = {key: sorted(values, key=str.casefold) for key, values in options.items()}
    data['options']['author_labels'] = author_labels
    data['dynamics'] = {'start': start, 'end': end, 'dates': dates, 'totals': totals, 'categories': groups,
                        'comparison_date': comparison_date, 'changes': total_changes}
    data['note'] += (' Динамика рассчитывается отдельно за каждый день по тем же правилам. '
                     'Изменение — значение отчётной даты минус значение предыдущего календарного дня; дневные итоги не суммируются. '
                     'Ноль означает отсутствие записей по выбранному показателю. Это не исторический снимок состава и справочников.')
    return data


def report_changes(db, day, metric, pps=None, category=None, author=None):
    """Compare authorized identities, including churn hidden by a zero net change."""
    if metric not in ('assigned', 'unassigned', 'absent', 'total') or day == date.min.isoformat():
        abort(400, description='Выберите показатель и дату сравнения.')
    previous_date = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    current = report_data(db, day, pps=pps, category=category, author=author)
    previous = report_data(db, previous_date, pps=pps, category=category, author=author)
    def people(data):
        return {person['id']: person for group in data['groups'] for person in group['people']}
    now, before = people(current), people(previous)
    current_ids = {key for key, person in now.items() if metric == 'total' or person['status'] == metric}
    previous_ids = {key for key, person in before.items() if metric == 'total' or person['status'] == metric}
    arrived, left = current_ids - previous_ids, previous_ids - current_ids
    statuses = {'assigned': 'Расставлен', 'unassigned': 'Не расставлен', 'absent': 'Неявка'}
    def status(person):
        return statuses[person['status']] if person else 'Нет в составе'
    rows = {worker_id: {'direction': 'arrived' if worker_id in arrived else 'left',
                        'previous_status': status(before.get(worker_id)), 'current_status': status(now.get(worker_id))}
            for worker_id in arrived | left}
    return {'date': day, 'previous_date': previous_date, 'metric': metric,
            'arrived': len(arrived), 'left': len(left), 'delta': len(arrived) - len(left), 'workers': rows}


def register_placement_report(app, get_db, roles_required):
    @app.get('/api/placement-report')
    @app.get('/api/placement-report/pdf')
    @roles_required('admin', 'foreman', 'viewer')
    def placement_report():
        try:
            day = date.fromisoformat(request.args.get('date', '')).isoformat()
        except ValueError:
            abort(400, description='Укажите существующую дату отчёта.')
        start = request.args.get('start')
        if start is not None:
            try:
                start = date.fromisoformat(start).isoformat()
                if day == date.min.isoformat():
                    raise ValueError
                if not 1 <= (date.fromisoformat(day) - date.fromisoformat(start)).days + 1 <= 92:
                    raise ValueError
            except ValueError:
                abort(400, description='Выберите период от 1 до 92 дней. Начало не должно быть позже окончания.')
        metric = request.args.get('metric', 'assigned')
        if metric not in ('assigned', 'unassigned', 'absent', 'total'):
            abort(400, description='Выберите существующий показатель динамики.')
        if request.args.get('pps_details', '0') not in ('0', '1'):
            abort(400, description='Выберите вариант детализации по ППС.')
        filters = {key: filter_argument(key) for key in ('pps', 'category')}
        if any(value is not None and len(value) > 200 for value in filters.values()):
            abort(400, description='Значение фильтра слишком длинное.')
        if request.args.get('details', '0') not in ('0', '1'):
            abort(400, description='Выберите вариант PDF: сводка или сводка с ФИО.')
        filters['author'] = report_author_argument()
        db = get_db()
        db.execute('BEGIN')
        data = report_period_data(db, start, day, **filters) if start is not None else report_data(db, day, **filters)
        db.rollback()
        if start is not None:
            data['dynamics'].update(metric=metric, pps_details=request.args.get('pps_details') == '1')
        if request.path.endswith('/pdf'):
            from placement_report_pdf import build_pdf
            response = send_file(BytesIO(build_pdf(data, include_people=request.args.get('details') == '1')), mimetype='application/pdf', as_attachment=True,
                                 download_name=f'placement-report-{day}.pdf')
        else:
            response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store'
        return response
