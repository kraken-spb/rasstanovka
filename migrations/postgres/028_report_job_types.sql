-- Deferred registry Excel and placement PDF artifacts retain their exact request snapshot.
ALTER TABLE workforce_export_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'urp';
ALTER TABLE workforce_export_jobs ADD CONSTRAINT workforce_export_jobs_job_type CHECK (job_type IN ('urp','registry','placement_pdf'));
ALTER TABLE workforce_export_jobs ADD COLUMN request_json JSONB NOT NULL DEFAULT '{}'::jsonb;
