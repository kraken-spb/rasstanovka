import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


class CrewColumnPreferencesTest(unittest.TestCase):
    def test_old_saved_orders_gain_crew_column_without_losing_settings(self):
        from user_preferences import COLUMNS, validate_patch
        for missing in ({'crew_number'}, {'crew_number', 'assignment_author'}):
            order = ['name', *sorted(COLUMNS - missing - {'name'})]
            result = validate_patch({'columns': {'order': order, 'hidden': ['employer'], 'widths': {'name': 220}}})['columns']
            self.assertEqual(result['order'][:len(order)], order)
            self.assertEqual(set(result['order']), COLUMNS)
            self.assertEqual(result['hidden'], ['employer'])
            self.assertEqual(result['widths'], {'name': 220})


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class WorkforceBulkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def prepare(self, ids=None, role='admin'):
        response = self.f.request('post', 'bulk/prepare', {'ids': ids or self.f.ids}, role)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def body(self, prepared, field, value=None):
        catalog = prepared['fields'][field]
        return {'field': field, 'value': value or catalog['options'][0]['value'],
                'reference_token': catalog['token'], 'people': [{key: row[key] for key in ('id', 'token')} for row in prepared['people']],
                'reason': 'Проверено массовое исправление', 'request_key': str(uuid4())}

    def test_all_reference_fields_sync_canonical_tables_and_audit(self):
        from workforce_bulk import FIELDS, snapshots
        f = self.f
        for field in FIELDS:
            with self.subTest(field=field):
                prepared = self.prepare()
                self.assertTrue(prepared['fields'][field]['options'], field)
                body = self.body(prepared, field)
                response = f.request('post', 'bulk/apply', body)
                self.assertEqual(response.status_code, 200, response.json)
                for row in snapshots(f.db, f.ids):
                    self.assertEqual(str(row[field]), body['value'])
                if field == 'employer_id':
                    expected = next(row['label'] for row in prepared['fields'][field]['options'] if row['value'] == body['value'])
                    self.assertEqual({row['employer'] for row in f.db.native('SELECT employer FROM employee_employers WHERE worker_id=ANY(%s)', (f.ids,))}, {expected})
                if field == 'profession_code':
                    self.assertTrue(all(row['profession'] for row in snapshots(f.db, f.ids)))
                if field == 'smu_id':
                    self.assertTrue(all(row['department'] for row in snapshots(f.db, f.ids)))
        audits = f.db.native('SELECT count(*) n FROM workforce_audit WHERE worker_id=ANY(%s) AND reason=%s', (f.ids, body['reason'])).fetchone()['n']
        self.assertGreaterEqual(audits, 14)

    def test_stale_worker_rolls_back_whole_selection(self):
        from workforce_bulk import snapshots
        f = self.f
        prepared = self.prepare()
        body = self.body(prepared, 'category_id')
        before = snapshots(f.db, f.ids)
        f.db.native("UPDATE workers SET full_name='Другое ФИО' WHERE id=%s", (f.ids[1],))
        response = f.request('post', 'bulk/apply', body)
        self.assertEqual(response.status_code, 409, response.json)
        after = snapshots(f.db, f.ids)
        self.assertEqual([row['category_id'] for row in after], [row['category_id'] for row in before])
        self.assertEqual(f.db.native('SELECT count(*) n FROM workforce_audit WHERE worker_id=ANY(%s)', (f.ids,)).fetchone()['n'], 0)

    def test_scope_service_ownership_and_inactive_workers(self):
        f = self.f
        for role in ('viewer', 'foreman', 'hr_viewer', 'recruitment'):
            self.assertEqual(f.request('post', 'bulk/prepare', {'ids':[f.worker]}, role).status_code, 403)
        self.assertEqual(f.request('post', 'bulk/prepare', {'ids': f.ids}, 'rotation').status_code, 404)
        prepared = self.prepare([f.worker], 'rotation')
        f.db.native("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=%s", (f.users['rotation']['id'],))
        self.assertEqual(f.request('post', 'bulk/apply', self.body(prepared, 'category_id'), 'rotation').status_code, 404)
        f.db.native('UPDATE workers SET active=0 WHERE id=%s', (f.worker,))
        self.assertEqual(f.request('post', 'bulk/prepare', {'ids':[f.worker]}).status_code, 409)

    def test_reference_revisions_and_unknown_values_are_rejected(self):
        f = self.f
        prepared = self.prepare()
        body = self.body(prepared, 'category_id')
        self.assertEqual(f.request('post', 'bulk/apply', {**body, 'value': '999999999'}).status_code, 400)
        f.db.native('UPDATE gdlr_categories SET edit_token=%s WHERE id=%s', (uuid4().hex, int(body['value'])))
        self.assertEqual(f.request('post', 'bulk/apply', body).status_code, 409)

    def test_staff_number_rule_and_recruitment_cannot_promote_to_staff(self):
        f = self.f
        f.db.native('UPDATE workers SET personnel_no=%s WHERE id=%s', ('998812345', f.worker))
        prepared = self.prepare([f.worker])
        nonstaff = next(row['value'] for row in prepared['fields']['employment_code']['options'] if row['value'] != 'employment.staff')
        self.assertEqual(f.request('post', 'bulk/apply', self.body(prepared, 'employment_code', nonstaff)).status_code, 400)
        f.db.native("UPDATE workers SET department='TEST-SMU' WHERE id=%s", (f.ids[1],))
        prepared = self.prepare([f.ids[1]], 'recruitment')
        self.assertNotIn('employment.staff', [row['value'] for row in prepared['fields']['employment_code']['options']])
        self.assertEqual(f.request('post', 'bulk/apply', self.body(prepared, 'employment_code', 'employment.staff'), 'recruitment').status_code, 400)

    def test_idempotent_retry_after_employment_changes_service_ownership(self):
        f = self.f
        prepared = self.prepare([f.worker], 'rotation')
        value = next(row['value'] for row in prepared['fields']['employment_code']['options'] if row['value'] != 'employment.staff')
        body = self.body(prepared, 'employment_code', value)
        first = f.request('post', 'bulk/apply', body, 'rotation')
        self.assertEqual(first.status_code, 200, first.json)
        second = f.request('post', 'bulk/apply', body, 'rotation')
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(first.json, second.json)
        self.assertEqual(f.db.native('SELECT count(*) n FROM workforce_audit WHERE worker_id=%s', (f.worker,)).fetchone()['n'], 1)
        self.assertEqual(f.request('post', 'bulk/apply', {**body, 'reason': 'Другой запрос'}, 'rotation').status_code, 409)

    def test_invalid_selection_and_unrelated_fields(self):
        f = self.f
        for ids in ([], [True], [f.worker, f.worker], ['1'], list(range(1, 102))):
            self.assertEqual(f.request('post', 'bulk/prepare', {'ids':ids}).status_code, 400)
        prepared = self.prepare()
        body = self.body(prepared, 'category_id')
        self.assertEqual(f.request('post', 'bulk/apply', {**body, 'field': 'full_name'}).status_code, 400)
        self.assertEqual(f.request('post', 'bulk/apply', {**body, 'reason': []}).status_code, 400)
        self.assertEqual(f.request('post', 'bulk/apply', {**body, 'reason': 'x' * 10001}).status_code, 400)

