"""Copy a prepared SQLite snapshot into an EMPTY PostgreSQL database atomically.

Run offline with the owner DSN. Runtime credentials have no DDL/role permissions.
No source writes, destructive reset, inferred merging or schema fallback.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import psycopg
from psycopg import sql
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]


def load_environment(path):
    for line in path.read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ[key] = value


def digest(rows):
    result = hashlib.sha256()
    for row in rows:
        result.update(json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode())
        result.update(b'\n')
    return result.hexdigest()


def migrate(source, report):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    reader = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    if reader.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or reader.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Source integrity validation failed.')
    tables = sorted(row[0] for row in reader.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    result = {'source_sha256': source_hash, 'tables': {}, 'clone_changes': []}
    with psycopg.connect(os.environ['ADMIN_DATABASE_URL'], connect_timeout=8) as target:
        if target.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
            raise ValueError('Target is not empty; use a new database. Existing data were not changed.')
        for path in sorted((ROOT / 'migrations/postgres').glob('00[01]_*.sql')):
            target.execute(path.read_text(encoding='utf-8'), prepare=False)
        destination_tables = sorted(row[0] for row in target.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        if tables != destination_tables:
            raise ValueError('Prepared source tables differ from the reviewed baseline.')
        target.execute('SET CONSTRAINTS ALL DEFERRED')
        for table in tables:
            safe_table = '"' + table.replace('"', '""') + '"'
            info = reader.execute(f'PRAGMA table_info({safe_table})').fetchall()
            fields = [row[1] for row in info]
            target_fields = [row[0] for row in target.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,))]
            if fields != target_fields:
                raise ValueError(f'Column mismatch: {table}')
            key = [row[1] for row in sorted(info, key=lambda item: item[5]) if row[5]]
            order = ','.join('"' + field.replace('"', '""') + '"' for field in (key or fields))
            rows = reader.execute(f'SELECT * FROM {safe_table} ORDER BY {order}').fetchall()
            statement = sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, fields)))
            with target.cursor().copy(statement) as copy:
                for row in rows:
                    copy.write_row(row)
            copied = target.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(
                sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, key or fields)))).fetchall()
            before, after = digest(rows), digest(copied)
            if len(rows) != len(copied) or before != after:
                raise ValueError(f'Row/count digest mismatch: {table}')
            result['tables'][table] = {'rows': len(rows), 'sha256': before, 'verified': True}
            identity = target.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND is_identity='YES'", (table,)).fetchone()
            if identity:
                field = identity[0]
                maximum = target.execute(sql.SQL('SELECT MAX({}) FROM {}').format(sql.Identifier(field), sql.Identifier(table))).fetchone()[0]
                target.execute('SELECT setval(pg_get_serial_sequence(%s,%s),%s,%s)', (table, field, maximum or 1, maximum is not None))
        target.execute('SET CONSTRAINTS ALL IMMEDIATE')
        target.execute('''CREATE TABLE workforce_schema_versions (
            version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_sha256 TEXT NOT NULL, verification_json JSONB NOT NULL)''')
        target.execute('INSERT INTO workforce_schema_versions(version,source_sha256,verification_json) VALUES (1,%s,%s::jsonb)',
                       (source_hash, json.dumps(result)))
        if os.environ.get('APP_ENVIRONMENT') == 'staging':
            now = int(time.time())
            target.execute('UPDATE user_sessions SET ended_at=%s WHERE ended_at IS NULL', (now,))
            target.execute('DELETE FROM telegram_link_codes')
            target.execute('''INSERT INTO users(username,password_hash,full_name,role,created_at)
                VALUES (%s,%s,'Администратор тестового стенда','super_admin',%s)''',
                (os.environ['ADMIN_USERNAME'], generate_password_hash(os.environ['ADMIN_PASSWORD']), time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))))
            result['clone_changes'] = ['Ended copied sessions', 'Removed pending Telegram login codes', 'Created independent staging administrator']
        role = 'workforce_app'
        if target.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
            raise ValueError('Runtime role already exists; review privileges instead of replacing it.')
        target.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(
            sql.Identifier(role), sql.Literal(os.environ['PG_APP_PASSWORD'])))
        target.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        target.execute('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC')
        target.execute('GRANT USAGE ON SCHEMA public TO workforce_app')
        target.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO workforce_app')
        target.execute('REVOKE INSERT,UPDATE,DELETE ON workforce_schema_versions FROM workforce_app')
        target.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO workforce_app')
        target.execute('ANALYZE')
    reader.close()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'verified_tables': len(tables), 'verified_rows': sum(item['rows'] for item in result['tables'].values()), 'report': str(report)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--env', type=Path, default=ROOT / '.env.staging')
    parser.add_argument('--report', type=Path, default=ROOT / 'output/postgres-migration-verification.json')
    args = parser.parse_args()
    load_environment(args.env)
    migrate(args.source, args.report)
