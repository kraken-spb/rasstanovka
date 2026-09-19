"""Dated handoff from recruitment to rotation after confirmed site attendance."""


def section_filter(section, day, sources):
    """Keep provenance intact; derive service ownership from unretracted facts.

    A later leave/PVP stage does not undo a completed handoff. Correcting or
    retracting its attendance event does, using the same as-of rules as stages.
    SQL is shared by table, board, counts, filter options and registry export.
    """
    def origin(service):
        return '''(EXISTS(SELECT 1 FROM workforce_source_records sr
            WHERE sr.worker_id=w.id AND sr.active AND sr.source_key=ANY(%s))
            OR EXISTS(SELECT 1 FROM workforce_registry_memberships me
                WHERE me.worker_id=w.id AND me.service=%s))''', [
                    [key for key, (owner, _) in sources.items() if owner == service], service]

    attended = '''EXISTS(SELECT 1 FROM workforce_stage_events handoff
        WHERE handoff.worker_id=w.id AND handoff.stage_code='stage.onsite'
        AND handoff.confirmed AND NOT handoff.retracted AND handoff.effective_date<=%s
        AND NOT EXISTS(SELECT 1 FROM workforce_stage_events correction
            WHERE correction.replaces_id=handoff.id AND (correction.confirmed OR correction.retracted)
            AND correction.effective_date<=%s))'''
    recruitment, recruitment_args = origin('recruitment')
    if section == 'recruitment':
        return f'({recruitment} AND NOT ({attended}))', [*recruitment_args, day, day]
    if section == 'rotation':
        rotation, rotation_args = origin('rotation')
        return f'({rotation} OR ({recruitment} AND ({attended})))', [
            *rotation_args, *recruitment_args, day, day]
    raise ValueError('Unknown workforce section')
