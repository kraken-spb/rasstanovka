import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import test_workforce_api as fixtures


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'),
                     'Select an isolated PostgreSQL database.')
class TicketRecognitionLinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        from ticket_import import register_ticket_import
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        native = self.f.db.native

        def native_in_fixture(query, parameters=()):
            if query == 'BEGIN ISOLATION LEVEL READ COMMITTED':
                return native('SELECT 1')
            return native(query, parameters)

        self.f.db.native = native_in_fixture
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {'TICKET_IMPORT_DIR': self.folder.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        register_ticket_import(self.f.app, lambda: self.f.db, lambda *roles: lambda fn: fn)

    def job(self, role='admin'):
        key = str(uuid4())
        self.f.db.native('''INSERT INTO workforce_ticket_jobs
            (id,actor_id,filename,file_hash,file_extension,state,preview_json)
            VALUES (%s,%s,'recognition.pdf',%s,'.pdf','ready','{}')''',
                         (key, self.f.users[role]['id'], key))
        return key

    def recognition(self, key, worker=None, direction='arrival', number='1234567890123'):
        arrival = '2026-09-16'
        departure = '2026-09-16'
        return {
            'job_id': key,
            'worker_id': worker or self.f.worker,
            'direction': direction,
            'passenger': 'Тестовый Сотрудник',
            'ticket_number': number,
            'transport': 'air',
            'segments': [{
                'origin': 'Казань', 'destination': 'Москва',
                'departure_date': departure, 'arrival_date': arrival,
                'departure_time': '10:00', 'arrival_time': '12:00',
                'flight': 'SU123', 'coach': '', 'seat': '', 'timezone_note': 'UTC+3',
            }],
        }

    def board_ticket(self, recognition, worker=None):
        return {
            'worker_id': worker or self.f.worker,
            'planned_date': (recognition['segments'][-1]['arrival_date']
                             if recognition['direction'] == 'arrival'
                             else recognition['segments'][0]['departure_date']),
            'travel_details': 'Данные из распознанного билета',
            'recognition': recognition,
        }

    def ticket_count(self):
        return self.f.db.native('SELECT count(*) FROM workforce_tickets WHERE worker_id=ANY(%s)',
                                (self.f.ids,)).fetchone()[0]

    def movement_count(self):
        return self.f.db.native('SELECT count(*) FROM workforce_movements WHERE worker_id=ANY(%s)',
                                (self.f.ids,)).fetchone()[0]

    def test_drafts_validate_recruitment_scope_without_writes(self):
        key = self.job('recruitment')
        item = self.recognition(key)
        response = self.f.client.post('/api/workforce/tickets/drafts', json={'items': [item]},
                                      headers={'Test-Role': 'recruitment'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.json['items'][0]['ticket_number'], item['ticket_number'])
        self.assertEqual(self.f.db.native('SELECT state FROM workforce_ticket_jobs WHERE id=%s',
                                          (key,)).fetchone()['state'], 'ready')
        self.assertEqual(self.ticket_count(), 0)
        self.assertEqual(self.movement_count(), 0)
        duplicate = self.f.client.post('/api/workforce/tickets/drafts', json={'items': [item, item]},
                                       headers={'Test-Role': 'recruitment'})
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(self.f.db.native('SELECT state FROM workforce_ticket_jobs WHERE id=%s',
                                          (key,)).fetchone()['state'], 'ready')

        foreign = self.recognition(key, worker=self.f.ids[1])
        denied = self.f.client.post('/api/workforce/tickets/drafts', json={'items': [foreign]},
                                    headers={'Test-Role': 'recruitment'})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.f.db.native('SELECT state FROM workforce_ticket_jobs WHERE id=%s',
                                          (key,)).fetchone()['state'], 'ready')

    def test_manual_pvp_ticket_is_planned_and_next_inbound_can_omit_ticket(self):
        manual = {
            'worker_id': self.f.worker, 'planned_date': '2026-09-17',
            'travel_details': 'Ручной билет в ПВП',
        }
        response = self.f.request('post', 'transitions',
                                  self.f.board_move('stage.pvp', tickets=[manual]), 'recruitment')
        self.assertEqual(response.status_code, 201, response.data)
        movement = self.f.db.native('SELECT direction,destination_kind,actual_date,result_code '
                                    'FROM workforce_movements WHERE worker_id=%s',
                                    (self.f.worker,)).fetchone()
        self.assertEqual(tuple(movement[key] for key in ('direction', 'destination_kind', 'actual_date', 'result_code')),
                         ('arrival', 'pvp', None, None))

        row = self.f.board_row(role='recruitment')
        optional = self.f.board_move('stage.inbound', [{'id': row['id'], 'token': row['stage_token']}])
        self.assertEqual(self.f.request('post', 'transitions', optional, 'recruitment').status_code, 201)
        self.assertEqual(self.movement_count(), 1)

    def test_recognition_board_pvp_links_existing_event_and_marks_job_applied(self):
        key = self.job('recruitment')
        item = self.recognition(key)
        body = self.f.board_move('stage.pvp', tickets=[self.board_ticket(item)])
        response = self.f.request('post', 'transitions', body, 'recruitment')
        self.assertEqual(response.status_code, 201, response.data)
        movement = self.f.db.native('''SELECT m.*,t.job_id FROM workforce_movements m
            JOIN workforce_tickets t ON t.movement_id=m.id WHERE m.worker_id=%s''',
                                    (self.f.worker,)).fetchone()
        self.assertEqual((movement['direction'], movement['destination_kind'], movement['actual_date'],
                          movement['result_code'], movement['origin'], movement['destination']),
                         ('arrival', 'pvp', None, None, 'Казань', 'Москва'))
        self.assertEqual(str(movement['job_id']), key)
        event = self.f.db.native('SELECT stage_code FROM workforce_stage_events WHERE id=%s',
                                 (movement['stage_event_id'],)).fetchone()
        self.assertEqual(event['stage_code'], 'stage.pvp')
        self.assertEqual(self.f.db.native('SELECT state FROM workforce_ticket_jobs WHERE id=%s',
                                          (key,)).fetchone()['state'], 'applied')

    def test_recognition_card_outbound_is_planned_and_standalone_confirm_cannot_duplicate(self):
        key = self.job('rotation')
        item = self.recognition(key, direction='departure', number='1234567890124')
        body = {
            'stage_code': 'stage.outbound', 'effective_date': '2026-09-16', 'confirmed': True,
            'reason': '', 'request_key': str(uuid4()), 'tickets': [self.board_ticket(item)],
        }
        response = self.f.request('post', f'people/{self.f.worker}/stage', body, 'rotation')
        self.assertEqual(response.status_code, 201, response.data)
        movement = self.f.db.native('''SELECT direction,destination_kind,actual_date,result_code,stage_event_id
            FROM workforce_movements WHERE worker_id=%s''', (self.f.worker,)).fetchone()
        self.assertEqual(tuple(movement[key] for key in ('direction', 'destination_kind', 'actual_date', 'result_code')),
                         ('departure', 'home', None, None))
        self.assertEqual(self.f.client.post('/api/workforce/tickets/confirm', json={'items': [item]},
                                            headers={'Test-Role': 'rotation'}).status_code, 201)
        self.assertEqual(self.ticket_count(), 1)

    def test_duplicate_or_invalid_recognition_batch_rolls_back_every_worker(self):
        first_job = self.job()
        first = self.recognition(first_job, worker=self.f.ids[0], number='1234567890125')
        second = self.recognition(self.job(), worker=self.f.ids[1], number='1234567890126')
        people = [{'id': worker, 'token': self.f.board_row(worker)['stage_token']} for worker in self.f.ids]
        stale = self.f.board_move('stage.inbound',
                                  [people[0], {**people[1], 'token': '0' * 64}],
                                  tickets=[self.board_ticket(first, self.f.ids[0]),
                                           self.board_ticket(second, self.f.ids[1])])
        self.assertEqual(self.f.request('post', 'transitions', stale).status_code, 409)
        self.assertEqual(self.f.db.native('SELECT state FROM workforce_ticket_jobs WHERE id=%s',
                                          (first_job,)).fetchone()['state'], 'ready')
        invalid = self.board_ticket(second, self.f.ids[1])
        invalid['recognition']['segments'][0]['arrival_date'] = 'bad-date'
        body = self.f.board_move('stage.inbound', people,
                                 tickets=[self.board_ticket(first, self.f.ids[0]), invalid])
        self.assertEqual(self.f.request('post', 'transitions', body).status_code, 400)
        self.assertEqual(self.movement_count(), 0)
        self.assertEqual(self.ticket_count(), 0)
        self.assertEqual(self.f.db.native('SELECT count(*) FROM workforce_stage_events WHERE worker_id=ANY(%s)',
                                          (self.f.ids,)).fetchone()[0], 0)

        body = self.f.board_move('stage.inbound', people,
                                 tickets=[self.board_ticket(first, self.f.ids[0])])
        self.assertEqual(self.f.request('post', 'transitions', body).status_code, 201)
        fresh = [{'id': worker, 'token': self.f.board_row(worker)['stage_token']} for worker in self.f.ids]
        replay = self.f.board_move('stage.pvp', fresh,
                                   tickets=[self.board_ticket(first, self.f.ids[0])])
        self.assertEqual(self.f.request('post', 'transitions', replay).status_code, 409)
        self.assertEqual(self.ticket_count(), 1)


if __name__ == '__main__':
    unittest.main()
