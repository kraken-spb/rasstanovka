import os
import unittest
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class TransitionTicketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def leave(self, people=None):
        body = self.f.board_move('stage.leave', people)
        self.assertEqual(self.f.request('post', 'transitions', body).status_code, 201)

    def ticket(self, worker=None, **changes):
        return {'worker_id': worker or self.f.worker, 'planned_date': '2026-09-15',
                'travel_details': 'Билет 123, рейс ТЕСТ-45, 14:30', **changes}

    def card_stage(self, stage, ticket=None, **changes):
        return {'stage_code': stage, 'effective_date': '2026-09-16', 'confirmed': True,
                'reason': 'Подтверждено в карточке', 'request_key': str(uuid4()),
                **({'tickets': [ticket]} if ticket else {}), **changes}

    def test_ticket_and_event_are_linked_audited_and_replay_does_not_duplicate(self):
        f = self.f
        self.leave()
        future = f.request('post', f'people/{f.worker}/movement', f.movement(
            actual_date=None, planned_date='2026-10-10', result_code=None), 'rotation').json
        body = f.board_move('stage.onsite', tickets=[self.ticket()])
        response = f.request('post', 'transitions', body, 'rotation')
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(f.request('post', 'transitions', body, 'rotation').status_code, 200)
        movements = f.request('get', f'people/{f.worker}').json['movements']
        self.assertEqual(len(movements), 2)
        ticket = next(row for row in movements if row['basis_code'] == 'basis.ticket')
        self.assertEqual(ticket['travel_details'], self.ticket()['travel_details'])
        self.assertEqual((ticket['direction'], ticket['destination_kind'], ticket['result_code']),
                         ('arrival', 'site', 'result.happened'))
        self.assertEqual(ticket['actual_date'], '2026-09-16')
        self.assertEqual(ticket['planned_date'], '2026-09-15')
        event = f.db.native('SELECT * FROM workforce_stage_events WHERE id=%s', (ticket['stage_event_id'],)).fetchone()
        self.assertEqual(event['stage_code'], 'stage.onsite')
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s', (f.worker,)).fetchone()[0], 2)
        self.assertEqual(next(row for row in movements if row['id'] == future['id'])['planned_date'], '2026-10-10')
        audit = f.db.native("SELECT count(*) FROM workforce_audit WHERE worker_id=%s AND entity_type='movement' AND entity_id=%s", (f.worker, ticket['id'])).fetchone()[0]
        self.assertEqual(audit, 1)
        changed = {**body, 'tickets': [self.ticket(travel_details='Другой билет')]}
        self.assertEqual(f.request('post', 'transitions', changed, 'rotation').status_code, 409)

    def test_leave_to_inbound_creates_distinct_planned_tickets_without_arrival(self):
        f = self.f
        ready_before = f.db.native('SELECT count(*) FROM workforce_profiles WHERE worker_id=ANY(%s) AND staffing_ready',
                                   (f.ids,)).fetchone()[0]
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        self.leave(people)
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        first = self.ticket(f.ids[0], planned_date='2026-09-17', travel_details='Билет первого сотрудника')
        second = self.ticket(f.ids[1], planned_date='2026-09-18', travel_details='Билет второго сотрудника')
        body = f.board_move('stage.inbound', people, tickets=[first, second])
        response = f.request('post', 'transitions', body)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(f.request('post', 'transitions', body).status_code, 200)
        tickets = f.db.native('''SELECT worker_id,planned_date,travel_details,actual_date,result_code,basis_code,
            destination_kind,stage_event_id FROM workforce_movements WHERE worker_id=ANY(%s) ORDER BY worker_id''',
            (f.ids,)).fetchall()
        self.assertEqual(len(tickets), 2)
        expected = {first['worker_id']: first, second['worker_id']: second}
        for ticket in tickets:
            source = expected[ticket['worker_id']]
            self.assertEqual((ticket['planned_date'].isoformat(), ticket['travel_details']),
                             (source['planned_date'], source['travel_details']))
            self.assertEqual((ticket['actual_date'], ticket['result_code'], ticket['basis_code'], ticket['destination_kind']),
                             (None, None, 'basis.ticket', 'site'))
            event = f.db.native('SELECT stage_code FROM workforce_stage_events WHERE id=%s',
                                (ticket['stage_event_id'],)).fetchone()
            self.assertEqual(event['stage_code'], 'stage.inbound')
        self.assertTrue(all(f.board_row(worker)['stage_code'] == 'stage.inbound' for worker in f.ids))
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_profiles WHERE worker_id=ANY(%s) AND staffing_ready',
                                     (f.ids,)).fetchone()[0], ready_before)

    def test_inbound_ticket_validation_and_stale_selection_are_atomic(self):
        f = self.f
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        self.leave(people)
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        body = f.board_move('stage.inbound', people, tickets=[self.ticket(f.ids[0]), self.ticket(f.ids[1])])
        invalid = {**body, 'request_key': str(uuid4()),
                   'tickets': [self.ticket(f.ids[0]), self.ticket(f.ids[1], planned_date='bad')]}
        self.assertEqual(f.request('post', 'transitions', invalid).status_code, 400)
        stale = {**body, 'request_key': str(uuid4()),
                 'people': [people[0], {**people[1], 'token': '0' * 64}]}
        self.assertEqual(f.request('post', 'transitions', stale).status_code, 409)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',
                                     (f.ids,)).fetchone()[0], 0)
        self.assertTrue(all(f.board_row(worker)['stage_code'] == 'stage.leave' for worker in f.ids))

    def test_outbound_bulk_tickets_are_planned_distinct_and_replay_safe(self):
        f = self.f
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        first = self.ticket(f.ids[0], planned_date='2026-09-17', travel_details='Выезд первого сотрудника')
        second = self.ticket(f.ids[1], planned_date='2026-09-18', travel_details='Выезд второго сотрудника')
        body = f.board_move('stage.outbound', people, tickets=[first, second])
        self.assertEqual(f.request('post', 'transitions', body).status_code, 201)
        self.assertEqual(f.request('post', 'transitions', body).status_code, 200)
        tickets = f.db.native('''SELECT worker_id,direction,destination_kind,planned_date,actual_date,result_code,
            basis_code,travel_details,stage_event_id FROM workforce_movements WHERE worker_id=ANY(%s) ORDER BY worker_id''',
                              (f.ids,)).fetchall()
        self.assertEqual(len(tickets), 2)
        expected = {first['worker_id']: first, second['worker_id']: second}
        for ticket in tickets:
            self.assertEqual((ticket['direction'], ticket['destination_kind'], ticket['actual_date'],
                              ticket['result_code'], ticket['basis_code']),
                             ('departure', 'home', None, None, 'basis.ticket'))
            self.assertEqual((ticket['planned_date'].isoformat(), ticket['travel_details']),
                             (expected[ticket['worker_id']]['planned_date'], expected[ticket['worker_id']]['travel_details']))
            event = f.db.native('SELECT stage_code FROM workforce_stage_events WHERE id=%s',
                                (ticket['stage_event_id'],)).fetchone()
            self.assertEqual(event['stage_code'], 'stage.outbound')

    def test_recruitment_pvp_to_inbound_ticket_is_planned(self):
        f = self.f
        self.assertEqual(f.request('post', 'transitions', f.board_move('stage.pvp'), 'recruitment').status_code, 201)
        row = f.board_row(role='recruitment')
        body = f.board_move('stage.inbound', [{'id': row['id'], 'token': row['stage_token']}],
                            tickets=[self.ticket(row['id'])])
        response = f.request('post', 'transitions', body, 'recruitment')
        self.assertEqual(response.status_code, 201, response.json)
        ticket = f.db.native('''SELECT direction,destination_kind,actual_date,result_code,basis_code,stage_event_id
            FROM workforce_movements WHERE worker_id=%s''', (f.worker,)).fetchone()
        self.assertEqual(tuple(ticket[key] for key in ('direction', 'destination_kind', 'actual_date',
                                                        'result_code', 'basis_code')),
                         ('arrival', 'site', None, None, 'basis.ticket'))
        event = f.db.native('SELECT stage_code FROM workforce_stage_events WHERE id=%s',
                            (ticket['stage_event_id'],)).fetchone()
        self.assertEqual(event['stage_code'], 'stage.inbound')

    def test_outbound_ticket_validation_and_stale_bulk_selection_are_atomic(self):
        f = self.f
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        body = f.board_move('stage.outbound', people, tickets=[self.ticket(f.ids[0]), self.ticket(f.ids[1])])
        invalid = {**body, 'request_key': str(uuid4()),
                   'tickets': [self.ticket(f.ids[0]), self.ticket(f.ids[1], planned_date='bad')]}
        self.assertEqual(f.request('post', 'transitions', invalid).status_code, 400)
        stale = {**body, 'request_key': str(uuid4()),
                 'people': [people[0], {**people[1], 'token': '0' * 64}]}
        self.assertEqual(f.request('post', 'transitions', stale).status_code, 409)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',
                                     (f.ids,)).fetchone()[0], 0)
        self.assertTrue(all(f.board_row(worker)['stage_code'] is None for worker in f.ids))

    def test_card_tickets_are_planned_audited_and_replay_safe_for_both_directions(self):
        f = self.f
        inbound = self.card_stage('stage.inbound', self.ticket(planned_date='2026-09-17'), confirmed=False)
        outbound = self.card_stage('stage.outbound', self.ticket(planned_date='2026-09-18',
                                   travel_details='Билет на выезд'), confirmed=True)
        for body, expected in ((inbound, ('arrival', 'site', False)), (outbound, ('departure', 'home', True))):
            response = f.request('post', f'people/{f.worker}/stage', body, 'rotation')
            self.assertEqual(response.status_code, 201, response.json)
            self.assertEqual(f.request('post', f'people/{f.worker}/stage', body, 'rotation').status_code, 200)
            ticket = f.db.native('''SELECT direction,destination_kind,planned_date,actual_date,result_code,basis_code,
                stage_event_id,id FROM workforce_movements WHERE worker_id=%s AND planned_date=%s''',
                                 (f.worker, body['tickets'][0]['planned_date'])).fetchone()
            self.assertEqual(tuple(ticket[key] for key in ('direction', 'destination_kind')),
                             expected[:2])
            self.assertEqual((ticket['actual_date'], ticket['result_code'], ticket['basis_code']),
                             (None, None, 'basis.ticket'))
            event = f.db.native('SELECT stage_code,confirmed FROM workforce_stage_events WHERE id=%s',
                                 (ticket['stage_event_id'],)).fetchone()
            self.assertEqual((event['stage_code'], event['confirmed']), (body['stage_code'], expected[2]))
            self.assertEqual(f.db.native("SELECT count(*) FROM workforce_audit WHERE worker_id=%s AND entity_type='movement' AND entity_id=%s",
                                         (f.worker, ticket['id'])).fetchone()[0], 1)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s',
                                     (f.worker,)).fetchone()[0], 2)

    def test_card_ticket_validation_is_atomic(self):
        f = self.f
        before_events = f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s', (f.worker,)).fetchone()[0]
        before_movements = f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s', (f.worker,)).fetchone()[0]
        invalid_tickets = [
            self.ticket(unexpected='field'),
            self.ticket(f.ids[1]),
            self.ticket(planned_date='bad'),
        ]
        for ticket in invalid_tickets:
            body = self.card_stage('stage.inbound', ticket)
            response = f.request('post', f'people/{f.worker}/stage', body, 'rotation')
            self.assertEqual(response.status_code, 400, response.json)
        too_many = self.card_stage('stage.outbound', self.ticket(), tickets=[self.ticket(), self.ticket()])
        self.assertEqual(f.request('post', f'people/{f.worker}/stage', too_many, 'rotation').status_code, 400)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=%s',
                                     (f.worker,)).fetchone()[0], before_events)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s',
                                     (f.worker,)).fetchone()[0], before_movements)

    def test_card_ticket_requires_rotation_role_and_worker_scope(self):
        f = self.f
        body = self.card_stage('stage.inbound', self.ticket())
        for role in ('foreman', 'recruitment', 'hr_viewer', 'viewer'):
            self.assertEqual(f.request('post', f'people/{f.worker}/stage', body, role).status_code, 403)
        outside = self.card_stage('stage.outbound', self.ticket(f.ids[1]))
        self.assertEqual(f.request('post', f'people/{f.ids[1]}/stage', outside, 'rotation').status_code, 404)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=ANY(%s)',
                                     (f.ids,)).fetchone()[0], 0)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',
                                     (f.ids,)).fetchone()[0], 0)

    def test_invalid_ticket_or_stale_bulk_selection_never_applies_partial_transition(self):
        f = self.f
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        self.leave(people)
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        body = f.board_move('stage.onsite', people, tickets=[self.ticket(), self.ticket(f.ids[1])])
        for invalid in [self.ticket(f.ids[1], planned_date='bad'), self.ticket(f.ids[1], travel_details=' '),
                        self.ticket(f.ids[1], origin_code='travelpoint.missing'), self.ticket(),
                        self.ticket(999999999), self.ticket(f.ids[1], unexpected='field')]:
            response = f.request('post', 'transitions', {**body, 'request_key': str(uuid4()), 'tickets': [self.ticket(), invalid]})
            self.assertEqual(response.status_code, 400, response.json)
        stale = {**body, 'people': [people[0], {**people[1], 'token': '0' * 64}]}
        self.assertEqual(f.request('post', 'transitions', stale).status_code, 409)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)', (f.ids,)).fetchone()[0], 0)
        self.assertTrue(all(f.board_row(worker)['stage_code'] == 'stage.leave' for worker in f.ids))
        self.assertEqual(f.request('post', 'transitions', body).status_code, 201)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)', (f.ids,)).fetchone()[0], 2)

    def test_only_leave_to_onsite_and_authorized_people_can_have_ticket(self):
        f = self.f
        body = f.board_move('stage.onsite', tickets=[self.ticket()])
        self.assertEqual(f.request('post', 'transitions', body).status_code, 400)
        self.leave()
        for role in ('foreman', 'recruitment', 'hr_viewer', 'viewer'):
            self.assertEqual(f.request('post', 'transitions', f.board_move('stage.onsite', tickets=[self.ticket()]), role).status_code, 403)
        self.assertEqual(f.request('post', 'transitions', f.board_move('stage.pvp', tickets=[self.ticket()])).status_code, 201)
        other = f.board_row(f.ids[1])
        self.assertEqual(f.request('post', 'transitions', f.board_move('stage.onsite',
            [{'id': other['id'], 'token': other['stage_token']}], tickets=[self.ticket(other['id'])]), 'rotation').status_code, 404)
        self.assertEqual(f.request('post', 'transitions', f.board_move('stage.onsite'), 'rotation').status_code, 201)
        self.assertEqual(f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=%s', (f.worker,)).fetchone()[0], 1)
