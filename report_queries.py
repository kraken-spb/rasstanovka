"""Shared read model for assignment reports; no HTTP parsing or file rendering.

Assignments and unique people are different measures. The calendar counts only
present assignments; Excel and position cards retain absent assignments as well.
Authorization is explicit: detail queries require an actor, while calendar totals
retain the existing site-wide aggregate visibility of their protected endpoint.
The caller owns the read transaction so every sheet uses one consistent snapshot.
"""
from contractor_api import placement_company_sql
from filter_values import matches, values
from user_smu_access import explicit_scope, worker_clause


SHIFT_LABELS = {'1 смена': 'День', '2 смена': 'Ночь'}
CATEGORY_SQL = "COALESCE(gc.name,w.category,'')"
CONTRACTOR_SQL = "COALESCE(ct.name,w.contractor,'')"


def normalized_shift_sql(expression='a.shift'):
    """Expression is an internal column name, never request input."""
    return f"CASE WHEN {expression}='Ночная смена' THEN '2 смена' ELSE {expression} END"


def coverage_status(attendance_status, assigned):
    return 'absent' if attendance_status != 'Явка' else 'assigned' if assigned else 'unassigned'


def assignment_facts_sql():
    """One row per saved assignment, independent of the latest import roster."""
    return f'''
        SELECT a.id,a.worker_id,a.work_date,a.crew_id,a.foreman_user_id,
               s.object_id,a.subobject_id,a.employer assignment_employer,
               {placement_company_sql()} display_company,
               {normalized_shift_sql()} normalized_shift,
               NOT EXISTS (SELECT 1 FROM staffing_attendance att
                   WHERE att.worker_id=a.worker_id AND att.work_date=a.work_date
                   AND att.status<>'Явка') present,
               o.name object_name,s.name subobject_name,
               w.full_name,w.personnel_no,w.profession,w.gsp_profession,w.department,
               {CATEGORY_SQL} category,{CONTRACTOR_SQL} contractor
        FROM assignments a
        JOIN workers w ON w.id=a.worker_id
        JOIN subobjects s ON s.id=a.subobject_id
        JOIN objects o ON o.id=s.object_id
        LEFT JOIN employee_contractors ec ON ec.worker_id=w.id
        LEFT JOIN contractors ct ON ct.id=ec.contractor_id
        LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id
        LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id
    '''


def assignment_rows(db, day, shifts, *, user):
    """Authorized saved assignments, including inactive workers and absences."""
    if not shifts:
        return []
    access, access_params = '1', []
    if explicit_scope(db, user):
        access, access_params = worker_clause(db, user, worker='f')
    elif user['role'] == 'foreman':
        access = '''(f.foreman_user_id=? OR f.crew_id IN
            (SELECT id FROM crews WHERE owner_user_id=?))'''
        access_params = [user['id'], user['id']]
    return db.execute(f'''
        WITH facts AS ({assignment_facts_sql()})
        SELECT f.*,pw.description performed_work,
               d.linear_itr_override,d.brigadier_override,
               c.linear_itr crew_linear_itr,c.brigadier crew_brigadier
        FROM facts f
        LEFT JOIN staffing_row_details d ON d.worker_id=f.worker_id
        LEFT JOIN crews c ON c.id=f.crew_id
        LEFT JOIN staffing_performed_work pw ON pw.worker_id=f.worker_id
            AND pw.work_date=f.work_date AND pw.shift=f.normalized_shift
        WHERE f.work_date=? AND f.normalized_shift IN ({','.join('?' for _ in shifts)})
            AND ({access})
        ORDER BY f.object_name COLLATE NOCASE,f.subobject_name COLLATE NOCASE,
                 f.assignment_employer COLLATE NOCASE,f.full_name COLLATE NOCASE,
                 f.personnel_no COLLATE NOCASE,f.id
    ''', [day, *shifts, *access_params]).fetchall()


def calendar_facts(db, start, end, *, category=None):
    """Site-wide present assignment counts, aggregated in SQLite, not Python."""
    category_clause, params = '', [start, end]
    if category is not None:
        selected = values(category)
        category_clause = ' AND category IN (' + ','.join('?' for _ in selected) + ')'
        params.extend(selected)
    return [dict(row) for row in db.execute(f'''
        WITH facts AS ({assignment_facts_sql()})
        SELECT object_id,subobject_id,work_date,display_company employer,contractor,
               SUM(CASE WHEN normalized_shift='1 смена' THEN 1 ELSE 0 END) day_count,
               SUM(CASE WHEN normalized_shift='2 смена' THEN 1 ELSE 0 END) night_count
        FROM facts WHERE work_date BETWEEN ? AND ? AND present {category_clause}
        GROUP BY subobject_id,work_date,display_company,contractor
    ''', params)]


def filter_assignment_rows(rows, *, category=None, department=None, contractor=None, query=''):
    """Compose filters without duplicating rows; an empty category is selectable."""
    words = query.casefold().replace('ё', 'е').split()
    return [row for row in rows
            if matches(category, row['category'] or '')
            and (not department or matches(department, row['department']))
            and (not contractor or matches(contractor, row['contractor']))
            and all(word in (row['object_name'] + ' ' + row['subobject_name']).casefold().replace('ё', 'е')
                    for word in words)]
