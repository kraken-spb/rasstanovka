-- One worker identity, independent employment status, dated presence and plans.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK(role IN ('super_admin','admin','foreman','viewer','hr_viewer','rotation','recruitment'));

ALTER TABLE workers ADD COLUMN uuid UUID NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX uq_worker_uuid ON workers(uuid);
ALTER TABLE workers ADD COLUMN personnel_is_internal BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workers ADD COLUMN name_search TEXT GENERATED ALWAYS AS (log_casefold(full_name)) STORED;
CREATE INDEX idx_worker_name_search ON workers USING gin(name_search gin_trgm_ops);

CREATE TABLE workforce_catalog (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('citizenship','employment','stage','basis','result','document','docstate','check','checkstate','project')),
    label TEXT NOT NULL CHECK(length(trim(label)) BETWEEN 1 AND 200),
    label_key TEXT GENERATED ALWAYS AS (log_casefold(trim(label))) STORED,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    system_value BOOLEAN NOT NULL DEFAULT FALSE,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(split_part(code,'.',1)=kind),
    UNIQUE(kind,label_key)
);

INSERT INTO workforce_catalog(code,kind,label,sort_order,system_value) VALUES
    ('employment.staff','employment','Штат',10,TRUE),
    ('employment.irs','employment','Трудоустройство ИРС',20,TRUE),
    ('employment.recruitment','employment','Трудоустройство',30,TRUE),
    ('employment.external','employment','Аутстафф внешний',40,TRUE),
    ('employment.internal','employment','Аутстафф внутренний',50,TRUE),
    ('stage.leave','stage','Неявка',10,TRUE),
    ('stage.inbound','stage','Заезд',20,TRUE),
    ('stage.pvp','stage','ПВП',30,TRUE),
    ('stage.onsite','stage','Явка',40,TRUE),
    ('basis.ticket','basis','на билете',10,TRUE),
    ('basis.request','basis','заявка',20,TRUE),
    ('basis.schedule','basis','по графику',30,TRUE),
    ('basis.no_request','basis','без заявки',40,TRUE),
    ('result.happened','result','состоялся',10,TRUE),
    ('result.postponed','result','перенос',20,TRUE),
    ('result.cancelled','result','отмена',30,TRUE),
    ('document.patent','document','Патент',10,TRUE),
    ('document.migration','document','Миграционный учёт',20,TRUE),
    ('document.passport','document','Паспорт',30,TRUE),
    ('document.medical','document','Медицинские документы',40,TRUE),
    ('document.safety','document','Охрана труда',50,TRUE),
    ('document.attestation','document','Аттестация',60,TRUE),
    ('docstate.pending','docstate','Оформляется',10,TRUE),
    ('docstate.ready','docstate','Готов',20,TRUE),
    ('docstate.renewal','docstate','Продление',30,TRUE),
    ('docstate.cancelled','docstate','Аннулирован',40,TRUE),
    ('check.hr','check','Оформление в отделе кадров',10,TRUE),
    ('check.medical','check','Медицинская комиссия',20,TRUE),
    ('check.safety','check','Охрана труда',30,TRUE),
    ('check.attestation','check','Аттестация',40,TRUE),
    ('check.hangar','check','Ангар',50,TRUE),
    ('checkstate.pending','checkstate','Не начато',10,TRUE),
    ('checkstate.in_progress','checkstate','В работе',20,TRUE),
    ('checkstate.ready','checkstate','Готово',30,TRUE),
    ('checkstate.blocked','checkstate','Нужно уточнение',40,TRUE),
    ('project.ukpg45','project','УКПГ-45',10,TRUE),
    ('project.stage15','project','Этап 15',20,TRUE),
    ('project.ukpg45line','project','УКПГ-45 ЛЧ',30,TRUE),
    ('project.stage5','project','Этап 5',40,TRUE);

CREATE TABLE workforce_organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 300),
    name_key TEXT GENERATED ALWAYS AS (log_casefold(trim(name))) STORED UNIQUE,
    contractor_id BIGINT UNIQUE REFERENCES contractors(id),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workforce_smu_projects (
    smu_id BIGINT PRIMARY KEY REFERENCES smu_catalog(id),
    project_code TEXT NOT NULL REFERENCES workforce_catalog(code) CHECK(project_code LIKE 'project.%'),
    updated_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workforce_profiles (
    worker_id BIGINT PRIMARY KEY REFERENCES workers(id),
    employer_id UUID REFERENCES workforce_organizations(id),
    citizenship_code TEXT REFERENCES workforce_catalog(code) CHECK(citizenship_code LIKE 'citizenship.%'),
    employment_code TEXT REFERENCES workforce_catalog(code) CHECK(employment_code LIKE 'employment.%'),
    birth_date DATE,
    phone TEXT NOT NULL DEFAULT '' CHECK(length(phone)<=300),
    messenger TEXT NOT NULL DEFAULT '' CHECK(length(messenger)<=300),
    origin_city TEXT NOT NULL DEFAULT '' CHECK(length(origin_city)<=300),
    rotation_schedule TEXT NOT NULL DEFAULT '' CHECK(length(rotation_schedule)<=500),
    arrival_date DATE,
    forecast_departure_date DATE,
    leave_start_date DATE,
    leave_end_date DATE,
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=30000),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    created_by BIGINT REFERENCES users(id),
    updated_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(leave_end_date IS NULL OR leave_start_date IS NULL OR leave_end_date>=leave_start_date)
);
CREATE INDEX idx_workforce_profile_employer ON workforce_profiles(employer_id);
CREATE INDEX idx_workforce_profile_employment ON workforce_profiles(employment_code);

