import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class CategoryAssignmentAuthorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def row(self):
        response = self.f.request('get','people?department=TEST-SMU&date=2026-09-19')
        self.assertEqual(response.status_code,200,response.json)
        return next(row for row in response.json['rows'] if row['id']==self.f.worker)

    def test_manual_assignment_and_cached_author_rename(self):
        f = self.f
        f.app.config['TESTING'] = False
        self.assertIsNone(self.row()['category_assignment'])
        prepared = f.request('post','bulk/prepare',{'ids':[f.worker]}).json
        category = prepared['fields']['category_id']
        value = category['options'][0]['value']
        result = f.request('post','bulk/apply',{
            'field':'category_id','value':value,'reference_token':category['token'],
            'people':[{k:r[k] for k in ('id','token')} for r in prepared['people']],
            'reason':'Проверка автора категории','request_key':str(uuid4())})
        self.assertEqual(result.status_code,200,result.json)
        assigned = self.row()['category_assignment']
        self.assertEqual(assigned['assigned_by'],'Проверка прав')
        stored = f.db.native('SELECT updated_at FROM employee_gdlr WHERE worker_id=%s',(f.worker,)).fetchone()['updated_at']
        self.assertEqual(assigned['assigned_at'],stored)
        self.assertEqual(self.row()['category_assignment'],assigned)
        f.db.native("UPDATE users SET full_name='Петров Пётр Петрович' WHERE id=%s",(f.users['admin']['id'],))
        renamed = self.row()['category_assignment']
        self.assertEqual(renamed['assigned_by'],'Петров Пётр Петрович')
        self.assertEqual(renamed['assigned_at'],stored)

    def test_legacy_category_without_binding_has_no_invented_author(self):
        self.f.db.native("UPDATE workers SET category='Старая категория' WHERE id=%s",(self.f.worker,))
        row = self.row()
        self.assertEqual(row['category'],'Старая категория')
        self.assertIsNone(row['category_assignment'])
