"""Deferred workforce exports with fresh scope checks."""
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode

from flask import abort, g, jsonify, request, send_file, session
from workforce_core import READERS, actor_scope, date_value, digest, plain, uuid_value

JOB_TYPES = {'urp', 'registry', 'placement_pdf'}
QUERY_KEY = re.compile(r'^[a-z][a-z_]{0,63}$')


def job_directory():
    path = Path(os.environ.get('WORKFORCE_EXPORT_DIR', '/app/data/workforce-exports'))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def scope_fingerprint(db):
    actor, scope, args = actor_scope(db)
    members = [] if scope == 'TRUE' else [row[0] for row in db.native('SELECT w.id FROM workers w WHERE ('+scope+') ORDER BY w.id', args)]
    return actor, digest([actor['role'], scope, args, members])


def request_parameters(data):
    if not isinstance(data, dict): abort(400, description='Укажите параметры формирования отчёта.')
    kind = data.get('type', 'urp')
    if kind not in JOB_TYPES: abort(400, description='Неизвестный тип выгрузки.')
    if kind == 'urp':
        if set(data) != {'date', 'request_key'}: abort(400, description='Укажите дату и ключ формирования отчёта.')
        return kind, date_value(data['date'], 'Отчётная дата', True), {}, uuid_value(data['request_key'])
    expected = {'type', 'date', 'filters', 'request_key'} | ({'section'} if kind == 'registry' else set())
    if set(data) != expected: abort(400, description='Параметры выгрузки неполны или содержат лишние поля.')
    day, filters = date_value(data['date'], 'Отчётная дата', True), data['filters']
    if not isinstance(filters, list) or len(filters) > 200: abort(400, description='Передайте до 200 параметров фильтра.')
    clean = []
    for pair in filters:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(value, str) for value in pair): abort(400, description='Некорректный параметр фильтра.')
        key, value = pair
        if not QUERY_KEY.fullmatch(key) or key in {'date','section','request_key','type'} or len(value) > 4000: abort(400, description='Некорректный параметр фильтра.')
        clean.append([key, value])
    if len(json.dumps(clean, ensure_ascii=False)) > 32000: abort(400, description='Параметры фильтра слишком велики.')
    params = {'filters': clean}
    if kind == 'registry':
        if data['section'] not in {'rotation', 'recruitment'}: abort(400, description='Выберите «Перевахту» или «Комплектацию».')
        params['section'] = data['section']
    return kind, day, params, uuid_value(data['request_key'])


def row_parameters(row):
    value = row['request_json']
    return json.loads(value) if isinstance(value, str) else (value or {})


def artifact(row):
    day, kind = row['report_date'].isoformat(), row['job_type']
    if kind == 'placement_pdf':
        return '.pdf', 'application/pdf', f'placement-report-{day}.pdf'
    if kind == 'registry':
        title = {'rotation': 'Перевахта', 'recruitment': 'Комплектация'}[row_parameters(row)['section']]
        return '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', title + '_' + day + '.xlsx'
    return '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'УРП — учёт персонала — {day}.xlsx'


def register_export_jobs(app, database, roles_required):
    @app.post('/api/workforce/export-jobs')
    @roles_required(*READERS)
    def workforce_export_job_create():
        kind, day, params, key = request_parameters(request.get_json(silent=True))
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor, scope = scope_fingerprint(db)
            old = db.native('SELECT * FROM workforce_export_jobs WHERE id=%s', (key,)).fetchone()
            if old:
                same = (old['actor_id'] == actor['id'] and old['report_date'].isoformat() == day
                        and old['scope_digest'] == scope and old['job_type'] == kind
                        and row_parameters(old) == params)
                if not same:
                    abort(409, description='Параметры задания изменились. Запустите новое формирование.')
                return jsonify(id=key, state=old['state'], job_type=kind)
            active = db.native("SELECT 1 FROM workforce_export_jobs WHERE actor_id=%s AND state IN ('pending','running') AND expires_at>now()", (actor['id'],)).fetchone()
            if active:
                abort(409, description='Предыдущий отчёт ещё формируется. Дождитесь его завершения.')
            queued = db.native("SELECT count(*) FROM workforce_export_jobs WHERE state IN ('pending','running') AND expires_at>now()").fetchone()[0]
            if queued >= 40:
                abort(429, description='Очередь отчётов заполнена. Повторите через минуту.')
            db.native("INSERT INTO workforce_export_jobs(id,actor_id,report_date,scope_digest,job_type,request_json) VALUES (%s,%s,%s,%s,%s,%s::jsonb)", (key,actor['id'],day,scope,kind,json.dumps(params,ensure_ascii=False,separators=(',',':'))))
        return jsonify(id=key, state='pending', job_type=kind), 202

    def owned(db, key):
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
            row = owned(db, job_id)
            return jsonify(plain({key:row[key] for key in ('id','job_type','state','first_count','second_count','error','created_at','finished_at')}))

    @app.get('/api/workforce/export-jobs/<uuid:job_id>/download')
    @roles_required(*READERS)
    def workforce_export_job_download(job_id):
        db = database()
        with db:
            db.execute('BEGIN')
            row = owned(db, job_id)
            if row['state'] != 'ready':
                abort(409, description='Отчёт ещё не готов.')
        suffix, mimetype, name = artifact(row)
        path = job_directory() / (str(job_id) + suffix)
        if not path.is_file():
            abort(410, description='Файл отчёта недоступен. Сформируйте его заново.')
        response = send_file(path, as_attachment=True, download_name=name, mimetype=mimetype)
        response.headers['Cache-Control'] = 'no-store'
        if row['job_type']=='urp': response.headers.update({'X-Export-Count':str(row['first_count']+row['second_count']),'X-Export-First-Count':str(row['first_count']),'X-Export-Second-Count':str(row['second_count'])})
        if row['job_type']=='registry': response.headers['X-Export-Row-Count']=str(row['first_count'])
        return response


