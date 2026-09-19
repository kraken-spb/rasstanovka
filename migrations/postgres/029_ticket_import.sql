-- Private recognition jobs and confirmed tickets extend existing movements.
CREATE TABLE workforce_ticket_jobs (
    id UUID PRIMARY KEY,
    actor_id BIGINT NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_extension TEXT NOT NULL CHECK(file_extension IN ('.pdf','.jpg','.png')),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','ready','failed','applied')),
    travel_year INTEGER CHECK(travel_year BETWEEN 2000 AND 2100),
    preview_json JSONB,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX workforce_ticket_jobs_queue ON workforce_ticket_jobs(state,created_at);
CREATE INDEX workforce_ticket_jobs_actor ON workforce_ticket_jobs(actor_id,created_at);

CREATE TABLE workforce_project_travelpoints (
    project_code TEXT NOT NULL REFERENCES workforce_catalog(code),
    point_code TEXT NOT NULL REFERENCES workforce_catalog(code),
    aliases TEXT[] NOT NULL DEFAULT '{}',
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(project_code,point_code)
);

CREATE TABLE workforce_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL UNIQUE REFERENCES workforce_ticket_jobs(id),
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    movement_id UUID NOT NULL UNIQUE REFERENCES workforce_movements(id),
    passenger TEXT NOT NULL,
    ticket_number TEXT NOT NULL,
    transport TEXT NOT NULL CHECK(transport IN ('air','rail')),
    identity_key TEXT NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workforce_tickets_worker ON workforce_tickets(worker_id);

CREATE TABLE workforce_ticket_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL REFERENCES workforce_tickets(id),
    position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 12),
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_date DATE NOT NULL,
    arrival_date DATE NOT NULL,
    departure_time TIME,
    arrival_time TIME,
    flight TEXT NOT NULL DEFAULT '',
    coach TEXT NOT NULL DEFAULT '',
    seat TEXT NOT NULL DEFAULT '',
    timezone_note TEXT NOT NULL DEFAULT '',
    UNIQUE(ticket_id,position)
);
