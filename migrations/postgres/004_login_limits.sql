CREATE TABLE security_login_windows (
    bucket_hash TEXT PRIMARY KEY,
    window_started TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL CHECK(attempts > 0)
);
CREATE INDEX idx_login_windows_expiry ON security_login_windows(window_started);
GRANT SELECT, INSERT, UPDATE, DELETE ON security_login_windows TO workforce_app;
