"""Authenticated HTTP workload with explicit think time and no error retries."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit

import psycopg
from psycopg.conninfo import make_conninfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment


class User:
    def __init__(self, base, account):
        parsed = urlsplit(base)
        if parsed.hostname != '127.0.0.1' or parsed.port != 18091 or parsed.scheme != 'http':
            raise ValueError('Only the isolated local load-test endpoint is allowed.')
        self.host, self.port, self.account = parsed.hostname,parsed.port,account
        self.connection = None
        self.cookie = SimpleCookie()
        self.csrf = None

    def request(self, method, path, data=None, form=False):
        if self.connection is None:
            self.connection = HTTPConnection(self.host,self.port,timeout=30)
        body = urlencode(data).encode() if form else json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        headers = {'Cookie':'; '.join(f'{key}={morsel.value}' for key,morsel in self.cookie.items())}
        if body:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        if self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        start = time.perf_counter()
        try:
            self.connection.request(method,path,body,headers)
            response = self.connection.getresponse()
            content = response.read()
            for key,value in response.getheaders():
                if key.casefold() == 'set-cookie':self.cookie.load(value)
            return response.status,content,(time.perf_counter()-start)*1000
        except Exception as error:
            self.connection.close();self.connection = None
            return 0,type(error).__name__.encode(),(time.perf_counter()-start)*1000

    def login(self):
        status,page,_ = self.request('GET','/login')
        if status != 200:raise RuntimeError('Login form unavailable: '+str(status))
        token = re.search(rb'name="csrf_token" value="([^"]+)"',page)
        if not token:raise RuntimeError('Login CSRF missing.')
        self.csrf = token[1].decode()
        status,_,_ = self.request('POST','/login',{'username':self.account['username'],'password':self.account['password'],'csrf_token':self.csrf},form=True)
        if status != 302:raise RuntimeError('Load-test login failed: '+str(status))
        status,page,_ = self.request('GET','/')
        token = re.search(rb'data-csrf="([^"]+)"',page)
        if status != 200 or not token:raise RuntimeError('Authenticated app page unavailable.')
        self.csrf = token[1].decode()
        return self


def percentile(values, fraction):
    values = sorted(values)
    return round(values[max(0,math.ceil(len(values)*fraction)-1)],2) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--output',default='workforce-loadtest-results.json')
    parser.add_argument('--think-ms',type=int,default=0)
    parser.add_argument('--warm-up',action='store_true')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or not 1 <= args.runs <= 3:raise SystemExit('Use up to three 60-second runs.')
    if not 0 <= args.think_ms <= 30000:raise SystemExit('Think time must be 0..30000 ms.')
    config = json.loads((ROOT/'output/workforce-loadtest-private.json').read_text(encoding='utf-8'))
    if not re.fullmatch('workforce_load_[a-f0-9]{10}',config['database']):raise SystemExit('Invalid isolated fixture.')
    load_environment(ROOT/'.env.staging')
    if os.environ.get('APP_ENVIRONMENT') != 'staging':raise SystemExit('Staging only.')
    users = [User(config['url'],account) for account in config['accounts']]
    sessions_path = ROOT/'output/workforce-loadsessions-private.json'
    saved = json.loads(sessions_path.read_text(encoding='utf-8')) if sessions_path.exists() else {}
    def authenticate(user):
        session = saved.get(user.account['username']) if saved.get('database') == config['database'] else None
        if session:
            user.cookie.load(session['cookie']);user.csrf = session['csrf']
            status,_,_ = user.request('GET','/api/preferences/staffing')
            if status == 200:return
            user.cookie = SimpleCookie();user.csrf = None
        user.login()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(authenticate,users))
    saved = {'database':config['database'],**{user.account['username']:{'cookie':user.cookie.output(header='',sep=';'),'csrf':user.csrf} for user in users}}
    sessions_path.write_text(json.dumps(saved),encoding='utf-8');sessions_path.chmod(0o600)
    if args.warm_up:
        def warm(pair):
            number,user = pair
            for path in ['/api/workforce/people?'+urlencode({'date':'2026-09-15','department':config['department'],'offset':number*50,'limit':50}),
                         '/api/staffing?'+urlencode({'date':'2026-09-15','shift':'all','crew_id':user.account['crew_id']})]:
                status,_,_ = user.request('GET',path)
                if status != 200:raise RuntimeError('Warm-up failed: '+str(status))
        with ThreadPoolExecutor(max_workers=8) as executor:list(executor.map(warm,enumerate(users)))
    print(json.dumps({'authenticated_users':len(users),'workers':config['workers_total'],'think_time_ms':args.think_ms,'warm_up':args.warm_up}),flush=True)
    report = {'database':config['database'],'container':config['container'],'users':len(users),'workers':config['workers_total'],
              'synthetic_workers':config['synthetic_workers'],'seconds':args.seconds,'think_time_ms':args.think_ms,'warm_up':args.warm_up,'save_endpoint':'/api/staffing/crews/<id>/assignments','runs':[]}
    for run in range(args.runs):
        barrier = threading.Barrier(len(users)+1)
        metrics, failures = [], []
        deadline = [None]
        def work(pair):
            number,user = pair
            account = user.account;iteration = 0
            worker = account['worker_ids'][0]
            crew_path = '/api/staffing?' + urlencode({'date':'2026-09-15','shift':'all','crew_id':account['crew_id']})
            registry_path = '/api/workforce/people?' + urlencode({'date':'2026-09-15','department':config['department'],'offset':number*50,'limit':50})
            local_metrics,local_failures = [],[]
            barrier.wait()
            if args.think_ms:
                time.sleep(number / len(users) * args.think_ms / 1000)
            while time.perf_counter() < deadline[0]:
                if iteration % 2 == 0:
                    status,content,elapsed = user.request('GET',registry_path)
                    local_metrics.append(('registry',status,elapsed,len(content)))
                    if status != 200:local_failures.append(('registry',status,content[:300].decode(errors='replace')))
                status,content,elapsed = user.request('GET',crew_path)
                local_metrics.append(('crew',status,elapsed,len(content)))
                if status != 200:
                    local_failures.append(('crew',status,content[:300].decode(errors='replace')));iteration += 1;continue
                body = json.loads(content)
                row = next((item for item in body['rows'] if item['id'] == worker),None)
                if row is None:
                    local_failures.append(('fixture',0,'Worker missing from scoped crew'));break
                target = config['subobjects'][1] if row['subobject_id'] == config['subobjects'][0] else config['subobjects'][0]
                payload = {'date':'2026-09-15','worker_ids':[worker],'subobject_id':target,'expected_tokens':{str(worker):row['day_token']}}
                status,content,elapsed = user.request('PUT',f'/api/staffing/crews/{account["crew_id"]}/assignments',payload)
                local_metrics.append(('save',status,elapsed,len(content)))
                if status != 200:local_failures.append(('save',status,content[:300].decode(errors='replace')))
                iteration += 1
                if args.think_ms:
                    time.sleep(min(args.think_ms/1000,max(0,deadline[0]-time.perf_counter())))
            metrics.extend(local_metrics);failures.extend(local_failures)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(users)) as executor:
            futures = [executor.submit(work,pair) for pair in enumerate(users)]
            deadline[0] = time.perf_counter()+args.seconds
            barrier.wait()
            for future in futures:future.result()
        elapsed = time.perf_counter()-started
        grouped = defaultdict(list)
        for kind,status,latency,size in metrics:grouped[kind].append((status,latency,size))
        result = {'run':run+1,'wall_seconds':round(elapsed,2),'requests':len(metrics),'endpoints':{},
                  'errors':dict(Counter(f'{kind}:{status}' for kind,status,_ in failures)),
                  'error_samples':[{'kind':kind,'status':status,'detail':detail} for kind,status,detail in failures[:8]]}
        for kind,values in grouped.items():
            latencies = [value[1] for value in values]
            result['endpoints'][kind] = {'count':len(values),'status':dict(Counter(str(value[0]) for value in values)),
                'p50_ms':percentile(latencies,.5),'p95_ms':percentile(latencies,.95),'p99_ms':percentile(latencies,.99),
                'average_bytes':round(sum(value[2] for value in values)/len(values))}
        report['runs'].append(result)
        (ROOT/'output'/Path(args.output).name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='error_samples'},ensure_ascii=False),flush=True)
    with psycopg.connect(make_conninfo(os.environ['ADMIN_DATABASE_URL'],dbname=config['database'])) as db:
        duplicates = db.execute('''SELECT count(*) FROM (SELECT worker_id,work_date,
            CASE WHEN shift='Ночная смена' THEN '2 смена' ELSE shift END shift FROM assignments
            GROUP BY 1,2,3 HAVING count(*)>1) duplicated''').fetchone()[0]
    report['duplicate_assignments'] = duplicates
    report['passed'] = not duplicates and all(not result['errors'] and all(
        entry['p95_ms'] <= (500 if kind=='save' else 250) for kind,entry in result['endpoints'].items()) for result in report['runs'])
    (ROOT/'output'/Path(args.output).name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'duplicate_assignments':duplicates,'report':str(ROOT/'output'/Path(args.output).name)}),flush=True)


if __name__ == '__main__':main()
