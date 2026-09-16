import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PlacementReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                'ADMIN_PASSWORD': 'report-test-password-123', 'SECRET_KEY': 'report-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'report.db'
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.foreman = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('report-foreman','hash','Прораб','foreman','now')").lastrowid
            self.viewer = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('report-viewer','hash','Наблюдатель','viewer','now')").lastrowid
            self.crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Отчёт',?,'now')", (self.foreman,)).lastrowid
            self.site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
            batch = db.execute("INSERT INTO staffing_imports(filename,sha256,imported_at,imported_by,selected_count,summary_json,source_label) VALUES ('report.xlsx','report-fixture','now',?,5,'{}','ППС15')", (self.admin,)).lastrowid
            categories = {}
            for name in ('Электромонтажники', 'Сварщики'):
                categories[name] = db.execute('''INSERT INTO gdlr_categories
                    (name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
                    VALUES (?,?,1,'report-category',?,'now')''', (name, name.casefold(), self.admin)).lastrowid
            self.people = []
            for index, (pps, category) in enumerate([('ППС15','Электромонтажники'),('ППС15','Электромонтажники'),
                    ('ППС15','Электромонтажники'),('ППС19','Сварщики'),('','')]):
                worker = db.execute('''INSERT INTO workers(full_name,personnel_no,pps,category,department,profession)
                    VALUES (?,?,?,?,?,?)''', ('Сотрудник ' + str(index), 'report-' + str(index), pps, category,
                    'СМУ 15' if index < 3 else 'СМУ 19', 'Профессия')).lastrowid
                self.people.append(worker)
                db.execute("INSERT INTO staffing_import_members(import_id,worker_id,source_row,source_crew) VALUES (?,?,?,'')", (batch, worker, index+1))
                if category:
                    db.execute("INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,'report-category',?,'now')",
                               (worker, categories[category], self.admin))
                if index < 3:
                    db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (self.crew,worker))
            for worker, shift in [(self.people[0],'1 смена'),(self.people[0],'2 смена'),(self.people[2],'1 смена')]:
                self.assign(db, worker, shift)
            db.execute("INSERT INTO staffing_attendance(worker_id,work_date,status,edit_token,updated_by,updated_at) VALUES (?,'2026-09-13','Больн','report',?,'now')", (self.people[2],self.admin))
            db.commit()

    def assign(self, db, worker, shift):
        db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id,edit_token)
            VALUES ('2026-09-13',?,?,?,'ЛГСС',?,'now',?,'report')''', (shift,self.site,worker,self.foreman,self.crew))

    def client(self, user=None):
        client = self.module.app.test_client()
        if user is not None:
            with client.session_transaction() as session:
                session['user_id'] = user
        return client

    def get(self, user=None, **filters):
        return self.client(self.admin if user is None else user).get('/api/placement-report',
            query_string={'date':'2026-09-13', **filters})

    def test_distinct_workers_three_exclusive_statuses_and_no_writes(self):
        with self.module.app.app_context():
            before = list(self.module.get_db().iterdump())
        data = self.get().get_json()
        self.assertEqual(data['totals'], {'total':4,'assigned':1,'unassigned':2,'absent':1})
        group = next(g for g in data['groups'] if g['pps']=='ППС15')
        self.assertEqual([group[k] for k in ('total','assigned','unassigned','absent')], [3,1,1,1])
        worker = next(p for p in group['people'] if p['id']==self.people[0])
        self.assertEqual(len(worker['assignments']),2)
        absent = next(p for p in group['people'] if p['id']==self.people[2])
        self.assertEqual(absent['status'],'absent')
        self.assertTrue(absent['assigned'])
        with self.module.app.app_context():
            self.assertEqual(list(self.module.get_db().iterdump()), before)

    def test_filters_blank_values_and_effective_category(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            category = db.execute("INSERT INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at) VALUES ('Новая категория','новая категория',1,'t',?,'now')", (self.admin,)).lastrowid
            db.execute("""INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,'t',?,'now') ON CONFLICT(worker_id) DO UPDATE SET
                category_id=excluded.category_id,edit_token=excluded.edit_token,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at""", (self.people[0],category,self.admin))
            db.commit()
        self.assertEqual(self.get(pps='ППС15',category='Новая категория').get_json()['totals']['assigned'],1)
        self.assertEqual(self.get(pps='',category='').get_json()['totals']['total'],0)
        self.assertEqual(self.get(pps='Не существует').get_json()['groups'],[])

    def test_contractor_columns_use_catalog_and_preserve_totals_and_drill(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET contractor='Исходный подрядчик' WHERE id IN (?,?)", self.people[:2])
            contractor = db.execute("INSERT INTO contractors(name,name_key,edit_token,updated_by,updated_at) VALUES ('Подрядчик А','подрядчик а','t',?,'now')", (self.admin,)).lastrowid
            db.execute("INSERT INTO employee_contractors VALUES (?,?,'t',?,'now')", (self.people[0], contractor, self.admin))
            self.assign(db, self.people[1], '1 смена')
            db.commit()
        data = self.get().get_json()
        self.assertEqual(data['contractors'], ['', 'Исходный подрядчик', 'Подрядчик А'])
        group = next(g for g in data['groups'] if g['pps'] == 'ППС15')
        self.assertEqual(group['companies']['Подрядчик А']['assigned'], 1)
        self.assertEqual(group['companies']['Исходный подрядчик']['assigned'], 1)
        self.assertEqual(group['companies']['']['absent'], 1)
        self.assertEqual(sum(c['total'] for g in data['groups'] for c in g['companies'].values()), data['totals']['total'])
        calendar = self.client(self.admin).get('/api/calendar?start=2026-09-13&days=1').get_json()
        self.assertEqual({f['contractor'] for f in calendar['facts']}, {'Подрядчик А', 'Исходный подрядчик'})
        self.assertEqual(sum(f['day_count'] + f['night_count'] for f in calendar['facts']), 3)
        drill = self.client(self.admin).get('/api/staffing', query_string={'date':'2026-09-13', 'shift':'all',
            'calendar_sites':str(self.site), 'calendar_contractor':'Подрядчик А'}).get_json()
        self.assertEqual([r['id'] for r in drill['rows']], [self.people[0]])

    def test_inactive_roster_excluded_but_historical_assignments_kept(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id IN (?,?)', self.people[:2])
            db.execute('DELETE FROM staffing_import_members WHERE worker_id=?', (self.people[0],))
            db.commit()
        data = self.get().get_json()
        self.assertEqual(data['totals'], {'total':3,'assigned':1,'unassigned':1,'absent':1})
        empty_date = self.get(date='2026-09-14').get_json()
        self.assertEqual(empty_date['totals'], {'total':2,'assigned':0,'unassigned':2,'absent':0})

    def test_permissions_dates_and_smu_scope(self):
        self.assertEqual(self.client().get('/api/placement-report?date=2026-09-13').status_code,401)
        self.assertEqual(self.get(self.viewer).get_json()['totals']['total'],4)
        self.assertEqual(self.get(self.foreman).get_json()['totals']['total'],3)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (?,'selected','[\"СМУ 19\"]','t',?,'now')", (self.foreman,self.admin))
            db.commit()
        self.assertEqual(self.get(self.foreman).get_json()['totals']['total'],1)
        self.assertEqual(self.get(date='2026-02-30').status_code,400)
        self.assertEqual(self.get(category='x'*201).status_code,400)

    def test_pdf_download_is_protected_filtered_and_embeds_font(self):
        url = '/api/placement-report/pdf?date=2026-09-13&pps=ППС15'
        self.assertEqual(self.client().get(url).status_code,401)
        response = self.client(self.admin).get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertIn(b'DejaVuSans',response.data)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        detailed = self.client(self.admin).get(url+'&details=1')
        self.assertEqual(detailed.status_code,200)
        self.assertGreater(len(detailed.data),len(response.data))


if __name__ == '__main__':
    unittest.main()
