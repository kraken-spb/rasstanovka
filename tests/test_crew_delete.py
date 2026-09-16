import inspect
import json
import sqlite3
import unittest
from unittest.mock import patch

from flask import g
from werkzeug.exceptions import HTTPException

import test_crew_catalog


class CrewDeleteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_crew_catalog.CrewCatalogTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_crew_catalog.CrewCatalogTest.tearDownClass()

    def setUp(self):
        self.catalog = test_crew_catalog.CrewCatalogTest()
        self.catalog.setUp()
        self.addCleanup(self.catalog.doCleanups)
        self.fx = self.catalog.fx
        self.module = self.fx.module
        self.crew = self.catalog.crew
        self.headers = self.catalog.headers
        with self.module.app.app_context():
            db = self.module.get_db()
            self.super_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('delete-super','unused','Супер-администратор','super_admin','now')").lastrowid
            db.commit()
        self.client = self.fx.client_for(self.super_id)

    def item(self):
        return next(row for row in self.client.get('/api/crew-catalog').json['rows'] if row['id'] == self.crew)

    def remove(self, item=None, client=None, headers=None):
        return (client or self.client).delete(f'/api/crew-catalog/{self.crew}',
            json={'expected_token': (item or self.item())['expected_token']},
            headers=self.headers if headers is None else headers)

    def empty(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM crew_members WHERE crew_id=?', (self.crew,))
            db.commit()

    def snapshot(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')]
                    for table in ('crews','crew_members','workers','assignments','assignment_events',
                                  'employee_removals','employee_restorations','staffing_action_history')}

    def test_empty_delete_keeps_people_and_creates_recoverable_backup(self):
        self.empty()
        before = self.snapshot()
        item = self.item()
        self.assertTrue(item['can_delete'])
        self.assertEqual(item['delete_reason'], '')
        backup_folder = self.module.DATABASE_PATH.parent / 'backups'
        existing_backups = set(backup_folder.glob('*.sqlite3'))
        self.assertEqual(self.remove(item).status_code, 200)
        after = self.snapshot()
        self.assertEqual(after.pop('crews'), [row for row in before.pop('crews') if row[0] != self.crew])
        self.assertEqual(after, before)
        backups = list(set(backup_folder.glob('*.sqlite3')) - existing_backups)
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as saved:
            self.assertEqual(saved.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertIsNotNone(saved.execute('SELECT id FROM crews WHERE id=?', (self.crew,)).fetchone())
        self.assertEqual(self.remove(item).status_code, 404)

    def test_roles_csrf_and_payload_checks_preserve_data(self):
        self.empty()
        item = self.item()
        before = self.snapshot()
        for role in ('admin','foreman','viewer'):
            self.assertEqual(self.remove(item, client=self.fx.clients[role]).status_code, 403)
        self.assertFalse(next(row for row in self.fx.clients['admin'].get('/api/crew-catalog').json['rows'] if row['id'] == self.crew)['can_delete'])
        self.assertEqual(self.remove(item, headers={}).status_code, 403)
        for payload in ({}, [], {'expected_token': True}, {'expected_token':item['expected_token'],'force':True}):
            self.assertEqual(self.client.delete(f'/api/crew-catalog/{self.crew}',json=payload,headers=self.headers).status_code,400)
        self.assertEqual(self.snapshot(), before)

    def test_roster_changes_and_stale_catalog_are_rejected_atomically(self):
        self.assertFalse(self.item()['can_delete'])
        before = self.snapshot()
        self.assertEqual(self.remove().status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.empty()
        stale = self.item()
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('INSERT INTO crew_members VALUES (?,?)', (self.crew,self.catalog.ids[0]))
            db.execute('UPDATE workers SET active=0 WHERE id=?', (self.catalog.ids[0],))
            db.commit()
        before = self.snapshot()
        self.assertEqual(self.remove(stale).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.empty()
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE crews SET name='Изменённая' WHERE id=?", (self.crew,))
            db.commit()
        before = self.snapshot()
        self.assertEqual(self.remove(stale).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_all_history_references_block_deletion_without_changes(self):
        self.empty()
        with self.module.app.app_context():
            site = self.module.get_db().execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
        worker = self.catalog.ids[0]
        cases = [
            ('assignments', "INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token) VALUES ('2026-09-15','1 смена',?,?,'ЛГСС',?,'now',?,'test')", (site,worker,self.super_id,self.crew)),
            ('assignment_events', "INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,before_subobject_id,after_subobject_id,changed_by,changed_at) VALUES (?,?,'2026-09-15','1 смена',NULL,NULL,?,'now')", (self.crew,worker,self.super_id)),
            ('employee_removals', "INSERT INTO employee_removals VALUES (?,'2026-09-15','Причина',?,'now','Сотрудник','test',?,'Бригада','Админ','super','[]')", (worker,self.super_id,self.crew)),
            ('employee_restorations', "INSERT INTO employee_restorations(worker_id,restored_date,changed_at,changed_by,worker_name,personnel_no,crew_id,crew_name,actor_name,actor_username,removal_json,edit_token) VALUES (?,'2026-09-15','now',?,'Сотрудник','test',?,'Бригада','Админ','super',NULL,'restore')", (worker,self.super_id,self.crew)),
            ('staffing_action_history', "INSERT INTO staffing_action_history(user_id,label,work_date,scope_json,changes_json,guards_json,state,edit_token,created_at) VALUES (?,'Перевод','2026-09-15','{}','{}',?,'done','history','now')", (self.super_id,json.dumps({'crew_ids':[self.crew]}))),
        ]
        for table, statement, values in cases:
            with self.subTest(table=table):
                with self.module.app.app_context():
                    db = self.module.get_db()
                    cursor = db.execute(statement, values)
                    inserted = cursor.lastrowid
                    db.commit()
                item = self.item()
                self.assertFalse(item['can_delete'])
                self.assertIn('истории', item['delete_reason'])
                before = self.snapshot()
                self.assertEqual(self.remove(item).status_code,409)
                self.assertEqual(self.snapshot(), before)
                with self.module.app.app_context():
                    db = self.module.get_db()
                    db.execute(f'DELETE FROM {table} WHERE rowid=?', (inserted,))
                    db.commit()

    def test_revoked_role_and_failed_backup_cannot_delete(self):
        self.empty()
        item = self.item()
        before = self.snapshot()
        with patch('backup_api.create_backup', side_effect=OSError('unavailable')):
            response = self.remove(item)
            self.assertEqual(response.status_code,503)
            self.assertIn('не удалена', response.json['error'])
        self.assertEqual(self.snapshot(), before)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.super_id,))
            db.commit()
        with self.module.app.test_request_context(f'/api/crew-catalog/{self.crew}',method='DELETE',json={'expected_token':item['expected_token']}):
            g.user = {'id':self.super_id,'role':'super_admin'}
            with self.assertRaises(HTTPException) as raised:
                inspect.unwrap(self.module.app.view_functions['delete_crew_catalog'])(self.crew)
            self.assertEqual(raised.exception.code,403)
        self.assertEqual(self.snapshot(), before)
