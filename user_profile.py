"""Personal contacts and keyboard bindings, isolated by authenticated account."""
import json
import re
import secrets

from flask import abort, g, jsonify, request


SHORTCUTS = [
    {'id': 'staffing', 'label': 'Открыть расстановку', 'default': 'Ctrl+Shift+Digit1', 'editing': True},
    {'id': 'employees', 'label': 'Открыть сотрудников', 'default': 'Ctrl+Shift+Digit2', 'editing': True},
    {'id': 'summary', 'label': 'Открыть сводную таблицу', 'default': 'Ctrl+Shift+Digit3'},
    {'id': 'profile', 'label': 'Мой профиль и настройки', 'default': 'Ctrl+Shift+Digit4'},
    {'id': 'search', 'label': 'Поиск в текущем разделе', 'default': 'Ctrl+Shift+Digit5'},
    {'id': 'actions', 'label': 'Меню действий расстановки', 'default': 'Ctrl+Shift+Digit6', 'editing': True},
    {'id': 'import', 'label': 'Открыть импорт', 'default': 'Ctrl+Shift+Digit7', 'admin': True},
    {'id': 'export', 'label': 'Открыть экспорт', 'default': 'Ctrl+Shift+Digit8', 'editing': True},
]
# Avoid browser/system commands and the existing native undo/redo shortcuts.
RESERVED = {'KeyA', 'KeyB', 'KeyC', 'KeyD', 'KeyG', 'KeyH', 'KeyI', 'KeyJ', 'KeyN',
            'KeyO', 'KeyP', 'KeyQ', 'KeyR', 'KeyS', 'KeyT', 'KeyW', 'KeyY', 'KeyZ'}


def migrate_user_profile(db):
    db.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY REFERENCES users(id), phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
        shortcuts_json TEXT NOT NULL DEFAULT '{}', shortcuts_enabled INTEGER NOT NULL DEFAULT 1,
        edit_token TEXT NOT NULL, updated_at TEXT NOT NULL)''')


def available(role):
    if role == 'hr_viewer':
        return [s for s in SHORTCUTS if s['id'] not in ('actions', 'import')]
    return [s for s in SHORTCUTS if not (s.get('admin') and role not in ('admin', 'super_admin'))
            and not (s.get('editing') and role == 'viewer')]


def profile(db, user):
    row = db.execute('SELECT * FROM user_profiles WHERE user_id=?', (user['id'],)).fetchone()
    saved = json.loads(row['shortcuts_json']) if row else {}
    actions = available(user['role'])
    bindings = {s['id']: saved[s['id']] for s in actions if s['id'] in saved}
    # A newly granted action must not reuse a user's customized binding.
    for action in actions:
        if action['id'] not in bindings:
            bindings[action['id']] = action['default'] if action['default'] not in bindings.values() else ''
    return {'user': {k: user[k] for k in ('id', 'username', 'full_name', 'role')},
            'phone': row['phone'] if row else '', 'email': row['email'] if row else '',
            'contact': row['contact'] if row else '', 'enabled': bool(row['shortcuts_enabled']) if row else True,
            'shortcuts': bindings,
            'actions': actions, 'reserved_codes': sorted(RESERVED), 'token': row['edit_token'] if row else 'new'}


def validate(data, role):
    fields = {'phone', 'email', 'contact', 'enabled', 'shortcuts', 'token'}
    if not isinstance(data, dict) or set(data) != fields:
        abort(400, description='Передайте контактные данные и настройки своего профиля.')
    for key, limit in [('phone', 50), ('email', 254), ('contact', 300)]:
        value = data[key]
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
            abort(400, description='Проверьте поле контакта: ' + key)
        data[key] = value.strip()
    if data['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', data['email']):
        abort(400, description='Укажите корректный email.')
    if type(data['enabled']) is not bool or not isinstance(data['token'], str):
        abort(400, description='Некорректные настройки профиля.')
    bindings = data['shortcuts']
    if not isinstance(bindings, dict) or set(bindings) != {s['id'] for s in available(role)}:
        abort(400, description='Список доступных действий изменился. Откройте профиль заново.')
    used = set()
    for combo in bindings.values():
        if combo == '':
            continue
        if not isinstance(combo, str) or not re.fullmatch(r'Ctrl\+Shift\+(Key[A-Z]|Digit[0-9])', combo) or combo.split('+')[-1] in RESERVED:
            abort(400, description='Используйте Ctrl + Shift + цифру или свободную букву. Системные сочетания недоступны.')
        if combo in used:
            abort(400, description='Одно сочетание назначено нескольким действиям.')
        used.add(combo)
    return data


def register_user_profile(app, get_db, roles_required, utc_now):
    @app.get('/api/profile')
    @roles_required('admin', 'foreman', 'viewer')
    def get_profile():
        response = jsonify(profile(get_db(), g.user))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.put('/api/profile')
    @roles_required('admin', 'foreman', 'viewer')
    def save_profile():
        data = validate(request.get_json(silent=True), g.user['role'])
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT * FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not user or not user['active'] or user['role'] != g.user['role']:
                abort(403, description='Доступ изменился. Войдите заново.')
            current = profile(db, user)
            if current['token'] != data['token']:
                abort(409, description='Профиль изменён в другой вкладке. Откройте его заново.')
            db.execute('''INSERT INTO user_profiles(user_id,phone,email,contact,shortcuts_json,shortcuts_enabled,edit_token,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone,email=excluded.email,
                contact=excluded.contact,shortcuts_json=excluded.shortcuts_json,shortcuts_enabled=excluded.shortcuts_enabled,
                edit_token=excluded.edit_token,updated_at=excluded.updated_at''',
                (user['id'], data['phone'], data['email'], data['contact'], json.dumps(data['shortcuts']),
                 data['enabled'], secrets.token_urlsafe(24), utc_now()))
        return jsonify(profile(db, user))
