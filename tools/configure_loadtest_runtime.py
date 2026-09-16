"""Prepare private env for the existing load DB; optionally replace its app container."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from psycopg.conninfo import make_conninfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import ROOT, load_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restart',action='store_true')
    args = parser.parse_args()
    load_environment(ROOT/'.env.staging')
    path = ROOT/'output/workforce-loadtest-private.json'
    config = json.loads(path.read_text(encoding='utf-8'))
    if not re.fullmatch('workforce_load_[a-f0-9]{10}',config['database']):raise SystemExit('Invalid load DB.')
    prefix = 'crewplacement-load-'+config['database'].rsplit('_',1)[1]
    if not config['container'].startswith(prefix):raise SystemExit('Container is outside this load fixture.')
    values = {}
    for line in (ROOT/'.env.staging').read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            key,value = line.split('=',1);values[key] = value
    for key in ('DATABASE_URL','ADMIN_DATABASE_URL'):
        values[key] = make_conninfo(values[key],dbname=config['database'])
    env_path = ROOT/'.env.loadtest'
    env_path.write_text('\n'.join(key+'='+value for key,value in values.items())+'\n',encoding='utf-8')
    env_path.chmod(0o600)
    if args.restart:
        environment = os.environ.copy()
        environment['DATABASE_URL'] = make_conninfo(values['DATABASE_URL'],host='postgres',port='5432')
        subprocess.run(['docker','stop',config['container']],check=True,capture_output=True,timeout=40)
        name = prefix+'-'+uuid4().hex[:6]
        result = subprocess.run(['docker','compose','--env-file','.env.staging','-f','compose.staging.yaml',
            'run','-d','--no-deps','--name',name,'-p','127.0.0.1:18091:8000','-e','DATABASE_URL','app'],
            cwd=ROOT,env=environment,capture_output=True,timeout=60)
        if result.returncode:raise RuntimeError('Load-test app failed; previous stopped container retained.')
        config['previous_container'] = config['container'];config['container'] = name
        path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'database':config['database'],'container':config['container'],'env_file':env_path.name}),flush=True)


if __name__ == '__main__':main()