CREATE TABLE workforce_import_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_key UUID NOT NULL UNIQUE,
    file_sha256 TEXT NOT NULL CHECK(length(file_sha256)=64),
    filename TEXT NOT NULL,
    service TEXT NOT NULL CHECK(service IN ('rotation','recruitment','reviewed')),
    source_key TEXT NOT NULL,
    report_date DATE NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('preview','applied','expired')),
    preview_json JSONB NOT NULL,
    preview_token UUID NOT NULL DEFAULT gen_random_uuid(),
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '30 minutes',
    applied_at TIMESTAMPTZ,
    UNIQUE(source_key,file_sha256)
);

CREATE TABLE workforce_source_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES workforce_import_batches(id),
    worker_id BIGINT REFERENCES workers(id),
    filename TEXT NOT NULL,
    sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK(source_row>0),
    source_role TEXT NOT NULL CHECK(source_role IN ('rotation','recruitment','outstaff')),
    raw_json JSONB NOT NULL,
    mapped_json JSONB NOT NULL,
    mapping_notes TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(batch_id,filename,sheet,source_row)
);
CREATE INDEX idx_workforce_sources_worker ON workforce_source_records(worker_id);
CREATE INDEX idx_workforce_sources_batch ON workforce_source_records(batch_id);

CREATE TABLE workforce_stage_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    stage_code TEXT NOT NULL REFERENCES workforce_catalog(code) CHECK(stage_code LIKE 'stage.%'),
    effective_date DATE NOT NULL,
    confirmed BOOLEAN NOT NULL,
    source_record_id UUID REFERENCES workforce_source_records(id),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 30000),
    replaces_id UUID UNIQUE REFERENCES workforce_stage_events(id),
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_key UUID NOT NULL UNIQUE
);
CREATE INDEX idx_workforce_stage_asof ON workforce_stage_events(worker_id,effective_date DESC,confirmed DESC,sequence DESC);

CREATE TABLE workforce_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    direction TEXT NOT NULL CHECK(direction IN ('arrival','departure')),
    planned_date DATE,
    actual_date DATE,
    basis_code TEXT REFERENCES workforce_catalog(code) CHECK(basis_code LIKE 'basis.%'),
    result_code TEXT REFERENCES workforce_catalog(code) CHECK(result_code LIKE 'result.%'),
    origin TEXT NOT NULL DEFAULT '' CHECK(length(origin)<=300),
    destination TEXT NOT NULL DEFAULT '' CHECK(length(destination)<=300),
    travel_details TEXT NOT NULL DEFAULT '' CHECK(length(travel_details)<=5000),
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=10000),
    source_record_id UUID REFERENCES workforce_source_records(id),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    request_key UUID NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(result_code IS DISTINCT FROM 'result.happened' OR actual_date IS NOT NULL)
);
CREATE INDEX idx_workforce_movements_date ON workforce_movements(planned_date,worker_id);
CREATE INDEX idx_workforce_movements_worker ON workforce_movements(worker_id,created_at DESC);

CREATE TABLE workforce_pvp_places (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 300),
    name_key TEXT GENERATED ALWAYS AS (log_casefold(trim(name))) STORED UNIQUE,
    address TEXT NOT NULL DEFAULT '' CHECK(length(address)<=500),
    capacity INTEGER CHECK(capacity>=0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workforce_pvp_stays (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    place_id UUID REFERENCES workforce_pvp_places(id),
    planned_arrival DATE,
    arrived_on DATE,
    departed_on DATE,
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=10000),
    source_record_id UUID REFERENCES workforce_source_records(id),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    request_key UUID NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(departed_on IS NULL OR arrived_on IS NOT NULL AND departed_on>=arrived_on)
);
CREATE UNIQUE INDEX uq_workforce_open_pvp ON workforce_pvp_stays(worker_id) WHERE arrived_on IS NOT NULL AND departed_on IS NULL;
CREATE INDEX idx_workforce_pvp_place ON workforce_pvp_stays(place_id,arrived_on,departed_on);

