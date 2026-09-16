ALTER TABLE workforce_stage_events ADD COLUMN retracted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workforce_movements ADD COLUMN stage_event_id UUID REFERENCES workforce_stage_events(id);
ALTER TABLE workforce_pvp_stays ADD COLUMN arrival_event_id UUID REFERENCES workforce_stage_events(id);
ALTER TABLE workforce_pvp_stays ADD COLUMN departure_event_id UUID REFERENCES workforce_stage_events(id);
UPDATE workforce_pvp_stays p SET arrival_event_id=(SELECT e.id FROM workforce_stage_events e
    WHERE e.worker_id=p.worker_id AND e.source_record_id=p.source_record_id AND e.stage_code='stage.pvp'
      AND e.confirmed ORDER BY e.sequence DESC LIMIT 1)
    WHERE p.source_record_id IS NOT NULL;
