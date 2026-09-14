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
        print('Fresh image: health, access control, login, reference and empty report OK')
    finally:
        if started:
            subprocess.run(['docker', 'stop', name], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
