import unittest
from unittest.mock import patch

import test_staffing as staffing_tests


class StaffingHistoryTest(unittest.TestCase):
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
        self.module = self.fixture.module
        self.client = self.fixture.admin
        self.ids = [r['id'] for r in self.rows() if r['personnel_no'] in ('70001', '70002')]

    def rows(self):
        return self.client.get('/api/staffing?date=2026-09-12&shift=all').get_json()['rows']

    def state(self, client=None):
        return (client or self.client).get('/api/staffing/history').get_json()

    def replay(self, direction, action=None, client=None):
        action = action or self.state(client)[direction]
        response = (client or self.client).post('/api/staffing/history/' + direction,
            json={'id': action['id'], 'token': action['token']}, headers={'X-CSRF-Token': 'staffing-csrf'})
        return response

    def change(self, kind, value):
        rows = [r for r in self.rows() if r['id'] in self.ids]
        payload = {'date': '2026-09-12', 'worker_ids': self.ids,
                   'expected_crews': {str(r['id']): r['crew_id'] for r in rows},
                   'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}}
        if kind in ('place', 'shift'):
            payload['expected_tokens'] = {str(r['id']): r['day_token'] for r in rows}
            payload['shift' if kind == 'shift' else 'subobject_id'] = value
            path = '/api/staffing/groups/' + ('shifts' if kind == 'shift' else 'assignments')
        elif kind == 'status':
            payload.update(status=value, expected_tokens={str(r['id']): r['attendance_token'] for r in rows})
            path = '/api/staffing/status'
        elif kind == 'work':
            payload.update(description=value, expected_tokens={str(r['id']): r['performed_work_token'] for r in rows},
                           expected_day_tokens={str(r['id']): r['day_token'] for r in rows})
            path = '/api/staffing/performed-work'
        elif kind == 'itr':
            payload.update(field='linear_itr', value=value, linear_itr_person_id=None,
                           expected_tokens={str(r['id']): r['row_token'] for r in rows})
            path = '/api/staffing/groups/responsible'
        elif kind == 'category':
            payload.update(category_id=value, category_token='catalog-token',
                           expected_tokens={str(r['id']): r['category_binding_token'] for r in rows})
            path = '/api/staffing/groups/category'
        else:
            raise AssertionError(kind)
        return self.fixture.write(path, payload)

    def site(self):
        with self.module.app.app_context():
            return self.module.get_db().execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]

    def test_place_undo_redo_is_atomic_and_does_not_touch_others(self):
        before = {r['id']: r['day_token'] for r in self.rows() if r['id'] not in self.ids}
        self.assertIsNone(self.state()['undo'])
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        old_action = self.state()['undo']
        self.assertEqual(old_action['workers'], 2)
        self.assertEqual(self.replay('undo').status_code, 200)
        self.assertTrue(all(r['assignment_id'] is None for r in self.rows()))
        self.assertEqual(self.replay('undo', old_action).status_code, 409)
        self.assertEqual(self.replay('redo').status_code, 200)
        for row in self.rows():
            if row['id'] in self.ids:
                self.assertEqual(row['subobject_id'], self.site())
            else:
                self.assertEqual(row['day_token'], before[row['id']])
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_action_history').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_history_replays').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignment_events').fetchone()[0], 6)

    def test_multiple_undo_redo_rotates_tokens_and_new_edit_discards_redo(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        self.assertEqual(self.change('shift', '2 смена').status_code, 200)
        for direction in ('undo', 'undo', 'redo', 'redo'):
            response = self.replay(direction)
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.replay('undo').status_code, 200)
        self.assertEqual(self.change('status', 'Вых').status_code, 200)
        self.assertIsNone(self.state()['redo'])

    def test_work_status_and_itr_can_each_be_reversed(self):
        for kind, value in [('work', 'Монтаж труб'), ('status', 'Вых'), ('itr', 'Новый ИТР')]:
            with self.subTest(kind=kind):
                response = self.change(kind, value)
                self.assertEqual(response.status_code, 200, response.get_json())
                for direction in ('undo', 'redo', 'undo'):
                    response = self.replay(direction)
                    self.assertEqual(response.status_code, 200, response.get_json())

    def test_category_restore_removes_override_and_rejects_inactive_redo(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            category = db.execute("INSERT INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at) VALUES ('Новая','новая',1,'catalog-token',?,'now')", (self.fixture.admin_id,)).lastrowid
            db.commit()
        self.assertEqual(self.change('category', category).status_code, 200)
        self.assertEqual(self.replay('undo').status_code, 200)
        self.assertTrue(all(r['category'] == 'Монтажник ТТ' for r in self.rows()))
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE gdlr_categories SET active=0 WHERE id=?', (category,)); db.commit()
        self.assertEqual(self.replay('redo').status_code, 409)

    def test_stale_second_worker_rolls_back_all(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET edit_token='other-user' WHERE worker_id=?", (self.ids[1],)); db.commit()
        self.assertEqual(self.replay('undo').status_code, 409)
        self.assertEqual(sum(bool(r['assignment_id']) for r in self.rows()), 2)

    def test_permissions_csrf_ownership_and_backup_failure(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        action = self.state()['undo']
        self.assertIsNone(self.state(self.fixture.foreman)['undo'])
        self.assertEqual(self.replay('undo', action, self.fixture.foreman).status_code, 409)
        self.assertEqual(self.fixture.viewer.get('/api/staffing/history').status_code, 403)
        self.assertEqual(self.client.post('/api/staffing/history/undo', json=action).status_code, 403)
        with patch('staffing_history.create_backup', side_effect=OSError('disk full')):
            self.assertEqual(self.replay('undo').status_code, 503)
        self.assertEqual(sum(bool(r['assignment_id']) for r in self.rows()), 2)

    def test_journal_failure_rolls_back_the_original_edit(self):
        with patch.dict(self.module.app.config, PROPAGATE_EXCEPTIONS=False), patch('staffing_history.HistoryConnection.finish_history', side_effect=RuntimeError('journal failed')):
            self.assertEqual(self.change('place', self.site()).status_code, 500)
        self.assertTrue(all(r['assignment_id'] is None for r in self.rows()))
        self.assertIsNone(self.state()['undo'])

    def test_reused_assignment_id_cannot_overwrite_another_worker(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        assigned = next(r['assignment_id'] for r in self.rows() if r['id'] == self.ids[0])
        self.assertEqual(self.replay('undo').status_code, 200)
        other = next(r for r in self.rows() if r['id'] not in self.ids)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(id,work_date,shift,subobject_id,worker_id,foreman_user_id,crew_id,created_at)
                VALUES (?,'2026-09-12','1 смена',?,?,?,?,'now')''',
                       (assigned, self.site(), other['id'], self.fixture.admin_id, other['crew_id']))
            db.commit()
        self.assertEqual(self.replay('redo').status_code, 409)
        self.assertEqual(sum(bool(r['assignment_id']) for r in self.rows()), 1)

    def test_crew_details_round_trip_and_new_member_block(self):
        crew_id = self.rows()[0]['crew_id']
        with self.module.app.app_context():
            old = dict(self.module.get_db().execute('SELECT * FROM crews WHERE id=?', (crew_id,)).fetchone())
        response = self.fixture.write(f'/api/staffing/crews/{crew_id}/details', {
            'linear_itr': 'Ответственный ИТР', 'brigadier': 'Новый бригадир', 'expected_token': old['details_token']})
        self.assertEqual(response.status_code, 200)
        for direction in ('undo', 'redo'):
            response = self.replay(direction)
            self.assertEqual(response.status_code, 200, response.get_json())
        with self.module.app.app_context():
            db = self.module.get_db()
            other = db.execute('SELECT worker_id FROM crew_members WHERE crew_id<>? LIMIT 1', (crew_id,)).fetchone()[0]
            db.execute('UPDATE crew_members SET crew_id=? WHERE worker_id=?', (crew_id, other)); db.commit()
        self.assertEqual(self.replay('undo').status_code, 409)

    def test_crew_transfer_and_contractor_round_trip(self):
        rows = [r for r in self.rows() if r['id'] in self.ids]
        target = next(r for r in self.client.get('/api/staffing/crew-options').get_json()['rows'] if r['id'] != rows[0]['crew_id'])
        response = self.fixture.write('/api/staffing/groups/crew', {
            'worker_ids': self.ids, 'crew_id': target['id'], 'target_token': target['target_token'],
            'expected_crews': {str(r['id']): r['crew_id'] for r in rows},
            'expected_group_tokens': {str(r['id']): r['group_token'] for r in rows}})
        self.assertEqual(response.status_code, 200, response.get_json())
        for direction in ('undo', 'redo', 'undo'):
            response = self.replay(direction)
            self.assertEqual(response.status_code, 200, response.get_json())
        row = next(r for r in self.rows() if r['id'] in self.ids)
        contractor = next(r for r in self.client.get('/api/contractors').get_json()['rows'] if r['name'] != row['contractor'])
        response = self.fixture.write(f"/api/staffing/workers/{row['id']}/contractor", {
            'contractor_id': contractor['id'], 'expected_crew_id': row['crew_id'], 'expected_token': row['contractor_token']})
        self.assertEqual(response.status_code, 200, response.get_json())
        for direction in ('undo', 'redo'):
            self.assertEqual(self.replay(direction).status_code, 200)

    def test_transfer_previous_day_undo_redo_preserves_source(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        self.assertEqual(self.change('work', 'Работы предыдущего дня').status_code, 200)
        headers = {'X-CSRF-Token': 'staffing-csrf'}
        data = {'date': '2026-09-13', 'worker_ids': self.ids}
        plan = self.client.post('/api/staffing/transfer-selected/preview', json=data, headers=headers).get_json()
        response = self.client.post('/api/staffing/transfer-selected', json={**data, 'expected_token': plan['expected_token']}, headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        for direction in ('undo', 'redo', 'undo', 'redo'):
            response = self.replay(direction)
            self.assertEqual(response.status_code, 200, response.get_json())
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE work_date='2026-09-12'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE work_date='2026-09-13'").fetchone()[0], 2)
            self.module.init_db()
        self.assertIsNotNone(self.state()['undo'])

    def test_revoked_smu_access_blocks_history(self):
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO user_smu_access VALUES (?,'selected','[]','token',?,'now')", (self.fixture.admin_id, self.fixture.admin_id)); db.commit()
        self.assertEqual(self.replay('undo').status_code, 403)

    def test_legacy_night_alias_cannot_create_a_double_assignment(self):
        self.assertEqual(self.change('shift', '2 смена').status_code, 200)
        self.assertEqual(self.change('place', self.site()).status_code, 200)
        self.assertEqual(self.replay('undo').status_code, 200)
        row = next(r for r in self.rows() if r['id'] == self.ids[0])
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO assignments(id,work_date,shift,subobject_id,worker_id,foreman_user_id,crew_id,created_at)
                VALUES (10000,'2026-09-12','Ночная смена',?,?,?,?,'now')''',
                       (self.site(), row['id'], self.fixture.admin_id, row['crew_id']))
            db.commit()
        self.assertEqual(self.replay('redo').status_code, 409)
        with self.module.app.app_context():
            self.assertEqual(self.module.get_db().execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 1)
