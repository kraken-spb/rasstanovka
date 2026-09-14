"""Explicit, backed-up organization of the reviewed initial object groups."""
from backup_api import create_backup


def organize_stages(db, expected_objects):
    expected = {row['id']: row['name'] for row in expected_objects}
    if not expected or len(expected) != len(expected_objects):
        raise ValueError('Нужен снимок текущих групп без повторов.')
    with db:
        db.execute('BEGIN IMMEDIATE')
        current = {row['id']: dict(row) for row in db.execute('SELECT id,name,stage_id FROM objects')}
        stages = {row['name']: row['id'] for row in db.execute('SELECT id,name FROM location_stages')}
        for group_id, name in expected.items():
            row = current.get(group_id)
            if not row or row['name'] != name or row['stage_id'] not in (None, stages.get('Этап 13')):
                raise ValueError('Группы изменились после проверки. Перенос отменён.')
        if set(current) != set(expected):
            raise ValueError('Состав групп изменился после проверки. Перенос отменён.')
        names = ('Этап 5', 'Этап 13', 'Этап 15')
        if all(name in stages for name in names) and all(current[i]['stage_id'] == stages['Этап 13'] for i in expected):
            return {'moved': 0, 'already_applied': True}
        protected = ('subobjects', 'assignments', 'daily_staffing_plans', 'staffing_plans', 'assignment_events')
        snapshot = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in protected}
        backup = create_backup(db, reason='location-stages')
        for name in names:
            if name not in stages:
                stages[name] = db.execute('INSERT INTO location_stages(name) VALUES (?)', (name,)).lastrowid
        db.executemany('UPDATE objects SET stage_id=? WHERE id=?', [(stages['Этап 13'], i) for i in expected])
        for table in protected:
            if snapshot[table] != [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]:
                raise ValueError('Изменились связанные данные. Перенос отменён.')
    return {'moved': len(expected), 'stages': stages, 'backup': str(backup),
            'preserved': {table: len(snapshot[table]) for table in protected}}