CREATE TABLE workforce_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    document_code TEXT NOT NULL REFERENCES workforce_catalog(code) CHECK(document_code LIKE 'document.%'),
    state_code TEXT REFERENCES workforce_catalog(code) CHECK(state_code LIKE 'docstate.%'),
    number TEXT NOT NULL DEFAULT '' CHECK(length(number)<=300),
    issued_on DATE,
    expires_on DATE,
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=10000),
    source_record_id UUID REFERENCES workforce_source_records(id),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    request_key UUID NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(issued_on IS NULL OR expires_on IS NULL OR expires_on>=issued_on)
);
CREATE INDEX idx_workforce_document_worker ON workforce_documents(worker_id,document_code);
CREATE INDEX idx_workforce_document_expiry ON workforce_documents(expires_on) WHERE expires_on IS NOT NULL;

CREATE TABLE workforce_checks (
    worker_id BIGINT NOT NULL REFERENCES workers(id),
    check_code TEXT NOT NULL REFERENCES workforce_catalog(code) CHECK(check_code LIKE 'check.%'),
    state_code TEXT REFERENCES workforce_catalog(code) CHECK(state_code LIKE 'checkstate.%'),
    planned_date DATE,
    completed_date DATE,
    responsible_user_id BIGINT REFERENCES users(id),
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=10000),
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_by BIGINT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(worker_id,check_code)
);

CREATE TABLE workforce_conflicts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_id BIGINT REFERENCES workers(id),
    batch_id UUID REFERENCES workforce_import_batches(id),
    kind TEXT NOT NULL CHECK(kind IN ('identity','field','stage','mapping','project')),
    field_name TEXT NOT NULL,
    description TEXT NOT NULL,
    candidates_json JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved')),
    resolution TEXT,
    resolved_by BIGINT REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    edit_token UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(state='open' OR resolution IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
);
CREATE INDEX idx_workforce_open_conflicts ON workforce_conflicts(worker_id) WHERE state='open';

CREATE TABLE workforce_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worker_id BIGINT REFERENCES workers(id),
    actor_id BIGINT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json JSONB,
    after_json JSONB,
    reason TEXT NOT NULL DEFAULT '',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_workforce_audit_worker ON workforce_audit(worker_id,id DESC);

CREATE TABLE workforce_requests (
    request_key UUID PRIMARY KEY,
    actor_id BIGINT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing employer labels become references; worker IDs and categories stay intact.
INSERT INTO workforce_organizations(name)
SELECT DISTINCT ON(log_casefold(trim(employer))) trim(employer)
FROM workers WHERE trim(employer)<>'' ORDER BY log_casefold(trim(employer)),trim(employer);
INSERT INTO workforce_organizations(name,contractor_id)
SELECT name,id FROM contractors
ON CONFLICT(name_key) DO UPDATE SET contractor_id=excluded.contractor_id;

INSERT INTO workforce_profiles(worker_id,employer_id,employment_code)
SELECT w.id,o.id,CASE WHEN w.personnel_no ~ '^[0-9]+$' THEN 'employment.staff'
    WHEN lower(om.staff_type) LIKE '%внеш%' THEN 'employment.external'
    WHEN lower(om.staff_type) LIKE '%внутр%' THEN 'employment.internal' ELSE NULL END
FROM workers w LEFT JOIN workforce_organizations o ON o.name_key=log_casefold(trim(w.employer))
LEFT JOIN outstaff_members om ON om.worker_id=w.id;

INSERT INTO workforce_smu_projects(smu_id,project_code)
SELECT id,CASE
    WHEN name ~ '(^|[^0-9])15([.]|[^0-9]|$)' THEN 'project.ukpg45'
    WHEN name ~ '(^|[^0-9])19[.]1([^0-9]|$)' THEN 'project.stage15'
    WHEN name ~ '(^|[^0-9])19[.]2([^0-9]|$)' THEN 'project.ukpg45line'
    WHEN name ~ '(^|[^0-9])19[.]3([^0-9]|$)' THEN 'project.stage5' END
FROM smu_catalog
WHERE name ~ '(^|[^0-9])(15([.]|[^0-9]|$)|19[.][123]([^0-9]|$))';

GRANT SELECT,INSERT,UPDATE,DELETE ON workforce_catalog,workforce_organizations,workforce_smu_projects,
    workforce_profiles,workforce_import_batches,workforce_source_records,workforce_movements,
    workforce_pvp_places,workforce_pvp_stays,workforce_documents,workforce_checks,workforce_conflicts,workforce_requests TO workforce_app;
GRANT SELECT,INSERT ON workforce_stage_events,workforce_audit TO workforce_app;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO workforce_app;
