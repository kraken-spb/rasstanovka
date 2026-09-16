"""Apply versioned, immutable PostgreSQL migrations using the owner account."""
import argparse
import hashlib
import os
from pathlib import Path

import psycopg

from migrate_sqlite_to_postgres import ROOT, load_environment


def apply():
    with psycopg.connect(os.environ['ADMIN_DATABASE_URL'], connect_timeout=8) as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext('workforce-schema-migration'))")
        current = db.execute('SELECT MAX(version) FROM workforce_schema_versions').fetchone()[0]
        for path in sorted((ROOT / 'migrations/postgres').glob('*.sql')):
            version = int(path.name.split('_', 1)[0])
            if version <= 1:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            existing = db.execute('SELECT source_sha256 FROM workforce_schema_versions WHERE version=%s', (version,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError(f'Applied migration was modified: {path.name}')
                continue
            if version != current + 1:
                raise ValueError('Migration sequence has a gap.')
            db.execute(path.read_text(encoding='utf-8'), prepare=False)
            db.execute("INSERT INTO workforce_schema_versions(version,source_sha256,verification_json) VALUES (%s,%s,'{}')", (version, digest))
            current = version
        print(f'PostgreSQL schema version: {current}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', type=Path, default=ROOT / '.env.staging')
    args = parser.parse_args()
    load_environment(args.env)
    apply()
