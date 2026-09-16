"""Upgrade an isolated SQLite copy using the checkpoint's existing migrations."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.source.resolve() == args.destination.resolve():
        raise SystemExit('Destination must be a new, separate file.')
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.source.resolve().as_uri() + '?mode=ro', uri=True) as source:
        admin = source.execute("SELECT username FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone()
        if not admin:
            raise SystemExit('Source has no super administrator; review the source before migrating.')
        with sqlite3.connect(args.destination) as target:
            source.backup(target)
    os.environ.update(DATABASE_BACKEND='sqlite', DATABASE_PATH=str(args.destination.resolve()),
                      ADMIN_USERNAME=admin[0], ADMIN_PASSWORD=secrets.token_urlsafe(32),
                      SECRET_KEY=secrets.token_urlsafe(48), OUTBOUND_INTEGRATIONS_ENABLED='false')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import app  # Runs the same incremental migrations as the checkpoint.
    with app.app.app_context():
        db = app.get_db()
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        errors = db.execute('PRAGMA foreign_key_check').fetchall()
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        print(json.dumps({'integrity': integrity, 'foreign_key_errors': len(errors),
                          'tables': len(tables), 'workers': db.execute('SELECT COUNT(*) FROM workers').fetchone()[0]}))
        if integrity != 'ok' or errors:
            raise SystemExit('Prepared copy failed integrity validation.')


if __name__ == '__main__':
    main()
