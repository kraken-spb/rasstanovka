"""Distinct people by report date and actual assignment author."""
from datetime import date, timedelta
import json

from flask import abort, g, jsonify, request

from filter_values import argument as filter_argument, matches as filter_matches
from staffing_api import assignment_authors
from user_smu_access import profile as permission_profile


def register_personnel_dashboard(app, get_db, roles_required):
    @app.get('/api/personnel-dashboard')
    @roles_required('admin', 'foreman', 'viewer')
    def personnel_dashboard():
        try:
            end = date.fromisoformat(request.args.get('end', date.today().isoformat()))
            start_text = request.args.get('start')
            start = date.fromisoformat(start_text) if start_text is not None else end - timedelta(days=13)
            count = (end - start).days + 1
            if not 1 <= count <= 366:
                raise ValueError
        except (ValueError, OverflowError):
            abort(400, description='Выберите период от 1 до 366 дней. Начало не должно быть позже окончания.')
        dates = [(start + timedelta(days=i)).isoformat() for i in range(count)]
        db = get_db()
        db.execute('BEGIN')
        params = [dates[0], dates[-1]]
        scope = ''
        actor, access = permission_profile(db)
        if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman', 'viewer', 'hr_viewer'):
            abort(403, description='Нет доступа к дашборду расстановки.')
        scope_name = 'all'
        if actor['role'] in ('admin', 'foreman') and access is not None:
            if access['mode'] == 'selected':
                departments = json.loads(access['departments_json'])
                scope = ' AND w.department IN (' + ','.join('?' for _ in departments) + ')' if departments else ' AND 0=1'
                params.extend(departments)
                scope_name = 'assigned_smu'
        elif actor['role'] == 'foreman':
            scope = ' AND (c.owner_user_id=? OR (a.crew_id IS NULL AND a.foreman_user_id=?))'
            params.extend([actor['id'], actor['id']])
            scope_name = 'own_crews'
        # Read only actual assignments, including people absent from the latest import.
        rows = db.execute('''SELECT a.worker_id id,a.id assignment_id,a.work_date,
            a.shift assignment_shift,a.created_at assignment_created_at,
            a.crew_id assignment_crew_id,a.subobject_id,
            gc.id category_id,gc.name category_name,gc.color category_color
            FROM assignments a LEFT JOIN crews c ON c.id=a.crew_id
            JOIN workers w ON w.id=a.worker_id
            LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id
            LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
            WHERE a.work_date BETWEEN ? AND ?''' + scope, params).fetchall()
        authors = assignment_authors(db, dates[0], rows, dates[-1])
        groups = {}
        if actor['role'] in ('super_admin', 'admin', 'viewer', 'hr_viewer'):
            for user in db.execute("SELECT id,full_name FROM users WHERE active=1 AND role IN ('super_admin','admin','foreman')"):
                groups[str(user['id'])] = {'id': str(user['id']), 'full_name': user['full_name'], 'days': {}, 'people': set()}
        chosen = filter_argument('user',ignore_empty=True)
        total_days, total_people = {}, set()
        categories = {}
        for row in rows:
            author = authors.get(row['assignment_id'])
            key = str(author['user_id']) if author and author['user_id'] is not None else 'unknown'
            name = (author['full_name'] or 'ФИО не указано') if author else 'Автор не сохранён'
            group = groups.setdefault(key, {'id': key, 'full_name': name, 'days': {}, 'people': set()})
            group['days'].setdefault(row['work_date'], set()).add(row['id'])
            group['people'].add(row['id'])
            if filter_matches(chosen,key):
                total_days.setdefault(row['work_date'], set()).add(row['id'])
                total_people.add(row['id'])
                category_key = str(row['category_id']) if row['category_id'] is not None else 'uncategorized'
                category = categories.setdefault(category_key, {
                    'id': category_key, 'name': row['category_name'] if row['category_id'] is not None else 'Без категории',
                    'color': row['category_color'] if row['category_id'] is not None else '#94A3B8', 'days': {}})
                category['days'].setdefault(row['work_date'], set()).add(row['id'])
        def series(days):
            return [len(days.get(day, ())) for day in dates]
        users = [{'id': group['id'], 'full_name': group['full_name'], 'counts': series(group['days']),
                  'unique_count': len(group['people'])} for group in groups.values()]
        users.sort(key=lambda user: (-user['counts'][-1], -user['unique_count'], user['full_name'].casefold(), user['id']))
        category_series = [{'id': category['id'], 'name': category['name'], 'color': category['color'],
                            'counts': series(category['days'])} for category in categories.values()]
        category_series.sort(key=lambda category: (category['id'] == 'uncategorized', category['name'].casefold(), category['id']))
        return jsonify({'dates': dates, 'counts': series(total_days), 'unique_count': len(total_people),
                        'users': users, 'category_series': category_series, 'scope': scope_name})
