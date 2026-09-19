"""Manual recruitment uses the same scoped registry and never invents an Excel source."""
import json
import os
import unittest
from uuid import uuid4

from tests import test_workforce_api as base


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class WorkforceCreateTest(unittest.TestCase):
    setUpClass = classmethod(base.WorkforceApiTest.setUpClass.__func__)
    setUp = base.WorkforceApiTest.setUp
    tearDown = base.WorkforceApiTest.tearDown
    request = base.WorkforceApiTest.request

    def data(self, role='admin', **changes):
        if role == 'recruitment':
            smu = self.db.native('SELECT name FROM smu_catalog WHERE active=1 ORDER BY id LIMIT 1').fetchone()['name']
            self.db.native('UPDATE user_smu_access SET departments_json=%s WHERE user_id=%s',
                           (json.dumps([smu]), self.users[role]['id']))
        response = self.request('get', 'people/create-options', role=role)
        self.assertEqual(response.status_code, 200, response.json)
        refs = response.json['fields']
        return {'full_name': 'Ручной Ёлкин ' + self.suffix, 'personnel_no': '',
                'smu_id': refs['smu_id']['options'][0]['value'], 'employment_code': 'employment.recruitment',
                'category_id': refs['category_id']['options'][0]['value'],
                'employer_id': refs['employer_id']['options'][0]['value'],
                'request_key': str(uuid4()), 'tokens': {key: value['token'] for key, value in refs.items()}, **changes}

    def test_create_is_visible_in_recruitment_audited_and_retry_safe(self):
        data = self.data('recruitment', email='new.worker@example.ru')
        first = self.request('post', 'people', data, role='recruitment')
        self.assertEqual(first.status_code, 201, first.json)
        worker = first.json['id']
        self.assertEqual(first.json['personnel_no'], '')
        self.assertEqual(first.json['email'], 'new.worker@example.ru')
        row = self.db.native('SELECT * FROM manual_employees WHERE worker_id=%s', (worker,)).fetchone()
        membership = self.db.native('SELECT service FROM workforce_registry_memberships WHERE worker_id=%s', (worker,)).fetchone()
        self.assertEqual(membership['service'], 'recruitment')
        self.assertEqual(row['created_by'], self.users['recruitment']['id'])
        self.assertEqual(self.db.native('SELECT count(*) count FROM workforce_source_records WHERE worker_id=%s', (worker,)).fetchone()['count'], 0)
        self.assertEqual(self.db.native('SELECT count(*) count FROM workforce_stage_events WHERE worker_id=%s', (worker,)).fetchone()['count'], 0)
        self.assertEqual(self.db.native("SELECT count(*) count FROM workforce_audit WHERE worker_id=%s AND action='create'", (worker,)).fetchone()['count'], 1)
        for section, visible in [('recruitment', True), ('rotation', False)]:
            rows = self.request('get', f'people?section={section}&q={self.suffix}', role='recruitment').json['rows']
            self.assertEqual(any(row['id'] == worker for row in rows), visible)
        repeat = self.request('post', 'people', data, role='recruitment')
        self.assertEqual(repeat.status_code, 200, repeat.json)
        self.assertEqual(repeat.json['id'], worker)
        changed = self.request('post', 'people', {**data, 'phone': '123'}, role='recruitment')
        self.assertEqual(changed.status_code, 409)

    def test_duplicates_include_inactive_names_and_preserve_leading_zeroes(self):
        data = self.data(personnel_no='000' + str(int(self.suffix[:10], 16)), employment_code='employment.staff')
        first = self.request('post', 'people', data)
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(first.json['personnel_no'], data['personnel_no'])
        self.db.native('UPDATE workers SET active=0 WHERE id=%s', (first.json['id'],))
        for changes in ({'full_name': 'Другое имя ' + self.suffix},
                        {'full_name': data['full_name'].upper().replace('Ё', 'Е'), 'personnel_no': ''}):
            result = self.request('post', 'people', {**data, **changes, 'request_key': str(uuid4())})
            self.assertEqual(result.status_code, 409, result.json)

    def test_roles_smu_scope_and_staff_rules_are_checked_on_server(self):
        data = self.data()
        for role in ('viewer', 'hr_viewer', 'foreman', 'rotation'):
            self.assertEqual(self.request('get', 'people/create-options', role=role).status_code, 403)
            self.assertEqual(self.request('post', 'people', data, role=role).status_code, 403)
        self.assertEqual(self.request('post', 'people', data, role='recruitment').status_code, 403)

        data = self.data('recruitment')
        self.assertEqual(self.request('post', 'people', {**data, 'employment_code': 'employment.staff'}, role='recruitment').status_code, 400)
        self.assertEqual(self.request('post', 'people', {**data, 'personnel_no': '00001234'}, role='recruitment').status_code, 400)
        self.db.native("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=%s", (self.users['recruitment']['id'],))
        self.assertEqual(self.request('post', 'people', data, role='recruitment').status_code, 403)

    def test_number_is_reserved_across_services_and_inactive_workers(self):
        number = '000' + str(int(self.suffix[:10], 16))
        owner = self.ids[1]
        self.db.native('UPDATE workers SET personnel_no=%s,personnel_is_internal=FALSE WHERE id=%s', (number, owner))
        self.db.native("INSERT INTO workforce_registry_memberships(worker_id,service,created_by) VALUES (%s,'rotation',%s)",
                       (owner, self.users['admin']['id']))
        before = self.db.native('SELECT count(*) n FROM workers').fetchone()['n']
        for active in (1, 0):
            self.db.native('UPDATE workers SET active=%s WHERE id=%s', (active, owner))
            response = self.request('post', 'people', self.data(personnel_no=number, employment_code='employment.staff'))
            self.assertEqual(response.status_code, 409, response.json)
            self.assertIn('Табельный номер уже занят', response.json['error'])
            self.assertNotIn('OTHER-SMU', response.json['error'])
            self.assertEqual(self.db.native('SELECT count(*) n FROM workers').fetchone()['n'], before)

    def test_stale_and_unknown_references_leave_no_partial_worker(self):
        data = self.data()
        data['tokens']['category_id'] = 'stale'
        result = self.request('post', 'people', data)
        self.assertEqual(result.status_code, 409, result.json)
        self.assertEqual(self.db.native('SELECT count(*) count FROM workers WHERE full_name=%s', (data['full_name'],)).fetchone()['count'], 0)
        result = self.request('post', 'people', {**data, 'profession_code': 'profession.missing'})
        self.assertEqual(result.status_code, 400, result.json)
