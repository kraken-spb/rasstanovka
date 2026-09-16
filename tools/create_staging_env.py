"""Generate independent local-only staging credentials without printing secrets."""
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / '.env.staging'
    if path.exists():
        raise SystemExit('The staging environment already exists; no credentials changed.')
    owner, runtime = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    values = {
        'PG_OWNER_PASSWORD': owner,
        'PG_APP_PASSWORD': runtime,
        'ADMIN_DATABASE_URL': f'postgresql://workforce_owner:{owner}@127.0.0.1:55439/workforce',
        'DATABASE_URL': f'postgresql://workforce_app:{runtime}@127.0.0.1:55439/workforce',
        'DATABASE_BACKEND': 'postgres',
        'APP_ENVIRONMENT': 'staging',
        'OUTBOUND_INTEGRATIONS_ENABLED': 'false',
        'SECRET_KEY': secrets.token_urlsafe(48),
        'ADMIN_USERNAME': 'staging-admin',
        'ADMIN_PASSWORD': secrets.token_urlsafe(24),
        'COOKIE_SECURE': 'false',
        'POOL_MIN_SIZE': '2',
        'POOL_MAX_SIZE': '16',
    }
    with path.open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
    path.chmod(0o600)
    print(f'Created {path.name}. Credentials were not printed; production values were not used.')


if __name__ == '__main__':
    main()
