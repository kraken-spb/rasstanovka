import copy
import unittest
import test_preferences


class UserProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preferences.PreferencesTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_preferences.PreferencesTest.tearDownClass()

    def setUp(self):
        self.fixture = test_preferences.PreferencesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.clients = self.fixture.clients
        self.module = self.fixture.module

    def payload(self, role='admin'):
        data = self.clients[role].get('/api/profile').get_json()
        return {k: data[k] for k in ('phone', 'email', 'contact', 'enabled', 'shortcuts', 'token')}

    def save(self, data, role='admin', csrf=True):
        return self.clients[role].put('/api/profile', json=data,
            headers={'X-CSRF-Token': 'preferences-csrf'} if csrf else {})

    def test_contacts_shortcuts_persist_and_accounts_are_isolated(self):
        data = self.payload()
        data.update(phone='+7 900 000-00-00', email='qa@example.test', contact='Рабочий добавочный 123')
        data['shortcuts']['staffing'] = 'Ctrl+Shift+Digit9'
        self.assertEqual(self.save(data).status_code, 200)
        fresh = self.fixture.client_for(self.fixture.admin).get('/api/profile')
        self.assertEqual(fresh.headers['Cache-Control'], 'no-store')
        saved = fresh.get_json()
        self.assertEqual(saved['contact'], data['contact'])
        self.assertEqual(saved['shortcuts']['staffing'], 'Ctrl+Shift+Digit9')
        self.assertEqual(self.payload('foreman')['phone'], '')
        self.assertEqual(self.payload('foreman')['shortcuts']['staffing'], 'Ctrl+Shift+Digit1')
        with self.module.app.app_context(): self.module.init_db()
        self.assertEqual(self.payload()['email'], 'qa@example.test')

    def test_viewer_has_own_profile_without_editing_shortcuts(self):
        data = self.payload('viewer')
        self.assertEqual(set(data['shortcuts']), {'summary', 'search', 'profile'})
        data['enabled'] = False
        data['shortcuts']['search'] = ''
        self.assertEqual(self.save(data, 'viewer').status_code, 200)
        self.assertFalse(self.payload('viewer')['enabled'])
        self.assertTrue(self.payload()['enabled'])

    def test_stale_update_does_not_overwrite_saved_contact(self):
        first = self.payload()
        second = copy.deepcopy(first)
        first['phone'] = '123'
        self.assertEqual(self.save(first).status_code, 200)
        second['phone'] = '456'
        self.assertEqual(self.save(second).status_code, 409)
        self.assertEqual(self.payload()['phone'], '123')

    def test_validation_csrf_identity_and_role_cannot_be_overridden(self):
        valid = self.payload()
        self.assertEqual(self.save(valid, csrf=False).status_code, 403)
        invalid = [dict(valid, user_id=999), dict(valid, role='super_admin'), dict(valid, email='invalid'),
                   dict(valid, contact='x' * 301), dict(valid, phone='abc\n'), dict(valid, enabled=1)]
        for combo in ('Ctrl+Shift+KeyR', 'Ctrl+KeyE', 'Ctrl+Shift+Digit2'):
            data = copy.deepcopy(valid)
            data['shortcuts']['staffing'] = combo
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data): self.assertEqual(self.save(data).status_code, 400)
        self.assertEqual(self.payload()['token'], 'new')
        anonymous = self.module.app.test_client()
        self.assertIn(anonymous.get('/api/profile').status_code, (302, 401))

    def test_role_change_refreshes_available_actions(self):
        data = self.payload('foreman')
        self.assertNotIn('import', data['shortcuts'])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='viewer' WHERE username='foreman'")
            db.commit()
        self.assertEqual(self.save(data, 'foreman').status_code, 400)
        self.assertNotIn('staffing', self.payload('foreman')['shortcuts'])
