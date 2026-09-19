-- Remove uploads from recognition without erasing confirmed movements or audit history.
ALTER TABLE workforce_ticket_jobs ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE workforce_ticket_jobs ADD COLUMN deleted_by BIGINT REFERENCES users(id);
