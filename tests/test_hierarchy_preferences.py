import unittest
import test_preferences


class HierarchyPreferencesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preferences.PreferencesTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_preferences.PreferencesTest.tearDownClass()

    def setUp(self):
        self.fixture=test_preferences.PreferencesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_order_persists_across_sessions_and_is_account_scoped(self):
        values={'groupMode':'hierarchy','groupLevels':['itr','crew','shift']}
        self.assertEqual(self.fixture.save(values).status_code,200)
        client=self.fixture.client_for(self.fixture.admin)
        self.assertEqual(client.get('/api/preferences/staffing').json['settings'],values)
        self.assertEqual(self.fixture.clients['foreman'].get('/api/preferences/staffing').json['settings'],{})
        self.assertEqual(self.fixture.save({'groupLevels':['shift','crew','itr']}).status_code,200)
        self.assertEqual(client.get('/api/preferences/staffing').json['settings']['groupLevels'],['shift','crew','itr'])

    def test_invalid_levels_cannot_poison_saved_preferences(self):
        for value in ([],['itr','itr'],['not-a-field'],None,{'crew':1},[1],[['crew']],['__proto__']):
            with self.subTest(value=value):
                self.assertEqual(self.fixture.save({'groupLevels':value}).status_code,400)
        self.assertEqual(self.fixture.clients['admin'].get('/api/preferences/staffing').json['settings'],{})
        self.assertEqual(self.fixture.save({'groupMode':'hierarchy','groupLevels':['crew']},csrf=False).status_code,403)
        self.assertEqual(self.fixture.save({'groupMode':'hierarchy','groupLevels':['crew']},role='viewer').status_code,403)
