-- Additional employment types. Existing people and their classifications stay unchanged.
INSERT INTO workforce_catalog(code,kind,label,sort_order,system_value) VALUES
    ('employment.rental','employment','Аренда',60,TRUE),
    ('employment.rso','employment','РСО',70,TRUE);
