-- Operational ownership and explicit daily demand; no inferred staffing targets.
CREATE TABLE workforce_attention_owners (
    issue_key TEXT PRIMARY KEY,
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    owner_id BIGINT NOT NULL REFERENCES users(id),
    due_date DATE NOT NULL,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE workforce_demand (
    day DATE NOT NULL,
    department TEXT NOT NULL,
    profession TEXT NOT NULL,
    required INTEGER NOT NULL CHECK(required BETWEEN 0 AND 100000),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(day,department,profession)
);
GRANT SELECT,INSERT,UPDATE ON workforce_attention_owners,workforce_demand TO workforce_app;

GRANT SELECT,INSERT ON report_closure_checks,report_closure_versions TO workforce_app;
GRANT USAGE,SELECT ON SEQUENCE report_closure_checks_id_seq,report_closure_versions_id_seq TO workforce_app;
