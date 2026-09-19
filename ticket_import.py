"""Private PDF recognition, explicit review and atomic planned-movement creation."""
import hashlib
import json
import os
import logging
from pathlib import Path
import re
import time
from uuid import uuid4

from flask import abort, g, jsonify, request, send_file
from psycopg.types.json import Jsonb
from workforce_core import ADMINS, actor_scope, audit, date_value, digest, plain, require_worker, text_value
from ticket_matching import recommend
from ticket_airports import translate_airports

ROLES=ADMINS|{'rotation','recruitment'}


def directory():
    folder=Path(os.environ.get('TICKET_IMPORT_DIR','/app/data/ticket-import'))
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    return folder


def source_path(job):return directory()/(str(job['id'])+job['file_extension'])


def mappings(db):
    values={}
    for row in db.native('''SELECT m.project_code,c.label,m.aliases FROM workforce_project_travelpoints m
        JOIN workforce_catalog c ON c.code=m.point_code WHERE c.active'''):
        values.setdefault(row['project_code'],[]).extend([row['label'],*row['aliases']])
    return values


def workers(db, search=''):
    _,scope,args=actor_scope(db,write=True)
    if search:
        scope+=' AND (w.full_name ILIKE %s OR w.personnel_no::text ILIKE %s)';args += ['%'+search+'%']*2
    return plain(db.native('''SELECT w.id,w.full_name,w.department,p.birth_date,
        CASE WHEN w.personnel_is_internal THEN '' ELSE w.personnel_no::text END personnel_no,
        sp.project_code,pr.label project,COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'direction',m.direction,'planned_date',m.planned_date,'source','Плановая дата поездки')) FROM workforce_movements m
            WHERE m.worker_id=w.id AND m.planned_date IS NOT NULL AND m.actual_date IS NULL
            AND COALESCE(m.result_code,'') NOT IN ('result.cancelled','result.happened')
            AND NOT EXISTS(SELECT 1 FROM workforce_movements newer WHERE newer.rescheduled_from=m.id)), '[]'::jsonb)
        || COALESCE((SELECT jsonb_build_array(jsonb_build_object('direction','departure','planned_date',r.planned_end_date,'source','Плановый выезд по вахте'),
             jsonb_build_object('direction','arrival','planned_date',r.next_arrival_date,'source','Следующий заезд по вахте')) FROM workforce_rotations r
             WHERE r.worker_id=w.id AND NOT r.cancelled AND r.actual_end_date IS NULL ORDER BY r.start_date DESC,r.id LIMIT 1),'[]'::jsonb)
        || jsonb_build_array(jsonb_build_object('direction','departure','planned_date',p.forecast_departure_date,'source','Прогноз выезда из карточки'),
             jsonb_build_object('direction','arrival','planned_date',p.leave_end_date+2,'source','Окончание МО + 2 дня'),
             jsonb_build_object('direction','arrival','planned_date',p.arrival_date,'source','Дата заезда из карточки')) plans
        FROM workers w LEFT JOIN workforce_profiles p ON p.worker_id=w.id LEFT JOIN employee_smu es ON es.worker_id=w.id
        LEFT JOIN workforce_smu_projects sp ON sp.smu_id=es.smu_id
        LEFT JOIN workforce_catalog pr ON pr.code=sp.project_code
        WHERE w.active=1 AND ('''+scope+') ORDER BY w.full_name,w.id'+(' LIMIT 100' if search else ''),args).fetchall())


def owned(db,job_id,lock=False):
    actor,_,_=actor_scope(db,write=True)
    row=db.native('SELECT * FROM workforce_ticket_jobs WHERE id=%s AND actor_id=%s AND deleted_at IS NULL'+(' FOR UPDATE' if lock else ''),(job_id,actor['id'])).fetchone()
    if not row:abort(404,description='Файл не найден в ваших загрузках.')
    return row


