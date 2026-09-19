"""PPS filters use explicit SMU links and the caller's existing worker scope."""
from flask import abort


def choices(db, scope, args):
    return [dict(row) for row in db.native(f'''
        SELECT DISTINCT pc.id,pc.name FROM pps_catalog pc
        JOIN (SELECT es.worker_id,sc.pps_id FROM employee_smu es JOIN smu_catalog sc ON sc.id=es.smu_id
              UNION SELECT p.worker_id,d.pps_id FROM workforce_profiles p
              JOIN workforce_divisions d ON d.id=p.division_id) links ON links.pps_id=pc.id
        JOIN workers w ON w.id=links.worker_id
        WHERE ({scope}) ORDER BY pc.name,pc.id''', args).fetchall()]


def add_filter(db, query, clauses, args):
    values = sorted({value for value in query.getlist('pps') if value})
    if not values:
        return
    try:
        if len(values) > 100:
            raise ValueError
        ids = [int(value) for value in values]
        if any(value <= 0 or value > 9223372036854775807 for value in ids):
            raise ValueError
    except ValueError:
        abort(400, description='Выберите ППС из справочника.')
    # SMU catalogue changes have no registry-revision trigger. Resolve links in
    # this transaction so changed membership also changes all registry cache keys.
    smu_ids = [row[0] for row in db.native(
        'SELECT id FROM smu_catalog WHERE pps_id=ANY(%s::bigint[]) ORDER BY id', (ids,))]
    division_ids=[row[0] for row in db.native('SELECT id FROM workforce_divisions WHERE pps_id=ANY(%s::bigint[]) ORDER BY id',(ids,))]
    clauses.append('(EXISTS(SELECT 1 FROM employee_smu ps WHERE ps.worker_id=w.id AND ps.smu_id=ANY(%s::bigint[])) OR p.division_id=ANY(%s::bigint[]))')
    args.extend([smu_ids,division_ids])
