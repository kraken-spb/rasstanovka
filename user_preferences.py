"""Account-scoped presentation preferences; never changes placement data."""
import json
from datetime import date

from flask import abort, g, jsonify, request


COLUMNS = {'number', 'object', 'subobject', 'contractor', 'employer', 'name', 'personnel',
           'category', 'itr', 'brigadier', 'shift', 'attendance', 'performed_work', 'assignment_author'}


def migrate_preferences(db):
    db.execute('''CREATE TABLE IF NOT EXISTS user_staffing_preferences (
        user_id INTEGER PRIMARY KEY REFERENCES users(id), settings_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)''')


def staffing_preferences(db, user_id):
    row = db.execute('SELECT settings_json FROM user_staffing_preferences WHERE user_id=?', (user_id,)).fetchone()
    return json.loads(row['settings_json']) if row else {}


def validate_patch(data):
    if not isinstance(data, dict) or not data or set(data) - {
            'groupMode', 'groupLevels', 'department', 'employer', 'contractor', 'category', 'author', 'unassigned', 'shift', 'search', 'regexMode', 'columns', 'freshness', 'date', 'pps'}:
        abort(400, description='Некорректные настройки расстановки.')
    for field, value in data.items():
        if field in ('department','employer','contractor','category','author','shift','freshness','pps') and isinstance(value,str) and value.startswith('__multi_filter_v1__:'):
            try:
                selected=json.loads(value[len('__multi_filter_v1__:'):])
            except (ValueError,TypeError):
                abort(400, description='Некорректный список значений фильтра.')
            if not isinstance(selected,list) or not 1 <= len(selected) <= 100 or any(not isinstance(item,str) or item.startswith('__multi_filter_v1__:') for item in selected):
                abort(400, description='Некорректный список значений фильтра.')
            for item in selected: validate_patch({field:item})
            continue
        valid = False
        if field == 'groupMode': valid = value in ('crew', 'itr', 'hierarchy')
        elif field == 'groupLevels':
            valid = (isinstance(value, list) and 1 <= len(value) <= 8
                     and all(isinstance(key, str) and key in {'itr', 'crew', 'shift', 'department', 'pps', 'category', 'employer', 'contractor'} for key in value)
                     and len(set(value)) == len(value))
        elif field == 'pps': valid = value in ('', 'ППС15', 'ППС19')
        elif field == 'shift': valid = value in ('', 'all', 'none', '1 смена', '2 смена')
        elif field == 'date':
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
        elif field == 'freshness': valid = value in ('', 'current', 'inherited', 'mixed', 'unknown', 'empty')
        elif field in ('unassigned', 'regexMode'): valid = type(value) is bool
        elif field in ('department', 'employer', 'contractor', 'category', 'author', 'search'):
            valid = isinstance(value, str) and len(value) <= (300 if field == 'search' else 500)
        elif field == 'columns' and isinstance(value, dict) and {'hidden', 'widths'} <= set(value) <= {'hidden', 'widths', 'order'}:
            hidden, widths = value['hidden'], value['widths']
            valid = (isinstance(hidden, list) and all(isinstance(v, str) and v in COLUMNS for v in hidden)
                     and len(hidden) == len(set(hidden)) and len(hidden) < len(COLUMNS)
                     and isinstance(widths, dict) and not set(widths) - COLUMNS
                     and all(type(v) is int and 64 <= v <= 800 for v in widths.values()))
            if valid and 'order' in value:
                order = value['order']
                # Open tabs may still send the complete order from before the author column.
                if (isinstance(order, list) and all(isinstance(v, str) for v in order)
                        and len(order) == len(COLUMNS) - 1 and set(order) == COLUMNS - {'assignment_author'}):
                    order = value['order'] = [*order, 'assignment_author']
                valid = (isinstance(order, list) and len(order) == len(COLUMNS)
                         and all(isinstance(v, str) for v in order) and set(order) == COLUMNS)
        if not valid:
            abort(400, description='Недопустимое значение настройки: ' + field)
    return data


def register_preferences(app, get_db, roles_required, utc_now):
    @app.get('/api/preferences/staffing')
    @roles_required('admin', 'foreman')
    def get_preferences():
        return jsonify({'settings': staffing_preferences(get_db(), g.user['id'])})

    @app.patch('/api/preferences/staffing')
    @roles_required('admin', 'foreman')
    def save_preferences():
        data = validate_patch(request.get_json(silent=True))
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            # Merge only fields touched by this browser, keeping unrelated account settings.
            settings = staffing_preferences(db, g.user['id'])
            if 'columns' in data and 'order' not in data['columns'] and 'order' in settings.get('columns', {}):
                data['columns']['order'] = settings['columns']['order']
            settings.update(data)
            db.execute('''INSERT INTO user_staffing_preferences(user_id,settings_json,updated_at) VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at''',
                (g.user['id'], json.dumps(settings, ensure_ascii=False), utc_now()))
        return jsonify({'settings': settings})
