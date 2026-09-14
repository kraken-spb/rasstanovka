"""Start an isolated, disposable image with no mounted host data and verify it."""
import argparse
from http.cookiejar import CookieJar
import json
import secrets
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def check_health_probes(name, client, base):
    # Exercise the normal Linux worker's lock and disabled-bot polling path too.
    docker('exec', name, 'python', '-c', '''
from unittest.mock import patch
import telegram_worker
with patch('telegram_worker.time.sleep', side_effect=SystemExit(0)) as pause:
    try:
        telegram_worker.main()
    except SystemExit as result:
        assert result.code == 0
    pause.assert_called_once_with(3)
''')
    fingerprint = r'''
import hashlib
from database_health import database_path, open_readonly
with open_readonly(database_path()) as db:
    print(hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest())
'''
    before = docker('exec', name, 'python', '-c', fingerprint)
    for path in ('/health', '/ready'):
        with client.open(base + path, timeout=3) as response:
            assert json.load(response) == {'status': 'ok'}
            assert response.headers['Cache-Control'] == 'no-store'
    # Missing startup secrets prove that this CLI path does not import app.py.
    docker('exec', '--env', 'ADMIN_PASSWORD=', '--env', 'SECRET_KEY=', name,
           'python', 'telegram_worker.py', '--healthcheck')
    assert docker('exec', name, 'python', '-c', fingerprint) == before

    # Only this disposable container's synthetic database is moved, never host data.
    move = 'from database_health import database_path; p=database_path(); p.rename(p.with_suffix(".health-backup"))'
    restore = 'from database_health import database_path; p=database_path(); p.with_suffix(".health-backup").rename(p)'
    docker('exec', name, 'python', '-c', move)
    try:
        with client.open(base + '/health', timeout=3) as response:
            assert json.load(response) == {'status': 'ok'}
        try:
            client.open(base + '/ready', timeout=3)
            raise AssertionError('Missing database was reported ready')
        except HTTPError as error:
            assert error.code == 503, error.code
            assert json.load(error)['status'] == 'unavailable'
        result = subprocess.run(['docker', 'exec', name, 'python', 'telegram_worker.py', '--healthcheck'],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 1, result.stderr
        assert result.stdout + result.stderr == '', 'Healthcheck must not expose errors or secrets'
        docker('exec', name, 'python', '-c',
               'from database_health import database_path; assert not database_path().exists()')
    finally:
        docker('exec', name, 'python', '-c', restore)
    with client.open(base + '/ready', timeout=3) as response:
        assert json.load(response) == {'status': 'ok'}
    assert docker('exec', name, 'python', '-c', fingerprint) == before


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image', help='Already built image tag or digest')
    args = parser.parse_args()
    name = 'crewplacement-check-' + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    started = False
    try:
        docker('run', '--detach', '--rm', '--name', name, '--publish', '127.0.0.1::8000',
               '--env', 'ADMIN_USERNAME=image-check', '--env', 'ADMIN_PASSWORD=' + password,
               '--env', 'SECRET_KEY=' + secrets.token_urlsafe(40), args.image)
        started = True
        port = docker('port', name, '8000/tcp').rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        deadline = time.monotonic() + 45
        while True:
            try:
                with client.open(base + '/health', timeout=2) as response:
                    assert json.load(response)['status'] == 'ok'
                break
            except (URLError, TimeoutError, ConnectionError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Image did not become healthy')
                time.sleep(1)
        try:
            client.open(base + '/api/reference', timeout=5)
            raise AssertionError('Anonymous API request was not denied')
        except HTTPError as error:
            assert error.code == 401, error.code
        with client.open(base + '/login', urlencode({'username': 'image-check', 'password': password}).encode(), timeout=10) as response:
            assert response.geturl().rstrip('/') == base
            assert 'Сводная таблица' in response.read().decode('utf-8')
        with client.open(base + '/api/reference', timeout=5) as response:
            assert 'objects' in json.load(response)
        with client.open(base + '/api/placement-report?date=2026-09-13', timeout=5) as response:
            assert json.load(response)['totals']['total'] == 0
        check_health_probes(name, client, base)
        print('Fresh image: liveness, readiness, read-only Telegram healthcheck, database recovery, access control, login, reference and empty report OK')
    finally:
        if started:
            subprocess.run(['docker', 'stop', name], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
