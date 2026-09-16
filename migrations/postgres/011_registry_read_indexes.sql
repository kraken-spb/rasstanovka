CREATE INDEX idx_workers_active_name ON workers(active,name_search,id);
CREATE INDEX idx_workforce_stage_confirmed_order ON workforce_stage_events(worker_id,effective_date DESC,sequence DESC)
    WHERE confirmed;
CREATE INDEX idx_workforce_source_live_key ON workforce_source_records(source_key,worker_id) WHERE active;
ANALYZE workers;
ANALYZE workforce_profiles;
ANALYZE workforce_stage_events;
ANALYZE workforce_source_records;
ANALYZE workforce_rotations;
