import sqlite3
import unittest
from unittest.mock import patch

import test_crews as crew_tests
from staffing_shifts import responsibility_states


class CreateCrewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        crew_tests.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        crew_tests.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.f = crew_tests.CrewWorkflowTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.ids = self.f.worker_ids[:2]
        self.assertEqual(self.f.add(self.f.crew_a, self.ids).status_code, 200)

    def payload(self, ids=None):
        ids = self.ids if ids is None else ids
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            states = responsibility_states(db, ids)
            crews = dict(db.execute('SELECT worker_id,crew_id FROM crew_members'))
        return {'name': 'Новая бригада', 'owner_user_id': self.f.owner_b,
                'linear_itr': 'Иванов Иван Иванович', 'brigadier': 'Петров Пётр Петрович',
                'worker_ids': ids, 'expected_crews': {str(i): crews.get(i) for i in ids},
                'expected_group_tokens': {str(i): states[i]['group_token'] for i in ids}}

    def create(self, payload, client=None):
        return self.f.write(client or self.f.admin, 'POST', '/api/crews', payload)

    def test_atomic_membership_preserves_assignments_and_row_details_with_backup(self):
        self.assertEqual(self.f.place(self.ids, self.f.sites[0]).status_code, 200)
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute("INSERT INTO staffing_row_details(worker_id,linear_itr_override,edit_token,updated_by,updated_at) VALUES (?,'Индивидуальный ИТР','token',?,'now')", (self.ids[0], self.f.admin_id))
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM assignments')]
        response = self.create(self.payload())
        self.assertEqual(response.status_code, 201, response.get_json())
        crew = response.get_json()['id']
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM assignments')])
            self.assertEqual({row[0] for row in db.execute('SELECT worker_id FROM crew_members WHERE crew_id=?', (crew,))}, set(self.ids))
            self.assertEqual(db.execute('SELECT linear_itr,brigadier FROM crews WHERE id=?', (crew,)).fetchone()[:], ('Иванов Иван Иванович', 'Петров Пётр Петрович'))
            self.assertEqual(db.execute('SELECT linear_itr_override FROM staffing_row_details WHERE worker_id=?', (self.ids[0],)).fetchone()[0], 'Индивидуальный ИТР')
        backups = list((self.f.module.DATABASE_PATH.parent / 'backups').glob('backup-*.sqlite3'))
        self.assertTrue(backups)
        with sqlite3.connect(max(backups, key=lambda p: p.stat().st_mtime_ns)) as backup:
            self.assertEqual(backup.execute('SELECT crew_id FROM crew_members WHERE worker_id=?', (self.ids[0],)).fetchone()[0], self.f.crew_a)

    def test_empty_and_unassigned_members_duplicate_does_not_create_twice(self):
        result = self.create(self.payload([]))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.get_json()['member_count'], 0)
        self.assertEqual(self.create(self.payload([])).status_code, 409)
        data = self.payload([self.f.worker_ids[2]])
        data['name'] = 'Из свободных сотрудников'
        self.assertEqual(self.create(data).status_code, 201)

    def test_stale_roster_or_responsible_rolls_back_entire_creation(self):
        for field in ('expected_crews', 'expected_group_tokens'):
            data = self.payload()
            data[field][str(self.ids[1])] = self.f.crew_b if field == 'expected_crews' else 'stale'
            self.assertEqual(self.create(data).status_code, 409)
            with self.f.module.app.app_context():
                db = self.f.module.get_db()
                self.assertFalse(db.execute('SELECT id FROM crews WHERE name=?', (data['name'],)).fetchone())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM crew_members WHERE crew_id=?', (self.f.crew_a,)).fetchone()[0], 2)

    def test_permissions_csrf_and_disabled_owner(self):
        data = self.payload()
        for client in (self.f.foreman_a, self.f.viewer):
            self.assertEqual(self.create(data, client).status_code, 403)
            self.assertNotIn('id="staffing-create-crew"', client.get('/').get_data(as_text=True))
        self.assertEqual(self.f.admin.post('/api/crews', json=data).status_code, 403)
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.f.owner_b,))
            db.commit()
        self.assertEqual(self.create(data).status_code, 400)

    def test_invalid_fields_and_backup_failure_leave_no_new_crew(self):
        for change in ({'name': ' '}, {'name': 123}, {'worker_ids': [True]}, {'linear_itr': ['ФИО']}, {'expected_crews': {}}):
            self.assertEqual(self.create({**self.payload(), **change}).status_code, 400)
        with patch('backup_api.create_backup', side_effect=OSError('backup failure')):
            with self.assertRaises(OSError):
                self.create(self.payload())
        with self.f.module.app.app_context():
            self.assertFalse(self.f.module.get_db().execute("SELECT id FROM crews WHERE name='Новая бригада'").fetchone())

    def test_missing_owner_uses_creator_and_selected_people_keep_identity(self):
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            batch = db.execute("INSERT INTO staffing_imports(filename,sha256,imported_by,imported_at,selected_count,summary_json) VALUES ('people.xlsx','people-fixture',?,'now',0,'{}')", (self.f.admin_id,)).lastrowid
            people = []
            for n, name in enumerate(('Иванов Иван Иванович','Петров Пётр Петрович'), 1):
                people.append(db.execute("INSERT INTO staffing_people(import_id,source_row,full_name,personnel_no,profession,department,qualification,source_crew) VALUES (?,?,?,?,'Инженер','Участок','Специалист','')", (batch,n,name,str(n))).lastrowid)
            db.commit()
        data = self.payload([])
        data.pop('owner_user_id')
        data.update(linear_itr_person_id=people[0],brigadier_person_id=people[1])
        response = self.create(data)
        self.assertEqual(response.status_code,201,response.get_json())
        with self.f.module.app.app_context():
            row = self.f.module.get_db().execute('SELECT owner_user_id,linear_itr_person_id,brigadier_person_id FROM crews WHERE id=?',(response.get_json()['id'],)).fetchone()
            self.assertEqual(tuple(row),(self.f.admin_id,*people))
        for invalid in (True, str(people[0]), -1, 999999, people[1]):
            rejected = self.create({**data,'name':'Неверная привязка','linear_itr_person_id':invalid})
            self.assertEqual(rejected.status_code,400)
        with self.f.module.app.app_context():
            self.assertFalse(self.f.module.get_db().execute("SELECT id FROM crews WHERE name='Неверная привязка'").fetchone())