def endpoint_content(app, endpoint, path, actor):
    """Invoke an existing protected GET view in-process, never over HTTP."""
    # placement_report rolls back its read transaction, so it must not share
    # the worker connection or app-context database with the queue state.
    with app.app_context():
        with app.test_request_context(path, headers={'Test-Role': actor['role']}):
            if app.secret_key:
                session['user_id'] = actor['id']
            early = app.preprocess_request()
            response = app.make_response(early if early is not None else app.view_functions[endpoint]())
            response.direct_passthrough = False
            try:
                if response.status_code != 200:
                    detail = (response.get_json(silent=True) or {}).get('error')
                    raise ValueError(detail or f'Не удалось сформировать файл (HTTP {response.status_code}).')
                return response.get_data(), dict(response.headers)
            finally:
                response.close()


def process_content(app, db, row, actor):
    kind,day=row['job_type'],row['report_date'].isoformat()
    if kind=='urp':
        from workforce_export import report_records,rows_for_report,workbook_bytes
        with db:
            db.execute('BEGIN')
            g.user = actor
            tabs=rows_for_report(report_records(db,day))
        return workbook_bytes(tabs).getvalue(),len(tabs[0]),len(tabs[1])
    params=row_parameters(row); pairs=[('date',day),*params['filters']]
    if kind=='registry':
        pairs.append(('section',params['section'])); content,headers=endpoint_content(app,'workforce_registry_export','/api/workforce/people/export?'+urlencode(pairs),actor)
        return content,int(headers.get('X-Export-Row-Count','0')),None
    content,_=endpoint_content(app,'placement_report','/api/placement-report/pdf?'+urlencode(pairs),actor)
    return content,None,None


def process_one(app,get_db,only_job_id=None):
    with app.app_context():
        db=get_db()
        with db:
            db.execute('BEGIN IMMEDIATE'); db.native("UPDATE workforce_export_jobs SET state='failed',error='Формирование прервано. Повторите запрос.',finished_at=now() WHERE state='running' AND started_at<now()-interval '10 minutes'")
            row=db.native("UPDATE workforce_export_jobs SET state='running',started_at=now() WHERE id=(SELECT id FROM workforce_export_jobs WHERE state='pending' AND expires_at>now() AND (%s::uuid IS NULL OR id=%s::uuid) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",(only_job_id,only_job_id)).fetchone()
        if not row:return False
        suffix,_,_=artifact(row); key=row['id']; path=job_directory()/(str(key)+suffix); temporary=path.with_suffix(path.suffix+'.partial')
        try:
            with db:
                db.execute('BEGIN'); actor=db.native('SELECT id,role,active FROM users WHERE id=%s',(row['actor_id'],)).fetchone()
                if not actor or not actor['active']: raise ValueError('Пользователь больше не имеет доступа к выгрузке.')
                g.user=actor; _,scope=scope_fingerprint(db)
                if scope != row['scope_digest']: raise ValueError('Права доступа изменились. Сформируйте отчёт заново.')
            content,first,second=process_content(app,db,row,actor)
            temporary.write_bytes(content); temporary.chmod(0o600); temporary.replace(path)
            db.native('UPDATE workforce_export_jobs SET state=%s,first_count=%s,second_count=%s,finished_at=now() WHERE id=%s',('ready',first,second,key))
        except Exception as error:
            db.rollback(); temporary.unlink(missing_ok=True); path.unlink(missing_ok=True); logging.exception('Workforce report job failed: %s',key)
            message = str(error).strip() if isinstance(error, ValueError) else 'Не удалось сформировать отчёт. Проверьте доступ и повторите запрос.'
            db.native('UPDATE workforce_export_jobs SET state=%s,error=%s,finished_at=now() WHERE id=%s',('failed',(message or 'Не удалось сформировать отчёт. Проверьте доступ и повторите запрос.')[:1000],key))
        return True


def main():
    if os.environ.get('APP_ENVIRONMENT')!='staging' or os.environ.get('DATABASE_BACKEND')!='postgres': raise SystemExit('This worker is configured for isolated PostgreSQL staging only.')
    from app import app,get_db
    while True:
        try: found=process_one(app,get_db)
        except Exception: logging.exception('Workforce report worker iteration failed.'); found=False
        if not found: time.sleep(1)

if __name__=='__main__': main()
