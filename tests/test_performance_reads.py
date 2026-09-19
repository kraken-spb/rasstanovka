"""Read optimizations preserve complete projections and scoped board snapshots."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4


class StaffingSummaryProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_staffing import StaffingWorkflowTest
        cls.fixture_type = StaffingWorkflowTest
        cls.fixture_type.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_type.tearDownClass()

    def test_summary_matches_full_rows_and_skips_page_only_warnings(self):
        f = self.fixture_type(); f.setUp(); self.addCleanup(f.doCleanups); f.apply()
        for client in (f.admin, f.foreman):
            for shift in ('all', '1 смена', '2 смена'):
                params = {'date': '2026-09-12', 'shift': shift}
                full = client.get('/api/staffing', query_string=params).get_json()
                with patch('workforce_core.departure_warnings', side_effect=AssertionError('Page-only work')):
                    response = client.get('/api/staffing', query_string={**params, 'view': 'summary'})
                self.assertEqual(response.status_code, 200)
                summary = response.get_json()
                self.assertEqual(summary['crews'], full['crews'])
                self.assertEqual(summary['import'], full['import'])
                self.assertEqual([r['id'] for r in summary['index']], [r['id'] for r in full['rows']])
                for index, row in zip(summary['index'], full['rows']):
                    for key, value in index.items():
                        if key != 'search_fields':
                            self.assertEqual(value, row.get(key), key)
                    self.assertEqual(index['search_fields'][0:2], [row['full_name'], row['personnel_no']])
                    self.assertEqual(index['search_fields'][-2:], [row['linear_itr_name'], row['brigadier_name']])


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'), 'Select isolated PostgreSQL staging explicitly.')
class WorkforceReadOptimizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_workforce_api import WorkforceApiTest
        cls.fixture_type = WorkforceApiTest
        cls.fixture_type.setUpClass()

    def setUp(self):
        self.f = self.fixture_type(); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def get(self, path, role='admin', **query):
        response = self.f.request('get', path + '?' + urlencode(query, doseq=True), role=role)
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def test_board_pages_match_individual_lanes_with_filters_and_sort(self):
        f = self.f
        for i in range(25):
            row = f.db.native('INSERT INTO workers(full_name,personnel_no,department) VALUES (%s,%s,%s) RETURNING id',
                (f'Карточка {i:02}', f.suffix + str(i), 'TEST-SMU')).fetchone()
            f.db.native('''INSERT INTO workforce_stage_events
                (worker_id,stage_code,effective_date,confirmed,reason,created_by,request_key)
                VALUES (%s,'stage.pvp','2026-09-16',TRUE,'Тест доски',%s,%s)''', (row['id'], f.users['admin']['id'], uuid4()))
        query = {'date': '2026-09-18', 'department': ['TEST-SMU', 'OTHER-SMU'],
                 'sort': json.dumps([{'field': 'name', 'direction': 'desc'}])}
        for role in ('admin', 'foreman'):
            for selected in ([], ['stage.pvp', 'unconfirmed']):
                board = self.get('board', role, **query, stage=selected)
                seen = []
                for code, lane in board['lanes'].items():
                    if selected and code not in selected:
                        self.assertEqual(lane, {'rows': [], 'totals': {'total': 0}})
                        continue
                    page = self.get('people', role, **query, stage=code, limit=20)
                    self.assertEqual(lane['rows'], page['rows'])
                    self.assertEqual(lane['totals']['total'], page['totals']['total'])
                    self.assertEqual(board['revision'], page['revision'])
                    seen.extend(row['id'] for row in lane['rows'])
                self.assertEqual(len(seen), len(set(seen)))
                combined = self.get('people', role, **query, stage=selected, date_options='1')
                self.assertEqual(board['totals'], combined['totals'])
                self.assertEqual(board['date_options'], combined['date_options'])
                self.assertEqual(len(board['lanes']['stage.pvp']['rows']), 20)

    def test_catalog_cache_keeps_permissions_and_places_fresh(self):
        f = self.f; f.app.config['TESTING'] = False
        first = self.get('reference', 'foreman')
        self.assertIn({'name': 'TEST-SMU'}, first['departments'])
        f.db.native("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=%s", (f.users['foreman']['id'],))
        self.assertEqual(self.get('reference', 'foreman')['departments'], [])
        category = f.db.native('SELECT id FROM gdlr_categories LIMIT 1').fetchone()[0]
        label = 'Категория ' + f.suffix
        f.db.native('UPDATE gdlr_categories SET name=%s WHERE id=%s', (label, category))
        self.assertIn(label, [r['name'] for r in self.get('reference')['categories']])
        place = f.db.native('SELECT id FROM workforce_pvp_places LIMIT 1').fetchone()
        if place:
            f.db.native('UPDATE workforce_pvp_places SET name=%s WHERE id=%s', ('ПВП ' + f.suffix, place[0]))
            self.assertIn('ПВП ' + f.suffix, [r['name'] for r in self.get('reference')['places']])
        from workforce_read_cache import _values
        self.assertLessEqual(sum(key[0] is f.db._pool and key[2] == ('reference_common',) for key in _values), 1)

    def test_whole_roster_source_is_equivalent_to_addressed_source(self):
        from staffing_import import postgres_member_source_sql
        f = self.f
        f.db.native('UPDATE workforce_profiles SET staffing_ready=TRUE WHERE worker_id=%s', (f.worker,))
        def source(whole):
            return {r['worker_id']: (r['source_row'], r['source_crew']) for r in f.db.native(
                'SELECT sm.* FROM ' + postgres_member_source_sql(whole=whole))}
        self.assertEqual(source(True), source(False))
        f.db.native('UPDATE workforce_profiles SET workforce_managed=TRUE,staffing_ready=FALSE WHERE worker_id=%s', (f.worker,))
        self.assertEqual(source(True), source(False))
