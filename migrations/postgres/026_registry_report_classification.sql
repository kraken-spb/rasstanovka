-- Preserve registry participation when Excel provenance is deliberately removed.
ALTER TABLE workforce_profiles ADD COLUMN has_main_registry_record BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE workforce_profiles p SET has_main_registry_record=TRUE
    WHERE EXISTS(SELECT 1 FROM workforce_source_records sr
                 WHERE sr.worker_id=p.worker_id AND sr.source_role<>'outstaff');
