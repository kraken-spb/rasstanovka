"""Transactional, user-owned undo/redo for staffing edits, with stale-state checks."""
import json
import secrets
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta

from flask import abort, g, has_request_context, jsonify, request

from backup_api import create_backup


TABLES = {
    'assignments': ('id',),
    'staffing_shifts': ('work_date', 'worker_id'),
    'staffing_attendance': ('work_date', 'worker_id'),
    'staffing_performed_work': ('work_date', 'worker_id', 'shift'),
    'staffing_inherited_rows': ('work_date', 'worker_id'),
    'staffing_row_details': ('worker_id',),
    'employee_contractors': ('worker_id',),
    'employee_gdlr': ('worker_id',),
    'crew_members': ('worker_id',),
    'crews': ('id',),
}
DAILY = ('assignments', 'staffing_shifts', 'staffing_attendance', 'staffing_performed_work', 'staffing_inherited_rows')
AUDIT_FIELDS = {'edit_token', 'details_token', 'updated_at', 'updated_by', 'created_at'}
ENDPOINTS = {
    'update_day': ('Назначение или смена', ('assignments', 'staffing_shifts')),
    'save_attendance_status': ('Статус сотрудника', ('staffing_attendance',)),
    'save_performed_work': ('Выполняемые работы', ('staffing_performed_work',)),
    'save_row_responsible': ('Ответственный сотрудника', ('staffing_row_details',)),
    'save_selected_responsible': ('Ответственные выбранных', ('staffing_row_details',)),
    'save_crew_details': ('Ответственные бригады', ('crews',)),
    'bind_group_contractor': ('Подрядчик выбранных', ('employee_contractors',)),
    'bind_contractor': ('Подрядчик сотрудника', ('employee_contractors',)),
    'bind_selected_category': ('Категория ГДЛР', ('employee_gdlr',)),
    'bind_staffing_category': ('Категория ГДЛР', ('employee_gdlr',)),
    'transfer_group_crew': ('Бригада выбранных', ('crew_members',)),
    'apply_selected_transfer': ('Перенос предыдущего дня', DAILY),
}


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def semantic(row):
    return None if row is None else {key: value for key, value in row.items() if key not in AUDIT_FIELDS}


def migrate_history(db):
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_action_history (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        label TEXT NOT NULL, work_date TEXT NOT NULL, scope_json TEXT NOT NULL,
        changes_json TEXT NOT NULL, guards_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('done','undone','discarded')),
        edit_token TEXT NOT NULL, created_at TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_staffing_history_user ON staffing_action_history(user_id,state,id)')
    db.execute('''CREATE TABLE IF NOT EXISTS staffing_history_replays (
        id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES staffing_action_history(id),
        user_id INTEGER NOT NULL REFERENCES users(id), direction TEXT NOT NULL,
        changed_at TEXT NOT NULL, backup_name TEXT NOT NULL
    )''')


def request_scope(db):
    if not has_request_context() or request.endpoint not in ENDPOINTS or not getattr(g, 'user', None):
        return None
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    args = request.view_args or {}
    ids = [args['worker_id']] if 'worker_id' in args else data.get('worker_ids', [])
    if request.endpoint == 'save_crew_details':
        ids = [row[0] for row in db.execute('SELECT worker_id FROM crew_members WHERE crew_id=?', (args['crew_id'],))]
    if not isinstance(ids, list) or len(ids) > 10000 or any(type(i) is not int or i <= 0 for i in ids):
        return None
    label, tables = ENDPOINTS[request.endpoint]
    day = data.get('date') or request.headers.get('X-Staffing-Date') or datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    try:
        day = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError):
        return None
    return {'ids': sorted(set(ids)), 'tables': tables, 'date': day, 'label': label,
            'crew_id': args.get('crew_id') if 'crews' in tables else None}


def capture(db, scope):
    result = {}
    for table in scope['tables']:
        if table not in TABLES:
            raise ValueError('Unsupported history table')
        if table == 'crews':
            where, params = 'id=?', [scope['crew_id']]
        else:
            where = 'worker_id IN (' + ','.join('?' for _ in scope['ids']) + ')'
            params = list(scope['ids'])
            if table in DAILY:
                where += ' AND work_date=?'
                params.append(scope['date'])
        for row in db.execute('SELECT * FROM ' + table + ' WHERE ' + where, params):
            value = dict(row)
            key = packed([table, [value[field] for field in TABLES[table]]])
            result[key] = value
    return result


