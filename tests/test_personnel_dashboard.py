import unittest

import test_crews


class PersonnelDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_crews.CrewWorkflowTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        test_crews.CrewWorkflowTest.tearDownClass()

    def setUp(self):
        self.case = test_crews.CrewWorkflowTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def dashboard(self, client=None, **query):
        return (client or self.case.admin).get('/api/personnel-dashboard', query_string={
            'start': '2026-09-11', 'end': '2026-09-13', **query})

    def test_distinct_people_dates_actual_authors_and_zero_days(self):
        c = self.case
        ids = c.worker_ids[:2]
        self.assertEqual(c.add(c.crew_a, ids).status_code, 200)
        self.assertEqual(c.place(ids, c.sites[0]).status_code, 200)
        self.assertEqual(c.place(ids[:1], c.sites[0], client=c.admin, shift='2 смена').status_code, 200)
        self.assertEqual(c.place(ids[:1], c.sites[0], work_date='2026-09-12').status_code, 200)
        result = self.dashboard().get_json()
        self.assertEqual(result['counts'], [2, 1, 0])
        self.assertEqual(result['unique_count'], 2)
        users = {u['id']: u for u in result['users']}
        self.assertEqual(users[str(c.owner_a)]['counts'], [2, 1, 0])
        self.assertEqual(users[str(c.admin_id)]['counts'], [1, 0, 0])
        self.assertEqual(users[str(c.empty_owner)]['counts'], [0, 0, 0])
        self.assertEqual(users[str(c.owner_a)]['unique_count'], 2)
        self.assertEqual(self.dashboard(c.viewer).get_json(), result)
        # Existing author identity remains distinct even with identical or corrected names.
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute("UPDATE users SET full_name='Иванов Иван Иванович' WHERE id IN (?,?)", (c.owner_a, c.admin_id))
            db.execute('UPDATE workers SET active=0 WHERE id=?', (ids[0],))
            db.commit()
        renamed = self.dashboard().get_json()
        self.assertEqual(renamed['counts'], [2, 1, 0])
        self.assertEqual(len([u for u in renamed['users'] if u['full_name'] == 'Иванов Иван Иванович']), 2)

    def test_scope_cleared_assignments_and_missing_author_history(self):
        c = self.case
        self.assertEqual(c.add(c.crew_a, c.worker_ids[:1]).status_code, 200)
        self.assertEqual(c.add(c.crew_b, c.worker_ids[1:2], c.foreman_b).status_code, 200)
        self.assertEqual(c.place(c.worker_ids[:1], c.sites[0], client=c.admin).status_code, 200)
        self.assertEqual(c.place(c.worker_ids[1:2], c.sites[0], crew=c.crew_b, client=c.foreman_b).status_code, 200)
        own = self.dashboard(c.foreman_a).get_json()
        self.assertEqual(own['scope'], 'own_crews')
        self.assertEqual(own['counts'], [1, 0, 0])
        self.assertEqual([u['id'] for u in own['users']], [str(c.admin_id)])
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute("UPDATE assignments SET created_at='2099-01-01T00:00:00Z' WHERE worker_id=?", (c.worker_ids[0],))
            db.execute('DELETE FROM assignments WHERE worker_id=?', (c.worker_ids[1],))
            db.commit()
        result = self.dashboard().get_json()
        self.assertEqual(result['counts'], [1, 0, 0])
        self.assertEqual(next(u for u in result['users'] if u['id'] == 'unknown')['counts'], [1, 0, 0])
        self.assertEqual(next(u for u in result['users'] if u['id'] == str(c.owner_b))['unique_count'], 0)

    def test_range_validation_authentication_and_empty_period(self):
        for query in ({'start': ''}, {'end': 'wrong'}, {'start': '2026-09-14'},
                      {'start': '2025-09-12'}, {'end': '2026-02-30'}):
            self.assertEqual(self.dashboard(**query).status_code, 400)
        result = self.dashboard(start='2025-09-13').get_json()
        self.assertEqual(len(result['dates']), 366)
        self.assertEqual(result['counts'], [0] * 366)
        self.assertEqual(result['unique_count'], 0)
        self.assertEqual(self.dashboard(start='2026-09-13').get_json()['counts'], [0])
        guest = self.case.module.app.test_client()
        self.assertEqual(self.dashboard(guest).status_code, 401)
        self.assertEqual(len(self.case.admin.get('/api/personnel-dashboard').get_json()['dates']), 14)

    def test_category_stack_deduplicates_shifts_and_authors_using_current_binding(self):
        c = self.case
        ids = c.worker_ids[:3]
        self.assertEqual(c.add(c.crew_a, ids).status_code, 200)
        self.assertEqual(c.place(ids, c.sites[0]).status_code, 200)
        self.assertEqual(c.place(ids[:1], c.sites[0], client=c.admin, shift='2 смена').status_code, 200)
        with c.module.app.app_context():
            db = c.module.get_db()
            second = db.execute('''INSERT INTO gdlr_categories(name,name_key,color,edit_token,updated_by,updated_at)
                VALUES ('Другая категория','другая категория','#AA3399','test',?,'now')''', (c.admin_id,)).lastrowid
            db.execute('UPDATE employee_gdlr SET category_id=? WHERE worker_id=?', (second, ids[1]))
            db.execute('DELETE FROM employee_gdlr WHERE worker_id=?', (ids[2],))
            db.execute("UPDATE workers SET category='Текст из файла не является привязкой' WHERE id=?", (ids[2],))
            db.commit()
        result = self.dashboard().json
        categories = {row['id']: row for row in result['category_series']}
        self.assertEqual(result['counts'], [3, 0, 0])
        self.assertEqual([sum(row['counts'][i] for row in categories.values()) for i in range(3)], result['counts'])
        self.assertEqual(categories[str(second)]['color'], '#AA3399')
        self.assertEqual(categories['uncategorized']['counts'], [1, 0, 0])
        self.assertEqual(categories[str(c.staffing_category_id)]['counts'], [1, 0, 0])
        selected = self.dashboard(user=[str(c.owner_a), str(c.admin_id)]).json
        self.assertEqual(selected['category_series'], result['category_series'])
        one_author = self.dashboard(user=str(c.admin_id)).json
        self.assertEqual(one_author['counts'], [1, 0, 0])
        self.assertEqual([row['id'] for row in one_author['category_series']], [str(c.staffing_category_id)])
        single_day = self.dashboard(start='2026-09-11', end='2026-09-11').json
        self.assertEqual([sum(row['counts']) for row in single_day['category_series']], [1, 1, 1])
        self.assertEqual(self.dashboard(start='2026-09-13').json['category_series'], [])

    def test_explicit_smu_scope_filters_stack_and_totals_together(self):
        import json
        c = self.case
        ids = c.worker_ids[:2]
        c.add(c.crew_a, ids[:1]);c.add(c.crew_b, ids[1:], c.foreman_b)
        c.place(ids[:1], c.sites[0]);c.place(ids[1:], c.sites[0], crew=c.crew_b, client=c.foreman_b)
        with c.module.app.app_context():
            db = c.module.get_db()
            for worker_id, department in zip(ids, ('СМУ А', 'СМУ Б')):
                db.execute('UPDATE workers SET department=? WHERE id=?', (department, worker_id))
            for user_id in (c.owner_a, c.admin_id):
                db.execute('''INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at)
                    VALUES (?,'selected',?,'test',?,'now')''', (user_id, json.dumps(['СМУ Б']), c.admin_id))
            db.commit()
        for client in (c.foreman_a, c.admin):
            result = self.dashboard(client).json
            self.assertEqual(result['scope'], 'assigned_smu')
            self.assertEqual(result['counts'], [1, 0, 0])
            self.assertEqual(result['category_series'][0]['counts'], [1, 0, 0])
        self.assertEqual(self.dashboard(c.super_admin).json['counts'], [2, 0, 0])

    def test_catalog_color_roundtrip_validation_permissions_and_concurrency(self):
        c = self.case
        path = '/api/gdlr-categories'
        for bad in ('red', '#fff', '#12345600', '#000000;url(x)', None, 5):
            response = c.write(c.super_admin, 'POST', path, {'name':'Некорректный цвет', 'color':bad})
            self.assertEqual(response.status_code, 400, bad)
        created = c.write(c.super_admin, 'POST', path, {'name':'Цветная категория','color':'#aabbcc'})
        self.assertEqual(created.status_code, 201, created.data)
        category = next(row for row in c.super_admin.get(path).json['rows'] if row['id']==created.json['id'])
        self.assertEqual(category['color'], '#AABBCC')
        patch = {'name':category['name'],'active':True,'color':'#123456','expected_token':category['edit_token']}
        for client in (c.admin, c.foreman_a, c.viewer):
            self.assertEqual(c.write(client, 'PATCH', path+'/'+str(category['id']), patch).status_code, 403)
        self.assertEqual(c.super_admin.patch(path+'/'+str(category['id']), json=patch).status_code, 403)
        self.assertEqual(c.write(c.super_admin, 'PATCH', path+'/'+str(category['id']), patch).status_code, 200)
        self.assertEqual(c.write(c.super_admin, 'PATCH', path+'/'+str(category['id']), patch).status_code, 409)
        current = next(row for row in c.super_admin.get(path).json['rows'] if row['id']==category['id'])
        self.assertEqual(current['color'], '#123456')
        legacy_patch = {'name':'Переименовано','active':True,'expected_token':current['edit_token']}
        self.assertEqual(c.write(c.super_admin, 'PATCH', path+'/'+str(category['id']), legacy_patch).status_code, 200)
        current = next(row for row in c.super_admin.get(path).json['rows'] if row['id']==category['id'])
        self.assertEqual(current['color'], '#123456')
        with c.module.app.app_context():
            db = c.module.get_db()
            db.execute('UPDATE employee_gdlr SET category_id=? WHERE worker_id=?', (category['id'], c.worker_ids[0]))
            db.execute('UPDATE gdlr_categories SET staffing_allowed=1 WHERE id=?', (category['id'],))
            db.commit()
        c.add(c.crew_a, c.worker_ids[:1]);c.place(c.worker_ids[:1], c.sites[0])
        self.assertEqual(self.dashboard().json['category_series'][0]['color'], '#123456')
