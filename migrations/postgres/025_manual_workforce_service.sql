-- Service membership is independent of Excel provenance and may include both services.
CREATE TABLE workforce_registry_memberships (
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    service TEXT NOT NULL CHECK(service IN ('rotation','recruitment')),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(worker_id,service)
);
CREATE INDEX idx_workforce_membership_service ON workforce_registry_memberships(service,worker_id);
INSERT INTO workforce_registry_memberships(worker_id,service,created_by)
    SELECT me.worker_id,CASE WHEN p.employment_code='employment.staff' THEN 'rotation' ELSE 'recruitment' END,me.created_by
    FROM manual_employees me JOIN workforce_profiles p ON p.worker_id=me.worker_id;
CREATE TRIGGER workforce_membership_registry_changed
    AFTER INSERT OR UPDATE OR DELETE ON workforce_registry_memberships
    FOR EACH STATEMENT EXECUTE FUNCTION workforce_registry_changed();
GRANT SELECT,INSERT,UPDATE,DELETE ON workforce_registry_memberships TO workforce_app;
