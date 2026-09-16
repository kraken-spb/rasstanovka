"""Authenticated read checks against the isolated PostgreSQL application."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.migrate_sqlite_to_postgres import load_environment, ROOT

load_environment(ROOT / '.env.staging')
import app

app.app.config['TESTING'] = True
client = app.app.test_client()
client.get('/login')
with client.session_transaction() as session:
    login_token = session['csrf_token']
login = client.post('/login', data={'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD'], 'csrf_token': login_token})
if login.status_code != 302:
    raise SystemExit('Staging authentication failed.')
results = []
for path in ['/health', '/', '/api/workforce/reference', '/api/workforce/people?date=2026-09-15', '/api/reference', '/api/staffing/people',
             '/api/staffing?date=2026-09-15&shift=all', '/api/employees?date=2026-09-15',
             '/api/crews', '/api/staffing/crew-options', '/api/gdlr-categories', '/api/contractors',
             '/api/calendar?start=2026-09-15&days=1', '/api/personnel-dashboard?date=2026-09-15',
             '/api/placement-report?date=2026-09-15', '/api/logs', '/api/logs?q=Иванов',
             '/api/staffing/export/options?date=2026-09-15&shift=all', '/api/user-activity?date=2026-09-15']:
    started = time.perf_counter()
    try:
        response = client.get(path)
        result = {'path': path, 'status': response.status_code, 'ms': round((time.perf_counter() - started) * 1000), 'bytes': len(response.data)}
    except Exception as error:
        result = {'path': path, 'error': type(error).__name__, 'detail': str(error)}
        traceback.print_exc(limit=5)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    results.append(result)
(ROOT / 'output/postgres-route-checks.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
raise SystemExit(any('error' in item or item.get('status', 500) >= 500 for item in results))
