from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_staffing
from user_roles import HR_PERSONAL_OPERATIONS, HR_ROLE_NAME, HR_VIEWER, migrate_user_roles


class HrViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_staffing.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_staffing.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.fx = test_staffing.StaffingWorkflowTest()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.apply()
        self.module = self.fx.module
        self.day = '2026-09-15'
        self.headers = {'X-CSRF-Token':'staffing-csrf'}
        with self.module.app.app_context():
            db = self.module.get_db()
            self.hr_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('hr-test','unused','Кадровая служба','hr_viewer','now')").lastrowid
            self.worker_ids = [row[0] for row in db.execute('SELECT worker_id FROM staffing_import_members ORDER BY worker_id')]
            self.site = db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 1').fetchone()[0]
            self.crew = db.execute('SELECT crew_id FROM crew_members WHERE worker_id=?', (self.worker_ids[0],)).fetchone()[0]
            db.execute("UPDATE workers SET department='Другой СМУ' WHERE id=?", (self.worker_ids[-1],))
            # Prior per-user edit grants must not restrict HR read access or grant writes.
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[]','old-access',?,'now')", (self.hr_id,self.fx.super_admin_id))
            for worker, shift in ((self.worker_ids[0],'1 смена'),(self.worker_ids[-1],'2 смена')):
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token)
                    VALUES (?,?,?,?,'ЛГСС',?,'now',?,'hr-fixture')''', (self.day,shift,self.site,worker,self.fx.admin_id,self.crew))
            db.execute('INSERT INTO daily_staffing_plans(work_date,subobject_id,planned_count,edit_token,updated_by,updated_at) VALUES (?,?,5,\'plan\',?,\'now\')',
                       (self.day,self.site,self.fx.admin_id))
            db.commit()
        self.client = self.fx.client(self.hr_id)

    def snapshot(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            excluded = {'user_sessions', 'user_login_events', 'user_staffing_preferences'}
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if row[0] not in excluded]
            return {table: [tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')] for table in tables}

    def test_all_smus_staffing_catalogs_and_reports_are_readable(self):
        queries = [
            '/', '/api/reference', '/api/employees?date=' + self.day, '/api/employees?scope=outstaff',
            '/api/crews', '/api/crew-catalog', f'/api/crew-catalog/{self.crew}/members',
            f'/api/crews/{self.crew}/board?date={self.day}&shift=1%20смена',
            '/api/staffing/people', '/api/staffing/crew-options', '/api/gdlr-categories',
            '/api/contractors', '/api/smu', '/api/locations', '/api/users', '/api/user-smu-access',
            '/api/user-activity?date=' + self.day, '/api/profile', '/api/preferences/staffing',
            '/api/calendar?start=' + self.day + '&days=1', '/api/placement-report?date=' + self.day,
            '/api/personnel-dashboard?start=' + self.day + '&end=' + self.day,
            '/api/staffing/verification?date=' + self.day, '/api/activity-dates', '/api/assignments?date=' + self.day,
        ]
        for url in queries:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code,200,response.get_json(silent=True))
        rows = self.client.get(f'/api/staffing?date={self.day}&shift=all').json['rows']
        self.assertEqual({row['id'] for row in rows}, set(self.worker_ids))
        self.assertTrue(all(row['locked'] for row in rows))
        self.assertTrue(all(not row['can_edit_whole'] for row in self.client.get(f'/api/staffing?date={self.day}&shift=all').json['crews']))
        employees = self.client.get('/api/employees').json['rows']
        self.assertTrue(all(not row['can_edit'] and not row['can_remove'] and not row['can_restore'] for row in employees))
        self.assertTrue(all(not row['can_edit'] and not row['can_delete'] for row in self.client.get('/api/crew-catalog').json['rows']))
        access = next(row for row in self.client.get('/api/user-smu-access').json['rows'] if row['user_id'] == self.hr_id)
        self.assertEqual(access['mode'],'all')
        self.assertEqual(access['departments'],[])

    def test_drillthrough_and_exports_reconcile_across_departments(self):
        calendar = self.client.get(f'/api/calendar?start={self.day}&days=1').json
        self.assertEqual(sum(row['day_count']+row['night_count'] for row in calendar['facts']),2)
        for shift, worker in [('1 смена',self.worker_ids[0]),('2 смена',self.worker_ids[-1])]:
            response = self.client.get('/api/staffing',query_string={'date':self.day,'shift':'all','calendar_sites':self.site,'calendar_shift':shift})
            self.assertEqual(response.status_code,200)
            self.assertEqual([row['id'] for row in response.json['rows']],[worker])
            self.assertTrue(response.json['rows'][0]['locked'])
        change = self.client.get('/api/staffing',query_string={'date':self.day,'shift':'all','change_metric':'assigned'})
        self.assertEqual(change.status_code,200)
        self.assertEqual({row['id'] for row in change.json['rows']},{self.worker_ids[0],self.worker_ids[-1]})
        for url in ('/api/staffing/export', '/api/staffing/position-cards/pdf', '/api/placement-report/pdf'):
            response = self.client.get(url,query_string={'date':self.day,'shift':'all'})
            self.assertEqual(response.status_code,200,response.get_json(silent=True))
            self.assertTrue(response.data.startswith(b'%PDF') or response.data.startswith(b'PK'))

    def test_every_business_write_route_is_forbidden_and_data_is_unchanged(self):
        before = self.snapshot()
        identifiers = {'worker_id':self.worker_ids[0], 'crew_id':self.crew, 'user_id':self.fx.admin_id,
                       'direction':'undo','kind':'subobjects','location_id':self.site,'field':'brigadier'}
        tested = 0
        for rule in self.module.app.url_map.iter_rules():
            if rule.endpoint == 'login':
                continue
            values = {key:identifiers.get(key,1) for key in rule.arguments}
            values.update(rule.defaults or {})
            for method in rule.methods & {'POST','PUT','PATCH','DELETE'}:
                if (rule.endpoint,method) in HR_PERSONAL_OPERATIONS:
                    continue
                url = rule.build(values)[1]
                with self.subTest(endpoint=rule.endpoint,method=method):
                    response = self.client.open(url,method=method,json={},headers=self.headers)
                    self.assertEqual(response.status_code,403,response.get_json(silent=True))
                tested += 1
        self.assertGreater(tested,45)
        self.assertEqual(self.snapshot(),before)
        for url in ('/api/backups','/api/backups/test.sqlite3/download','/api/employees/create-options',
                    f'/api/employees/{self.worker_ids[0]}/removal-preview?date={self.day}'):
            self.assertEqual(self.client.get(url).status_code,403)

    def test_personal_filters_presence_and_logout_work_without_business_writes(self):
        before = self.snapshot()
        settings = {'date':self.day,'department':'Другой СМУ','search':'Сотрудник','groupMode':'crew'}
        self.assertEqual(self.client.patch('/api/preferences/staffing',json=settings,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/preferences/staffing').json['settings'],settings)
        self.assertEqual(self.client.patch('/api/preferences/staffing',json={'date':self.day}).status_code,403)
        self.assertEqual(self.client.post('/api/session/heartbeat',json={},headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/staffing/history').json,{'undo':None,'redo':None})
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.client.post('/logout',headers=self.headers).status_code,302)

    def test_role_assignment_is_super_only_and_demotion_takes_effect_immediately(self):
        payload = {'username':'new-hr','full_name':HR_ROLE_NAME,'password':'test-only-password','role':HR_VIEWER}
        self.assertEqual(self.fx.admin.post('/api/users',json=payload,headers=self.headers).status_code,403)
        self.assertEqual(self.fx.super_admin.post('/api/users',json=payload,headers=self.headers).status_code,201)
        self.assertEqual(self.fx.super_admin.patch(f'/api/users/{self.fx.admin_id}',json={'role':HR_VIEWER,'expected_role':'admin'},headers=self.headers).status_code,200)
        self.assertEqual(self.fx.admin.post('/api/crews',json={'name':'Запрещено'},headers=self.headers).status_code,403)
        self.assertEqual(self.fx.admin.get(f'/api/staffing?date={self.day}&shift=all').status_code,200)
        access = next(row for row in self.fx.super_admin.get('/api/user-smu-access').json['rows'] if row['user_id']==self.hr_id)
        self.assertEqual(self.fx.super_admin.put(f'/api/users/{self.hr_id}/smu-access',json={'mode':'all','departments':[],'expected_token':access['expected_token']},headers=self.headers).status_code,400)
        self.assertEqual(self.fx.viewer.get(f'/api/staffing?date={self.day}&shift=all').status_code,403)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?',(self.hr_id,))
            db.commit()
        self.assertEqual(self.client.get('/api/employees').status_code,401)


class HrRoleMigrationTest(unittest.TestCase):
    def test_existing_four_role_schema_keeps_users_links_indexes_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'roles.db'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('''PRAGMA foreign_keys=ON;
                    CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        password_hash TEXT NOT NULL,full_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('super_admin','admin','foreman','viewer')),
                        active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
                    INSERT INTO users VALUES (7,'Super','keep-hash','Супер','super_admin',1,'keep-date');
                    INSERT INTO users VALUES (8,'Old','old-hash','Сотрудник','foreman',0,'old-date');
                    CREATE TABLE links(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id));
                    INSERT INTO links VALUES (9,7);
                    CREATE INDEX users_active ON users(active);
                    CREATE TABLE renames(name TEXT);
                    CREATE TRIGGER users_renamed AFTER UPDATE OF full_name ON users BEGIN INSERT INTO renames VALUES (new.full_name); END;
                ''')
                before = db.execute('SELECT * FROM users').fetchall()
                with patch('user_roles.backup_database',side_effect=OSError('backup failure')):
                    with self.assertRaises(OSError):
                        migrate_user_roles(db)
                self.assertEqual(db.execute('SELECT * FROM users').fetchall(),before)
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
                migrate_user_roles(db)
                migrate_user_roles(db)
                self.assertEqual(db.execute('SELECT * FROM users').fetchall(),before)
                self.assertEqual(db.execute('SELECT * FROM links').fetchall(),[(9,7)])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='users_active'").fetchone())
                backups = list((path.parent / 'backups').glob('*.db'))
                self.assertEqual(len(backups),1)
                with closing(sqlite3.connect(backups[0])) as saved:
                    self.assertEqual(saved.execute('SELECT * FROM users').fetchall(),before)
                db.execute("UPDATE users SET role='hr_viewer',full_name='Кадры' WHERE id=8")
                self.assertEqual(db.execute('SELECT * FROM renames').fetchall(),[('Кадры',)])
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('DELETE FROM users WHERE id=7')
