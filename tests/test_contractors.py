import io
import unittest
from unittest.mock import patch

import openpyxl

from contractor_api import populate_contractors
import test_staffing as staffing_tests
from staffing_import import apply_attendance, parse_attendance


class ContractorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        staffing_tests.StaffingWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        staffing_tests.StaffingWorkflowTest.tearDownClass()

    def setUp(self):
        self.fixture = staffing_tests.StaffingWorkflowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.apply()
        self.app = self.fixture.app
        self.module = self.fixture.module
        self.admin = self.fixture.admin
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.fixture.admin_id,))
            db.commit()
        self.headers = {'X-CSRF-Token': 'staffing-csrf'}
        self.row = next(row for row in self.fixture.table().get_json()['rows'] if row['personnel_no'] == '70001')
        self.url = f"/api/staffing/workers/{self.row['id']}/contractor"

    def create(self, name='Новый подрядчик'):
        result = self.admin.post('/api/contractors', json={'name': name}, headers=self.headers)
        self.assertEqual(result.status_code, 201)
        return result.get_json()['id']

    def bind(self, contractor_id, client=None, **changes):
        data = {'contractor_id': contractor_id, 'expected_token': self.row['contractor_token'],
                'expected_crew_id': self.row['crew_id'], **changes}
        return (client or self.admin).put(self.url, json=data, headers=self.headers)

    def test_seed_normalizes_duplicates_and_backs_up_before_population(self):
        with self.app.app_context():
            db = self.module.get_db()
            catalog_count = db.execute('SELECT COUNT(*) FROM contractors').fetchone()[0]
            bound_count = db.execute('SELECT COUNT(*) FROM employee_contractors').fetchone()[0]
            db.execute("INSERT INTO workers(full_name,personnel_no,contractor) VALUES ('Дубликат','duplicate','  лгсс  ')")
            db.execute("INSERT INTO workers(full_name,personnel_no,contractor) VALUES ('Пустой','blank','')")
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM workers ORDER BY id')]
            with patch('staffing_import.backup_database') as backup:
                populate_contractors(db)
                backup.assert_called_once_with(db)
                populate_contractors(db)
                backup.assert_called_once()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM contractors').fetchone()[0], catalog_count)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM employee_contractors').fetchone()[0], bound_count + 1)
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM workers ORDER BY id')])

    def test_correction_survives_import_and_reaches_export(self):
        contractor_id = self.create()
        result = self.bind(contractor_id)
        self.assertEqual(result.status_code, 200)
        with self.app.app_context():
            db = self.module.get_db()
            parsed = parse_attendance(staffing_tests.workbook_bytes([{'category': 'Новая категория'}]), 'повторный.xlsx')
            apply_attendance(db, parsed, self.fixture.admin_id, self.module.utc_now)
            site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
            db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at)
                VALUES ('2026-09-12','1 смена',?,?,'Работодатель',?,'now')''',
                (site, self.row['id'], self.fixture.admin_id))
            db.commit()
            before = [tuple(row) for row in db.execute('SELECT * FROM assignments')]
            self.assertEqual(db.execute('SELECT contractor FROM workers WHERE id=?', (self.row['id'],)).fetchone()[0], 'ЛГСС')
        current = next(row for row in self.fixture.table().get_json()['rows'] if row['id'] == self.row['id'])
        self.assertEqual(current['contractor'], 'Новый подрядчик')
        response = self.admin.get('/api/staffing/export?date=2026-09-12&shift=all')
        self.assertEqual(response.status_code, 200)
        book = openpyxl.load_workbook(io.BytesIO(response.data))
        self.assertEqual(book.active['D2'].value, 'Новый подрядчик')
        with self.app.app_context():
            self.assertEqual(before, [tuple(row) for row in self.module.get_db().execute('SELECT * FROM assignments')])

    def test_catalog_validation_permissions_and_stale_writes(self):
        contractor_id = self.create()
        duplicate = self.admin.post('/api/contractors', json={'name': '  новый   ПОДРЯДЧИК  '}, headers=self.headers)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.fixture.foreman.post('/api/contractors', json={'name': 'Нет'}, headers=self.headers).status_code, 403)
        self.assertEqual(self.fixture.viewer.get('/api/contractors').status_code, 403)
        self.assertEqual(self.admin.post('/api/contractors', json={'name': 'Без CSRF'}).status_code, 403)
        self.assertEqual(self.bind(contractor_id, self.fixture.foreman).status_code, 403)
        self.assertEqual(self.bind(contractor_id, expected_crew_id=None).status_code, 409)
        self.assertEqual(self.bind(999999).status_code, 400)
        self.assertEqual(self.bind(contractor_id).status_code, 200)
        self.assertEqual(self.bind(contractor_id).status_code, 409)
        item = next(row for row in self.admin.get('/api/contractors').get_json()['rows'] if row['id'] == contractor_id)
        payload = {'name': 'Переименован', 'active': False, 'expected_token': item['edit_token']}
        self.assertEqual(self.admin.patch('/api/contractors/' + str(contractor_id), json=payload, headers=self.headers).status_code, 200)
        self.assertEqual(self.admin.patch('/api/contractors/' + str(contractor_id), json=payload, headers=self.headers).status_code, 409)
        self.row = next(row for row in self.fixture.table().get_json()['rows'] if row['id'] == self.row['id'])
        self.assertEqual(self.row['contractor'], 'Переименован')
        self.assertEqual(self.bind(contractor_id).status_code, 400)

    def test_foreman_can_correct_own_crew_only(self):
        contractor_id = self.create()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.fixture.foreman_id, self.row['crew_id']))
            db.commit()
        self.assertEqual(self.bind(contractor_id, self.fixture.foreman).status_code, 200)

    def test_selected_company_drives_calendar_and_drill_without_rewriting_employers(self):
        target = self.create('Монтажная компания')
        other = next(row for row in self.fixture.table().get_json()['rows'] if row['personnel_no'] == '70002')
        with self.app.app_context():
            db = self.module.get_db()
            lgss_id = db.execute("SELECT id FROM contractors WHERE name_key='лгсс'").fetchone()[0]
            for worker_id, shift, employer in [(self.row['id'], '1 смена', 'ЛГСС'),
                    (self.row['id'], 'Ночная смена', 'СТНГ'), (other['id'], '1 смена', 'СТНГ')]:
                db.execute('''INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id)
                    VALUES ('2026-09-13',?,1,?, ?,?,'now',?)''', (shift, worker_id, employer, self.fixture.admin_id, self.row['crew_id']))
            db.commit()
            source = [tuple(r) for r in db.execute('SELECT * FROM assignments ORDER BY id')]
        def facts():
            response = self.admin.get('/api/calendar?start=2026-09-13&days=1')
            self.assertEqual(response.status_code, 200)
            return {r['employer']: (r['day_count'], r['night_count']) for r in response.json['facts']}
        def drill(company, **extra):
            response = self.admin.get('/api/staffing', query_string={'date': '2026-09-13', 'shift': 'all',
                'calendar_sites': '1', 'calendar_employer': company, **extra})
            self.assertEqual(response.status_code, 200)
            return response.json
        self.assertEqual(facts(), {'ЛГСС': (1, 0), 'СТНГ': (1, 1)})
        self.assertEqual(self.bind(target).status_code, 200)
        self.assertEqual(facts(), {'Монтажная компания': (1, 1), 'СТНГ': (1, 0)})
        result = drill('Монтажная компания')
        self.assertEqual([r['id'] for r in result['rows']], [self.row['id']])
        self.assertEqual(result['calendar_assignment_count'], 2)
        self.assertEqual(drill('Монтажная компания', calendar_shift='2 смена')['calendar_assignment_count'], 1)
        self.assertEqual(drill('Монтажная компания', view='summary')['index'][0]['id'], self.row['id'])
        self.assertEqual([r['id'] for r in drill('СТНГ')['rows']], [other['id']])
        self.assertEqual(drill('ЛГСС')['rows'], [])
        self.assertEqual(self.bulk(self.bulk_payload(target)).status_code, 200)
        self.assertEqual(facts(), {'Монтажная компания': (2, 1)})
        self.assertEqual(self.bulk(self.bulk_payload(lgss_id)).status_code, 200)
        self.assertEqual(facts(), {'ЛГСС': (1, 0), 'СТНГ': (1, 1)})
        with self.app.app_context():
            self.assertEqual(source, [tuple(r) for r in self.module.get_db().execute('SELECT * FROM assignments ORDER BY id')])

    def bulk_payload(self, contractor_id):
        rows = [r for r in self.fixture.table().get_json()['rows'] if r['crew_id'] == self.row['crew_id']]
        self.assertGreaterEqual(len(rows), 2)
        return {'contractor_id': contractor_id, 'worker_ids': [r['id'] for r in rows],
                'expected_tokens': {str(r['id']): r['contractor_token'] for r in rows},
                'expected_crews': {str(r['id']): r['crew_id'] for r in rows},
                'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}

    def bulk(self, data, client=None):
        return (client or self.admin).put('/api/staffing/groups/contractor', json=data, headers=self.headers)

    def bindings(self):
        with self.app.app_context():
            return [tuple(r) for r in self.module.get_db().execute('SELECT * FROM employee_contractors ORDER BY worker_id')]

    def test_bulk_updates_selected_only_and_preserves_source(self):
        target = self.create()
        data = self.bulk_payload(target)
        data['worker_ids'] = data['worker_ids'][:1]
        before = self.bindings()
        result = self.bulk(data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual([r['id'] for r in result.json['rows']], data['worker_ids'])
        after = self.bindings()
        self.assertEqual([r for r in before if r[0] not in data['worker_ids']],
                         [r for r in after if r[0] not in data['worker_ids']])
        with self.app.app_context():
            self.assertEqual(self.module.get_db().execute('SELECT contractor FROM workers WHERE id=?',
                             (self.row['id'],)).fetchone()[0], 'ЛГСС')

    def test_bulk_stale_member_rolls_back_everyone(self):
        data = self.bulk_payload(self.create())
        data['expected_tokens'][str(data['worker_ids'][-1])] = 'stale'
        before = self.bindings()
        self.assertEqual(self.bulk(data).status_code, 409)
        self.assertEqual(self.bindings(), before)

    def test_bulk_membership_group_and_permissions_are_checked(self):
        target = self.create()
        data = self.bulk_payload(target)
        before = self.bindings()
        self.assertEqual(self.bulk(data, self.fixture.foreman).status_code, 403)
        self.assertEqual(self.bulk(data, self.fixture.viewer).status_code, 403)
        self.assertEqual(self.admin.put('/api/staffing/groups/contractor', json=data).status_code, 403)
        key = str(data['worker_ids'][-1])
        data['expected_crews'][key] += 1
        self.assertEqual(self.bulk(data).status_code, 409)
        data = self.bulk_payload(target)
        data['expected_group_tokens'][key] = 'stale'
        self.assertEqual(self.bulk(data).status_code, 409)
        self.assertEqual(self.bindings(), before)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.fixture.foreman_id, self.row['crew_id']))
            db.commit()
        data = self.bulk_payload(target)
        self.assertEqual(self.bulk(data, self.fixture.foreman).status_code, 200)
        self.assertTrue(all(r[1] == target for r in self.bindings() if r[0] in data['worker_ids']))

    def test_bulk_rejects_invalid_and_inactive_targets(self):
        target = self.create()
        data = self.bulk_payload(target)
        before = self.bindings()
        for change in ({'worker_ids': []}, {'worker_ids': [True]}, {'expected_tokens': {}}, {'contractor_id': 999999}):
            self.assertEqual(self.bulk({**data, **change}).status_code, 400)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE contractors SET active=0 WHERE id=?', (target,))
            db.commit()
        self.assertEqual(self.bulk(data).status_code, 400)
        self.assertEqual(self.bindings(), before)