def guards(db, scope, crew_ids=None):
    ids = scope['ids']
    workers = [semantic(dict(r)) for r in db.execute(
        'SELECT * FROM workers WHERE id IN (' + ','.join('?' for _ in ids) + ') ORDER BY id', ids)]
    members = [dict(r) for r in db.execute(
        'SELECT worker_id,crew_id FROM crew_members WHERE worker_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY worker_id', ids)]
    if crew_ids is None:
        crew_ids = sorted({r['crew_id'] for r in members} | ({scope['crew_id']} if scope['crew_id'] else set()))
    crews = [semantic(dict(r)) for r in db.execute(
        'SELECT * FROM crews WHERE id IN (' + ','.join('?' for _ in crew_ids) + ') ORDER BY id', crew_ids)]
    result = {'workers': workers, 'members': members, 'crews': crews, 'crew_ids': crew_ids}
    if 'crews' in scope['tables']:
        result['whole_crew'] = [dict(r) for r in db.execute('SELECT worker_id,crew_id FROM crew_members WHERE crew_id=? ORDER BY worker_id', (scope['crew_id'],))]
    return result


def rebase(db, user_id, old, new):
    """Refresh only exact predecessor snapshots after this user's token rotation."""
    for entry in db.execute("SELECT id,changes_json FROM staffing_action_history WHERE user_id=? AND state<>'discarded'", (user_id,)).fetchall():
        changes = json.loads(entry['changes_json'])
        changed = False
        for key, pair in changes.items():
            if key not in old:
                continue
            for side in ('before', 'after'):
                if pair[side] == old[key]:
                    pair[side] = new[key]
                    changed = True
        if changed:
            db.execute('UPDATE staffing_action_history SET changes_json=?,edit_token=? WHERE id=?',
                       (packed(changes), secrets.token_hex(16), entry['id']))


class HistoryConnection(sqlite3.Connection):
    """Capture under the route's write lock and save the journal before its commit."""
    _history = None

    def execute(self, sql, parameters=(), /):
        result = super().execute(sql, parameters)
        if sql.strip().upper() == 'BEGIN IMMEDIATE' and self._history is None:
            scope = request_scope(self)
            if scope is not None:
                self._history = (scope, capture(self, scope), guards(self, scope))
        return result

    def finish_history(self):
        pending, self._history = self._history, None
        if pending is None:
            return
        scope, before, old_guards = pending
        after = capture(self, scope)
        keys = set(before) | set(after)
        changes = {key: {'before': before.get(key), 'after': after.get(key)} for key in keys
                   if semantic(before.get(key)) != semantic(after.get(key))}
        if not changes:
            rotated = {key: before.get(key) for key in keys if before.get(key) != after.get(key)}
            if rotated:
                rebase(self, g.user['id'], rotated, {key: after.get(key) for key in rotated})
            return
        new_guards = guards(self, scope)
        crew_ids = sorted(set(old_guards['crew_ids']) | set(new_guards['crew_ids']))
        dependencies = guards(self, scope, crew_ids)
        # Membership/crew details are themselves reversible; worker and crew identity must remain stable.
        self.execute("UPDATE staffing_action_history SET state='discarded',edit_token=? WHERE user_id=? AND state='undone'",
                     (secrets.token_hex(16), g.user['id']))
        self.execute('''INSERT INTO staffing_action_history(user_id,label,work_date,scope_json,changes_json,guards_json,state,edit_token,created_at)
            VALUES (?,?,?,?,?,?,'done',?,?)''', (g.user['id'], scope['label'], scope['date'], packed(scope),
            packed(changes), packed(dependencies), secrets.token_hex(16), datetime.now(timezone.utc).isoformat()))

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self._history = None
            return super().__exit__(exc_type, exc_value, traceback)
        try:
            self.finish_history()
        except BaseException:
            super().__exit__(*sys.exc_info())
            raise
        return super().__exit__(exc_type, exc_value, traceback)

    def commit(self):
        try:
            self.finish_history()
            return super().commit()
        except BaseException:
            super().rollback()
            raise

    def rollback(self):
        self._history = None
        return super().rollback()


def current_action(db, direction):
    state, order = ('done', 'DESC') if direction == 'undo' else ('undone', 'ASC')
    return db.execute(f'SELECT * FROM staffing_action_history WHERE user_id=? AND state=? ORDER BY id {order} LIMIT 1',
                      (g.user['id'], state)).fetchone()


