"""Create a new isolated DB/container and synthetic workload; never overwrite one."""
import argparse
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', help='Existing staging archive inside app container')
    parser.add_argument('--users', type=int, default=200)
    parser.add_argument('--workers', type=int, default=10000)
    parser.add_argument('--reuse-restored-db', help='Retry only a tool-created restored DB whose fixture transaction rolled back')
    args = parser.parse_args()
    if not args.archive.startswith('/app/data/backups/') or '..' in args.archive or not args.archive.endswith('.dump'):
        raise SystemExit('Choose an existing staging backup.')
    if not 1 <= args.users <= 200 or not args.users <= args.workers <= 20000:
        raise SystemExit('Invalid fixture size.')
    load_environment(ROOT / '.env.staging')
    if os.environ.get('APP_ENVIRONMENT') != 'staging':
        raise SystemExit('Staging only.')
    if args.reuse_restored_db and not re.fullmatch('workforce_load_[a-f0-9]{10}', args.reuse_restored_db):
        raise SystemExit('Invalid load-test database name.')
    suffix = args.reuse_restored_db.rsplit('_',1)[1] if args.reuse_restored_db else uuid4().hex[:10]
    target = 'workforce_load_' + suffix
    container = 'crewplacement-load-' + suffix
    owner_dsn = os.environ['ADMIN_DATABASE_URL']
    owner_settings = conninfo_to_dict(owner_dsn)
    if not args.reuse_restored_db:
        with psycopg.connect(owner_dsn, autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
    env = os.environ.copy()
    env['PGPASSWORD'] = owner_settings['password']
    base = ['docker','compose','--env-file','.env.staging','-f','compose.staging.yaml']
    if not args.reuse_restored_db:
        restored = subprocess.run([*base,'exec','-T','-e','PGPASSWORD','app','pg_restore','--exit-on-error','--no-owner','--no-acl',
            '--host=postgres','--username='+owner_settings['user'],'--dbname='+target,args.archive],cwd=ROOT,env=env,capture_output=True,timeout=120)
        if restored.returncode:
            raise RuntimeError('Load-test copy restore failed; new DB retained: ' + target)
    password = secrets.token_urlsafe(28)
    password_hash = generate_password_hash(password)
    accounts = []
    department = 'Строительно-монтажный участок № НАГРУЗКА-' + suffix
    with psycopg.connect(make_conninfo(owner_dsn, dbname=target)) as db:
        if db.execute("SELECT 1 FROM users WHERE username::text LIKE 'load-%' LIMIT 1").fetchone():
            raise RuntimeError('This database already contains a load-test fixture; refusing to append another.')
        db.execute('SET LOCAL statement_timeout=0')
        db.execute('GRANT USAGE ON SCHEMA public TO workforce_app')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO workforce_app')
        db.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO workforce_app')
        db.execute('REVOKE UPDATE,DELETE ON workforce_stage_events,workforce_audit FROM workforce_app')
        category_id, category_name = db.execute('SELECT id,name FROM gdlr_categories WHERE active=1 AND staffing_allowed=1 ORDER BY id LIMIT 1').fetchone()
        subobjects = [row[0] for row in db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 2')]
        if len(subobjects) < 2:
            raise RuntimeError('Two existing subobjects are required for the assignment workload.')
        smu_id = db.execute('INSERT INTO smu_catalog(name) VALUES (%s) RETURNING id',(department,)).fetchone()[0]
        for number in range(args.users):
            username = f'load-{suffix}-{number:03}'
            user_id = db.execute('''INSERT INTO users(username,password_hash,full_name,role,created_at)
                VALUES (%s,%s,%s,'foreman','2026-09-16') RETURNING id''',
                (username,password_hash,f'Нагрузочный пользователь {number+1}')).fetchone()[0]
            db.execute('''INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at)
                VALUES (%s,'selected',%s,%s,%s,'2026-09-16')''',(user_id,json.dumps([department]),uuid4().hex,user_id))
            crew_id = db.execute('''INSERT INTO crews(name,owner_user_id,created_at,details_token)
                VALUES (%s,%s,'2026-09-16',%s) RETURNING id''', (f'НАГРУЗКА {suffix} {number+1:03}',user_id,uuid4().hex)).fetchone()[0]
            accounts.append({'username':username,'password':password,'user_id':user_id,'crew_id':crew_id,'worker_ids':[]})
        # Copy is private to this tool until commit. Runtime schema and constraints
        # stay enabled, including employer/profile synchronization.
        workers = db.execute('''INSERT INTO workers(full_name,personnel_no,personnel_is_internal,contractor,employer,profession,category,department)
            SELECT 'Нагрузочный Сотрудник '||lpad(n::text,5,'0'),%s||n::text,TRUE,'ЛГСС','ЛГСС','Монтажник',%s,%s
            FROM generate_series(1,%s) n RETURNING id,personnel_no::text''',
            ('LOAD-'+suffix+'-',category_name,department,args.workers)).fetchall()
        memberships = []
        for worker_id, personnel in workers:
            account = accounts[(int(personnel.rsplit('-',1)[1])-1) % args.users]
            account['worker_ids'].append(worker_id)
            memberships.append((account['crew_id'],worker_id))
        with db.cursor().copy('COPY crew_members(crew_id,worker_id) FROM STDIN') as copy:
            for row in memberships:
                copy.write_row(row)
        worker_ids = [worker[0] for worker in workers]
        db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
            SELECT m.worker_id,%s,gen_random_uuid()::text,c.owner_user_id,'2026-09-16'
            FROM crew_members m JOIN crews c ON c.id=m.crew_id WHERE m.worker_id=ANY(%s)''',(category_id,worker_ids))
        db.execute('''INSERT INTO employee_smu(worker_id,smu_id,source_department)
            SELECT id,%s,%s FROM workers WHERE id=ANY(%s)''',(smu_id,department,worker_ids))
        db.execute("UPDATE workforce_profiles SET staffing_ready=TRUE,workforce_managed=TRUE,employment_code='employment.staff' WHERE worker_id=ANY(%s)",(worker_ids,))
        db.execute('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
            SELECT m.worker_id,'stage.onsite','2026-09-01',TRUE,'Синтетический нагрузочный сценарий',c.owner_user_id,gen_random_uuid()
            FROM crew_members m JOIN crews c ON c.id=m.crew_id WHERE m.worker_id=ANY(%s)''',(worker_ids,))
        db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token)
            SELECT '2026-09-15','1 смена',%s,m.worker_id,'ЛГСС',c.owner_user_id,'2026-09-16',m.crew_id,gen_random_uuid()::text
            FROM crew_members m JOIN crews c ON c.id=m.crew_id WHERE m.worker_id=ANY(%s)''',(subobjects[0],worker_ids))
        total = db.execute('SELECT count(*) FROM workers').fetchone()[0]
    with psycopg.connect(make_conninfo(owner_dsn, dbname=target),autocommit=True) as db:
        db.execute('SET statement_timeout=0')
        db.execute('ANALYZE')
    app_settings = conninfo_to_dict(os.environ['DATABASE_URL'])
    env['DATABASE_URL'] = make_conninfo(**{**app_settings,'host':'postgres','port':'5432','dbname':target})
    started = subprocess.run([*base,'run','-d','--no-deps','--name',container,'-p','127.0.0.1:18091:8000','-e','DATABASE_URL','app'],
                             cwd=ROOT,env=env,capture_output=True,timeout=60)
    if started.returncode:
        raise RuntimeError('Load-test container failed to start; DB retained: ' + target)
    config = {'database':target,'container':container,'url':'http://127.0.0.1:18091','workers_total':total,
              'synthetic_workers':args.workers,'department':department,'subobjects':subobjects,'accounts':accounts}
    path = ROOT / 'output/workforce-loadtest-private.json'
    path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    path.chmod(0o600)
    print(json.dumps({k:v for k,v in config.items() if k not in ('accounts','subobjects')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
