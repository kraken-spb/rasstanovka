CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE workforce_rotation_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
    name_key TEXT GENERATED ALWAYS AS (log_casefold(trim(name))) STORED UNIQUE,
    onsite_days INTEGER NOT NULL CHECK(onsite_days BETWEEN 1 AND 366),
    leave_days INTEGER NOT NULL CHECK(leave_days BETWEEN 0 AND 366),
    travel_days INTEGER NOT NULL CHECK(travel_days BETWEEN 1 AND 30),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE workforce_rotations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    schedule_id UUID NOT NULL REFERENCES workforce_rotation_schedules(id),
    schedule_snapshot JSONB NOT NULL,
    start_date DATE NOT NULL,
    planned_end_date DATE NOT NULL,
    actual_end_date DATE,
    leave_end_date DATE NOT NULL,
    next_arrival_date DATE NOT NULL,
    cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=10000),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    request_key UUID NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(planned_end_date>=start_date),
    CHECK(actual_end_date IS NULL OR actual_end_date>=start_date),
    CHECK(leave_end_date>=planned_end_date AND next_arrival_date>leave_end_date),
    EXCLUDE USING gist(worker_id WITH =,daterange(start_date,planned_end_date,'[]') WITH &&)
        WHERE (NOT cancelled)
);
CREATE INDEX idx_workforce_rotation_worker ON workforce_rotations(worker_id,start_date DESC);
GRANT SELECT,INSERT,UPDATE ON workforce_rotation_schedules,workforce_rotations TO workforce_app;
