-- Employee contact, separate from the login account's email.
ALTER TABLE workforce_profiles ADD COLUMN email TEXT NOT NULL DEFAULT ''
    CHECK (length(email) <= 254);
