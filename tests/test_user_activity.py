import unittest
from datetime import datetime
from unittest.mock import patch

import test_super_admin


def stamp(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


class UserActivityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_super_admin.SuperAdminTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_super_admin.SuperAdminTest.tearDownClass()

    def setUp(self):
        self.case = test_super_admin.SuperAdminTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.module = self.case.module
        self.app = self.module.app
        self.admin = self.case.clients['admin']
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE user_activity_meta SET started_at=?', (stamp('2026-09-12T00:00:00Z'),))
            db.execute('UPDATE users SET password_hash=? WHERE id=?',
                       (self.module.generate_password_hash('activity-test-password'), self.case.ids['admin']))
            self.username = db.execute('SELECT username FROM users WHERE id=?', (self.case.ids['admin'],)).fetchone()[0]
            db.commit()
        self.module.LOGIN_ATTEMPTS.clear()

    def login(self, client, at, password='activity-test-password'):
        with patch('user_activity.now_seconds', return_value=stamp(at)):
            return client.post('/login', data={'username': self.username, 'password': password})

    def write(self, client, path, at):
        with client.session_transaction() as session:
            token = session['csrf_token']
        with patch('user_activity.now_seconds', return_value=stamp(at)):
            return client.post(path, headers={'X-CSRF-Token': token})

    def stats(self, at='2026-09-13T12:00:00Z', day='2026-09-13', client=None):
        with patch('user_activity.now_seconds', return_value=stamp(at)):
            return (client or self.admin).get('/api/user-activity', query_string={'date': day})

    def own(self, result):
        return next(row for row in result.get_json()['rows'] if row['id'] == self.case.ids['admin'])

    def test_successful_logins_moscow_midnight_failed_login_and_refresh(self):
        a, b = self.app.test_client(), self.app.test_client()
        self.assertEqual(self.login(a, '2026-09-12T20:59:59Z', 'wrong').status_code, 401)
        self.assertEqual(self.login(a, '2026-09-12T20:59:59Z').status_code, 302)
        self.assertEqual(self.login(b, '2026-09-12T21:00:00Z').status_code, 302)
        self.assertEqual(self.own(self.stats(day='2026-09-12'))['login_count'], 1)
        result = self.stats(at='2026-09-12T21:00:01Z')
        self.assertEqual(self.own(result)['login_count'], 1)
        self.assertEqual(self.own(result)['last_login_at'], '2026-09-12T21:00:00Z')
        self.assertEqual(self.write(b, '/api/session/heartbeat', '2026-09-12T21:01:00Z').status_code, 200)
        with patch('user_activity.now_seconds', return_value=stamp('2026-09-12T21:01:00Z')):
            self.assertEqual(b.get('/').status_code, 200)
            self.assertEqual(b.get('/api/reference').status_code, 200)
        self.assertEqual(self.own(self.stats())['login_count'], 1)
        self.assertEqual(self.login(b, '2026-09-13T10:00:00Z').status_code, 302)
        self.assertEqual(self.own(self.stats())['login_count'], 2)

    def test_presence_timeout_logout_and_other_device_remains_online(self):
        a, b = self.app.test_client(), self.app.test_client()
        self.login(a, '2026-09-13T10:00:00Z')
        self.assertTrue(self.own(self.stats(at='2026-09-13T10:02:59Z'))['online'])
        self.assertFalse(self.own(self.stats(at='2026-09-13T10:03:00Z'))['online'])
        self.write(a, '/api/session/heartbeat', '2026-09-13T10:04:00Z')
        self.login(b, '2026-09-13T10:04:01Z')
        cookie = a.get_cookie('session').value
        self.assertEqual(self.write(a, '/logout', '2026-09-13T10:04:02Z').status_code, 302)
        self.assertTrue(self.own(self.stats(at='2026-09-13T10:04:03Z'))['online'])
        self.write(b, '/logout', '2026-09-13T10:04:04Z')
        self.assertFalse(self.own(self.stats(at='2026-09-13T10:04:05Z'))['online'])
        a.set_cookie('session', cookie)
        with patch('user_activity.now_seconds', return_value=stamp('2026-09-13T10:04:06Z')):
            self.assertEqual(a.get('/api/reference').status_code, 401)
        self.assertEqual(self.own(self.stats())['login_count'], 2)

    def test_legacy_sessions_are_online_without_invented_logins_and_require_csrf(self):
        self.assertEqual(self.admin.post('/api/session/heartbeat').status_code, 403)
        for instant in ('2026-09-13T11:59:00Z', '2026-09-13T12:00:00Z'):
            self.assertEqual(self.write(self.admin, '/api/session/heartbeat', instant).status_code, 200)
        row = self.own(self.stats())
        self.assertTrue(row['online'])
        self.assertEqual(row['login_count'], 0)
        self.assertIsNone(row['last_login_at'])
        with self.app.app_context():
            self.assertEqual(self.module.get_db().execute('SELECT COUNT(*) FROM user_sessions').fetchone()[0], 1)

    def test_permissions_inactive_users_and_expired_sessions(self):
        for role in ('foreman', 'viewer'):
            self.assertEqual(self.stats(client=self.case.clients[role]).status_code, 403)
            self.assertNotIn('id="user-activity-panel"', self.case.clients[role].get('/').get_data(as_text=True))
            self.assertEqual(self.write(self.case.clients[role], '/api/session/heartbeat', '2026-09-13T12:00:00Z').status_code, 200)
        self.assertEqual(self.stats(client=self.case.clients['super_admin']).status_code, 200)
        self.assertEqual(self.app.test_client().get('/api/user-activity').status_code, 401)
        logged_in = self.app.test_client()
        self.login(logged_in, '2026-09-13T00:00:00Z')
        self.assertEqual(self.write(logged_in, '/api/session/heartbeat', '2026-09-13T12:00:00Z').status_code, 401)
        self.login(logged_in, '2026-09-13T12:00:00Z')
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.case.ids['admin'],)); db.commit()
        result = self.stats(client=self.case.clients['super_admin'])
        self.assertFalse(self.own(result)['online'])
        self.assertEqual(self.own(result)['login_count'], 2)

    def test_tracking_coverage_invalid_dates_and_restart_preserve_history(self):
        self.assertEqual(self.stats(day='2026-09-11').get_json()['coverage'], 'unavailable')
        self.assertIsNone(self.stats(day='2026-09-11').get_json()['login_count'])
        self.assertIsNone(self.own(self.stats(day='2026-09-11'))['login_count'])
        self.assertEqual(self.stats(day='2026-09-12').get_json()['coverage'], 'partial')
        self.assertEqual(self.stats(day='2026-09-13').get_json()['coverage'], 'available')
        for day in ('', 'invalid', '2026-9-1', '2026-02-30', '9999-12-31'):
            self.assertEqual(self.stats(day=day).status_code, 400)
        self.login(self.app.test_client(), '2026-09-13T11:59:00Z')
        before = self.stats().get_json()
        with self.app.app_context():
            self.module.init_db()
        self.assertEqual(before, self.stats().get_json())
        self.assertEqual(before['online_count'], 1)
        self.assertEqual(before['users_logged_in'], 1)


if __name__ == '__main__':
    unittest.main()
