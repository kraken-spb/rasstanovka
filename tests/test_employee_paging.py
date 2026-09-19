import subprocess
import unittest
from unittest.mock import patch

import test_crews as fixtures


class EmployeePagingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls): fixtures.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.f = fixtures.CrewWorkflowTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            for n in range(62):
                db.execute('INSERT INTO workers(full_name,personnel_no,profession) VALUES (?,?,?)',
                           (f'Страница {n:03d}', f'page-{n:03d}', 'Монтажник'))
            self.special = db.execute("INSERT INTO workers(full_name,personnel_no,active) VALUES ('Ёлкин Иван','far-away',0)").lastrowid
            db.commit()

    def page(self, **params):
        return self.f.admin.get('/api/employees', query_string={'page': 0, 'page_size': 25, **params})

    def test_pages_cover_legacy_list_without_duplicates_and_only_page_is_signed(self):
        import crew_api
        original = self.f.admin.get('/api/employees').json
        ids = []
        for page in range((len(original['rows']) + 24) // 25):
            with patch('crew_api.signed_employee_snapshot', wraps=crew_api.signed_employee_snapshot) as sign:
                result = self.page(page=page).json
                self.assertEqual(sign.call_count, len(result['rows']))
                self.assertLessEqual(sign.call_count, 25)
            self.assertEqual(result['summary'], original['summary'])
            self.assertEqual(result['filtered_summary'], original['summary'])
            ids.extend(row['id'] for row in result['rows'])
        self.assertEqual(ids, [row['id'] for row in original['rows']])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLess(self.page(page=99999).json['pagination']['page'], 99999)

    def test_search_filters_counts_and_empty_pages_use_full_dataset(self):
        self.assertEqual(self.f.add(self.f.crew_a, [self.f.worker_ids[0]]).status_code, 200)
        response = self.page(q='елкин ИВАН', active='0', page=900)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.json['rows']], [self.special])
        self.assertEqual(response.json['pagination'], {'page': 0, 'page_size': 25, 'total': 1})
        self.assertGreater(response.json['summary']['total'], 25)
        self.assertEqual(response.json['filtered_summary']['total'], 1)
        self.assertIn(self.f.crew_a, [row['id'] for row in response.json['filters']['crews']])
        result = self.page(crew=str(self.f.crew_a), category=str(self.f.staffing_category_id)).json
        self.assertEqual([r['id'] for r in result['rows']], [self.f.worker_ids[0]])
        result = self.page(q='absent-person', page=3).json
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['pagination']['page'], 0)
        self.assertEqual(result['filtered_summary']['total'], 0)
        self.assertEqual(self.page(q='page-061').json['pagination']['total'], 1)

    def test_ecmascript_regex_and_explicit_errors_and_time_limit(self):
        result = self.page(q=r'^page-06[01]$, ^far-away$', regex='1').json
        self.assertEqual({r['personnel_no'] for r in result['rows']}, {'page-060', 'page-061', 'far-away'})
        self.assertEqual(self.page(q=r'(?<=page-)061$', regex='1').json['pagination']['total'], 1)
        self.assertEqual(self.page(q='[', regex='1').status_code, 400)
        with patch('employee_listing.subprocess.run', side_effect=subprocess.TimeoutExpired('node', 1.5)):
            self.assertEqual(self.page(q='x', regex='1').status_code, 400)
        # Real runaway expression: the process is killed rather than occupying a server thread indefinitely.
        with self.f.module.app.app_context():
            db = self.f.module.get_db()
            db.execute('UPDATE workers SET profession=? WHERE id=?', ('a' * 30000 + '!', self.special))
            db.commit()
        self.assertEqual(self.page(q='^(a+)+$', regex='1').status_code, 400)

    def test_validation_and_authorization_are_not_bypassed(self):
        for params in ({'page': '-1'}, {'page': 'bad'}, {'page_size': '1000'}, {'q': 'x'*301},
                       {'active': '2'}, {'crew': 'bad'}, {'category': '-1'}, {'regex': 'yes'}):
            self.assertEqual(self.page(**params).status_code, 400, params)
        self.assertEqual(self.f.viewer.get('/api/employees?page=0').status_code, 403)
        self.assertEqual(self.f.module.app.test_client().get('/api/employees?page=0').status_code, 401)
        request = {'page': 0, 'active': ['0', '1'], 'category': ['none', str(self.f.staffing_category_id)]}
        self.assertEqual(self.f.admin.get('/api/employees', query_string=request).status_code, 200)


class OutstaffPagingTest(unittest.TestCase):
    def test_scope_search_and_global_category_counts(self):
        import test_outstaff
        test_outstaff.OutstaffTest.setUpClass()
        self.addCleanup(test_outstaff.OutstaffTest.tearDownClass)
        fixture = test_outstaff.OutstaffTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.assertEqual(fixture.apply().status_code, 200)
        full = fixture.client.get('/api/employees?scope=outstaff').json
        paged = fixture.client.get('/api/employees', query_string={'scope': 'outstaff', 'page': 0, 'q': 'внешний'}).json
        self.assertEqual([r['id'] for r in paged['rows']], [r['id'] for r in full['rows']])
        self.assertEqual(paged['summary']['workers'], sum(bool(r['category_id']) for r in full['rows']))
        self.assertEqual(paged['summary']['total'], len(full['rows']))


if __name__ == '__main__': unittest.main()
