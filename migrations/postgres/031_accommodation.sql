-- A catalog binding, separate from factual PVP stays and employee lifecycle.
ALTER TABLE workforce_catalog DROP CONSTRAINT workforce_catalog_kind_check;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_kind_check CHECK(kind IN
    ('citizenship','employment','stage','basis','result','document','docstate','check','checkstate','project',
     'profession','specialty','travelpoint','direction','destination','accommodation'));
INSERT INTO workforce_catalog(code,kind,label,sort_order)
VALUES ('accommodation.hostel','accommodation','Хостел',10),
       ('accommodation.hotel','accommodation','Гостиница',20);
ALTER TABLE workforce_profiles ADD COLUMN accommodation_code TEXT
    REFERENCES workforce_catalog(code) CHECK(accommodation_code LIKE 'accommodation.%');
CREATE INDEX idx_workforce_profile_accommodation ON workforce_profiles(accommodation_code);
