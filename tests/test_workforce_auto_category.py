import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class AutoCategoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.db = self.f.db
        self.code = 'profession.' + uuid4().hex
        self.db.native("INSERT INTO workforce_catalog(code,kind,label) VALUES (%s,'profession',%s)",
                       (self.code, 'Тестовая профессия ' + uuid4().hex))
        self.category = self.add_category()
        self.db.native('UPDATE workers SET profession_code=%s,category=\'\' WHERE id=ANY(%s)', (self.code, self.f.ids))
        self.donor = self.db.native("""INSERT INTO workers(full_name,personnel_no,department,profession_code)
            VALUES ('Основание подбора',%s,'TEST-SMU',%s) RETURNING id""", (uuid4().hex, self.code)).fetchone()['id']
        self.bind(self.donor, self.category)

    def add_category(self):
        name = 'Категория ' + uuid4().hex
        return self.db.native('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,'2026-09-19') RETURNING id''',
            (name, name.lower(), uuid4().hex, self.f.users['admin']['id'])).fetchone()['id']

    def bind(self, worker, category):
        self.db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,'2026-09-19') ON CONFLICT(worker_id) DO UPDATE SET category_id=excluded.category_id,
            edit_token=excluded.edit_token''', (worker, category, uuid4().hex, self.f.users['admin']['id']))

    def prepare(self, ids=None, role='admin'):
        response = self.f.request('post', 'bulk/category-auto/prepare', {'ids':ids or self.f.ids}, role)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def body(self, data):
        return {'ids':[r['id'] for r in data['people']], 'token':data['token'],
                'reason':'Проверка автоматического назначения', 'request_key':str(uuid4())}

    def apply(self, data, role='admin'):
        return self.f.request('post', 'bulk/category-auto/apply', data, role)

    def test_assigns_only_selected_atomically_audits_and_replays(self):
        data = self.prepare([self.f.worker])
        self.assertEqual(data['matched'], 1)
        body = self.body(data)
        first = self.apply(body)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json, {'changed':1, 'skipped':0})
        self.assertEqual(self.apply(body).json, first.json)
        self.assertEqual(self.db.native('SELECT category_id FROM employee_gdlr WHERE worker_id=%s', (self.f.worker,)).fetchone()['category_id'], self.category)
        self.assertIsNone(self.db.native('SELECT 1 FROM employee_gdlr WHERE worker_id=%s', (self.f.ids[1],)).fetchone())
        audits = self.db.native("SELECT * FROM workforce_audit WHERE action='auto_category' AND worker_id=%s", (self.f.worker,)).fetchall()
        self.assertEqual(len(audits), 1)
        self.assertIn('category_match_basis', audits[0]['after_json'])
        self.assertEqual(self.apply({**body, 'reason':'Другой запрос'}).status_code, 409)

    def test_existing_binding_and_unbound_legacy_text_are_preserved(self):
        self.bind(self.f.worker, self.category)
        self.db.native("UPDATE workers SET category='Ручное значение' WHERE id=%s", (self.f.ids[1],))
        data = self.prepare()
        self.assertEqual(data['matched'], 0)
        self.assertEqual({r['status'] for r in data['people']}, {'preserved'})
        self.assertEqual(self.apply(self.body(data)).json, {'changed':0,'skipped':2})

    def test_conflict_is_not_decided_by_majority_or_inactive_filter(self):
        other = self.add_category()
        self.bind(self.f.ids[1], other)
        self.db.native('UPDATE gdlr_categories SET active=0 WHERE id=%s', (other,))
        data = self.prepare([self.f.worker])
        self.assertEqual(data['people'][0]['status'], 'ambiguous')
        self.assertEqual(data['matched'], 0)

    def test_inactive_category_profession_and_missing_mapping(self):
        self.db.native('UPDATE gdlr_categories SET active=0 WHERE id=%s', (self.category,))
        self.assertEqual(self.prepare()['matched'], 0)
        self.db.native('UPDATE gdlr_categories SET active=1 WHERE id=%s', (self.category,))
        self.db.native('UPDATE workforce_catalog SET active=false WHERE code=%s', (self.code,))
        self.assertEqual(self.prepare()['matched'], 0)
        self.db.native('UPDATE workforce_catalog SET active=true WHERE code=%s', (self.code,))
        self.db.native('DELETE FROM employee_gdlr WHERE worker_id=%s', (self.donor,))
        self.assertEqual(self.prepare()['matched'], 0)
        self.db.native('UPDATE workers SET profession_code=NULL WHERE id=ANY(%s)', (self.f.ids,))
        self.assertEqual(self.prepare()['matched'], 0)

    def test_changed_worker_rejects_entire_batch(self):
        body = self.body(self.prepare())
        self.db.native("UPDATE workers SET full_name='Изменён' WHERE id=%s", (self.f.ids[1],))
        self.assertEqual(self.apply(body).status_code, 409)
        self.assertEqual(self.db.native('SELECT count(*) n FROM employee_gdlr WHERE worker_id=ANY(%s)', (self.f.ids,)).fetchone()['n'], 0)

    def test_changed_evidence_or_category_rejects_preview(self):
        body = self.body(self.prepare())
        self.db.native('UPDATE gdlr_categories SET edit_token=%s WHERE id=%s', (uuid4().hex, self.category))
        self.assertEqual(self.apply(body).status_code, 409)
        body = self.body(self.prepare())
        self.bind(self.donor, self.add_category())
        self.assertEqual(self.apply(body).status_code, 409)

    def test_late_binding_is_never_overwritten(self):
        from unittest.mock import patch
        from workforce_auto_category import preview
        body = self.body(self.prepare([self.f.worker]))
        other = self.add_category()

        def raced_preview(db, ids):
            result = preview(db, ids)
            self.bind(self.f.worker, other)
            return result

        with patch('workforce_auto_category.preview', side_effect=raced_preview):
            self.assertEqual(self.apply(body).status_code, 409)
        self.assertEqual(self.db.native('SELECT category_id FROM employee_gdlr WHERE worker_id=%s',
                                       (self.f.worker,)).fetchone()['category_id'], other)

    def test_scope_role_ownership_and_deactivation_are_enforced(self):
        for role in ('foreman','viewer','hr_viewer','recruitment'):
            self.assertEqual(self.f.request('post','bulk/category-auto/prepare',{'ids':[self.f.worker]},role).status_code, 403)
        self.assertEqual(self.f.request('post','bulk/category-auto/prepare',{'ids':self.f.ids},'rotation').status_code,404)
        body = self.body(self.prepare([self.f.worker], 'rotation'))
        self.db.native("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=%s", (self.f.users['rotation']['id'],))
        self.assertEqual(self.apply(body,'rotation').status_code,404)
        self.db.native('UPDATE workers SET active=0 WHERE id=%s', (self.f.worker,))
        self.assertEqual(self.apply(body).status_code,409)

    def test_request_validation(self):
        for ids in ([], [True], [self.f.worker,self.f.worker], ['1'], list(range(1,102))):
            self.assertEqual(self.f.request('post','bulk/category-auto/prepare',{'ids':ids}).status_code,400)
        body = self.body(self.prepare())
        for change in ({'reason':[]},{'reason':'x' * 10001},{'token':None},{'category_id':self.category},{'request_key':'bad'}):
            self.assertEqual(self.apply({**body,**change}).status_code,400)

