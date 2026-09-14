"""Successful sign-ins and presence of authenticated browser sessions."""
import hashlib
import secrets
import time
from datetime import datetime, time as day_time, timedelta, timezone

from flask import abort, g, jsonify, request, session


MOSCOW = timezone(timedelta(hours=3))
ONLINE_SECONDS = 180
SESSION_SECONDS = 12 * 60 * 60


def now_seconds():
    return int(time.time())


def migrate_user_activity(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS user_activity_meta (
            id INTEGER PRIMARY KEY CHECK(id=1), started_at INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO user_activity_meta VALUES (1, CAST(strftime('%s','now') AS INTEGER));
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            logged_in_at INTEGER,
            last_seen_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            ended_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_user_sessions_login ON user_sessions(logged_in_at, user_id);
        CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id, logged_in_at);
        CREATE INDEX IF NOT EXISTS idx_user_sessions_presence ON user_sessions(last_seen_at) WHERE ended_at IS NULL;
    """)


def session_key():
    token = session.get('activity_token')
    return hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) and token else None


def session_valid(db, user_id):
    key = session_key()
    if key is None:
        return True  # Existing signed sessions acquire presence on their first heartbeat.
    return db.execute('''SELECT 1 FROM user_sessions
        WHERE session_key=? AND user_id=? AND ended_at IS NULL AND expires_at>?''',
        (key, user_id, now_seconds())).fetchone() is not None


def end_session(db):
    key = session_key()
    if key:
        with db:
            db.execute('UPDATE user_sessions SET ended_at=? WHERE session_key=? AND ended_at IS NULL',
                       (now_seconds(), key))


def start_session(db, user_id, *, successful_login):
    now = now_seconds()
    session['activity_token'] = secrets.token_urlsafe(32)
    with db:
        db.execute('''INSERT INTO user_sessions(session_key,user_id,logged_in_at,last_seen_at,expires_at)
            VALUES (?,?,?,?,?)''', (session_key(), user_id, now if successful_login else None, now, now + SESSION_SECONDS))


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None


def register_user_activity(app, get_db, login_required, roles_required):
    @app.post('/api/session/heartbeat')
    @login_required
    def heartbeat():
        db = get_db()
        if session_key() is None:
            start_session(db, g.user['id'], successful_login=False)
        else:
            now = now_seconds()
            with db:
                changed = db.execute('''UPDATE user_sessions SET last_seen_at=?,expires_at=?
                    WHERE session_key=? AND user_id=? AND ended_at IS NULL AND expires_at>?''',
                    (now, now + SESSION_SECONDS, session_key(), g.user['id'], now)).rowcount
            if not changed:
                session.clear()
                abort(401, description='Сессия завершена. Войдите снова.')
        return jsonify({'online': True})

    @app.get('/api/user-activity')
    @roles_required('admin')
    def activity():
        value = request.args.get('date', datetime.now(MOSCOW).date().isoformat())
        try:
            selected = datetime.strptime(value, '%Y-%m-%d').date()
            if selected.isoformat() != value:
                raise ValueError()
            start = int(datetime.combine(selected, day_time.min, MOSCOW).timestamp())
            end = int(datetime.combine(selected + timedelta(days=1), day_time.min, MOSCOW).timestamp())
        except (ValueError, OverflowError):
            abort(400, description='Укажите дату в формате ГГГГ-ММ-ДД.')
        now = now_seconds()
        db = get_db()
        db.execute('BEGIN')
        started = db.execute('SELECT started_at FROM user_activity_meta WHERE id=1').fetchone()[0]
        coverage = 'unavailable' if end <= started else 'partial' if start < started else 'available'
        counts = dict(db.execute('''SELECT user_id,COUNT(*) FROM user_sessions
            WHERE logged_in_at>=? AND logged_in_at<? GROUP BY user_id''', (start, end)).fetchall())
        latest = dict(db.execute('SELECT user_id,MAX(logged_in_at) FROM user_sessions GROUP BY user_id').fetchall())
        online = {row[0] for row in db.execute('''SELECT DISTINCT user_id FROM user_sessions
            WHERE ended_at IS NULL AND expires_at>? AND last_seen_at>?''', (now, now - ONLINE_SECONDS))}
        rows = []
        for user in db.execute('SELECT id,username,full_name,role,active FROM users ORDER BY full_name,username'):
            rows.append({**dict(user), 'login_count': counts.get(user['id'], 0) if coverage != 'unavailable' else None,
                         'online': bool(user['active'] and user['id'] in online),
                         'last_login_at': iso_timestamp(latest.get(user['id']))})
        return jsonify({'date': value, 'timezone': 'Europe/Moscow', 'rows': rows,
                        'login_count': sum(counts.values()) if coverage != 'unavailable' else None,
                        'online_count': sum(row['online'] for row in rows),
                        'users_logged_in': len(counts) if coverage != 'unavailable' else None,
                        'coverage': coverage, 'tracking_started_at': iso_timestamp(started),
                        'as_of': iso_timestamp(now), 'online_window_seconds': ONLINE_SECONDS})
