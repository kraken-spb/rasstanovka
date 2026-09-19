import os
import unittest
from collections import defaultdict
from uuid import uuid4

from workforce_operations import departures_for, register_workforce_operations


def person(**changes):
    return {
        'id': 1, 'full_name': 'Тестовый сотрудник', 'department': 'ТЕСТ-СМУ', 'active': True,
        'stage_code': 'stage.onsite', 'effective_date': '2026-09-15', 'arrival_date': '2026-09-15',
        'forecast_departure_date': '2026-09-16', 'profile_token': 'p' * 64, 'stage_revision': 7,
        'cycle_start': '2026-09-15', 'cycle_sequence': 7, **changes,
    }


def related():
    return {key: defaultdict(list) for key in ('movements', 'documents', 'conflicts', 'rotations')}


class DepartureCalculationTest(unittest.TestCase):
    def test_due_stage_and_historical_cycle_rules(self):
        data = related()
        due = departures_for(person(), data, '2026-09-17')
        self.assertEqual(len(due), 1)
        item = due[0]
        self.assertEqual((item['key'], item['kind'], item['due_date']), ('departure:profile:1', 'departure', '2026-09-16'))
        self.assertEqual(item['departure'], {
            'source': 'profile', 'source_label': 'Прогноз окончания вахты', 'planned_date': '2026-09-16',
            'movement_id': None, 'rotation_id': None, 'min_date': '2026-09-15', 'token': item['departure']['token'],
        })
        self.assertEqual(len(item['departure']['token']), 64)
        outbound = departures_for(person(stage_code='stage.outbound'), data, '2026-09-17')
        self.assertEqual((outbound[0]['key'], outbound[0]['due_date'], outbound[0]['departure']['source']),
                         (item['key'], item['due_date'], item['departure']['source']))
        self.assertNotEqual(outbound[0]['departure']['token'], item['departure']['token'])
        self.assertEqual(departures_for(person(stage_code='stage.inbound'), data, '2026-09-17'), [])
        self.assertEqual(departures_for(person(forecast_departure_date='2026-09-18'), data, '2026-09-17'), [])
        data['movements'][1].append({'id': str(uuid4()), 'direction': 'departure', 'actual_date': '2026-09-16',
                                     'result_code': 'result.happened', 'event_sequence': 8})
        self.assertEqual(departures_for(person(), data, '2026-09-17'), [])

    def test_pending_movement_overrides_forecast_and_old_or_rescheduled_plans_do_not(self):
        data = related()
        old, current, future = str(uuid4()), str(uuid4()), str(uuid4())
        data['movements'][1] = [
            {'id': old, 'direction': 'departure', 'planned_date': '2026-09-10', 'result_code': None},
            {'id': current, 'direction': 'departure', 'planned_date': '2026-09-16', 'basis_code': 'basis.ticket',
             'result_code': None},
        ]
        item = departures_for(person(forecast_departure_date='2026-09-15'), data, '2026-09-17')[0]
        self.assertEqual(item['key'], 'departure:movement:' + current)
        self.assertEqual(item['departure']['source_label'], 'Дата по билету')
        self.assertEqual(item['departure']['movement_id'], current)
        data['movements'][1] = [
            {'id': old, 'direction': 'departure', 'planned_date': '2026-09-16', 'result_code': 'result.postponed'},
            {'id': future, 'direction': 'departure', 'planned_date': '2026-09-20', 'result_code': None,
             'rescheduled_from': old},
        ]
        # A later pending replacement governs the decision; an older profile forecast must not leak through.
        self.assertEqual(departures_for(person(forecast_departure_date='2026-09-15'), data, '2026-09-17'), [])

    def test_rotation_then_profile_fallback_and_tokens_track_changed_sources(self):
        data = related()
        rotation = str(uuid4())
        data['rotations'][1].append({'id': rotation, 'start_date': '2026-09-01', 'planned_end_date': '2026-09-16',
                                     'cancelled': False, 'actual_end_date': None, 'edit_token': 'r' * 64})
        item = departures_for(person(), data, '2026-09-17')[0]
        self.assertEqual((item['key'], item['departure']['source'], item['departure']['rotation_id']),
                         ('departure:rotation:' + rotation, 'rotation', rotation))
        token = item['departure']['token']
        data['rotations'][1][0]['planned_end_date'] = '2026-09-17'
        self.assertNotEqual(departures_for(person(), data, '2026-09-17')[0]['departure']['token'], token)
        data['rotations'][1][0]['cancelled'] = True
        fallback = departures_for(person(), data, '2026-09-17')[0]
        self.assertEqual((fallback['key'], fallback['departure']['source']), ('departure:profile:1', 'profile'))


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class DeparturePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_workforce_api as fixtures
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        import test_workforce_api as fixtures
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        register_workforce_operations(self.f.app, lambda: self.f.db, lambda *roles: lambda fn: fn)
        f = self.f
        f.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
            VALUES (%s,'stage.onsite','2026-09-15',TRUE,'Тест',%s,%s)''',
                    (f.worker, f.users['admin']['id'], uuid4()))
        f.db.native("UPDATE workforce_profiles SET staffing_ready=TRUE,forecast_departure_date='2026-09-16' WHERE worker_id=%s",
                    (f.worker,))

    def departure_item(self, day='2026-09-17'):
        response = self.f.request('get', f'operations?date={day}&department=TEST-SMU', role='rotation')
        self.assertEqual(response.status_code, 200, response.data)
        return next(item for item in response.json['rows']
                    if item['kind'] == 'departure' and item['worker_id'] == self.f.worker)

    def body(self, item, actual='2026-09-16'):
        return {'date': '2026-09-17', 'worker_id': self.f.worker, 'key': item['key'],
                'token': item['departure']['token'], 'actual_date': actual,
                'reason': 'Фактический выезд подтверждён', 'request_key': str(uuid4())}

    def test_profile_departure_is_replayable_linked_and_keeps_staffing_state(self):
        f = self.f
        item = self.departure_item()
        body = self.body(item)
        response = f.request('post', 'operations/departure', body, 'rotation')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(f.request('post', 'operations/departure', body, 'rotation').status_code, 200)
        result = response.json
        movement = f.db.native('SELECT * FROM workforce_movements WHERE id=%s', (result['movement_id'],)).fetchone()
        self.assertEqual((movement['direction'], movement['destination_kind'], movement['actual_date'].isoformat(),
                          movement['result_code'], str(movement['stage_event_id'])),
                         ('departure', 'home', '2026-09-16', 'result.happened', result['stage_event_id']))
        event = f.db.native('SELECT stage_code,effective_date FROM workforce_stage_events WHERE id=%s',
                            (result['stage_event_id'],)).fetchone()
        self.assertEqual((event['stage_code'], event['effective_date'].isoformat()), ('stage.leave', '2026-09-16'))
        self.assertTrue(f.db.native('SELECT staffing_ready FROM workforce_profiles WHERE worker_id=%s',
                                    (f.worker,)).fetchone()[0])
        self.assertEqual(f.db.native("SELECT count(*) FROM workforce_audit WHERE worker_id=%s AND entity_type='movement'",
                                     (f.worker,)).fetchone()[0], 1)
        operations = f.request('get', 'operations?date=2026-09-17&department=TEST-SMU', role='rotation').json
        self.assertFalse(any(item['kind'] == 'departure' and item['worker_id'] == f.worker for item in operations['rows']))

    def test_rotation_departure_closes_rotation_and_pending_movement_is_updated(self):
        f = self.f
        schedule = f.db.native('SELECT id FROM workforce_rotation_schedules LIMIT 1').fetchone()['id']
        rotation = f.db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,start_date,planned_end_date,
            leave_end_date,next_arrival_date,request_key,created_by,updated_by)
            VALUES (%s,%s,'{}','2026-09-01','2026-09-16','2026-10-16','2026-10-17',%s,%s,%s) RETURNING id''',
                               (f.worker, schedule, uuid4(), f.users['admin']['id'], f.users['admin']['id'])).fetchone()['id']
        item = self.departure_item()
        self.assertEqual(item['departure']['rotation_id'], str(rotation))
        self.assertEqual(self.f.request('post', 'operations/departure', self.body(item), 'rotation').status_code, 200)
        self.assertEqual(f.db.native('SELECT actual_end_date FROM workforce_rotations WHERE id=%s',
                                     (rotation,)).fetchone()[0].isoformat(), '2026-09-16')

    def test_pending_departure_movement_is_confirmed_without_creating_another(self):
        f = self.f
        movement = f.db.native('''INSERT INTO workforce_movements(worker_id,direction,destination_kind,planned_date,request_key,created_by,updated_by)
            VALUES (%s,'departure','home','2026-09-16',%s,%s,%s) RETURNING id''',
                               (f.worker, uuid4(), f.users['admin']['id'], f.users['admin']['id'])).fetchone()['id']
        item = self.departure_item()
        self.assertEqual(item['departure']['movement_id'], str(movement))
        self.assertEqual(f.request('post', 'operations/departure', self.body(item), 'rotation').status_code, 200)
        saved = f.db.native('SELECT actual_date,result_code FROM workforce_movements WHERE id=%s', (movement,)).fetchone()
        self.assertEqual((saved['actual_date'].isoformat(), saved['result_code']), ('2026-09-16', 'result.happened'))
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s',
                                     (f.worker,)).fetchone()[0], 1)

    def test_departure_rejects_invalid_dates_stale_versions_roles_and_foreign_scope_without_writes(self):
        f = self.f
        item = self.departure_item()
        before = (f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s', (f.worker,)).fetchone()[0],
                  f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s', (f.worker,)).fetchone()[0])
        for actual in ('bad', '2026-09-14', '2026-09-18'):
            response = f.request('post', 'operations/departure', self.body(item, actual), 'rotation')
            self.assertEqual(response.status_code, 400, response.json)
        for role in ('foreman', 'recruitment', 'hr_viewer', 'viewer'):
            self.assertEqual(f.request('post', 'operations/departure', self.body(item), role).status_code, 403)
        foreign = self.body(item)
        foreign['worker_id'] = f.ids[1]
        self.assertEqual(f.request('post', 'operations/departure', foreign, 'rotation').status_code, 404)
        f.db.native("UPDATE workforce_profiles SET forecast_departure_date='2026-09-17' WHERE worker_id=%s", (f.worker,))
        self.assertEqual(f.request('post', 'operations/departure', self.body(item), 'rotation').status_code, 409)
        self.assertEqual((f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s', (f.worker,)).fetchone()[0],
                          f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s', (f.worker,)).fetchone()[0]), before)

    def test_new_onsite_cycle_and_rotation_extension_stale_the_departure(self):
        f = self.f
        item = self.departure_item()
        f.db.native('''INSERT INTO workforce_stage_events(worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
            VALUES (%s,'stage.onsite','2026-09-17',TRUE,'Новый цикл',%s,%s)''',
                    (f.worker, f.users['admin']['id'], uuid4()))
        self.assertEqual(f.request('post', 'operations/departure', self.body(item), 'rotation').status_code, 409)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s',
                                     (f.worker,)).fetchone()[0], 0)

        schedule = f.db.native('SELECT id FROM workforce_rotation_schedules LIMIT 1').fetchone()['id']
        rotation = f.db.native('''INSERT INTO workforce_rotations(worker_id,schedule_id,schedule_snapshot,start_date,planned_end_date,
            leave_end_date,next_arrival_date,request_key,created_by,updated_by)
            VALUES (%s,%s,'{}','2026-09-17','2026-09-17','2026-10-17','2026-10-18',%s,%s,%s) RETURNING id''',
                               (f.worker, schedule, uuid4(), f.users['admin']['id'], f.users['admin']['id'])).fetchone()['id']
        f.db.native("UPDATE workforce_profiles SET forecast_departure_date='2026-09-17' WHERE worker_id=%s", (f.worker,))
        item = self.departure_item()
        self.assertEqual(item['departure']['rotation_id'], str(rotation))
        f.db.native("UPDATE workforce_rotations SET planned_end_date='2026-09-18',edit_token=gen_random_uuid() WHERE id=%s",
                    (rotation,))
        self.assertEqual(f.request('post', 'operations/departure', self.body(item, '2026-09-17'), 'rotation').status_code, 409)
