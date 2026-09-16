ALTER TABLE workforce_profiles ADD COLUMN workforce_managed BOOLEAN NOT NULL DEFAULT FALSE;
-- Existing legacy membership remains authoritative until an explicit URP import.
UPDATE workforce_profiles SET staffing_ready=FALSE
    WHERE NOT EXISTS(SELECT 1 FROM workforce_source_records sr WHERE sr.worker_id=workforce_profiles.worker_id);
ALTER TABLE workforce_source_records ADD COLUMN source_key TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_workforce_sources_current ON workforce_source_records(source_key,worker_id) WHERE active;
ALTER TABLE workforce_movements ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'site'
    CHECK(destination_kind IN ('site','pvp','home','other'));
ALTER TABLE workforce_pvp_stays ADD COLUMN observed_on DATE;
