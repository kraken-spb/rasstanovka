"""User role schema migration and explicit first super-administrator setup."""
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


HR_VIEWER = 'hr_viewer'
HR_ROLE_NAME = 'Управление по работе с персоналом'
USER_ROLES = {'super_admin', 'admin', 'foreman', 'viewer', HR_VIEWER, 'rotation', 'recruitment'}
WORKFORCE_SERVICE_ROLES = {'rotation', 'recruitment'}
WORKFORCE_LEGACY_READ_ENDPOINTS = {
    'index', 'reference', 'dashboard', 'activity_dates', 'assignments',
    'employees', 'crew_transfer_options', 'list_crew_catalog', 'crew_catalog_members',
    'list_crews', 'crew_departments', 'crew_board', 'crew_candidates',
    'contractors', 'categories', 'location_catalogs', 'list_smu',
    'personnel_dashboard', 'placement_report', 'get_verification', 'position_cards_pdf',
    'staffing_people', 'staffing_table', 'staffing_export_options', 'staffing_export',
    'staffing_history_state', 'calendar', 'get_preferences', 'get_profile',
}

# Reviewed business-data reads. New endpoints must explicitly opt in here.
HR_READ_ENDPOINTS = {
    'workforce_reference', 'workforce_people', 'workforce_person', 'workforce_export',
    'workforce_export_job_status', 'workforce_export_job_download',
    'index', 'reference', 'dashboard', 'activity_dates', 'assignments', 'retired_plans',
    'users', 'employees', 'crew_transfer_options', 'list_crew_catalog', 'crew_catalog_members',
    'list_crews', 'crew_departments', 'crew_board', 'crew_candidates',
    'contractors', 'categories', 'location_catalogs', 'list_smu', 'assignment_logs',
    'personnel_dashboard', 'placement_report', 'get_verification', 'position_cards_pdf',
    'staffing_people', 'staffing_table', 'staffing_export_options', 'staffing_export',
    'staffing_history_state', 'calendar', 'telegram_status', 'activity',
    'get_preferences', 'get_profile', 'list_access',
}
HR_PERSONAL_OPERATIONS = {
    ('save_preferences', 'PATCH'), ('heartbeat', 'POST'), ('logout', 'POST'),
    # Creating a private export artifact does not edit personnel business data.
    ('workforce_export_job_create', 'POST'),
}


def hr_request_allowed(endpoint, method):
    return ((method in {'GET', 'HEAD', 'OPTIONS'} and endpoint in HR_READ_ENDPOINTS)
            or (endpoint, method) in HR_PERSONAL_OPERATIONS)


def backup_database(db, label):
    if getattr(db, 'dialect', None) == 'postgres':
        from backup_api import create_backup
        return create_backup(db, reason=label)
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
    if not schema or all("'" + role + "'" in schema[0] for role in USER_ROLES):
        return
    if db.in_transaction:
        raise RuntimeError('Миграция ролей должна запускаться до других изменений базы.')
    db.execute('PRAGMA foreign_keys = OFF')
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
            if all("'" + role + "'" in schema for role in USER_ROLES):
                return
            backup_database(db, 'before-hr-viewer-role')
            dependents = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='users' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            db.execute("""CREATE TABLE users_new (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'foreman', 'viewer', 'hr_viewer', 'rotation', 'recruitment')),
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
