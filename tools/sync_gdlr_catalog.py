"""Preview/apply the current import's categories without changing placement data."""
import argparse
import hashlib
import json
import secrets
import sqlite3
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def category_name(value):
    name = unicodedata.normalize('NFC', ' '.join((value or '').split()))
    if not name or len(name) > 200 or name.startswith('#') or any(unicodedata.category(c).startswith('C') for c in name):
        raise ValueError('Пустая или некорректная категория: ' + repr(value))
    return name, name.casefold()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def preview(db, explicit=None):
    explicit = explicit or {}
    batch = db.execute('SELECT id,filename,sha256 FROM staffing_imports ORDER BY id DESC LIMIT 1').fetchone()
    if batch is None:
        raise ValueError('Нет текущего импорта сотрудников.')
    sources = [dict(row) for row in db.execute('''SELECT w.id,w.category FROM staffing_import_members sm
        JOIN workers w ON w.id=sm.worker_id WHERE sm.import_id=? ORDER BY w.id''', (batch['id'],))]
    catalog = [dict(row) for row in db.execute('SELECT * FROM gdlr_categories ORDER BY id')]
    existing, names = {}, {}
    for item in catalog:
        _, key = category_name(item['name'])
        if key in existing:
            raise ValueError('В существующем справочнике неоднозначный дубликат: ' + item['name'])
        existing[key] = item
    for row in sources:
        if not (row['category'] or '').strip():
            continue
        name, key = category_name(row['category'])
        names.setdefault(key, name)
    people = [dict(row) for row in db.execute('''SELECT w.id,w.full_name,w.personnel_no,w.category,
        e.category_id,e.edit_token,EXISTS(SELECT 1 FROM assignments a WHERE a.worker_id=w.id) assigned
        FROM workers w LEFT JOIN employee_gdlr e ON e.worker_id=w.id
        WHERE w.id IN (SELECT worker_id FROM staffing_import_members WHERE import_id=?)
           OR EXISTS(SELECT 1 FROM assignments a WHERE a.worker_id=w.id)
        ORDER BY w.id''', (batch['id'],))]
    if set(explicit) - {p['id'] for p in people}:
        raise ValueError('Явная привязка допустима только для сотрудников импорта или расстановки.')
    bindings, unresolved = [], []
    kept = 0
    for person in people:
        requested = explicit.get(person['id'])
        if person['category_id'] is not None:
            if requested is not None:
                _, key = category_name(requested)
                if key not in existing or existing[key]['id'] != person['category_id']:
                    raise ValueError('У сотрудника уже есть другая ручная привязка: ' + person['personnel_no'])
            kept += 1
            continue
        value = requested if requested is not None else person['category']
        key = category_name(value)[1] if (value or '').strip() else None
        if key not in names:
            if requested is not None:
                raise ValueError('Явная категория отсутствует в текущем файле: ' + requested)
            if person['assigned']:
                unresolved.append({k: person[k] for k in ('id', 'full_name', 'personnel_no', 'category')})
            continue
        if key in existing and not existing[key]['active']:
            raise ValueError('Категория отключена; необходим отдельный выбор: ' + existing[key]['name'])
        bindings.append({'worker_id': person['id'], 'key': key})
    categories = [{'key': key, 'name': names[key], 'existing_id': existing[key]['id'] if key in existing else None}
                  for key in sorted(names)]
    return {'fingerprint': digest([dict(batch), sources, catalog, people, sorted(explicit.items())]),
            'import': dict(batch), 'source_workers': len(sources), 'categories': categories,
            'new_categories': sum(c['existing_id'] is None for c in categories),
            'bindings': bindings, 'kept_bindings': kept, 'unresolved_assigned': unresolved}


def protected_hashes(db):
    return {table: digest([list(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')])
            for table in ('workers', 'assignments', 'crew_members', 'daily_staffing_plans')}


def apply(db, expected, actor_id, backup_dir, explicit=None):
    db.execute('BEGIN IMMEDIATE')
    try:
        actor = db.execute("SELECT role,active FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor['role'] != 'super_admin' or not actor['active']:
            raise ValueError('Требуется действующий супер-администратор.')
        plan = preview(db, explicit)
        if plan['fingerprint'] != expected:
            raise ValueError('Данные изменились после проверки. Повторите предварительный просмотр.')
        if not plan['new_categories'] and not plan['bindings']:
            db.rollback()
            return {'already_applied': True, 'categories_created': 0, 'employees_bound': 0,
                    'unresolved_assigned': plan['unresolved_assigned']}
        before = protected_hashes(db)
        database = Path(db.execute('PRAGMA database_list').fetchone()[2])
        folder = Path(backup_dir)
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / ('before-gdlr-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(6) + '.sqlite3')
        # A separate reader copies the pre-write snapshot while this connection holds the writer lock.
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Резервная копия не прошла проверку целостности.')
        now = datetime.now(timezone.utc).isoformat()
        ids = {}
        for category in plan['categories']:
            category_id = category['existing_id']
            if category_id is None:
                category_id = db.execute('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                    VALUES (?,?,?,?,?)''', (category['name'], category['key'], secrets.token_hex(16), actor_id, now)).lastrowid
            ids[category['key']] = category_id
        for binding in plan['bindings']:
            db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,?,?,?)''', (binding['worker_id'], ids[binding['key']], secrets.token_hex(16), actor_id, now))
        if protected_hashes(db) != before:
            raise ValueError('Контроль сохранности работников или расстановки не пройден.')
        db.commit()
        return {'categories_created': plan['new_categories'], 'employees_bound': len(plan['bindings']),
                'backup': str(backup), 'protected_hashes': before, 'unresolved_assigned': plan['unresolved_assigned']}
    except BaseException:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description='Справочник ГДЛР из текущего импорта')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected')
    parser.add_argument('--actor-id', type=int)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--bind', action='append', default=[], metavar='WORKER_ID=CATEGORY')
    args = parser.parse_args()
    explicit = {}
    for value in args.bind:
        worker, category = value.split('=', 1)
        if int(worker) in explicit:
            parser.error('Повторная явная привязка сотрудника.')
        explicit[int(worker)] = category
    mode = 'rw' if args.apply else 'ro'
    with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        if args.apply:
            if not args.expected or args.actor_id is None or args.backup_dir is None:
                parser.error('--apply требует --expected, --actor-id и --backup-dir')
            result = apply(db, args.expected, args.actor_id, args.backup_dir, explicit)
        else:
            db.execute('BEGIN')
            result = preview(db, explicit)
            result['employees_to_bind'] = len(result.pop('bindings'))
            db.rollback()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
