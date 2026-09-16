"""Consistent database snapshots and administrator backup inventory."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from flask import abort, g, jsonify, request, send_file


MOSCOW = timezone(timedelta(hours=3))
SUFFIXES = {'.sqlite3', '.sqlite', '.db', '.dump'}


def backup_folder(db):
    if getattr(db, 'dialect', None) == 'postgres':
        from postgres_backup import folder
        return None, folder()
    database = Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    return database, database.parent / 'backups'


def placement_counts(db, work_date):
    row = db.execute('''SELECT COUNT(DISTINCT worker_id),COUNT(*) FROM assignments
        WHERE work_date=? AND subobject_id IS NOT NULL
          AND shift IN ('1 смена','2 смена','Ночная смена')''', (work_date,)).fetchone()
    return {'employee_count': row[0], 'assignment_count': row[1]}


def create_backup(db, work_date=None, reason='manual'):
    work_date = work_date or datetime.now(MOSCOW).date().isoformat()
    if getattr(db, 'dialect', None) == 'postgres':
        from postgres_backup import create
        return create(work_date, reason)
    database, folder = backup_folder(db)
    folder.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    identifier = uuid4().hex
    temporary = folder / f'.backup-{identifier}.partial'
    target = None
    try:
        # A separate reader captures committed data even when the caller holds a transaction.
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise sqlite3.DatabaseError('Проверка резервной копии не пройдена.')
                counts = placement_counts(destination, work_date)
        target = folder / f"backup-{started:%Y%m%dT%H%M%SZ}-{counts['employee_count']}people-{identifier}.sqlite3"
        temporary.replace(target)
        metadata = {'version': 1, 'created_at': started.isoformat(), 'work_date': work_date,
                    'reason': reason, **counts}
        stat = target.stat()
        metadata.update(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        pending = folder / f'.backup-{identifier}.json.partial'
        pending.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        pending.replace(target.with_suffix(target.suffix + '.json'))
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


@lru_cache(maxsize=256)
def inspect_backup(filename, size, modified_ns, work_date):
    path = Path(filename)
    if path.suffix == '.dump':
        from postgres_backup import inspect
        return inspect(path, size, modified_ns, work_date)
    created_at = datetime.fromtimestamp(modified_ns / 1_000_000_000, timezone.utc).isoformat()
    result = {'name': path.name, 'size_bytes': size, 'created_at': created_at,
              'time_source': 'file', 'reason': 'existing', 'work_date': work_date}
    metadata_path = path.with_suffix(path.suffix + '.json')
    try:
        if metadata_path.is_file() and not metadata_path.is_symlink():
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if (isinstance(metadata, dict) and metadata.get('version') == 1 and metadata.get('size_bytes') == size
                    and metadata.get('mtime_ns') == modified_ns):
                timestamp = datetime.fromisoformat(metadata['created_at'])
                if timestamp.tzinfo is not None:
                    result.update(created_at=timestamp.isoformat(), time_source='recorded',
                                  reason=metadata.get('reason', 'existing'))
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Older snapshots can be read independently of their optional metadata.
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as saved:
            saved.execute('PRAGMA trusted_schema=OFF')
            result.update(placement_counts(saved, work_date), status='ready')
    except sqlite3.DatabaseError:
        result.update(employee_count=None, assignment_count=None, status='unreadable')
    return result


def register_backup_routes(app, get_db, roles_required):
    def requested_date(value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            abort(400, description='Укажите дату расстановки.')
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            abort(400, description='Укажите существующую дату расстановки.')

    def files():
        _, folder = backup_folder(get_db())
        if not folder.exists():
            return []
        return [path for path in folder.iterdir()
                if path.suffix in SUFFIXES and path.is_file() and not path.is_symlink()]

    @app.get('/api/backups')
    @roles_required('super_admin')
    def list_backups():
        work_date = requested_date(request.args.get('date', datetime.now(MOSCOW).date().isoformat()))
        try:
            page = int(request.args.get('page', '1'))
            if page < 1:
                raise ValueError
        except ValueError:
            abort(400, description='Укажите номер страницы.')
        entries = sorted(((path, path.stat()) for path in files()),
                         key=lambda item: (item[1].st_mtime_ns, item[0].name), reverse=True)
        rows = [inspect_backup(str(path), stat.st_size, stat.st_mtime_ns, work_date)
                for path, stat in entries[(page - 1) * 20:page * 20]]
        return jsonify({'rows': rows, 'page': page, 'total': len(entries), 'page_size': 20,
                        'work_date': work_date, 'can_download': g.user['role'] == 'super_admin'})

    @app.post('/api/backups')
    @roles_required('super_admin')
    def save_backup():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Укажите параметры резервной копии.')
        work_date = requested_date(data.get('date'))
        target = create_backup(get_db(), work_date)
        stat = target.stat()
        return jsonify(inspect_backup(str(target), stat.st_size, stat.st_mtime_ns, work_date)), 201

    @app.get('/api/backups/<name>/download')
    @roles_required('super_admin')
    def download_backup(name):
        _, folder = backup_folder(get_db())
        if Path(name).name != name or '/' in name or '\\' in name:
            abort(404)
        target = folder / name
        if (target.is_symlink() or not target.is_file() or target.suffix not in SUFFIXES
                or target.resolve().parent != folder.resolve()):
            abort(404, description='Резервная копия не найдена.')
        response = send_file(target, as_attachment=True, download_name=target.name,
                             mimetype='application/octet-stream' if target.suffix == '.dump' else 'application/vnd.sqlite3', max_age=0, conditional=False)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
