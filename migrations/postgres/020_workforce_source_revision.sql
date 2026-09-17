-- Section membership is a registry dependency even when employee facts do not change.
CREATE TRIGGER registry_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON workforce_source_records FOR EACH STATEMENT EXECUTE FUNCTION workforce_registry_changed();
