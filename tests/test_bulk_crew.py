import hashlib
import json
import unittest

import test_crews
from staffing_shifts import responsibility_states


class BulkCrewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_crews.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_crews.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.c = test_crews.CrewWorkflowTest()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.c.add(self.c.crew_a, self.c.worker_ids[:3])
        self.c.add(self.c.crew_b, self.c.worker_ids[3:], self.c.foreman_b)

    def options(self, client=None):
        return (client or self.c.admin).get('/api/staffing/crew-options').get_json()['rows']

    def payload(self, ids=None, target=None):
        ids = ids or self.c.worker_ids[:2]
        target = target or self.c.crew_a2
        choice = next(row for row in self.options() if row['id'] == target)
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            crews = dict(db.execute('SELECT worker_id,crew_id FROM crew_members').fetchall())
            groups = responsibility_states(db, ids)
        return {'worker_ids': ids, 'crew_id': target, 'target_token': choice['target_token'],
                'expected_crews': {str(i): crews[i] for i in ids},
                'expected_group_tokens': {str(i): groups[i]['group_token'] for i in ids}}

    def move(self, data, client=None):
        return self.c.write(client or self.c.admin, 'PUT', '/api/staffing/groups/crew', data)

    def snapshot(self, include_members=True):
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: hashlib.sha256(json.dumps([tuple(row) for row in db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')],
                        ensure_ascii=False).encode()).hexdigest()
                    for name in tables if include_members or name not in ('crew_members', 'staffing_action_history')}

    def test_selected_only_preserves_history_overrides_and_source(self):
        self.c.place(self.c.worker_ids[:1], self.c.sites[0])
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            db.execute("UPDATE crews SET linear_itr='Новый ИТР',brigadier='Новый бригадир' WHERE id=?", (self.c.crew_a2,))
            db.execute('''INSERT INTO staffing_row_details(worker_id,brigadier_override,edit_token,updated_by,updated_at)
                VALUES (?,'Индивидуальный бригадир','kept',?,'now')''', (self.c.worker_ids[0], self.c.admin_id))
            db.commit()
        data = self.payload()
        before = self.snapshot(include_members=False)
        result = self.move(data)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.json['changed'], 2)
        self.assertEqual(self.snapshot(include_members=False), before)
        with self.c.module.app.app_context():
            entry = self.c.module.get_db().execute('SELECT scope_json FROM staffing_action_history ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(json.loads(entry['scope_json'])['ids'], sorted(self.c.worker_ids[:2]))
        for worker in self.c.worker_ids[:2]:
            self.assertEqual(self.c.employee(worker)['crew_id'], self.c.crew_a2)
            self.assertEqual(self.c.employee(worker)['linear_itr_name'], 'Новый ИТР')
        self.assertEqual(self.c.employee(self.c.worker_ids[0])['brigadier_name'], 'Индивидуальный бригадир')
        self.assertEqual(self.c.employee(self.c.worker_ids[1])['brigadier_name'], 'Новый бригадир')
        self.assertEqual(self.c.employee(self.c.worker_ids[2])['crew_id'], self.c.crew_a)
        self.assertEqual(self.c.employee(self.c.worker_ids[3])['crew_id'], self.c.crew_b)
        self.assertEqual(self.move(data).status_code, 409)
        self.assertEqual(self.move(self.payload()).json['changed'], 0)

    def test_mixed_sources_are_atomic_and_only_admin_can_cross_owners(self):
        data = self.payload(ids=[self.c.worker_ids[0], self.c.worker_ids[3]])
        before = self.snapshot()
        self.assertEqual(self.move(data, self.c.foreman_a).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.move(data).status_code, 200)
        self.assertEqual(self.c.employee(self.c.worker_ids[3])['crew_id'], self.c.crew_a2)

    def test_foreman_options_both_owners_roles_and_csrf(self):
        self.assertEqual({row['id'] for row in self.options(self.c.foreman_a)}, {self.c.crew_a, self.c.crew_a2})
        data = self.payload(target=self.c.crew_b)
        before = self.snapshot()
        self.assertEqual(self.move(data, self.c.foreman_a).status_code, 403)
        self.assertEqual(self.move(data, self.c.foreman_b).status_code, 403)
        self.assertEqual(self.move(data, self.c.viewer).status_code, 403)
        self.assertEqual(self.c.admin.put('/api/staffing/groups/crew', json=data).status_code, 403)
        self.assertEqual(self.c.viewer.get('/api/staffing/crew-options').status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.move(self.payload(), self.c.foreman_a).status_code, 200)
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.c.admin_id,)); db.commit()
        self.assertEqual(self.move(self.payload(target=self.c.crew_b)).status_code, 200)

    def test_stale_group_source_target_and_disabled_worker_never_partially_move(self):
        for field in ('expected_group_tokens', 'expected_crews', 'target_token'):
            data = self.payload()
            if field == 'target_token':
                data[field] = 'stale'
            elif field == 'expected_crews':
                data[field][str(data['worker_ids'][-1])] = self.c.crew_b
            else:
                data[field][str(data['worker_ids'][-1])] = 'stale'
            before = self.snapshot()
            self.assertEqual(self.move(data).status_code, 409)
            self.assertEqual(self.snapshot(), before)
        data = self.payload()
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            db.execute('UPDATE workers SET active=0 WHERE id=?', (data['worker_ids'][-1],)); db.commit()
        before = self.snapshot()
        self.assertEqual(self.move(data).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_destination_changes_and_disabled_owner_require_new_choice(self):
        data = self.payload()
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            db.execute("UPDATE crews SET name='Переименована' WHERE id=?", (data['crew_id'],)); db.commit()
        before = self.snapshot()
        self.assertEqual(self.move(data).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        data = self.payload()
        with self.c.module.app.app_context():
            db = self.c.module.get_db()
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.c.owner_a,)); db.commit()
        before = self.snapshot()
        self.assertEqual(self.move(data).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(data['crew_id'], {row['id'] for row in self.options()})

    def test_bad_payloads_rejected_without_changes(self):
        valid = self.payload()
        before = self.snapshot()
        for change in ({'worker_ids': []}, {'worker_ids': [True]}, {'worker_ids': [0]}, {'crew_id': None},
                       {'crew_id': True}, {'crew_id': -1}, {'expected_crews': {}}, {'expected_group_tokens': {}}, {'target_token': None}):
            self.assertEqual(self.move({**valid, **change}).status_code, 400)
        self.assertEqual(self.move({**valid, 'crew_id': 999999}).status_code, 404)
        self.assertEqual(self.snapshot(), before)
