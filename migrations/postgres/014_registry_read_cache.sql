-- Registry pages depend on personnel facts, not on daily staffing writes.
CREATE TABLE workforce_registry_revision (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 1
);
INSERT INTO workforce_registry_revision DEFAULT VALUES;
CREATE FUNCTION workforce_registry_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
    UPDATE public.workforce_registry_revision SET revision=revision+1 WHERE singleton;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION workforce_registry_changed() FROM PUBLIC;
DO $$
DECLARE source_table text;
BEGIN
    FOREACH source_table IN ARRAY ARRAY[
        'workers','workforce_profiles','employee_gdlr','gdlr_categories',
        'employee_smu','workforce_smu_projects','workforce_organizations',
        'workforce_catalog','workforce_stage_events','workforce_conflicts','workforce_rotations',
        'crews','crew_members'
    ] LOOP
        EXECUTE format('CREATE TRIGGER registry_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION workforce_registry_changed()',source_table);
    END LOOP;
END;
$$;
GRANT SELECT ON workforce_registry_revision TO workforce_app;
CREATE INDEX idx_workers_department_name ON workers(department,active,name_search,id);
CREATE INDEX idx_staffing_import_worker_latest ON staffing_import_members(worker_id,import_id DESC);
ANALYZE workers;
ANALYZE staffing_import_members;
