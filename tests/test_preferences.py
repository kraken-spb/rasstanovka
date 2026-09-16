import importlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PreferencesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                                    'ADMIN_PASSWORD': 'preferences-test-password-123',
                                    'SECRET_KEY': 'preferences-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'preferences.db'
        self.clients = {}
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            for role in ('admin', 'foreman', 'viewer'):
                user_id = self.admin if role == 'admin' else db.execute('''INSERT INTO users
                    (username,password_hash,full_name,role,created_at) VALUES (?,'unused',?,?, 'now')''',
                    (role, 'Пользователь ' + role, role)).lastrowid
                self.clients[role] = self.client_for(user_id)
            db.commit()

    def client_for(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = user_id
            session['csrf_token'] = 'preferences-csrf'
        return client

    def save(self, data, role='admin', csrf=True):
        return self.clients[role].patch('/api/preferences/staffing', json=data,
                                       headers={'X-CSRF-Token': 'preferences-csrf'} if csrf else {})

    def test_account_isolation_new_session_and_initial_page(self):
        values = {'groupMode': 'itr', 'columns': {'hidden': ['employer'], 'widths': {'name': 320, 'itr': 220}},
                  'category': 'name:Монтажник', 'department': 'СМУ 15.1', 'employer': 'name:ЛГСС', 'contractor': 'name:Подрядчик', 'unassigned': True, 'shift': '2 смена'}
        self.assertEqual(self.save(values).status_code, 200)
        fresh = self.client_for(self.admin)
        self.assertEqual(fresh.get('/api/preferences/staffing').get_json()['settings'], values)

        page = fresh.get('/').get_data(as_text=True)
        embedded = re.search(r'<script id="staffing-preferences-data" type="application/json">(.*?)</script>', page, re.S)
        self.assertEqual(json.loads(embedded[1]), values)
        self.assertEqual(self.clients['foreman'].get('/api/preferences/staffing').get_json()['settings'], {})
        self.assertEqual(self.save({'groupMode': 'crew'}, role='foreman').status_code, 200)
        self.assertEqual(fresh.get('/api/preferences/staffing').get_json()['settings'], values)

    def test_moscow_midnight_and_explicit_date_survive_new_login(self):
        from datetime import datetime, UTC
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime(2026, 9, 13, 23, 15, tzinfo=UTC)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        with patch.object(self.module, 'datetime', Clock):
            page = self.clients['admin'].get('/').get_data(as_text=True)
            self.assertIn('data-today="2026-09-14"', page)
            self.assertEqual(self.save({'date': '2026-09-16'}).status_code, 200)
            fresh = self.client_for(self.admin)
            page = fresh.get('/').get_data(as_text=True)
            embedded = re.search(r'<script id="staffing-preferences-data" type="application/json">(.*?)</script>', page, re.S)
            self.assertEqual(json.loads(embedded[1])['date'], '2026-09-16')
            self.assertNotIn('date', self.clients['foreman'].get('/api/preferences/staffing').get_json()['settings'])
    def test_partial_updates_reset_columns_and_restart(self):
        self.save({'groupMode': 'itr', 'columns': {'hidden': [], 'widths': {'name': 320}}})
        self.save({'unassigned': True})
        self.save({'columns': {'hidden': [], 'widths': {}}})
        with self.module.app.app_context(): self.module.init_db()
        result = self.clients['admin'].get('/api/preferences/staffing').get_json()['settings']
        self.assertEqual(result, {'groupMode': 'itr', 'unassigned': True, 'columns': {'hidden': [], 'widths': {}}})

    def test_freshness_filter_is_saved_and_validated(self):
        self.assertEqual(self.save({'freshness': 'inherited'}).status_code, 200)
        self.assertEqual(self.save({'freshness': 'invalid'}).status_code, 400)
        self.assertEqual(self.clients['admin'].get('/api/preferences/staffing').get_json()['settings']['freshness'], 'inherited')

    def test_validation_rejects_entire_patch_and_never_writes_placement(self):
        self.save({'groupMode': 'itr'})
        invalid = [None, [], {}, {'user_id': 2}, {'date': '2001-02-30'}, {'groupMode': 'invalid'},
                   {'unassigned': 1}, {'columns': {'hidden': ['unknown'], 'widths': {}}},
                   {'columns': {'hidden': [], 'widths': {'name': 801}}},
                   {'columns': {'hidden': [], 'widths': {'name': True}}},
                   {'groupMode': 'crew', 'search': 'x' * 301}, {'employer': 1}, {'employer': 'x' * 501}, {'contractor': 1}, {'contractor': 'x' * 501}]
        for payload in invalid:
            with self.subTest(payload=payload): self.assertEqual(self.save(payload).status_code, 400)
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignment_events').fetchone()[0], 0)
        self.assertEqual(self.clients['admin'].get('/api/preferences/staffing').get_json()['settings'], {'groupMode': 'itr'})

    def test_date_all_shifts_and_filter_reset_survive_new_session(self):
        columns = {'hidden': ['employer'], 'widths': {'name': 320}}
        self.assertEqual(self.save({'date': '2024-02-29', 'shift': '2 смена', 'groupMode': 'itr',
            'department': 'СМУ 15.1', 'category': 'name:Монтажник', 'author': '12',
            'search': 'Монтаж.*', 'regexMode': True, 'unassigned': True, 'columns': columns}).status_code, 200)
        # "All shifts" is sent as an empty value by the select, including an outbox replay.
        self.assertEqual(self.save({'shift': '', 'search': 'Сварщик'}).status_code, 200)
        fresh = self.client_for(self.admin)
        saved = fresh.get('/api/preferences/staffing').get_json()['settings']
        self.assertEqual((saved['shift'], saved['search'], saved['date']), ('', 'Сварщик', '2024-02-29'))
        defaults = {'department': '', 'category': '', 'author': '', 'unassigned': False,
                    'search': '', 'regexMode': False, 'shift': '', 'freshness': ''}
        self.assertEqual(self.save(defaults).status_code, 200)
        saved = fresh.get('/api/preferences/staffing').get_json()['settings']
        self.assertEqual({key: saved[key] for key in defaults}, defaults)
        self.assertEqual((saved['date'], saved['groupMode'], saved['columns']), ('2024-02-29', 'itr', columns))
        for invalid in ('2023-02-29', '2024-2-29', '20240229', '', None, 20240229, {}, []):
            with self.subTest(date=invalid):
                self.assertEqual(self.save({'date': invalid, 'search': 'не сохранять'}).status_code, 400)
        self.assertEqual(fresh.get('/api/preferences/staffing').get_json()['settings'], saved)
        self.assertEqual(self.clients['foreman'].get('/api/preferences/staffing').get_json()['settings'], {})
        with self.module.app.app_context():
            self.assertEqual(self.module.get_db().execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 0)

    def test_auth_csrf_and_safe_embedded_search(self):
        self.assertEqual(self.module.app.test_client().get('/api/preferences/staffing').status_code, 401)
        self.assertEqual(self.save({'groupMode': 'itr'}, role='viewer').status_code, 403)
        self.assertEqual(self.save({'groupMode': 'itr'}, csrf=False).status_code, 403)
        attack = '</script><script>alert("Тест")</script>'
        self.assertEqual(self.save({'search': attack}).status_code, 200)
        self.assertNotIn(attack, self.clients['admin'].get('/').get_data(as_text=True))
        self.assertEqual(self.clients['admin'].get('/api/preferences/staffing').get_json()['settings']['search'], attack)

    def test_column_order_roundtrip_validation_and_old_client_compatibility(self):
        from user_preferences import COLUMNS
        order = ['name', *sorted(COLUMNS - {'name'})]
        columns = {'hidden': ['employer'], 'widths': {'name': 350}, 'order': order}
        self.assertEqual(self.save({'columns': columns}).status_code, 200)
        fresh = self.client_for(self.admin)
        self.assertEqual(fresh.get('/api/preferences/staffing').get_json()['settings']['columns'], columns)
        for bad in (order[:-1], [order[0]] * len(order), order + ['unknown'], None, {'name': 0}):
            self.assertEqual(self.save({'columns': {**columns, 'order': bad}}).status_code, 400)
        self.save({'columns': {'hidden': [], 'widths': {'name': 400}}})
        settings = fresh.get('/api/preferences/staffing').get_json()['settings']['columns']
        self.assertEqual(settings['order'], order)
        self.assertEqual(settings['widths'], {'name': 400})

    def test_author_column_and_legacy_order_survive_new_session(self):
        from user_preferences import COLUMNS
        old_order = ['name', *sorted(COLUMNS - {'name', 'assignment_author'})]
        self.assertEqual(self.save({'columns': {'order': old_order, 'hidden': [], 'widths': {}}}).status_code, 200)
        fresh = self.client_for(self.admin)
        columns = fresh.get('/api/preferences/staffing').get_json()['settings']['columns']
        self.assertEqual(columns['order'], [*old_order, 'assignment_author'])
        columns.update(hidden=['assignment_author'], widths={'assignment_author': 180})
        columns['order'] = ['assignment_author', *old_order]
        self.assertEqual(self.save({'columns': columns}).status_code, 200)
        self.assertEqual(fresh.get('/api/preferences/staffing').get_json()['settings']['columns'], columns)
