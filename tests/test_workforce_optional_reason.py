import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class OptionalReasonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.db = self.f.db

    def profile(self):
        response = self.f.request('get', f'people/{self.f.worker}')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['profile']

    def assert_blank_audit(self, worker_id, action=None, actor='admin', before_after=True):
        where = 'worker_id=%s'
        args = [worker_id]
        if action:
            where += ' AND action=%s'
            args.append(action)
        row = self.db.native(f'''SELECT actor_id,changed_at,before_json,after_json,reason
            FROM workforce_audit WHERE {where} ORDER BY id DESC LIMIT 1''', args).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['reason'], '')
        self.assertEqual(row['actor_id'], self.f.users[actor]['id'])
        self.assertIsNotNone(row['changed_at'])
        if before_after:
            self.assertIsNotNone(row['before_json'])
            self.assertIsNotNone(row['after_json'])
        return row

    def test_profile_empty_omitted_whitespace_audit_and_replay(self):
        cases = [
            ({'phone': '+7 900 000 00 01', 'reason': ''}, 'phone'),
            ({'email': 'optional.reason@example.test'}, 'email'),
            ({'messenger': 'telegram', 'reason': ' \t '}, 'messenger'),
        ]
        for changes, field in cases:
            with self.subTest(field=field):
                before = self.profile()
                body = {**changes, 'token': before['token'], 'request_key': str(uuid4())}
                response = self.f.request('patch', f'people/{self.f.worker}/profile', body, 'rotation')
                self.assertEqual(response.status_code, 200, response.json)
                replay = self.f.request('patch', f'people/{self.f.worker}/profile', body, 'rotation')
                self.assertEqual(replay.status_code, 200, replay.json)
                self.assertEqual(replay.json, response.json)
                audit = self.assert_blank_audit(self.f.worker, 'update', 'rotation')
                self.assertEqual(audit['before_json'][field], before[field])
                self.assertEqual(audit['after_json'][field], response.json[field])

    def test_card_stage_and_board_transition_allow_missing_or_whitespace_reason(self):
        direct = {'stage_code': 'stage.leave', 'effective_date': '2026-09-16',
                  'confirmed': True, 'request_key': str(uuid4())}
        response = self.f.request('post', f'people/{self.f.worker}/stage', direct, 'rotation')
        self.assertEqual(response.status_code, 201, response.json)
        self.assert_blank_audit(self.f.worker, 'create', 'rotation', before_after=False)

        board = self.f.board_move('stage.onsite', reason=' \n ')
        response = self.f.request('post', 'transitions', board, 'rotation')
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.f.request('post', 'transitions', board, 'rotation').status_code, 200)
        self.assert_blank_audit(self.f.worker, 'transition', 'rotation')

    def test_bulk_reference_and_table_apply_allow_omitted_reason(self):
        prepared = self.f.request('post', 'bulk/prepare', {'ids': [self.f.worker]})
        self.assertEqual(prepared.status_code, 200, prepared.json)
        data = prepared.json
        field = 'category_id'
        body = {'field': field, 'value': data['fields'][field]['options'][0]['value'],
                'reference_token': data['fields'][field]['token'],
                'people': [{key: row[key] for key in ('id', 'token')} for row in data['people']],
                'request_key': str(uuid4())}
        response = self.f.request('post', 'bulk/apply', body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assert_blank_audit(self.f.worker, 'update', 'admin')

        prepared = self.f.request('post', 'bulk/table/prepare', {'ids': [self.f.worker], 'date': '2026-09-18'})
        self.assertEqual(prepared.status_code, 200, prepared.json)
        data = prepared.json
        body = {'date': data['date'], 'people': [{key: row[key] for key in ('id', 'token')} for row in data['people']], 'field': 'arrival_date', 'value': '2026-09-17',
                'reference_token': data['fields']['arrival_date']['token'], 'extra': {}, 'request_key': str(uuid4())}
        response = self.f.request('post', 'bulk/table/apply', body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assert_blank_audit(self.f.worker, 'bulk_update', 'admin')

    def test_auto_gdlr_allows_empty_reason_and_preserves_idempotency(self):
        code = 'profession.' + uuid4().hex
        self.db.native("INSERT INTO workforce_catalog(code,kind,label) VALUES (%s,'profession',%s)",
                       (code, 'Профессия optional reason'))
        category = self.db.native('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,now()) RETURNING id''',
            ('Категория optional reason', 'category-optional-' + uuid4().hex, uuid4().hex, self.f.users['admin']['id'])).fetchone()['id']
        donor = self.db.native('''INSERT INTO workers(full_name,personnel_no,department,profession_code)
            VALUES ('Донор optional reason',%s,'TEST-SMU',%s) RETURNING id''',
            (uuid4().hex, code)).fetchone()['id']
        self.db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
            VALUES (%s,%s,%s,%s,now())''', (donor, category, uuid4().hex, self.f.users['admin']['id']))
        self.db.native('UPDATE workers SET profession_code=%s,category=%s WHERE id=%s', (code, '', self.f.worker))
        prepared = self.f.request('post', 'bulk/category-auto/prepare', {'ids': [self.f.worker]})
        self.assertEqual(prepared.status_code, 200, prepared.json)
        body = {'ids': [self.f.worker], 'token': prepared.json['token'], 'reason': '', 'request_key': str(uuid4())}
        first = self.f.request('post', 'bulk/category-auto/apply', body)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(self.f.request('post', 'bulk/category-auto/apply', body).json, first.json)
        self.assert_blank_audit(self.f.worker, 'auto_category')

    def test_my_day_departure_allows_omitted_reason_and_replay(self):
        from workforce_operations import register_workforce_operations
        register_workforce_operations(self.f.app, lambda: self.db, lambda *roles: lambda fn: fn)
        self.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
            VALUES (%s,'stage.onsite','2026-09-15',TRUE,'Исходный статус',%s,%s)''',
                       (self.f.worker, self.f.users['admin']['id'], uuid4()))
        self.db.native("UPDATE workforce_profiles SET forecast_departure_date='2026-09-16' WHERE worker_id=%s",
                       (self.f.worker,))
        operations = self.f.request('get', 'operations?date=2026-09-17&department=TEST-SMU', role='rotation')
        self.assertEqual(operations.status_code, 200, operations.data)
        item = next(row for row in operations.json['rows']
                    if row['kind'] == 'departure' and row['worker_id'] == self.f.worker)
        body = {'date': '2026-09-17', 'worker_id': self.f.worker, 'key': item['key'],
                'token': item['departure']['token'], 'actual_date': '2026-09-16', 'request_key': str(uuid4())}
        response = self.f.request('post', 'operations/departure', body, 'rotation')
        self.assertEqual(response.status_code, 200, response.json)
        replay = self.f.request('post', 'operations/departure', body, 'rotation')
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(replay.json, response.json)
        self.assert_blank_audit(self.f.worker, 'create', 'rotation', before_after=False)
    def test_schedule_rotation_extend_and_reschedule_allow_omitted_reason(self):
        schedule = self.f.request('post', 'rotation-schedules', {
            'name': 'График optional ' + uuid4().hex, 'onsite_days': 30, 'leave_days': 30,
            'travel_days': 2, 'request_key': str(uuid4())})
        self.assertEqual(schedule.status_code, 200, schedule.json)
        rotation = self.f.request('post', f'people/{self.f.worker}/rotations', {
            'schedule_id': schedule.json['id'], 'start_date': '2026-09-01', 'request_key': str(uuid4())}, 'rotation')
        self.assertEqual(rotation.status_code, 201, rotation.json)
        extension = self.f.request('post', f'people/{self.f.worker}/rotations/{rotation.json["id"]}/extend', {
            'new_end_date': '2026-10-05', 'token': rotation.json['edit_token'], 'request_key': str(uuid4())}, 'rotation')
        self.assertEqual(extension.status_code, 200, extension.json)

        movement = self.f.request('post', f'people/{self.f.worker}/movement', {
            'direction': 'arrival', 'destination_kind': 'site', 'planned_date': '2026-11-06',
            'request_key': str(uuid4())}, 'rotation')
        self.assertEqual(movement.status_code, 201, movement.json)
        rescheduled = self.f.request('post', f'people/{self.f.worker}/movements/{movement.json["id"]}/reschedule', {
            'planned_date': '2026-11-07', 'token': movement.json['edit_token'], 'request_key': str(uuid4())}, 'rotation')
        self.assertEqual(rescheduled.status_code, 201, rescheduled.json)
        self.assertEqual(self.db.native('SELECT count(*) FROM workforce_audit WHERE worker_id=%s AND reason=%s',
                                        (self.f.worker, '')).fetchone()[0], 5)


if __name__ == '__main__':
    unittest.main()




