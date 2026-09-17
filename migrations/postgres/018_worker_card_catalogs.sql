-- Preserve all original text; bind only case/outer-whitespace-equivalent values.
ALTER TABLE workforce_catalog DROP CONSTRAINT workforce_catalog_kind_check;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_kind_check CHECK(kind IN
    ('citizenship','employment','stage','basis','result','document','docstate','check','checkstate','project',
     'profession','travelpoint','direction','destination'));
ALTER TABLE workforce_catalog DROP CONSTRAINT workforce_catalog_label_check;
ALTER TABLE workforce_catalog ADD CONSTRAINT workforce_catalog_label_check CHECK(length(trim(label)) BETWEEN 1 AND
    CASE kind WHEN 'profession' THEN 500 WHEN 'travelpoint' THEN 300 ELSE 200 END);

INSERT INTO workforce_catalog(code,kind,label,sort_order,system_value) VALUES
    ('direction.arrival','direction','Заезд',10,TRUE),
    ('direction.departure','direction','Выезд',20,TRUE),
    ('destination.site','destination','Участок',10,TRUE),
    ('destination.pvp','destination','ПВП',20,TRUE),
    ('destination.home','destination','Домой',30,TRUE),
    ('destination.other','destination','Другое',40,TRUE);

INSERT INTO workforce_catalog(code,kind,label)
    SELECT 'profession.'||gen_random_uuid()::text,'profession',min(trim(profession))
    FROM workers WHERE trim(profession)<>'' GROUP BY log_casefold(trim(profession));
INSERT INTO workforce_catalog(code,kind,label)
    SELECT 'travelpoint.'||gen_random_uuid()::text,'travelpoint',min(trim(value)) FROM (
        SELECT origin_city value FROM workforce_profiles
        UNION ALL SELECT origin FROM workforce_movements
        UNION ALL SELECT destination FROM workforce_movements
    ) source WHERE trim(value)<>'' GROUP BY log_casefold(trim(value));

ALTER TABLE workers ADD COLUMN profession_code TEXT REFERENCES workforce_catalog(code)
    CHECK(profession_code IS NULL OR split_part(profession_code,'.',1)='profession');
ALTER TABLE workforce_profiles ADD COLUMN origin_code TEXT REFERENCES workforce_catalog(code)
    CHECK(origin_code IS NULL OR split_part(origin_code,'.',1)='travelpoint');
ALTER TABLE workforce_movements ADD COLUMN origin_code TEXT REFERENCES workforce_catalog(code)
    CHECK(origin_code IS NULL OR split_part(origin_code,'.',1)='travelpoint');
ALTER TABLE workforce_movements ADD COLUMN destination_code TEXT REFERENCES workforce_catalog(code)
    CHECK(destination_code IS NULL OR split_part(destination_code,'.',1)='travelpoint');
ALTER TABLE workforce_movements ADD COLUMN direction_code TEXT
    GENERATED ALWAYS AS ('direction.'||direction) STORED REFERENCES workforce_catalog(code);
ALTER TABLE workforce_movements ADD COLUMN destination_kind_code TEXT
    GENERATED ALWAYS AS ('destination.'||destination_kind) STORED REFERENCES workforce_catalog(code);

UPDATE workers w SET profession_code=c.code FROM workforce_catalog c
    WHERE c.kind='profession' AND c.label_key=log_casefold(trim(w.profession));
UPDATE workforce_profiles p SET origin_code=c.code FROM workforce_catalog c
    WHERE c.kind='travelpoint' AND c.label_key=log_casefold(trim(p.origin_city));
UPDATE workforce_movements m SET origin_code=c.code FROM workforce_catalog c
    WHERE c.kind='travelpoint' AND c.label_key=log_casefold(trim(m.origin));
UPDATE workforce_movements m SET destination_code=c.code FROM workforce_catalog c
    WHERE c.kind='travelpoint' AND c.label_key=log_casefold(trim(m.destination));
CREATE INDEX idx_workers_profession_code ON workers(profession_code);
CREATE INDEX idx_workforce_profile_origin ON workforce_profiles(origin_code);
CREATE INDEX idx_workforce_movement_origin ON workforce_movements(origin_code);
CREATE INDEX idx_workforce_movement_destination ON workforce_movements(destination_code);

-- A shared text/code pair for the existing staffing and import writers. New unknown
-- import text remains unbound and visible for review; it never creates catalog values.
CREATE FUNCTION workforce_bind_catalog_pair() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    catalog_kind TEXT:=TG_ARGV[0]; text_field TEXT:=TG_ARGV[1]; code_field TEXT:=TG_ARGV[2];
    incoming JSONB:=to_jsonb(NEW); previous JSONB; chosen_code TEXT; chosen_label TEXT;
    text_changed BOOLEAN; code_changed BOOLEAN;