def authorize(db, scope, target):
    actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
    if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin', 'foreman'):
        abort(403, description='Нет права изменять расстановку.')
    rows = db.execute('''SELECT w.id,w.active,w.department,c.owner_user_id FROM workers w
        LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
        WHERE w.id IN (''' + ','.join('?' for _ in scope['ids']) + ')', scope['ids']).fetchall()
    if len(rows) != len(scope['ids']) or any(not r['active'] for r in rows):
        abort(409, description='Список сотрудников изменился. Отмена недоступна.')
    # Respect both deployed crew ownership and the optional explicit SMU access schema.
    access = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_smu_access'").fetchone():
        access = db.execute('SELECT * FROM user_smu_access WHERE user_id=?', (g.user['id'],)).fetchone()
    if actor['role'] != 'super_admin':
        if access is not None and access['mode'] == 'selected':
            allowed = set(json.loads(access['departments_json']))
            if any(r['department'] not in allowed for r in rows):
                abort(403, description='Нет права редактировать сотрудников этого СМУ.')
        elif access is None and actor['role'] == 'foreman' and any(r['owner_user_id'] != g.user['id'] for r in rows):
            abort(403, description='Сотрудники больше не входят в ваши бригады.')
    for key, row in target.items():
        if row is None:
            continue
        table = json.loads(key)[0]
        if table == 'crew_members':
            crew = db.execute('SELECT owner_user_id FROM crews WHERE id=?', (row['crew_id'],)).fetchone()
            if not crew or (actor['role'] == 'foreman' and access is None and crew['owner_user_id'] != g.user['id']):
                abort(403, description='Нет права вернуть сотрудника в прежнюю бригаду.')
        if table in ('employee_gdlr', 'employee_contractors'):
            catalog, field = ('gdlr_categories', 'category_id') if table == 'employee_gdlr' else ('contractors', 'contractor_id')
            active = db.execute(f'SELECT active FROM {catalog} WHERE id=?', (row[field],)).fetchone()
            if not active or not active['active']:
                abort(409, description='Прежнее значение справочника удалено или отключено.')
        if table == 'assignments' and row['subobject_id'] is not None:
            if not db.execute('SELECT 1 FROM subobjects WHERE id=?', (row['subobject_id'],)).fetchone():
                abort(409, description='Подобъект удалён из справочника. Отмена недоступна.')


def restore(db, target, now):
    # Delete only owned leaf rows first so a restored date/shift cannot collide with this action's removed row.
    for key, row in target.items():
        table, values = json.loads(key)
        if row is None:
            if table == 'crews':
                raise ValueError('Crew deletion cannot be replayed')
            db.execute('DELETE FROM ' + table + ' WHERE ' + ' AND '.join(field + '=?' for field in TABLES[table]), values)
    for key, original in target.items():
        if original is None:
            continue
        table, _ = json.loads(key)
        row = dict(original)
        for token in ('edit_token', 'details_token'):
            if token in row:
                row[token] = secrets.token_hex(16)
        if 'updated_at' in row:
            row['updated_at'] = now
        if 'updated_by' in row:
            row['updated_by'] = g.user['id']
        fields = list(row)
        updates = [field for field in fields if field not in TABLES[table]]
        sql = 'INSERT INTO ' + table + '(' + ','.join(fields) + ') VALUES (' + ','.join('?' for _ in fields) + ')'
        sql += ' ON CONFLICT(' + ','.join(TABLES[table]) + ') DO UPDATE SET ' + ','.join(field + '=excluded.' + field for field in updates)
        db.execute(sql, [row[field] for field in fields])


