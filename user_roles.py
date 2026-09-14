"""User role schema migration and explicit first super-administrator setup."""
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def backup_database(db, label):
    database = Path(db.execute('PRAGMA database_list').fetchone()[2])
    folder = database.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{label}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex}.db'
    # A separate reader can back up committed data while this connection holds
    # BEGIN IMMEDIATE, preventing writes between the snapshot and the change.
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    return target


def migrate_user_roles(db):
    schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if not schema or "'super_admin'" in schema[0]:
        return
    if db.in_transaction:
        raise RuntimeError('Миграция ролей должна запускаться до других изменений базы.')
    db.execute('PRAGMA foreign_keys = OFF')
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
            if "'super_admin'" in schema:
                return
            backup_database(db, 'before-super-admin')
            dependents = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='users' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            db.execute("""CREATE TABLE users_new (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'foreman', 'viewer')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )""")
            db.execute('INSERT INTO users_new SELECT id,username,password_hash,full_name,role,active,created_at FROM users')
            db.execute('DROP TABLE users')
            db.execute('ALTER TABLE users_new RENAME TO users')
            for row in dependents:
                db.execute(row[0])
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Нарушены связи базы при миграции ролей.')
    finally:
        db.execute('PRAGMA foreign_keys = ON')


def promote_super_admin(db, username):
    """Explicit operator action; never promote users automatically on restart."""
    with db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute('SELECT id,role,active FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
        if not user or not user['active'] or user['role'] not in ('admin', 'super_admin'):
            raise ValueError('Укажите логин действующего администратора.')
        if user['role'] == 'super_admin':
            return None
        backup = backup_database(db, 'before-super-admin-promotion')
        db.execute("UPDATE users SET role='super_admin' WHERE id=?", (user['id'],))
    return backup
