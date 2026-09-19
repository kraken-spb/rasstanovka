"""Legacy catalog queries must also execute against the selected PostgreSQL fixture."""
from functools import wraps
import json
import os
import unittest
from unittest.mock import patch

from flask import abort, g
from tests import test_workforce_api as workforce_tests


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select isolated PostgreSQL staging explicitly.')
class PostgresCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workforce_tests.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.fixture = workforce_tests.WorkforceApiTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.db, self.app, self.client = self.fixture.db, self.fixture.app, self.fixture.client
        self.actor = self.fixture.users['admin']['id']
        self.db.native("UPDATE users SET role='super_admin' WHERE id=%s", (self.actor,))
        self.fixture.users['admin']['role'] = 'super_admin'

        def roles_required(*roles):
            def decorate(view):
                @wraps(view)
                def guarded(*args, **kwargs):
                    if g.user['role'] not in roles and g.user['role'] != 'super_admin':
                        abort(403)
                    return view(*args, **kwargs)
                return guarded
            return decorate

        from smu_api import register_smu_routes
        from crew_api import register_crew_routes
        register_smu_routes(self.app, lambda: self.db, roles_required, lambda: '2026-09-17T00:00:00Z')
        register_crew_routes(self.app, lambda: self.db, roles_required, lambda: '2026-09-17T00:00:00Z')

    def test_category_staffing_toggle_uses_current_binding_and_preserves_default(self):
        from gdlr_api import register_gdlr_routes, staffing_eligible_sql
        register_gdlr_routes(self.app, lambda: self.db, lambda *roles: lambda view: view, lambda: '2026-09-18T00:00:00Z')
        name = 'Проверка расстановки ' + self.fixture.suffix
        created = self.client.post('/api/gdlr-categories',json={'name':name})
        self.assertEqual(created.status_code,201,created.json)
        cid = created.json['id']
        self.db.native('INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s)',
                       (self.fixture.worker,cid,'binding',self.actor,'now'))
        def eligible():
            return self.db.execute('SELECT COUNT(*) FROM workers w WHERE w.id=? AND '+staffing_eligible_sql(),(self.fixture.worker,)).fetchone()[0]
        self.assertEqual(eligible(),0)
        row = self.db.native('SELECT * FROM gdlr_categories WHERE id=%s',(cid,)).fetchone()
        body = {'name':name,'active':True,'staffing_allowed':True,'expected_token':row['edit_token']}
        url = '/api/gdlr-categories/'+str(cid)
        response = self.client.patch(url,json=body)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(eligible(),1)
        self.assertEqual(self.client.patch(url,json=body).status_code,409)
        row = self.db.native('SELECT * FROM gdlr_categories WHERE id=%s',(cid,)).fetchone()
        body.update(staffing_allowed=False,expected_token=row['edit_token'])
        self.assertEqual(self.client.patch(url,json=body).status_code,200)
        self.assertEqual(eligible(),0)
        self.assertEqual(self.db.native('SELECT category_id FROM employee_gdlr WHERE worker_id=%s',(self.fixture.worker,)).fetchone()[0],cid)

    def test_employer_catalog_and_correction_keep_worker_and_profile_in_sync(self):
        from employer_api import register_employer_routes, employer_token
        from staffing_shifts import responsibility_states
        register_employer_routes(self.app, lambda: self.db, lambda *roles: lambda view: view,
                                 lambda: '2026-09-18T00:00:00Z')
        name = 'Новый работодатель ' + self.fixture.suffix
        archived = 'Отключённый ' + self.fixture.suffix
        self.db.native('INSERT INTO workforce_organizations(name) VALUES (%s)', (name,))
        self.db.native('INSERT INTO workforce_organizations(name,active) VALUES (%s,FALSE)', (archived,))
        options = self.client.get('/api/staffing/employers')
        self.assertEqual(options.status_code, 200)
        names = [row['name'] for row in options.json['rows']]
        self.assertIn(name, names)
        self.assertNotIn(archived, names)
        worker, untouched = self.fixture.ids
        before = self.db.native('SELECT employer FROM workers WHERE id=%s', (worker,)).fetchone()['employer']
        group = responsibility_states(self.db, [worker])[worker]['group_token']
        data = {'employer': name, 'worker_ids': [worker], 'expected_crews': {str(worker): None},
                'expected_group_tokens': {str(worker): group},
                'expected_tokens': {str(worker): employer_token(before)}}
        self.assertEqual(self.client.put('/api/staffing/groups/employer', json={**data, 'employer': archived}).status_code, 400)
        response = self.client.put('/api/staffing/groups/employer', json=data)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.db.native('SELECT employer FROM workers WHERE id=%s', (worker,)).fetchone()['employer'], name)
        self.assertEqual(self.db.native('SELECT o.name FROM workforce_profiles p JOIN workforce_organizations o ON o.id=p.employer_id WHERE p.worker_id=%s', (worker,)).fetchone()['name'], name)
        self.assertEqual(self.db.native('SELECT source_employer FROM employee_employers WHERE worker_id=%s', (worker,)).fetchone()['source_employer'], before)
        self.assertEqual(self.db.native('SELECT employer FROM workers WHERE id=%s', (untouched,)).fetchone()['employer'], before)
        self.assertEqual(self.client.put('/api/staffing/groups/employer', json=data).status_code, 409)
        # Undo to a missing override and redo must update the effective profile too.
        from staffing_history import restore
        row = dict(self.db.native('SELECT * FROM employee_employers WHERE worker_id=%s', (worker,)).fetchone())
        key = json.dumps(['employee_employers', [worker]])
        with self.app.test_request_context():
            g.user = self.fixture.users['admin']
            restore(self.db, {key: None}, '2026-09-18T01:00:00Z')
            self.assertEqual(self.db.native('SELECT employer FROM workers WHERE id=%s', (worker,)).fetchone()['employer'], before)
            restore(self.db, {key: row}, '2026-09-18T02:00:00Z')
            self.assertEqual(self.db.native('SELECT employer FROM workers WHERE id=%s', (worker,)).fetchone()['employer'], name)


    def test_smu_counts_with_and_without_chief_and_archived_chief(self):
        chief = self.fixture.users['foreman']['id']
        populated = self.db.native('INSERT INTO smu_catalog(name,site_chief_user_id) VALUES (%s,%s) RETURNING id',
            ('PG СМУ '+self.fixture.suffix, chief)).fetchone()[0]
        empty = self.db.native('INSERT INTO smu_catalog(name) VALUES (%s) RETURNING id',
            ('PG пустой СМУ '+self.fixture.suffix,)).fetchone()[0]
        for worker_id in self.fixture.ids:
            self.db.native('''INSERT INTO employee_smu(worker_id,smu_id,source_department) VALUES (%s,%s,'TEST-SMU')
                ON CONFLICT(worker_id) DO UPDATE SET smu_id=excluded.smu_id''', (worker_id,populated))
        response = self.client.get('/api/smu')
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row['id']:row for row in response.json['rows']}
        self.assertEqual(rows[populated]['employee_count'], 2)
        self.assertEqual(rows[populated]['site_chief_user_id'], chief)
        self.assertEqual(rows[populated]['site_chief_name'], 'Проверка прав')
        self.assertEqual(rows[empty]['employee_count'], 0)
        self.assertIsNone(rows[empty]['site_chief_name'])
        self.db.native("UPDATE users SET full_name='Новое ФИО',active=0 WHERE id=%s", (chief,))
        result = self.client.get('/api/smu')
        self.assertEqual(result.status_code, 200, result.data)
        row = next(row for row in result.json['rows'] if row['id']==populated)
        self.assertEqual((row['site_chief_name'],row['site_chief_active'],row['employee_count']), ('Новое ФИО',0,2))
        self.assertNotIn(chief, [row['id'] for row in result.json['chief_options']])
        result = self.client.get('/api/smu', headers={'Test-Role':'foreman'})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.json['chief_options'], [])

    def crew(self):
        return self.db.native('INSERT INTO crews(name,owner_user_id,created_at) VALUES (%s,%s,%s) RETURNING id',
            ('PG бригада '+self.fixture.suffix,self.actor,'2026-09-17')).fetchone()[0]

    def item(self, crew_id):
        response = self.client.get('/api/crew-catalog')
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in response.json['rows'] if row['id']==crew_id)

    def history(self, guards, state='done'):
        self.db.execute('''INSERT INTO staffing_action_history
            (user_id,label,work_date,scope_json,changes_json,guards_json,state,edit_token,created_at)
            VALUES (?,'Перевод','2026-09-17','{}','{}',?,?,'test','now')''',
            (self.actor,json.dumps(guards),state))

    def assert_protected(self, crew_id):
        item = self.item(crew_id)
        self.assertFalse(item['can_delete'])
        self.assertIn('истории', item['delete_reason'])
        with patch('backup_api.create_backup') as backup:
            response = self.client.delete('/api/crew-catalog/'+str(crew_id), json={'expected_token':item['expected_token']})
        self.assertEqual(response.status_code, 409, response.data)
        backup.assert_not_called()
        self.assertIsNotNone(self.db.native('SELECT id FROM crews WHERE id=%s', (crew_id,)).fetchone())

    def test_crew_json_history_blocks_deletion_in_all_history_states(self):
        crew_id = self.crew()
        self.history({})
        self.history({'crew_ids':[]})
        self.history({'crew_ids':None})
        self.assertTrue(self.item(crew_id)['can_delete'])
        for state in ('done','undone','discarded'):
            with self.subTest(state=state):
                self.db.native('SAVEPOINT catalog_history')
                self.history({'crew_ids':[crew_id,crew_id+1,crew_id,'unrelated',None]}, state)
                self.assert_protected(crew_id)
                self.db.native('ROLLBACK TO SAVEPOINT catalog_history')
                self.db.native('RELEASE SAVEPOINT catalog_history')
        self.assertTrue(self.item(crew_id)['can_delete'])

    def test_crew_relational_history_sources_still_block_deletion(self):
        crew_id, worker = self.crew(), self.fixture.worker
        site = self.db.native('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
        cases = [
            ('assignments', "INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token) VALUES ('2026-09-17','1 смена',?,?,'ЛГСС',?,'now',?,'test')", (site,worker,self.actor,crew_id)),
            ('events', "INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-17','1 смена',NULL,NULL,?,'now')", (crew_id,worker,self.actor)),
            ('removals', "INSERT INTO employee_removals VALUES (?,'2026-09-17','Причина',?,'now','Сотрудник','test',?,'Бригада','Админ','super','[]')", (worker,self.actor,crew_id)),
            ('restorations', "INSERT INTO employee_restorations(worker_id,restored_date,changed_at,changed_by,worker_name,personnel_no,crew_id,crew_name,actor_name,actor_username,removal_json,edit_token) VALUES (?,'2026-09-17','now',?,'Сотрудник','test',?,'Бригада','Админ','super',NULL,'restore')", (worker,self.actor,crew_id)),
        ]
        for name, sql, values in cases:
            with self.subTest(source=name):
                self.db.native('SAVEPOINT catalog_history')
                self.db.execute(sql,values)
                self.assert_protected(crew_id)
                self.db.native('ROLLBACK TO SAVEPOINT catalog_history')
                self.db.native('RELEASE SAVEPOINT catalog_history')
