"""Consistent PostgreSQL custom-format backups without secrets in command lines."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict


def folder():
    return Path(os.getenv('BACKUP_PATH', Path(__file__).resolve().parent / 'data/backups')).resolve()


def client_environment(dsn):
    values = conninfo_to_dict(dsn)
    environment = os.environ.copy()
    for source, destination in [('host', 'PGHOST'), ('port', 'PGPORT'), ('user', 'PGUSER'),
                                ('password', 'PGPASSWORD'), ('dbname', 'PGDATABASE'), ('sslmode', 'PGSSLMODE')]:
        if source in values:
            environment[destination] = values[source]
    environment['PGCONNECT_TIMEOUT'] = '8'
    return environment


def create(work_date, reason):
    executable = shutil.which('pg_dump')
    inspector = shutil.which('pg_restore')
    if not executable or not inspector:
        raise OSError('PostgreSQL backup tools are unavailable; run the staging application image.')
    target_folder = folder()
    target_folder.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    started = datetime.now(timezone.utc)
    temporary = target_folder / ('.backup-' + identifier + '.partial')
    try:
        dsn = os.environ['DATABASE_URL']
        with psycopg.connect(dsn, connect_timeout=8) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snapshot = reader.execute('SELECT pg_export_snapshot()').fetchone()[0]
            counts = {row[0]: {'employee_count': row[1], 'assignment_count': row[2]}
                      for row in reader.execute('''SELECT work_date,COUNT(DISTINCT worker_id),COUNT(*)
                          FROM assignments WHERE subobject_id IS NOT NULL
                          AND shift IN ('1 смена','2 смена','Ночная смена') GROUP BY work_date''')}
            command = [executable, '--format=custom', '--no-owner', '--no-acl', '--snapshot=' + snapshot, '--file=' + str(temporary)]
            result = subprocess.run(command, env=client_environment(dsn), capture_output=True, timeout=120)
            if result.returncode:
                raise OSError('PostgreSQL backup failed; no snapshot was published.')
        verified = subprocess.run([inspector, '--list', str(temporary)], capture_output=True, timeout=20)
        if verified.returncode or not temporary.stat().st_size:
            raise OSError('PostgreSQL archive validation failed.')
        selected = counts.get(work_date, {'employee_count': 0, 'assignment_count': 0})
        target = target_folder / f"backup-{started:%Y%m%dT%H%M%SZ}-{selected['employee_count']}people-{identifier}.dump"
        with temporary.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        temporary.chmod(0o600)
        temporary.replace(target)
        stat = target.stat()
        metadata = {'version': 2, 'format': 'postgres-custom', 'created_at': started.isoformat(),
                    'reason': reason, 'work_date': work_date, 'counts_by_date': counts,
                    'sha256': digest, 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        pending = target_folder / ('.backup-' + identifier + '.json.partial')
        pending.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        pending.chmod(0o600)
        pending.replace(target.with_suffix('.dump.json'))
        return target
    finally:
        if temporary.exists():
            temporary.unlink()


def inspect(path, size, modified_ns, work_date):
    result = {'name': path.name, 'size_bytes': size,
              'created_at': datetime.fromtimestamp(modified_ns / 1e9, timezone.utc).isoformat(),
              'time_source': 'file', 'reason': 'existing', 'work_date': work_date,
              'employee_count': None, 'assignment_count': None, 'status': 'unreadable'}
    metadata_path = path.with_suffix('.dump.json')
    try:
        if metadata_path.is_symlink():
            return result
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        with path.open('rb') as source:
            header = source.read(5)
            source.seek(0)
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if (header != b'PGDMP' or metadata['version'] != 2 or metadata['sha256'] != digest or
                metadata['size_bytes'] != size or metadata['mtime_ns'] != modified_ns):
            return result
        result.update(created_at=metadata['created_at'], time_source='recorded', reason=metadata['reason'], status='ready',
                      **metadata['counts_by_date'].get(work_date, {'employee_count': 0, 'assignment_count': 0}))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result
