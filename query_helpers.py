"""Small explicit SQL dialect differences shared by existing and new routes."""
import re


def membership(db, column, values):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)?', column):
        raise ValueError('Membership column must be a fixed SQL identifier.')
    values = list(values)
    if not values:
        return '1=0', []
    if getattr(db, 'dialect', None) == 'postgres':
        return column + ' = ANY(?)', [values]
    return column + ' IN (' + ','.join('?' for _ in values) + ')', values


def dated_records(db, table, day, ids):
    if table not in {'assignments', 'staffing_shifts', 'staffing_attendance',
                     'staffing_performed_work', 'staffing_inherited_rows'}:
        raise ValueError('Unknown staffing records table.')
    clause, parameters = membership(db, 'worker_id', ids)
    order = ' ORDER BY id' if table == 'assignments' else ''
    return [dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE work_date=? AND {clause}' + order, [day, *parameters])]
