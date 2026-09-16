-- Legacy staffing readers and the normalized registry share one employer value.
CREATE FUNCTION workforce_sync_worker_profile() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE organization_uuid UUID;
BEGIN
    IF trim(NEW.employer)<>'' THEN
        INSERT INTO workforce_organizations(name) VALUES(trim(NEW.employer))
            ON CONFLICT(name_key) DO NOTHING;
        SELECT id INTO organization_uuid FROM workforce_organizations
            WHERE name_key=log_casefold(trim(NEW.employer));
    END IF;
    INSERT INTO workforce_profiles(worker_id,employer_id,employment_code)
        VALUES(NEW.id,organization_uuid,
            CASE WHEN NEW.personnel_no::text ~ '^[0-9]+$' AND NOT NEW.personnel_is_internal
                THEN 'employment.staff' ELSE NULL END)
        ON CONFLICT(worker_id) DO UPDATE SET employer_id=excluded.employer_id,
            employment_code=CASE WHEN excluded.employment_code='employment.staff'
                THEN excluded.employment_code ELSE workforce_profiles.employment_code END,
            edit_token=gen_random_uuid(),updated_at=now();
    RETURN NEW;
END $$;
CREATE TRIGGER workforce_worker_profile_insert AFTER INSERT ON workers
    FOR EACH ROW EXECUTE FUNCTION workforce_sync_worker_profile();
CREATE TRIGGER workforce_worker_profile_update AFTER UPDATE OF employer,personnel_no,personnel_is_internal ON workers
    FOR EACH ROW WHEN(OLD.employer IS DISTINCT FROM NEW.employer
        OR OLD.personnel_no IS DISTINCT FROM NEW.personnel_no
        OR OLD.personnel_is_internal IS DISTINCT FROM NEW.personnel_is_internal)
    EXECUTE FUNCTION workforce_sync_worker_profile();

CREATE FUNCTION workforce_organization_rename() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE employee_employers SET employer=NEW.name,edit_token=gen_random_uuid()::text,
        updated_by=NEW.updated_by,updated_at=NEW.updated_at::text
        WHERE worker_id IN(SELECT worker_id FROM workforce_profiles WHERE employer_id=NEW.id);
    UPDATE workers SET employer=NEW.name WHERE id IN(
        SELECT worker_id FROM workforce_profiles WHERE employer_id=NEW.id) AND employer IS DISTINCT FROM NEW.name;
    RETURN NEW;
END $$;
CREATE TRIGGER workforce_organization_rename AFTER UPDATE OF name ON workforce_organizations
    FOR EACH ROW WHEN(OLD.name IS DISTINCT FROM NEW.name)
    EXECUTE FUNCTION workforce_organization_rename();

CREATE INDEX idx_workforce_replacement ON workforce_stage_events(replaces_id,effective_date);
CREATE INDEX idx_workforce_confirmed_travel ON workforce_movements(worker_id,actual_date DESC,updated_at DESC)
    WHERE result_code='result.happened';
