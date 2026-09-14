from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Read-only placement coverage: distinct workers by PPS and effective GDLR."""
from datetime import date
from io import BytesIO

from flask import abort, g, jsonify, request, send_file

from staffing_import import active_members_sql
from report_queries import CATEGORY_SQL, CONTRACTOR_SQL, coverage_status
from user_smu_access import legacy_foreman, worker_clause


REPORT_NOTE = ('Каждый сотрудник учитывается один раз за дату независимо от числа смен. '
               'Среди сотрудников со статусом «Явка»: расставлен — есть назначение на подобъект, '
               'включая перенесённое с предыдущего дня; не расставлен — назначения нет. '
               'Все остальные статусы показаны отдельно в строке «Неявка». '
               'Состав: текущие сотрудники и все назначения выбранной даты; '
               'ППС и ГДЛР — текущие значения справочника.')


def report_data(db, day, pps=None, category=None):
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
            SELECT r.worker_id FROM roster r JOIN workers w ON w.id=r.worker_id WHERE w.active=1
            UNION SELECT worker_id FROM assignments WHERE work_date=?
        )
        SELECT w.id,w.full_name,w.personnel_no,w.profession,w.department,
               COALESCE(w.pps,'') pps,{CATEGORY_SQL} category,
               {CONTRACTOR_SQL} contractor,
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
    for assignment in db.execute('''
        SELECT a.worker_id,a.shift,o.name object_name,s.name subobject_name
        FROM assignments a JOIN subobjects s ON s.id=a.subobject_id JOIN objects o ON o.id=s.object_id
        WHERE a.work_date=? ORDER BY a.shift,o.name,s.name,a.id
    ''', (day,)):
        if assignment['worker_id'] in people:
            people[assignment['worker_id']]['assignments'].append({
                'shift': 'Ночь' if assignment['shift'] in ('2 смена', 'Ночная смена') else 'День',
                'object_name': assignment['object_name'], 'subobject_name': assignment['subobject_name']})
    groups = {}
    totals = {'total': len(people), 'assigned': 0, 'unassigned': 0, 'absent': 0}
    for person in people.values():
        person['assigned'] = bool(person['assignments'])
        status = coverage_status(person['attendance_status'], person['assigned'])
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
    return {'date': day, 'filters': {'pps': pps, 'category': category}, 'options': options,
            'contractors': sorted({p['contractor'] for p in people.values()}, key=str.casefold),
            'totals': totals, 'groups': sorted(groups.values(), key=lambda r: (r['pps'], r['category'].casefold())),
            'note': REPORT_NOTE}


def register_placement_report(app, get_db, roles_required):
    @app.get('/api/placement-report')
    @app.get('/api/placement-report/pdf')
    @roles_required('admin', 'foreman', 'viewer')
    def placement_report():
        try:
            day = date.fromisoformat(request.args.get('date', '')).isoformat()
        except ValueError:
            abort(400, description='Укажите существующую дату отчёта.')
        filters = {key: filter_argument(key) for key in ('pps', 'category')}
        if any(value is not None and len(value) > 200 for value in filters.values()):
            abort(400, description='Значение фильтра слишком длинное.')
        if request.args.get('details', '0') not in ('0', '1'):
            abort(400, description='Выберите вариант PDF: сводка или сводка с ФИО.')
        db = get_db()
        db.execute('BEGIN')
        data = report_data(db, day, **filters)
        db.rollback()
        if request.path.endswith('/pdf'):
            from placement_report_pdf import build_pdf
            response = send_file(BytesIO(build_pdf(data, include_people=request.args.get('details') == '1')), mimetype='application/pdf', as_attachment=True,
                                 download_name=f'placement-report-{day}.pdf')
        else:
            response = jsonify(data)
        response.headers['Cache-Control'] = 'no-store'
        return response
