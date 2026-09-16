"""Shared login throttling and browser security policy."""
import hashlib
import hmac


def login_bucket(secret, address, username=None):
    source = ('address:' + address) if username is None else ('account:' + address + ':' + username.casefold())
    return hmac.new(secret.encode(), source.encode(), hashlib.sha256).hexdigest()


def reserve_login(db, secret, address, username):
    """Atomic counters shared by every app process; no passwords or names stored."""
    account = login_bucket(secret, address, username)
    for key, limit in ((login_bucket(secret, address), 500), (account, 5)):
        row = db.native('''INSERT INTO security_login_windows(bucket_hash,window_started,attempts)
            VALUES (%s,now(),1) ON CONFLICT(bucket_hash) DO UPDATE SET
                attempts=CASE WHEN security_login_windows.window_started < now()-interval '10 minutes'
                    THEN 1 ELSE security_login_windows.attempts+1 END,
                window_started=CASE WHEN security_login_windows.window_started < now()-interval '10 minutes'
                    THEN now() ELSE security_login_windows.window_started END
            RETURNING attempts''', (key,)).fetchone()
        if row[0] > limit:
            return False
    return True


def clear_login(db, secret, address, username):
    db.native('DELETE FROM security_login_windows WHERE bucket_hash=%s', (login_bucket(secret, address, username),))


def browser_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'")
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    return response
