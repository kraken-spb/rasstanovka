"""URP reports run in a separate, local worker process with fresh scope checks."""
import logging
import os
from pathlib import Path
import time

from flask import abort, g, jsonify, request, send_file

from workforce_core import READERS, actor_scope, date_value, digest, plain, uuid_value


def job_directory():
    path = Path(os.environ.get('WORKFORCE_EXPORT_DIR', '/app/data/workforce-exports'))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def scope_fingerprint(db):
    actor, scope, args = actor_scope(db)
    # Legacy brigade access may change without an SMU permission record.
    members = []
    if scope != 'TRUE':
        members = [row[0] for row in db.native('SELECT w.id FROM workers w WHERE ('+scope+') ORDER BY w.id', args)]
    return actor, digest([actor['role'], scope, args, members])


def register_export_jobs(app, database, roles_required):
    @app.post('/api/workforce/export-jobs')
    @roles_required(*READERS)
    def workforce_export_job_create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'date', 'request_key'}:
            abort(400, description='Укажите дату и ключ формирования отчёта.')
        day = date_value(data['date'], 'Отчётная дата', True)
        key = uuid_value(data['request_key'])
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, scope = scope_fingerprint(db)
            existing = db.native('SELECT * FROM workforce_export_jobs WHERE id=%s', (key,)).fetchone()
            if existing:
                if existing['actor_id'] != actor['id'] or existing['report_date'].isoformat() != day or existing['scope_digest'] != scope:
                    abort(409, description='Параметры задания изменились. Запустите новое формирование.')
                return jsonify(id=key, state=existing['state'])
            if db.native("SELECT 1 FROM workforce_export_jobs WHERE actor_id=%s AND state IN ('pending','running') AND expires_at>now()", (actor['id'],)).fetchone():
                abort(409, description='Предыдущий отчёт ещё формируется. Дождитесь его завершения.')
            if db.native("SELECT count(*) FROM workforce_export_jobs WHERE state IN ('pending','running') AND expires_at>now()").fetchone()[0] >= 40:
                abort(429, description='Очередь отчётов заполнена. Повторите через минуту.')
            db.native('INSERT INTO workforce_export_jobs(id,actor_id,report_date,scope_digest) VALUES (%s,%s,%s,%s)', (key, actor['id'], day, scope))
        return jsonify(id=key, state='pending'), 202

    def owned_job(db, key):
        actor, scope = scope_fingerprint(db)
        row = db.native('SELECT * FROM workforce_export_jobs WHERE id=%s AND actor_id=%s AND expires_at>now()', (key, actor['id'])).fetchone()
        if not row:
            abort(404, description='Отчёт не найден или срок скачивания истёк.')
        if row['scope_digest'] != scope:
            abort(403, description='Права доступа изменились. Сформируйте отчёт заново.')
        return row

    @app.get('/api/workforce/export-jobs/<uuid:job_id>')
    @roles_required(*READERS)
    def workforce_export_job_status(job_id):
        db = database()
        with db:
            db.execute('BEGIN')
            row = owned_job(db, job_id)
            return jsonify(plain({key:row[key] for key in ('id','state','first_count','second_count','error','created_at','finished_at')}))

    @app.get('/api/workforce/export-jobs/<uuid:job_id>/download')
    @roles_required(*READERS)
    def workforce_export_job_download(job_id):
        db = database()
        with db:
            db.execute('BEGIN')
            row = owned_job(db, job_id)
            if row['state'] != 'ready':
                abort(409, description='Отчёт ещё не готов.')
        path = job_directory() / (str(job_id) + '.xlsx')
        if not path.is_file():
            abort(410, description='Файл отчёта недоступен. Сформируйте его заново.')
        response = send_file(path, as_attachment=True, download_name=f'УРП — учёт персонала — {row["report_date"]}.xlsx')
        response.headers.update({'Cache-Control':'no-store','X-Export-Count':str(row['first_count']+row['second_count']),
                                 'X-Export-First-Count':str(row['first_count']),'X-Export-Second-Count':str(row['second_count'])})
        return response


def process_one(app, get_db, only_job_id=None):
    from workforce_export import report_records, rows_for_report, workbook_bytes
    with app.app_context():
        db = get_db()
        # Claimed jobs survive web/worker restarts; interrupted work gets an explicit
        # failure after its lease, not an unnoticed permanent "running" state.
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.native("UPDATE workforce_export_jobs SET state='failed',error='Формирование прервано. Повторите запрос.',finished_at=now() WHERE state='running' AND started_at<now()-interval '10 minutes'")
            row = db.native("""UPDATE workforce_export_jobs SET state='running',started_at=now()
                WHERE id=(SELECT id FROM workforce_export_jobs WHERE state='pending' AND expires_at>now()
                    AND (%s::uuid IS NULL OR id=%s::uuid)
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""", (only_job_id, only_job_id)).fetchone()
        if not row:
            return False
        key = row['id']
        path = job_directory() / (str(key)+'.xlsx')
        temporary = path.with_suffix('.partial')
        try:
            with db:
                db.execute('BEGIN')
                g.user = db.native('SELECT id,role,active FROM users WHERE id=%s', (row['actor_id'],)).fetchone()
                _, scope = scope_fingerprint(db)
                if scope != row['scope_digest']:
                    raise ValueError('Report scope changed.')
                tabs = rows_for_report(report_records(db, row['report_date'].isoformat()))
            content = workbook_bytes(tabs)
            temporary.write_bytes(content.getvalue());temporary.chmod(0o600)
            temporary.replace(path)
            db.native("UPDATE workforce_export_jobs SET state='ready',first_count=%s,second_count=%s,finished_at=now() WHERE id=%s", (len(tabs[0]),len(tabs[1]),key))
        except Exception:
            db.rollback()
            if temporary.exists():temporary.unlink()
            if path.exists():path.unlink()
            logging.exception('Workforce report job failed: %s', key)
            db.native("UPDATE workforce_export_jobs SET state='failed',error='Не удалось сформировать отчёт. Проверьте доступ и повторите запрос.',finished_at=now() WHERE id=%s", (key,))
        return True


def main():
    if os.environ.get('APP_ENVIRONMENT') != 'staging' or os.environ.get('DATABASE_BACKEND') != 'postgres':
        raise SystemExit('This worker is configured for isolated PostgreSQL staging only.')
    from app import app, get_db
    while True:
        try:
            found = process_one(app, get_db)
        except Exception:
            logging.exception('Workforce report worker iteration failed.')
            found = False
        if not found:time.sleep(1)


if __name__ == '__main__':
    main()
