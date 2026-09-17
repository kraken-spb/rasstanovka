-- Organize existing job titles without replacing their codes, text or worker links.
ALTER TABLE workforce_catalog DROP CONSTRAINT workforce_catalog_kind_check;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_kind_check CHECK(kind IN
    ('citizenship','employment','stage','basis','result','document','docstate','check','checkstate','project',
     'profession','specialty','travelpoint','direction','destination'));
ALTER TABLE workforce_catalog DROP CONSTRAINT workforce_catalog_label_check;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_label_check CHECK(length(trim(label)) BETWEEN 1 AND
    CASE WHEN kind IN ('profession','specialty') THEN 500 WHEN kind='travelpoint' THEN 300 ELSE 200 END);

ALTER TABLE workforce_catalog ADD COLUMN specialty_code TEXT REFERENCES workforce_catalog(code);
ALTER TABLE workforce_catalog ADD COLUMN grade SMALLINT;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_specialty_check CHECK(
    specialty_code IS NULL OR (kind='profession' AND split_part(specialty_code,'.',1)='specialty'));
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_grade_check CHECK(
    grade IS NULL OR (kind='profession' AND specialty_code IS NOT NULL AND grade BETWEEN 1 AND 99));
CREATE INDEX idx_workforce_catalog_specialty ON workforce_catalog(specialty_code) WHERE specialty_code IS NOT NULL;

-- Only an explicit terminal "N разряд/разряда" proves the grade. Bare numbers,
-- categories, driver classes and spelling variants remain part of the original name.
CREATE TEMP TABLE profession_hierarchy_source ON COMMIT DROP AS
SELECT code,COALESCE(parts[1],trim(label)) specialty_label,parts[2]::smallint grade,active
FROM (
    SELECT code,label,active,regexp_match(trim(label),
        '^(.*[^[:space:]])[[:space:]]+([1-9][0-9]?)[[:space:]]+разряд(?:а)?$','i') parts
    FROM workforce_catalog WHERE kind='profession'
) source;

INSERT INTO workforce_catalog(code,kind,label,active)
    SELECT 'specialty.'||gen_random_uuid()::text,'specialty',min(specialty_label),bool_or(active)
    FROM profession_hierarchy_source GROUP BY log_casefold(trim(specialty_label));
UPDATE workforce_catalog c SET specialty_code=s.code,grade=p.grade
    FROM profession_hierarchy_source p JOIN workforce_catalog s
        ON s.kind='specialty' AND s.label_key=log_casefold(trim(p.specialty_label))
    WHERE c.code=p.code;
