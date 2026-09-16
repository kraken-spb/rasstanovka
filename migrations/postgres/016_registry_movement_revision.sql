-- Pending trips and schedule labels are also dependencies of registry/kanban pages.
CREATE TRIGGER registry_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON workforce_movements FOR EACH STATEMENT EXECUTE FUNCTION workforce_registry_changed();
CREATE TRIGGER registry_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON workforce_rotation_schedules FOR EACH STATEMENT EXECUTE FUNCTION workforce_registry_changed();
