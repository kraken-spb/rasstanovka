"""Real HTTP auth, CSRF, scope, simultaneous edits and undo on the load fixture only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import re
import secrets
import sys
import threading
from urllib.parse import urlencode
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from werkzeug.security import generate_password_hash

from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment
from tools.run_workforce_loadtest import User


def main():
    load_environment(ROOT/'.env.staging')
    config=json.loads((ROOT/'output/workforce-loadtest-private.json').read_text(encoding='utf-8'))
    if os.environ.get('APP_ENVIRONMENT')!='staging' or not re.fullmatch('workforce_load_[a-f0-9]{10}',config['database']):
        raise SystemExit('Only the synthetic local load database is allowed.')
    dsn=make_conninfo(os.environ['ADMIN_DATABASE_URL'],dbname=config['database'])
    prefix='http-security-'+uuid4().hex[:12]
    users, ids, results={},[],[]
    def check(name,user,method,path,body=None,expected=200):
        status,content,elapsed=user.request(method,path,body)
        results.append({'case':name,'status':status,'expected':expected,'ms':round(elapsed,2)})
        if status!=expected:raise AssertionError(name+': expected '+str(expected)+', got '+str(status))
        return json.loads(content) if content.startswith((b'{',b'[')) else content
    try:
        with psycopg.connect(dsn) as db:
            for key,role in [('super','super_admin'),('admin','admin'),('rotation','rotation'),('recruitment','recruitment'),('first','foreman'),('second','foreman'),('hr','hr_viewer')]:
                account={'username':prefix+'-'+key,'password':secrets.token_urlsafe(28)}
                identifier=db.execute('''INSERT INTO users(username,password_hash,full_name,role,created_at)
                    VALUES (%s,%s,'Проверка HTTP',%s,%s) RETURNING id''',(account['username'],generate_password_hash(account['password']),role,datetime.now(timezone.utc).isoformat())).fetchone()[0]
                ids.append(identifier);account['id']=identifier
                if role!='super_admin':
                    db.execute('INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (%s,\'selected\',%s,%s,%s,%s)',
                        (identifier,json.dumps([config['department']]),uuid4().hex,identifier,datetime.now(timezone.utc).isoformat()))
                users[key]=User(config['url'],account)
            outside=db.execute('SELECT id FROM workers WHERE department<>%s LIMIT 1',(config['department'],)).fetchone()[0]
        anonymous=User(config['url'],{})
        check('unauthenticated registry',anonymous,'GET','/api/workforce/people',expected=401)
        check('login requires CSRF',anonymous,'POST','/login',{},expected=403)
        for user in users.values():user.login()
        crew=config['accounts'][0]['crew_id'];worker=config['accounts'][0]['worker_ids'][-1]
        path='/api/staffing?'+urlencode({'date':'2026-09-15','shift':'all','crew_id':crew})
        csrf=users['first'].csrf;users['first'].csrf=None
        check('authenticated write requires CSRF',users['first'],'POST','/api/workforce/export-jobs',{},expected=403)
        users['first'].csrf=csrf
        for role in ('first','admin','rotation','recruitment'):
            check('out-of-scope person '+role,users[role],'GET',f'/api/workforce/people/{outside}',expected=404)
        check('staffing cannot create employee',users['first'],'POST','/api/employees',{},expected=403)
        check('staffing cannot edit master card',users['first'],'PATCH',f'/api/workforce/people/{worker}/profile',{},expected=403)
        check('recruitment cannot confirm site movement',users['recruitment'],'POST',f'/api/workforce/people/{worker}/movement',
            {'direction':'arrival','planned_date':'2030-01-01','reason':'Проверка прав','request_key':str(uuid4())},expected=403)
        check('rotation cannot edit recruitment documents',users['rotation'],'POST',f'/api/workforce/people/{worker}/document',
            {'document_code':'document.patent','reason':'Проверка прав','request_key':str(uuid4())},expected=403)
        card=check('staffing card is private-data limited',users['first'],'GET',f'/api/workforce/people/{worker}')
        assert not {'phone','birth_date','notes'}&set(card['profile']) and card['sources']==[] and card['documents']==[]
        job_key=str(uuid4())
        check('HR viewer can request private export',users['hr'],'POST','/api/workforce/export-jobs',
              {'date':'2026-09-15','request_key':job_key},expected=202)
        check('HR viewer can read export status',users['hr'],'GET',f'/api/workforce/export-jobs/{job_key}')
        check('HR viewer download waits for report',users['hr'],'GET',f'/api/workforce/export-jobs/{job_key}/download',expected=409)
        check('HR viewer still cannot edit personnel',users['hr'],'PATCH',f'/api/workforce/people/{worker}/profile',{},expected=403)
        before=check('staffing read',users['first'],'GET',path)
        row=next(row for row in before['rows'] if row['id']==worker)
        target=config['subobjects'][1] if row['subobject_id']==config['subobjects'][0] else config['subobjects'][0]
        body={'date':'2026-09-15','worker_ids':[worker],'subobject_id':target,'expected_tokens':{str(worker):row['day_token']}}
        barrier=threading.Barrier(2)
        def save(user):
            barrier.wait()
            return user.request('PUT',f'/api/staffing/crews/{crew}/assignments',body)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses=list(executor.map(save,[users['first'],users['second']]))
        results.append({'case':'simultaneous stale-token saves','statuses':statuses,'expected':[200,409]})
        assert sorted(statuses)==[200,409],statuses
        winner=users['first'] if statuses[0]==200 else users['second']
        history=check('assignment undo available',winner,'GET','/api/staffing/history')['undo']
        assert history
        check('undo assignment',winner,'POST','/api/staffing/history/undo',{'id':history['id'],'token':history['token']})
        undone=check('staffing after undo',winner,'GET',path)
        assert next(item for item in undone['rows'] if item['id']==worker)['subobject_id']==row['subobject_id']
        history=check('assignment redo available',winner,'GET','/api/staffing/history')['redo']
        check('redo assignment',winner,'POST','/api/staffing/history/redo',{'id':history['id'],'token':history['token']})
        role_body={'role':'rotation','expected_role':'foreman'}
        check('change role through account API',users['super'],'PATCH',f'/api/users/{users["first"].account["id"]}',role_body)
        check('role change invalidates old login',users['first'],'GET','/api/workforce/people',expected=401)
        check('stale role change rejected',users['super'],'PATCH',f'/api/users/{users["first"].account["id"]}',role_body,expected=409)
        check('disable account',users['super'],'PATCH',f'/api/users/{users["second"].account["id"]}',{'active':False})
        check('disabled login denied',users['second'],'GET','/api/workforce/people',expected=401)
    finally:
        with psycopg.connect(dsn) as db:
            db.execute("UPDATE users SET active=0,password_hash='disabled-test-fixture' WHERE id=ANY(%s) AND username LIKE %s",(ids,prefix+'%'))
            db.execute('UPDATE user_sessions SET ended_at=%s WHERE user_id=ANY(%s) AND ended_at IS NULL',(int(datetime.now(timezone.utc).timestamp()),ids))
        (ROOT/'output/workforce-http-security.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(results),'report':'output/workforce-http-security.json'}))


if __name__=='__main__':main()
