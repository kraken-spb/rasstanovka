-- History comments are optional. Actor, time, state and audit values remain required.
ALTER TABLE workforce_stage_events DROP CONSTRAINT workforce_stage_events_reason_check;
ALTER TABLE workforce_stage_events ADD CONSTRAINT workforce_stage_events_reason_check
    CHECK(length(trim(reason)) <= 30000);
