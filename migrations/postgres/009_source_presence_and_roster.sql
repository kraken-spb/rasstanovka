ALTER TABLE workforce_stage_events ADD COLUMN date_basis TEXT NOT NULL DEFAULT 'event'
    CHECK(date_basis IN ('event','observed'));
ALTER TABLE workforce_profiles ADD COLUMN staffing_ready BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workforce_profiles ADD COLUMN pure_outstaff BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workforce_profiles ADD COLUMN rotation_schedule_id UUID REFERENCES workforce_rotation_schedules(id);
CREATE INDEX idx_workforce_ready ON workforce_profiles(worker_id) WHERE staffing_ready;
UPDATE workforce_profiles p SET staffing_ready=TRUE
    WHERE EXISTS(SELECT 1 FROM staffing_import_members s WHERE s.worker_id=p.worker_id)
       OR EXISTS(SELECT 1 FROM outstaff_members o WHERE o.worker_id=p.worker_id)
       OR EXISTS(SELECT 1 FROM manual_employees m WHERE m.worker_id=p.worker_id);
UPDATE workforce_profiles p SET pure_outstaff=TRUE
    WHERE EXISTS(SELECT 1 FROM outstaff_members o WHERE o.worker_id=p.worker_id)
      AND NOT EXISTS(SELECT 1 FROM staffing_import_members s WHERE s.worker_id=p.worker_id);
UPDATE workers w SET personnel_is_internal=TRUE
    WHERE personnel_no::text !~ '^[0-9]+$' AND EXISTS(SELECT 1 FROM outstaff_members o WHERE o.worker_id=w.id);

ALTER TABLE workforce_rotation_schedules ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workforce_rotation_schedules ALTER COLUMN onsite_days DROP NOT NULL;
ALTER TABLE workforce_rotation_schedules ALTER COLUMN leave_days DROP NOT NULL;
ALTER TABLE workforce_rotation_schedules ALTER COLUMN travel_days DROP NOT NULL;
ALTER TABLE workforce_rotation_schedules ADD CONSTRAINT workforce_schedule_calculable
    CHECK(needs_review OR onsite_days IS NOT NULL AND leave_days IS NOT NULL AND travel_days IS NOT NULL);
