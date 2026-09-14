"""Small read-only probes; importing this module never opens a database."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3


HEALTH_TIMEOUT_SECONDS = 0.25


def database_path():
    return Path(os.getenv('DATABASE_PATH', Path(__file__).resolve().parent / 'data' / 'placement.db'))


@contextmanager
def open_readonly(path):
    # URI quoting also protects paths containing spaces, Cyrillic, '?' or '#'.
    # Do not use immutable=1: polling state can still be in the active WAL.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=HEALTH_TIMEOUT_SECONDS)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only = ON')
        # SELECT 1 alone does not read SQLite pages or detect a corrupt header.
        db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
        yield db
    finally:
        db.close()


def database_ready(path):
    try:
        with open_readonly(path) as db:
            # Bounded reads of the core schema, including valid empty tables.
            # This is a readiness probe, not a full integrity/migration audit.
            for query in ('SELECT id FROM users LIMIT 1',
                          'SELECT id FROM workers LIMIT 1',
                          'SELECT id FROM assignments LIMIT 1',
                          'SELECT version FROM schema_versions LIMIT 1'):
                db.execute(query).fetchone()
        return True
    except (sqlite3.Error, OSError, ValueError):
        return False
