-- History snapshots and per-employee saves must not scan a whole reporting day.
CREATE INDEX idx_assignments_worker_day ON assignments(worker_id,work_date);
CREATE INDEX idx_assignment_events_worker_day ON assignment_events(worker_id,work_date,id DESC);
-- Inserts of completed actions should not touch the predicate used to discard
-- this user's undone actions under SERIALIZABLE isolation.
CREATE INDEX idx_staffing_history_undone_user ON staffing_action_history(user_id) WHERE state='undone';
ANALYZE assignments;
ANALYZE assignment_events;
ANALYZE staffing_action_history;