BEGIN
    previous:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
    chosen_code:=incoming->>code_field;
    text_changed:=TG_OP='INSERT' OR (incoming->>text_field) IS DISTINCT FROM (previous->>text_field);
    code_changed:=(incoming->>code_field) IS DISTINCT FROM (previous->>code_field);
    IF code_changed AND chosen_code IS NOT NULL THEN
        SELECT label INTO chosen_label FROM workforce_catalog WHERE code=chosen_code AND kind=catalog_kind;
        IF NOT FOUND THEN RAISE EXCEPTION 'Invalid % catalog binding',catalog_kind USING ERRCODE='23503'; END IF;
        incoming:=jsonb_set(incoming,ARRAY[text_field],to_jsonb(chosen_label));
    ELSIF code_changed AND NOT text_changed THEN
        incoming:=jsonb_set(incoming,ARRAY[text_field],to_jsonb(''::text));
    ELSIF text_changed THEN
        SELECT code INTO chosen_code FROM workforce_catalog
            WHERE kind=catalog_kind AND label_key=log_casefold(trim(incoming->>text_field))
                AND (active OR code=incoming->>code_field);
        incoming:=jsonb_set(incoming,ARRAY[code_field],COALESCE(to_jsonb(chosen_code),'null'::jsonb));
    END IF;
    NEW:=jsonb_populate_record(NEW,incoming);
    RETURN NEW;
END $$;
CREATE TRIGGER workforce_bind_profession BEFORE INSERT OR UPDATE OF profession,profession_code ON workers
    FOR EACH ROW EXECUTE FUNCTION workforce_bind_catalog_pair('profession','profession','profession_code');
CREATE TRIGGER workforce_bind_origin BEFORE INSERT OR UPDATE OF origin_city,origin_code ON workforce_profiles
    FOR EACH ROW EXECUTE FUNCTION workforce_bind_catalog_pair('travelpoint','origin_city','origin_code');
CREATE TRIGGER workforce_bind_movement_origin BEFORE INSERT OR UPDATE OF origin,origin_code ON workforce_movements
    FOR EACH ROW EXECUTE FUNCTION workforce_bind_catalog_pair('travelpoint','origin','origin_code');
CREATE TRIGGER workforce_bind_movement_destination BEFORE INSERT OR UPDATE OF destination,destination_code ON workforce_movements
    FOR EACH ROW EXECUTE FUNCTION workforce_bind_catalog_pair('travelpoint','destination','destination_code');

-- Renaming an administrator-owned catalog updates current readers, including staffing.
-- Audit/source snapshots are not rewritten. Creating a value binds only exact matches.
CREATE FUNCTION workforce_catalog_sync_text() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind='profession' THEN
        IF TG_OP='UPDATE' AND OLD.label IS DISTINCT FROM NEW.label THEN
            UPDATE workers SET profession=NEW.label WHERE profession_code=NEW.code AND profession IS DISTINCT FROM NEW.label;
        END IF;
        IF NEW.active THEN
            UPDATE workers SET profession_code=NEW.code WHERE profession_code IS NULL
                AND log_casefold(trim(profession))=NEW.label_key;
        END IF;
    ELSIF NEW.kind='travelpoint' THEN
        IF TG_OP='UPDATE' AND OLD.label IS DISTINCT FROM NEW.label THEN
            UPDATE workforce_profiles SET origin_city=NEW.label,edit_token=gen_random_uuid(),updated_at=now()
                WHERE origin_code=NEW.code AND origin_city IS DISTINCT FROM NEW.label;
            UPDATE workforce_movements SET origin=NEW.label,edit_token=gen_random_uuid(),updated_at=now()
                WHERE origin_code=NEW.code AND origin IS DISTINCT FROM NEW.label;
            UPDATE workforce_movements SET destination=NEW.label,edit_token=gen_random_uuid(),updated_at=now()
                WHERE destination_code=NEW.code AND destination IS DISTINCT FROM NEW.label;
        END IF;
        IF NEW.active THEN
            UPDATE workforce_profiles SET origin_code=NEW.code,edit_token=gen_random_uuid(),updated_at=now()
                WHERE origin_code IS NULL AND log_casefold(trim(origin_city))=NEW.label_key;
            UPDATE workforce_movements SET origin_code=NEW.code,edit_token=gen_random_uuid(),updated_at=now()
                WHERE origin_code IS NULL AND log_casefold(trim(origin))=NEW.label_key;
            UPDATE workforce_movements SET destination_code=NEW.code,edit_token=gen_random_uuid(),updated_at=now()
                WHERE destination_code IS NULL AND log_casefold(trim(destination))=NEW.label_key;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER workforce_catalog_sync_text AFTER INSERT OR UPDATE OF label,active ON workforce_catalog
    FOR EACH ROW EXECUTE FUNCTION workforce_catalog_sync_text();