def assignment_events(db, before, after, now):
    def places(snapshot):
        return {(r['worker_id'], r['work_date'], '2 смена' if r['shift'] == 'Ночная смена' else r['shift']): r
                for key, r in snapshot.items() if json.loads(key)[0] == 'assignments' and r is not None}
    old, new = places(before), places(after)
    for key in set(old) | set(new):
        left, right = old.get(key), new.get(key)
        previous, current = left['subobject_id'] if left else None, right['subobject_id'] if right else None
        if previous != current:
            row = right or left
            db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at)
                VALUES (?,?,?,?,?,?,?,?)''', (row['crew_id'], *key, previous, current, g.user['id'], now))


def refresh_copied_snapshots(db, target):
    from selected_transfer import records_for, digest
    from staffing_shifts import canonical_shift
    kinds = {'assignments': 'assignment', 'staffing_shifts': 'shift', 'staffing_attendance': 'attendance', 'staffing_performed_work': 'work'}
    for key, row in target.items():
        if json.loads(key)[0] != 'staffing_inherited_rows' or row is None:
            continue
        snapshot = {}
        for table, records in records_for(db, row['work_date'], [row['worker_id']]).items():
            if table not in kinds:
                continue
            kind = kinds[table]
            for record in records:
                item = kind + (':' + canonical_shift(record['shift']) if kind in ('assignment', 'work') else '')
                snapshot.setdefault(item, []).append(digest(record))
        for values in snapshot.values():
            values.sort()
        db.execute('UPDATE staffing_inherited_rows SET snapshot_json=? WHERE work_date=? AND worker_id=?',
                   (json.dumps(snapshot, sort_keys=True, ensure_ascii=False), row['work_date'], row['worker_id']))


def register_history(app, get_db, roles_required, utc_now):
    @app.get('/api/staffing/history')
    @roles_required('admin', 'foreman')
    def staffing_history_state():
        result = {}
        for direction in ('undo', 'redo'):
            row = current_action(get_db(), direction)
            result[direction] = None if row is None else {'id': row['id'], 'token': row['edit_token'],
                'label': row['label'], 'date': row['work_date'], 'workers': len(json.loads(row['scope_json'])['ids'])}
        return jsonify(result)

    @app.post('/api/staffing/history/<direction>')
    @roles_required('admin', 'foreman')
    def staffing_history_replay(direction):
        if direction not in ('undo', 'redo'):
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('id')) is not int or not isinstance(data.get('token'), str):
            abort(400, description='Обновите историю действий.')
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                entry = current_action(db, direction)
                if entry is None or data.get('id') != entry['id'] or data.get('token') != entry['edit_token']:
                    abort(409, description='История действий изменилась. Обновите расстановку.')
                scope, changes = json.loads(entry['scope_json']), json.loads(entry['changes_json'])
                expected_side, target_side = ('after', 'before') if direction == 'undo' else ('before', 'after')
                current = capture(db, scope)
                if any(current.get(key) != pair[expected_side] for key, pair in changes.items()):
                    abort(409, description='Данные уже изменены. Отмена не затронула более свежие правки.')
                for key, pair in changes.items():
                    table, values = json.loads(key)
                    row = db.execute('SELECT * FROM ' + table + ' WHERE ' + ' AND '.join(field + '=?' for field in TABLES[table]), values).fetchone()
                    if (dict(row) if row else None) != pair[expected_side]:
                        abort(409, description='Запись уже используется другой расстановкой. Действие не выполнено.')
                expected_guards = json.loads(entry['guards_json'])
                actual_guards = guards(db, scope, expected_guards['crew_ids'])
                if actual_guards != expected_guards:
                    abort(409, description='Сотрудники или бригады изменились. Обновите расстановку.')
                target = {key: pair[target_side] for key, pair in changes.items()}
                authorize(db, scope, target)
                try:
                    backup = create_backup(db, scope['date'], reason='staffing-' + direction)
                except (OSError, sqlite3.Error):
                    abort(503, description='Не удалось создать резервную копию. Действие не выполнено.')
                now = utc_now()
                restore(db, target, now)
                refresh_copied_snapshots(db, target)
                # Preserve the one-assignment-per-date-and-shift rule, including legacy night aliases.
                if 'assignments' in scope['tables']:
                    for row in db.execute('''SELECT worker_id,COUNT(*) n FROM assignments WHERE work_date=?
                        AND worker_id IN (''' + ','.join('?' for _ in scope['ids']) + ") GROUP BY worker_id,CASE WHEN shift='Ночная смена' THEN '2 смена' ELSE shift END", [scope['date'], *scope['ids']]):
                        if row['n'] > 1:
                            abort(409, description='У сотрудника уже есть другое назначение на эту дату и смену.')
                restored = capture(db, scope)
                assignment_events(db, current, restored, now)
                rebase(db, g.user['id'], target, {key: restored.get(key) for key in target})
                # Update dependency snapshots only when they exactly match the state we just restored.
                new_guards = guards(db, scope, expected_guards['crew_ids'])
                db.execute('UPDATE staffing_action_history SET state=?,guards_json=?,edit_token=? WHERE id=?',
                           ('undone' if direction == 'undo' else 'done', packed(new_guards), secrets.token_hex(16), entry['id']))
                db.execute('INSERT INTO staffing_history_replays(action_id,user_id,direction,changed_at,backup_name) VALUES (?,?,?,?,?)',
                           (entry['id'], g.user['id'], direction, now, backup.name))
        except sqlite3.IntegrityError:
            abort(409, description='Связанные данные изменились. Действие не выполнено.')
        return jsonify({'direction': direction, 'label': entry['label'], 'date': scope['date'], 'workers': len(scope['ids'])})
