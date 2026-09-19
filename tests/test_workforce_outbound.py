import os
import unittest

import test_workforce_api as fixtures


class OutboundPermissionsTest(unittest.TestCase):
    def test_departure_is_available_to_rotation_only_and_excludes_current_stage(self):
        from workforce_board import transition_targets
        for role in ('admin', 'super_admin', 'rotation'):
            self.assertIn('stage.outbound', transition_targets(role, 'stage.onsite'))
            self.assertNotIn('stage.outbound', transition_targets(role, 'stage.outbound'))
            self.assertEqual(transition_targets(role, 'stage.onsite', False), [])
        for role in ('recruitment', 'foreman', 'viewer', 'hr_viewer'):
            self.assertNotIn('stage.outbound', transition_targets(role, 'stage.onsite'))


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select an isolated PostgreSQL database.')
class OutboundStageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkforceApiTest.setUpClass()

    def setUp(self):
        self.f = fixtures.WorkforceApiTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_departure_is_dated_filtered_counted_audited_and_can_end_in_leave(self):
        from workforce_core import departure_warnings
        f = self.f
        f.add_stage_record(f.worker, 'stage.onsite', '2026-09-15')
        plan = f.request('post', f'people/{f.worker}/movement', f.movement(
            actual_date=None, planned_date='2026-10-10', result_code=None), 'rotation').json
        body = f.board_move('stage.outbound')
        for role in ('recruitment', 'foreman', 'viewer', 'hr_viewer'):
            self.assertEqual(f.request('post', 'transitions', body, role).status_code, 403)
        self.assertEqual(f.request('post', 'transitions', body, 'rotation').status_code, 201)
        self.assertEqual(f.request('post', 'transitions', body, 'rotation').status_code, 200)
        self.assertEqual(f.board_row(day='2026-09-15')['stage_code'], 'stage.onsite')
        self.assertEqual(f.board_row()['stage_code'], 'stage.outbound')
        page = f.request('get', f'people?date=2026-09-16&q={f.suffix}&stage=stage.outbound&queue=movements').json
        self.assertEqual([row['id'] for row in page['rows']], [f.worker])
        self.assertEqual(page['totals']['outbound'], 1)
        self.assertEqual(page['totals']['total'], 1)
        self.assertIn('Подтверждён выезд', departure_warnings(f.db, '2026-09-16', [f.worker])[f.worker])
        history = f.request('get', f'people/{f.worker}', role='rotation').json['history']
        self.assertEqual(sum(item['action'] == 'transition' for item in history), 1)
        current_plan = f.db.native('SELECT * FROM workforce_movements WHERE id=%s', (plan['id'],)).fetchone()
        self.assertEqual(current_plan['planned_date'].isoformat(), '2026-10-10')
        self.assertIsNone(current_plan['actual_date'])
        self.assertEqual(f.request('post', 'transitions', f.board_move('stage.leave'), 'rotation').status_code, 201)
        self.assertEqual(f.board_row()['stage_code'], 'stage.leave')

    def test_departure_bulk_preserves_atomicity_for_stale_or_inaccessible_selection(self):
        f = self.f
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        body = f.board_move('stage.outbound', people)
        self.assertEqual(f.request('post', 'transitions', body, 'rotation').status_code, 404)
        self.assertIsNone(f.board_row()['stage_code'])
        f.add_stage_record(f.ids[1], 'stage.inbound')
        self.assertEqual(f.request('post', 'transitions', body).status_code, 409)
        self.assertIsNone(f.board_row()['stage_code'])
        people = [{'id': worker, 'token': f.board_row(worker)['stage_token']} for worker in f.ids]
        response = f.request('post', 'transitions', f.board_move('stage.outbound', people))
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json['changed'], 2)
        for worker in f.ids:
            self.assertEqual(f.board_row(worker)['stage_code'], 'stage.outbound')
