from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Distinct people by report date and actual assignment author."""
from datetime import date, timedelta

from flask import abort, g, jsonify, request

from staffing_api import assignment_authors


def register_personnel_dashboard(app, get_db, roles_required):
    @app.get('/api/personnel-dashboard')
    @roles_required('admin', 'foreman', 'viewer')
    def personnel_dashboard():
        try:
            end = date.fromisoformat(request.args.get('end', date.today().isoformat()))
            start_text = request.args.get('start')
            start = date.fromisoformat(start_text) if start_text is not None else end - timedelta(days=13)
            count = (end - start).days + 1
            if not 1 <= count <= 92:
                raise ValueError
        except (ValueError, OverflowError):
            abort(400, description='Выберите период от 1 до 92 дней. Начало не должно быть позже окончания.')
        dates = [(start + timedelta(days=i)).isoformat() for i in range(count)]
        db = get_db()
        db.execute('BEGIN')
        params = [dates[0], dates[-1]]
        scope = ''
        if g.user['role'] == 'foreman':
            scope = ' AND (c.owner_user_id=? OR (a.crew_id IS NULL AND a.foreman_user_id=?))'
            params.extend([g.user['id'], g.user['id']])
        # Read only actual assignments, including people absent from the latest import.
        rows = db.execute('''SELECT a.worker_id id,a.id assignment_id,a.work_date,
            a.shift assignment_shift,a.created_at assignment_created_at,
            a.crew_id assignment_crew_id,a.subobject_id
            FROM assignments a LEFT JOIN crews c ON c.id=a.crew_id
            WHERE a.work_date BETWEEN ? AND ?''' + scope, params).fetchall()
        authors = assignment_authors(db, dates[0], rows, dates[-1])
        groups = {}
        if g.user['role'] in ('super_admin', 'admin', 'viewer', 'hr_viewer'):
            for user in db.execute("SELECT id,full_name FROM users WHERE active=1 AND role IN ('super_admin','admin','foreman')"):
                groups[str(user['id'])] = {'id': str(user['id']), 'full_name': user['full_name'], 'days': {}, 'people': set()}
        chosen = filter_argument('user',ignore_empty=True)
        total_days, total_people = {}, set()
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
        def series(days):
            return [len(days.get(day, ())) for day in dates]
        users = [{'id': group['id'], 'full_name': group['full_name'], 'counts': series(group['days']),
                  'unique_count': len(group['people'])} for group in groups.values()]
        users.sort(key=lambda user: (-user['counts'][-1], -user['unique_count'], user['full_name'].casefold(), user['id']))
        return jsonify({'dates': dates, 'counts': series(total_days), 'unique_count': len(total_people),
                        'users': users, 'scope': 'own_crews' if g.user['role'] == 'foreman' else 'all'})
