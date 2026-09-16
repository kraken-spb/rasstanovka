"""Measure one authenticated staffing read without logging personnel values."""
import cProfile
import argparse
import json
import os
from pathlib import Path
import pstats
import sys
import time
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment
parser = argparse.ArgumentParser()
parser.add_argument('--path', default='/api/staffing?date=2026-09-15&shift=all')
parser.add_argument('--output', default='staffing-postgres-profile.txt')
parser.add_argument('--env', type=Path, default=ROOT / '.env.staging')
parser.add_argument('--load-user', type=int)
parser.add_argument('--explain', action='store_true')
arguments = parser.parse_args()
load_environment(arguments.env)
import app
from postgres_db import PostgresConnection

client = app.app.test_client()
client.get('/login')
with client.session_transaction() as session:
    login_token = session['csrf_token']
credentials = {'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']}
measure_save = False
if arguments.load_user is not None:
    private = json.loads((ROOT / 'output/workforce-loadtest-private.json').read_text(encoding='utf-8'))
    account = private['accounts'][arguments.load_user]
    credentials = {key: account[key] for key in ('username', 'password')}
    if arguments.path in ('crew', 'save'):
        from urllib.parse import urlencode
        measure_save = arguments.path == 'save'
        arguments.path = '/api/staffing?' + urlencode({'date':'2026-09-15','shift':'all','crew_id':account['crew_id']})
    elif arguments.path == 'registry':
        from urllib.parse import urlencode
        arguments.path = '/api/workforce/people?' + urlencode({'date':'2026-09-15','department':private['department'],'offset':arguments.load_user*50})
assert client.post('/login', data={**credentials, 'csrf_token': login_token}).status_code == 302
url = arguments.path
def invoke():
    if measure_save:
        snapshot = client.get(url).get_json()
        row = next(row for row in snapshot['rows'] if row['id'] == account['worker_ids'][0])
        with client.session_transaction() as state:
            csrf = state['csrf_token']
        target = private['subobjects'][1] if row['subobject_id'] == private['subobjects'][0] else private['subobjects'][0]
        return client.put('/api/staffing/crews/'+str(account['crew_id'])+'/assignments',json={
            'date':'2026-09-15','worker_ids':[row['id']],'subobject_id':target,
            'expected_tokens':{str(row['id']):row['day_token']}},headers={'X-CSRF-Token':csrf})
    return client.get(url)
assert invoke().status_code == 200
queries = defaultdict(lambda: {'count': 0, 'seconds': 0})
samples = {}
original = PostgresConnection._run

def measured(self, query, parameters=()):
    started = time.perf_counter()
    try:
        return original(self, query, parameters)
    finally:
        item = queries[query]
        samples[query] = parameters
        item['count'] += 1
        item['seconds'] += time.perf_counter() - started

PostgresConnection._run = measured
profile = cProfile.Profile()
profile.enable()
response = invoke()
profile.disable()
PostgresConnection._run = original
output = ROOT / 'output' / Path(arguments.output).name
with output.open('w', encoding='utf-8') as stream:
    pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(45)
    for query, item in sorted(queries.items(), key=lambda item: item[1]['seconds'], reverse=True)[:15]:
        stream.write(json.dumps({**item, 'query_prefix': query[:180]}, ensure_ascii=False) + '\n')
print(json.dumps({'status': response.status_code, 'query_count': sum(item['count'] for item in queries.values()), 'profile': str(output)}))
if arguments.explain:
    plans = []
    reader = PostgresConnection()
    try:
        for query, item in sorted(queries.items(), key=lambda entry:entry[1]['seconds'],reverse=True)[:50]:
            if not query.startswith(('SELECT', 'UPDATE', 'INSERT')) or 'user_sessions' in query or 'login_rate_limits' in query:
                continue
            plan = reader.native('EXPLAIN (FORMAT JSON) '+query,samples[query]).fetchone()[0]
            plans.append({'query':query,'plan':plan})
        output.with_suffix('.plans.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        reader.close()
