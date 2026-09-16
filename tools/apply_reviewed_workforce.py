"""Load the explicitly reviewed initial dataset into staging only, atomically."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment
from postgres_db import PostgresConnection
from workforce_seed import apply_reviewed_bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--env', type=Path, default=ROOT / '.env.staging')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    load_environment(args.env)
    if os.environ.get('APP_ENVIRONMENT') != 'staging':
        raise SystemExit('This command is restricted to the isolated staging environment.')
    if args.apply:
        if not args.backup or not args.backup.is_file():
            raise SystemExit('Supply a verified PostgreSQL archive created before this import.')
        metadata = json.loads(args.backup.with_suffix('.dump.json').read_text(encoding='utf-8'))
        if (hashlib.sha256(args.backup.read_bytes()).hexdigest() != metadata['sha256']
                or metadata.get('reason') != 'before-reviewed-workforce-import'):
            raise SystemExit('Backup integrity or purpose does not match.')
    bundle = json.loads(args.bundle.read_text(encoding='utf-8'))
    db = PostgresConnection()
    try:
        db.native('BEGIN ISOLATION LEVEL SERIALIZABLE')
        db.native("SET LOCAL statement_timeout='60s'")
        actor = db.native('SELECT id FROM users WHERE username=%s', (os.environ['ADMIN_USERNAME'],)).fetchone()
        result = apply_reviewed_bundle(db, bundle, actor['id'])
        if args.apply:
            db.commit()
        else:
            db.rollback()
        result['committed'] = args.apply
        (ROOT / 'output/reviewed-workforce-import-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        db.close()


if __name__ == '__main__':
    main()
