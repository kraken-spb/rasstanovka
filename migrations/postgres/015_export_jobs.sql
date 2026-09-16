CREATE TABLE workforce_export_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id bigint NOT NULL REFERENCES users(id),
    report_date date NOT NULL,
    scope_digest text NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','ready','failed')),
    first_count integer,
    second_count integer,
    error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    expires_at timestamptz NOT NULL DEFAULT now()+interval '24 hours'
);
CREATE INDEX idx_workforce_export_pending ON workforce_export_jobs(created_at) WHERE state='pending';
CREATE INDEX idx_workforce_export_actor ON workforce_export_jobs(actor_id,created_at DESC);
GRANT SELECT,INSERT,UPDATE,DELETE ON workforce_export_jobs TO workforce_app;
