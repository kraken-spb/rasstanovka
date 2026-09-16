"""Extend the catalog from reviewed names without changing employee bindings."""
import argparse
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tools.sync_gdlr_catalog import category_name, digest


def protected_state(db):
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
              if r[0] != 'gdlr_categories' and not r[0].startswith('sqlite_')]
    return {table: digest(sorted([list(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')],
                               key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True)))
            for table in tables}


def preview(db, candidates):
    names = {}
    for item in candidates:
        name, key = category_name(item['name'])
        if key in names:
            raise ValueError('Повторная категория в перечне: ' + name)
        names[key] = {'name': name, 'key': key, 'sources': item.get('sources', [])}
    catalog = [dict(r) for r in db.execute('SELECT * FROM gdlr_categories ORDER BY id')]
    if any('staffing_allowed' not in row for row in catalog):
        raise ValueError('Сначала обновите схему тестовой базы.')
    existing = {row['name_key']: row for row in catalog}
    return {'fingerprint': digest([catalog, names]),
            'added': [item for key, item in names.items() if key not in existing],
            'existing': len(set(names) & set(existing)),
            'staffing_allowed': [r['name'] for r in catalog if r['staffing_allowed']]}


def apply(db, candidates, expected, actor_id, backup_dir):
    db.execute('BEGIN IMMEDIATE')
    try:
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (actor_id,)).fetchone()
        if not actor or actor['role'] != 'super_admin' or not actor['active']:
            raise ValueError('Требуется действующий супер-администратор.')
        plan = preview(db, candidates)
        if plan['fingerprint'] != expected:
            raise ValueError('Справочник изменился после проверки. Повторите просмотр.')
        if not plan['added']:
            db.rollback()
            return {'already_applied': True, 'created': 0}
        before = protected_state(db)
        database = Path(db.execute('PRAGMA database_list').fetchone()[2])
        folder = Path(backup_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc)
        backup = folder / ('before-catalog-extension-' + stamp.strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(4) + '.db')
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Резервная копия не прошла проверку целостности.')
        for item in plan['added']:
            db.execute('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at,staffing_allowed)
                VALUES (?,?,?,?,?,0)''', (item['name'], item['key'], secrets.token_hex(16), actor_id, stamp.isoformat()))
        if protected_state(db) != before:
            raise ValueError('Изменились таблицы за пределами справочника ГДЛР.')
        db.commit()
        return {'created': len(plan['added']), 'added': plan['added'], 'backup': str(backup),
                'actor_id': actor_id, 'created_at': stamp.isoformat(), 'protected_hashes': before,
                'staffing_allowed': plan['staffing_allowed']}
    except BaseException:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description='Дополнение справочника без изменения категорий сотрудников')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--categories', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected')
    parser.add_argument('--actor-id', type=int)
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    candidates = json.loads(args.categories.read_text(encoding='utf-8'))
    with closing(sqlite3.connect(args.database.resolve().as_uri() + ('?mode=rw' if args.apply else '?mode=ro'),
                                 uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        if args.apply:
            if not args.expected or args.actor_id is None or args.backup_dir is None:
                parser.error('--apply требует --expected, --actor-id и --backup-dir')
            result = apply(db, candidates, args.expected, args.actor_id, args.backup_dir)
        else:
            result = preview(db, candidates)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
