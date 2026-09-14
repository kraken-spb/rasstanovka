"""Explicitly promote an existing active administrator, with a backup."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description='Назначить существующего администратора супер-администратором.')
    parser.add_argument('--username', required=True)
    args = parser.parse_args()
    from app import app, get_db
    from user_roles import promote_super_admin
    with app.app_context():
        try:
            backup = promote_super_admin(get_db(), args.username)
        except ValueError as error:
            parser.exit(1, str(error) + '\n')
    print('Роль: супер-администратор. ' + (f'Резервная копия: {backup}' if backup else 'Роль уже назначена.'))


if __name__ == '__main__':
    main()
