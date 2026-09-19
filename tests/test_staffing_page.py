import json
import unittest

from tests import test_staffing as staffing_tests


class StaffingWorkerIdsPageTest(unittest.TestCase):
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

    def get(self, ids=None, client=None, **params):
        query = {'date': '2026-09-12', 'shift': 'all', **params}
        if ids is not None:
            query['worker_ids'] = ','.join(str(worker_id) for worker_id in ids)
        return (client or self.fixture.admin).get('/api/staffing', query_string=query)

    def rows_for_page(self):
        rows = self.get().get_json()['rows']
        crew_rows = [row for row in rows if row['crew_id'] is not None]
        members = {}
        for row in crew_rows:
            members.setdefault(row['crew_id'], []).append(row)
        self.assertGreaterEqual(len(members), 2)
        crewless_source = next(group[0] for group in members.values() if len(group) > 1)
        with self.fixture.app.app_context():
            db = self.fixture.module.get_db()
            db.execute('DELETE FROM crew_members WHERE worker_id=?', (crewless_source['id'],))
            db.commit()
        rows = self.get().get_json()['rows']
        crew_rows = [row for row in rows if row['crew_id'] is not None]
        return rows, crew_rows, next(row for row in rows if row['id'] == crewless_source['id'])

    def test_worker_ids_returns_full_current_rows_for_mixed_crews_and_crewless(self):
        rows, crew_rows, crewless = self.rows_for_page()
        first_crew = crew_rows[0]
        second_crew = next(row for row in crew_rows if row['crew_id'] != first_crew['crew_id'])
        selected_ids = [first_crew['id'], second_crew['id'], crewless['id']]

        page = self.get(selected_ids).get_json()
        selected = {row['id']: row for row in page['rows']}
        full = {row['id']: row for row in rows}
        self.assertEqual(set(selected), set(selected_ids))
        self.assertEqual({row['crew_id'] for row in selected.values()},
                         {first_crew['crew_id'], second_crew['crew_id'], None})
        self.assertEqual({crew['id'] for crew in page['crews']},
                         {first_crew['crew_id'], second_crew['crew_id'], None})
        for worker_id in selected_ids:
            for field in ('day_token', 'row_token', 'group_token', 'locked', 'assignment_id',
                          'assignment_author', 'employee_shift', 'freshness'):
                self.assertEqual(selected[worker_id][field], full[worker_id][field])

    def test_worker_ids_respects_foreman_smu_scope_and_omits_outside_rows(self):
        rows, crew_rows, crewless = self.rows_for_page()
        visible = crew_rows[0]
        outside = next(row for row in crew_rows if row['crew_id'] != visible['crew_id'])
        with self.fixture.app.app_context():
            db = self.fixture.module.get_db()
            db.execute("UPDATE workers SET department='СМУ доступ' WHERE id IN (?,?)",
                       (visible['id'], crewless['id']))
            db.execute("UPDATE workers SET department='СМУ вне доступа' WHERE id=?", (outside['id'],))
            db.execute('''INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at)
                VALUES (?,'selected',?,'page-scope',?,'now')''',
                       (self.fixture.foreman_id, json.dumps(['СМУ доступ'], ensure_ascii=False), self.fixture.admin_id))
            db.commit()

        response = self.get([visible['id'], outside['id'], crewless['id']], self.fixture.foreman)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual({row['id'] for row in response.get_json()['rows']},
                         {visible['id'], crewless['id']})

    def test_worker_ids_rejects_invalid_csv_and_summary_view(self):
        row_id = self.get().get_json()['rows'][0]['id']
        invalid = ('', f'{row_id},', f'{row_id},{row_id}', '0', '-1', 'not-an-id',
                   '9' * 10000, '9223372036854775808',
                   ','.join(str(value) for value in range(1, 102)))
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(self.fixture.admin.get('/api/staffing', query_string={
                    'date': '2026-09-12', 'shift': 'all', 'worker_ids': value}).status_code, 400)
        self.assertEqual(self.get([row_id], view='summary').status_code, 400)