def confirm_values(item):
    if not isinstance(item,dict):abort(400,description='Некорректная строка билета.')
    if type(item.get('worker_id')) is not int:abort(400,description='Выберите сотрудника.')
    if item.get('direction') not in {'arrival','departure'}:abort(400,description='Выберите заезд или выезд.')
    if item.get('transport') not in {'air','rail'}:abort(400,description='Выберите авиа или ЖД.')
    item={**item,'passenger':text_value(item.get('passenger'),'ФИО пассажира',300,True),
          'ticket_number':text_value(item.get('ticket_number'),'Номер билета',80,True)}
    item['ticket_number']=re.sub(r'\s+','',item['ticket_number']).upper()
    if not re.fullmatch(r'[A-Z0-9-]{6,40}',item['ticket_number']):abort(400,description='Проверьте номер билета.')
    rows=item.get('segments')
    if not isinstance(rows,list) or not 1<=len(rows)<=12:abort(400,description='Нужен хотя бы один участок маршрута.')
    clean=[]
    for row in rows:
        if not isinstance(row,dict):abort(400,description='Некорректный участок маршрута.')
        value={key:text_value(row.get(key,''),key,300,key in {'origin','destination'})
               for key in ('origin','destination','flight','coach','seat','timezone_note')}
        for key in ('departure_date','arrival_date'):value[key]=date_value(row.get(key),key,True)
        for key in ('departure_time','arrival_time'):
            raw=row.get(key,'')
            if raw and (not isinstance(raw,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',raw)):
                abort(400,description='Время должно быть в формате ЧЧ:ММ.')
            value[key]=raw or None
        clean.append(value)
    item['segments']=clean
    return item


def recognition_values(db, actor, value, worker_id, direction):
    item = confirm_values(value)
    if item['worker_id'] != worker_id or item['direction'] != direction:
        abort(400, description='Распознанный билет должен совпадать с выбранным сотрудником и направлением.')
    from workforce_core import uuid_value
    job = owned(db, uuid_value(item.get('job_id')), True)
    if job['state'] != 'ready':
        abort(409, description='Этот файл уже применён или ещё обрабатывается.')
    identity = digest([item['transport'], item['ticket_number'].replace('-', '')])
    db.native("SELECT pg_advisory_xact_lock(hashtext(%s))", ('ticket:' + identity,))
    if db.native('SELECT 1 FROM workforce_tickets WHERE identity_key=%s', (identity,)).fetchone():
        abort(409, description='Билет с таким номером уже сохранён. Повтор или переоформление требует проверки.')
    return item, job, identity


def link_recognition(db, actor, item, job, identity, movement, reason):
    ticket = db.native('INSERT INTO workforce_tickets(job_id,worker_id,movement_id,passenger,ticket_number,transport,identity_key,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id', (job['id'], movement['worker_id'], movement['id'], item['passenger'], item['ticket_number'], item['transport'], identity, actor['id'])).fetchone()
    keys=('origin','destination','departure_date','arrival_date','departure_time','arrival_time','flight','coach','seat','timezone_note')
    for index, segment in enumerate(item['segments'], 1):
        db.native('INSERT INTO workforce_ticket_segments(ticket_id,position,' + ','.join(keys) + ') VALUES (' + ','.join(['%s'] * 12) + ')', [ticket['id'], index, *[segment[key] for key in keys]])
    audit(db, actor, 'create', 'ticket', ticket['id'], None, {'file_hash':job['file_hash'],'filename':job['filename'],'values':item}, worker_id=movement['worker_id'], reason=reason)
    signature = digest({key: item[key] for key in ('worker_id', 'direction', 'transport', 'passenger', 'ticket_number', 'segments')})
    preview = {**translate_airports(job['preview_json']), 'confirmed_digest': signature, 'confirmed': item}
    db.native("UPDATE workforce_ticket_jobs SET state='applied',preview_json=%s WHERE id=%s", (Jsonb(preview), job['id']))
    return ticket


def register_ticket_import(app,database,roles_required):
    def db():
        connection=database()
        if getattr(connection,'dialect',None)!='postgres':abort(404)
        return connection

    @app.get('/api/workforce/tickets/geography')
    @roles_required(*ROLES)
    def ticket_geography():
        connection=db()
        with connection:
            actor,_,_=actor_scope(connection,write=True)
            reference=plain(connection.native("SELECT code,kind,label FROM workforce_catalog WHERE kind IN ('project','travelpoint') AND active ORDER BY sort_order,label").fetchall())
            links=plain(connection.native('SELECT project_code,point_code,aliases FROM workforce_project_travelpoints ORDER BY project_code,point_code').fetchall())
        return jsonify(reference=reference,links=links,token=digest(links),editable=actor['role'] in ADMINS)

    @app.put('/api/workforce/tickets/geography')
    @roles_required(*ADMINS)
    def ticket_geography_save():
        from workforce_core import catalog_value
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('links'),list) or len(data['links'])>300:abort(400,description='Некорректные связи проектов и городов.')
        connection=db()
        with connection:
            connection.execute('BEGIN IMMEDIATE');actor,_,_=actor_scope(connection,write=True)
            if actor['role'] not in ADMINS:abort(403)
            before=plain(connection.native('SELECT project_code,point_code,aliases FROM workforce_project_travelpoints ORDER BY project_code,point_code').fetchall())
            if data.get('token')!=digest(before):abort(409,description='Связи изменились. Откройте настройки заново.')
            links=[];seen=set()
            for row in data['links']:
                if not isinstance(row,dict):abort(400)
                project=catalog_value(connection,row.get('project_code'),'project');point=catalog_value(connection,row.get('point_code'),'travelpoint')
                aliases=row.get('aliases',[])
                if not project or not point or not isinstance(aliases,list) or len(aliases)>30 or (project,point) in seen:abort(400,description='Проверьте проект, пункт и повторяющиеся связи.')
                aliases=[text_value(a,'Название или код города',150,True) for a in aliases];seen.add((project,point))
                links.append({'project_code':project,'point_code':point,'aliases':aliases})
            connection.native('DELETE FROM workforce_project_travelpoints')
            for row in links:connection.native('INSERT INTO workforce_project_travelpoints(project_code,point_code,aliases,updated_by) VALUES (%s,%s,%s,%s)',(row['project_code'],row['point_code'],row['aliases'],actor['id']))
            audit(connection,actor,'update','ticket_geography','all',before,links,reason='Изменены пункты заезда и выезда проектов')
        return jsonify(ok=True)

    @app.post('/api/workforce/tickets/uploads')
    @roles_required(*ROLES)
    def ticket_upload():
        file=request.files.get('file')
        if not file:abort(400,description='Выберите PDF или изображение билета.')
        content=file.stream.read(12_000_001)
        if len(content)>12_000_000:abort(413,description='Один файл должен быть не больше 12 МБ.')
        extension='.pdf' if content.startswith(b'%PDF-') else '.jpg' if content.startswith(b'\xff\xd8\xff') else '.png' if content.startswith(b'\x89PNG\r\n\x1a\n') else ''
        if not extension:abort(400,description='Поддерживаются PDF, JPEG/JFIF и PNG.')
        year=request.form.get('travel_year','')
        if year and (not year.isdigit() or not 2000<=int(year)<=2100):abort(400,description='Проверьте год поездки.')
        connection=db();key=str(uuid4());file_hash=hashlib.sha256(content).hexdigest()
        path=directory()/(key+extension)
        with connection:
            connection.native('BEGIN ISOLATION LEVEL READ COMMITTED');actor,_,_=actor_scope(connection,write=True)
            # A per-account lock makes upload quotas safe across all web workers.
            connection.native('SELECT pg_advisory_xact_lock(%s)',(actor['id'],))
            old=connection.native('''SELECT id,state FROM workforce_ticket_jobs WHERE actor_id=%s
                AND file_hash=%s AND state<>'failed' AND deleted_at IS NULL AND travel_year IS NOT DISTINCT FROM %s
                ORDER BY created_at DESC LIMIT 1''',(actor['id'],file_hash,int(year) if year else None)).fetchone()
            if old:return jsonify(id=str(old['id']),state=old['state'],duplicate=True)
            pending=connection.native("SELECT count(*) FROM workforce_ticket_jobs WHERE state IN ('pending','running') AND deleted_at IS NULL AND actor_id=%s",(actor['id'],)).fetchone()[0]
            if pending>=6:abort(429,description='В очереди уже шесть файлов. Дождитесь обработки.')
            try:
                path.write_bytes(content);path.chmod(0o600)
                connection.native('''INSERT INTO workforce_ticket_jobs(id,actor_id,filename,file_hash,file_extension,travel_year)
                    VALUES (%s,%s,%s,%s,%s,%s)''',(key,actor['id'],(file.filename or 'Билет')[:250],file_hash,extension,int(year) if year else None))
            except Exception:
                path.unlink(missing_ok=True);raise
        return jsonify(id=key,state='pending'),202

    @app.get('/api/workforce/tickets/uploads')
    @roles_required(*ROLES)
    def ticket_upload_list():
        connection=db()
        with connection:
            actor,_,_=actor_scope(connection,write=True)
            rows=connection.native('''SELECT id,filename,state,error,created_at FROM workforce_ticket_jobs
                WHERE actor_id=%s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 200''',(actor['id'],)).fetchall()
        return jsonify(rows=plain(rows))

    @app.delete('/api/workforce/tickets/uploads/<uuid:key>')
    @roles_required(*ROLES)
    def ticket_delete(key):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or data.get('state') not in {'pending','running','ready','failed','applied'}:
            abort(400,description='Обновите список билетов перед удалением.')
        connection=db()
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            actor,_,_=actor_scope(connection,write=True)
            if actor['role'] not in ROLES:abort(403)
            row=owned(connection,key,True)
            if row['state']=='applied' and data['state']!='applied':
                abort(409,description='Билет уже подтверждён. Обновите список и повторите удаление: сохранённая поездка останется в карточке сотрудника.')
            connection.native('UPDATE workforce_ticket_jobs SET deleted_at=now(),deleted_by=%s WHERE id=%s',(actor['id'],key))
            audit(connection,actor,'delete','ticket_upload',key,
                  {k:row[k] for k in ('filename','file_hash','state')},
                  {'deleted':True,'movement_preserved':row['state']=='applied'},
                  reason='Удалено из списка распознавания билетов; история сохранена')
        return jsonify(deleted=str(key),movement_preserved=row['state']=='applied')

    @app.get('/api/workforce/tickets/uploads/<uuid:key>')
    @roles_required(*ROLES)
    def ticket_preview(key):
        connection=db()
        with connection:
            row=owned(connection,key);value=plain({k:row[k] for k in ('id','filename','state','error','travel_year')})
            if row['state']=='ready':
                value['ticket']=translate_airports(row['preview_json']);value['candidates']=recommend(value['ticket'],workers(connection),mappings(connection))
        return jsonify(value)

    @app.get('/api/workforce/tickets/uploads/<uuid:key>/source')
    @roles_required(*ROLES)
    def ticket_source(key):
        connection=db()
        with connection:row=owned(connection,key)
        response=send_file(source_path(row),mimetype={'.pdf':'application/pdf','.jpg':'image/jpeg','.png':'image/png'}[row['file_extension']],download_name=row['filename'])
        response.headers['Cache-Control']='private, no-store';response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="sandbox; default-src 'none'"
        return response

    @app.get('/api/workforce/tickets/workers')
    @roles_required(*ROLES)
    def ticket_workers():
        query=request.args.get('q','').strip()
        if len(query)<2:return jsonify(rows=[])
        connection=db()
        with connection:rows=workers(connection,query[:100])
        return jsonify(rows=[{k:r[k] for k in ('id','full_name','department','personnel_no','project','birth_date')} for r in rows])

    @app.post('/api/workforce/tickets/drafts')
    @roles_required(*ROLES)
    def ticket_drafts():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or not isinstance(payload.get('items'),list) or not 1<=len(payload['items'])<=50:abort(400,description='Передайте от 1 до 50 билетов.')
        items=[confirm_values(value) for value in payload['items']]
        from workforce_core import uuid_value
        job_ids=[uuid_value(item.get('job_id')) for item in items]
        identities=[digest([item['transport'],item['ticket_number'].replace('-','')]) for item in items]
        if len(set(job_ids))!=len(job_ids):abort(400,description='Файл выбран дважды.')
        if len(set(identities))!=len(identities):abort(400,description='Билет с таким номером указан дважды.')
        connection=db()
        with connection:
            connection.execute('BEGIN IMMEDIATE');actor,_,_=actor_scope(connection,write=True)
            result=[None]*len(items)
            for index,item in sorted(enumerate(items),key=lambda row:job_ids[row[0]]):
                _,worker=require_worker(connection,item['worker_id'])
                if not worker['active']:abort(409,description='Сотрудник исключён из состава.')
                recognition_values(connection,actor,item,item['worker_id'],item['direction'])
                result[index]=item
        return jsonify(items=result)

    @app.post('/api/workforce/tickets/confirm')
    @roles_required(*ROLES)
    def ticket_confirm():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or not isinstance(payload.get('items'),list) or not 1<=len(payload['items'])<=50:
            abort(400,description='Отметьте от 1 до 50 билетов.')
        items=[confirm_values(item) for item in payload['items']]
        if len({i.get('job_id') for i in items})!=len(items):abort(400,description='Файл выбран дважды.')
        connection=db();saved=[]
        with connection:
            connection.execute('BEGIN IMMEDIATE');actor,_,_=actor_scope(connection,write=True)
            for item in sorted(items,key=lambda i:str(i.get('job_id',''))):
                from workforce_core import uuid_value
                job=owned(connection,uuid_value(item.get('job_id')),True)
                _,worker=require_worker(connection, item['worker_id'])
                if not worker['active']:abort(409,description='Сотрудник исключён из текущего состава.')
                signature=digest({k:item[k] for k in ('worker_id','direction','transport','passenger','ticket_number','segments')})
                if job['state']=='applied':
                    if job['preview_json'].get('confirmed_digest')!=signature:abort(409,description='Этот билет уже сохранён с другими данными.')
                    saved.append(str(job['id']));continue
                if job['state']!='ready':abort(409,description='Дождитесь окончания обработки.')
                identity=digest([item['transport'],item['ticket_number'].replace('-','')])
                connection.native("SELECT pg_advisory_xact_lock(hashtext(%s))",('ticket:'+identity,))
                if connection.native('SELECT 1 FROM workforce_tickets WHERE identity_key=%s',(identity,)).fetchone():
                    abort(409,description='Билет с таким номером уже сохранён. Повтор или переоформление требует проверки.')
                segments=item['segments'];first,last=segments[0],segments[-1]
                planned=last['arrival_date'] if item['direction']=='arrival' else first['departure_date']
                details=('Авиа' if item['transport']=='air' else 'ЖД')+' · '+item['ticket_number']+' · '+item['passenger']
                for s in segments:details+='\n'+s['flight']+' · '+s['origin']+' → '+s['destination']+' · '+s['departure_date']+' '+(s['departure_time'] or '')+' → '+s['arrival_date']+' '+(s['arrival_time'] or '')
                movement=connection.native('''INSERT INTO workforce_movements(worker_id,direction,destination_kind,
                    planned_date,basis_code,origin,destination,travel_details,notes,request_key,created_by,updated_by)
                    VALUES (%s,%s,%s,%s,'basis.ticket',%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    (worker['id'],item['direction'],'site' if item['direction']=='arrival' else 'home',planned,
                     first['origin'],last['destination'],details[:5000],'Подтверждено после распознавания: '+job['filename'],str(uuid4()),actor['id'],actor['id'])).fetchone()
                ticket=connection.native('''INSERT INTO workforce_tickets(job_id,worker_id,movement_id,passenger,
                    ticket_number,transport,identity_key,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (job['id'],worker['id'],movement['id'],item['passenger'],item['ticket_number'],item['transport'],identity,actor['id'])).fetchone()
                keys=('origin','destination','departure_date','arrival_date','departure_time','arrival_time','flight','coach','seat','timezone_note')
                for index,s in enumerate(segments,1):
                    connection.native('INSERT INTO workforce_ticket_segments(ticket_id,position,'+','.join(keys)+') VALUES ('+','.join(['%s']*12)+')',
                                      [ticket['id'],index,*[s[k] for k in keys]])
                audit(connection,actor,'create','movement',movement['id'],None,movement,worker_id=worker['id'],reason='Подтверждён импорт билета '+job['filename'])
                audit(connection,actor,'create','ticket',ticket['id'],None,{'file_hash':job['file_hash'],'filename':job['filename'],'values':item},worker_id=worker['id'],reason='Подтверждены распознанные реквизиты билета')
                preview={**translate_airports(job['preview_json']),'confirmed_digest':signature,'confirmed':item}
                connection.native("UPDATE workforce_ticket_jobs SET state='applied',preview_json=%s WHERE id=%s",(Jsonb(preview),job['id']))
                saved.append(str(job['id']))
        return jsonify(saved=saved),201


def process_one(app,database):
    from ticket_parser import extract,parse
    with app.app_context():
        db=database()
        with db:
            db.native('BEGIN ISOLATION LEVEL READ COMMITTED')
            db.native("UPDATE workforce_ticket_jobs SET state='failed',error='Обработка прервана. Загрузите файл повторно.' WHERE state='running' AND deleted_at IS NULL AND started_at<now()-interval '15 minutes'")
            job=db.native("UPDATE workforce_ticket_jobs SET state='running',started_at=now() WHERE id=(SELECT id FROM workforce_ticket_jobs WHERE state='pending' AND deleted_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *").fetchone()
        if not job:return False
        try:
            pages,methods=extract(source_path(job));result=parse(pages,job['filename'],methods,job['travel_year'])
            db.native("UPDATE workforce_ticket_jobs SET state='ready',preview_json=%s,finished_at=now() WHERE id=%s AND deleted_at IS NULL",(Jsonb(result),job['id']))
        except Exception as exc:
            db.rollback();message=str(exc) if isinstance(exc,ValueError) else 'Не удалось распознать файл. Проверьте исходный документ.'
            db.native("UPDATE workforce_ticket_jobs SET state='failed',error=%s,finished_at=now() WHERE id=%s AND deleted_at IS NULL",(message[:500],job['id']))
        return True


if __name__=='__main__':
    if os.environ.get('APP_ENVIRONMENT')!='staging' or os.environ.get('DATABASE_BACKEND')!='postgres':
        raise SystemExit('Ticket worker requires the isolated PostgreSQL staging environment.')
    from app import app,get_db
    while True:
        try:found=process_one(app,get_db)
        except Exception:
            logging.exception('Ticket recognition worker iteration failed.');found=False
        if not found:time.sleep(2)
