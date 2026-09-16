"""Restore a custom archive into a NEW isolated database and verify every table.

Never restores over an existing database. The verification database is retained
for inspection; this command never drops a database or volume.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from migrate_sqlite_to_postgres import ROOT, load_environment


def table_digest(connection, table):
    rows = connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier(table))).fetchall()
    normalized = sorted(json.dumps(json.loads(row[0]), sort_keys=True, ensure_ascii=False, separators=(',', ':')) for row in rows)
    return len(rows), hashlib.sha256('\n'.join(normalized).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', help='Absolute archive path inside the staging app container')
    args = parser.parse_args()
    if not args.archive.startswith('/app/data/backups/') or not args.archive.endswith('.dump') or '..' in args.archive:
        raise ValueError('Select a staging backup archive.')
    load_environment(ROOT / '.env.staging')
    settings = conninfo_to_dict(os.environ['ADMIN_DATABASE_URL'])
    target = 'workforce_restore_' + uuid4().hex[:12]
    with psycopg.connect(os.environ['ADMIN_DATABASE_URL'], autocommit=True, connect_timeout=8) as owner:
        owner.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
    environment = os.environ.copy()
    environment['PGPASSWORD'] = settings['password']
    command = ['docker', 'compose', '--env-file', '.env.staging', '-f', 'compose.staging.yaml',
               'exec', '-T', '-e', 'PGPASSWORD', 'app', 'pg_restore', '--exit-on-error', '--no-owner', '--no-acl',
               '--host=postgres', '--username=' + settings['user'], '--dbname=' + target, args.archive]
    restored = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=120)
    if restored.returncode:
        raise RuntimeError('Restore validation failed; the isolated database is retained for inspection: ' + target)
    report = {'database': target, 'archive': Path(args.archive).name, 'tables': {}, 'source_unchanged': True}
    with psycopg.connect(os.environ['ADMIN_DATABASE_URL']) as source, psycopg.connect(make_conninfo(os.environ['ADMIN_DATABASE_URL'], dbname=target)) as copy:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        copy.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        tables = [row[0] for row in source.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
        for table in tables:
            before, after = table_digest(source, table), table_digest(copy, table)
            report['tables'][table] = {'rows': after[0], 'sha256': after[1], 'matches_live': before == after}
        constraints = copy.execute("SELECT COUNT(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f' AND NOT convalidated").fetchone()[0]
        report['unvalidated_foreign_keys'] = constraints
    output = ROOT / 'output/postgres-restore-verification.json'
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    if constraints or not all(item['matches_live'] for item in report['tables'].values()):
        raise RuntimeError('Restored contents differ from the current staging DB. Check snapshot timing and the report.')
    print(json.dumps({'database': target, 'verified_tables': len(tables), 'foreign_keys_valid': True, 'report': str(output)}))


if __name__ == '__main__':
    main()
