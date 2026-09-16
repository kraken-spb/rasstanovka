-- Each reschedule keeps the previous plan and creates exactly one successor.
ALTER TABLE workforce_movements ADD COLUMN rescheduled_from UUID UNIQUE REFERENCES workforce_movements(id);
ALTER TABLE workforce_movements ADD CONSTRAINT workforce_movement_not_self_rescheduled
    CHECK(rescheduled_from IS DISTINCT FROM id);
CREATE INDEX idx_workforce_movement_pending ON workforce_movements(planned_date,worker_id)
    WHERE result_code IS NULL;
