import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from staffing_import import ImportProblem, apply_attendance, import_conflicts, migrate_staffing, parse_attendance


def workbook_bytes(rows, title="Явка"):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(['№', '', '', 'Таб. Номер', 'ФИО', 'Должность', 'Категория ГДЛР', '', '', '',
                  'Подразделение', 'Квалификация'] + [''] * 12 + ['Номер бригады'])
    for changes in rows:
        record = {'number': '70001', 'name': 'Иванов Иван Иванович', 'position': 'Монтажник 4 разряда',
                  'category': 'Монтажник ТТ', 'department': 'Строительно-монтажный участок № 15.2',
                  'qualification': 'Рабочие', 'crew': 'Бригада №12_261', **changes}
        sheet.append([1, '', '', record['number'], record['name'], record['position'], record['category'],
                      '', '', '', record['department'], record['qualification']] + [''] * 12 + [record['crew']])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class AttendanceParserTest(unittest.TestCase):
    def test_filters_and_preserves_exact_identifiers(self):
        content = workbook_bytes([{}, {'number': '000123', 'crew': '#N/A'}, {'number': 'А12'},
            {'number': '4', 'department': 'Группа складского учета'},
            {'number': '5', 'qualification': 'Специалисты'},
            {'number': '6', 'category': 'Прочие водители'},
            {'number': '7', 'category': 'Машинист крана'},
            {'number': '8', 'category': 'Вспомогательные рабочие РММ'},
            {'number': '9', 'category': 'Прочие основные рабочие', 'position': 'Подсобный рабочий'}])
        data = parse_attendance(content, 'source.xlsx')
        self.assertEqual(data['summary']['selected'], 4)
        self.assertEqual(data['summary']['without_crew'], 1)
        self.assertEqual(data['summary']['crews'], 1)
        self.assertEqual(data['summary']['category_excluded'], 3)
        self.assertEqual(data['rows'][1]['personnel_no'], '000123')
        self.assertEqual(data['rows'][1]['employer'], 'ЛГСС')
        self.assertEqual(data['rows'][2]['employer'], '')
        self.assertEqual(data['rows'][2]['contractor'], 'ЛГСС')
        self.assertEqual(data['rows'][3]['category'], 'Прочие основные рабочие')
        self.assertFalse(data['issues'])

    def test_rejects_duplicate_identity_and_missing_sheet(self):
        self.assertTrue(parse_attendance(workbook_bytes([{}, {}]), 'source.xlsx')['issues'])
        with self.assertRaises(ImportProblem):
            parse_attendance(workbook_bytes([{}], 'Неявка'), 'source.xlsx')

    def test_missing_category_is_not_invented(self):
        data = parse_attendance(workbook_bytes([{'category': ''}]), 'source.xlsx')
        self.assertTrue(data['issues'])


class StaffingWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
            'ADMIN_PASSWORD': 'staffing-test-password-123', 'SECRET_KEY': 'staffing-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temporary.name) / 'staffing.db'
        self.app = self.module.app
        with self.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.foreman_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('staff-f','unused','Прораб','foreman','now')").lastrowid
            self.viewer_id = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('staff-v','unused','Просмотр','viewer','now')").lastrowid
            db.commit()
        self.admin = self.client(self.admin_id)
        self.foreman = self.client(self.foreman_id)
        self.viewer = self.client(self.viewer_id)
        self.content = workbook_bytes([{}, {'number': '70002', 'name': 'Петров Пётр Петрович'},
                                      {'number': '70003', 'name': 'Сидоров Сидор Сидорович', 'crew': '#N/A'}])
        self.parsed = parse_attendance(self.content, 'численность.xlsx')

    def client(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = user_id
            session['csrf_token'] = 'staffing-csrf'
        return client

    def write(self, url, payload, client=None):
        return (client or self.admin).put(url, json=payload, headers={'X-CSRF-Token': 'staffing-csrf'})

    def table(self, client=None):
        return (client or self.admin).get('/api/staffing?date=2026-09-12&shift=1%20смена')

    def apply(self):
        with self.app.app_context():
            return apply_attendance(self.module.get_db(), self.parsed, self.admin_id, self.module.utc_now)

    def test_manual_category_survives_import_and_reaches_staffing_search(self):
        self.apply()
        headers = {'X-CSRF-Token': 'staffing-csrf'}
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='super_admin' WHERE id=?", (self.admin_id,))
            db.commit()
        created = self.admin.post('/api/gdlr-categories', json={'name': 'Утверждённая категория'}, headers=headers)
        self.assertEqual(created.status_code, 201)
        category = self.admin.get('/api/gdlr-categories').get_json()['rows'][0]
        employee = next(row for row in self.admin.get('/api/employees').get_json()['rows'] if row['personnel_no'] == '70001')
        response = self.write(f"/api/employees/{employee['id']}/category", {
            'category_id': category['id'], 'category_token': category['edit_token'], 'expected_token': employee['membership_token']})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            parsed = parse_attendance(workbook_bytes([{'category': 'Новая исходная категория'}]), 'новая явка.xlsx')
            apply_attendance(self.module.get_db(), parsed, self.admin_id, self.module.utc_now)
            self.module.init_db()
        employee = next(row for row in self.admin.get('/api/employees').get_json()['rows'] if row['id'] == employee['id'])
        self.assertEqual(employee['source_category'], 'Новая исходная категория')
        self.assertEqual(employee['category'], 'Утверждённая категория')
        self.assertEqual(len(self.admin.get('/api/gdlr-categories').get_json()['rows']), 1)
        self.assertEqual(self.table().get_json()['rows'][0]['category'], 'Утверждённая категория')
        summary = self.admin.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()
        self.assertIn('Утверждённая категория', summary['index'][0]['search_fields'])

    def group_payload(self, rows, responsible=False):
        return {'date': '2026-09-12', 'worker_ids': [row['id'] for row in rows],
                'expected_crews': {str(row['id']): row['crew_id'] for row in rows},
                'expected_group_tokens': {str(row['id']): row['group_token'] for row in rows},
                'expected_tokens': {str(row['id']): row['row_token' if responsible else 'day_token'] for row in rows}}

    def group_table(self):
        return self.admin.get('/api/staffing?date=2026-09-12&shift=all').get_json()['rows']

    def test_itr_group_summary_respects_row_override_and_person_identity(self):
        self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE crews SET linear_itr='Общий ИТР'")
            db.commit()
        rows = self.group_table()
        self.assertEqual(len({row['itr_group_key'] for row in rows}), 1)
        row = rows[0]
        self.assertEqual(self.write(f"/api/staffing/crews/{row['crew_id']}/workers/{row['id']}/linear-itr", {
            'linear_itr_override': 'Другой ИТР', 'expected_token': row['row_token']}).status_code, 200)
        rows = self.group_table()
        self.assertEqual(len({row['itr_group_key'] for row in rows}), 2)
        summary = self.admin.get('/api/staffing?date=2026-09-12&shift=all&view=summary').get_json()
        self.assertEqual(summary['rows'], [])
        full = {row['id']: row for row in rows}
        for entry in summary['index']:
            self.assertEqual(entry['itr_group_key'], full[entry['id']]['itr_group_key'])
            self.assertEqual(entry['group_token'], full[entry['id']]['group_token'])
            self.assertIn(full[entry['id']]['linear_itr_name'], entry['search_fields'])
        with self.app.app_context():
            db = self.module.get_db()
            people = db.execute('SELECT id FROM staffing_people ORDER BY id LIMIT 2').fetchall()
            db.execute("UPDATE staffing_people SET full_name='Однофамилец ИТР' WHERE id IN (?,?)", (people[0]['id'], people[1]['id']))
            db.execute("UPDATE staffing_row_details SET linear_itr_override='Однофамилец ИТР',linear_itr_person_id=? WHERE worker_id=?", (people[0]['id'], row['id']))
            db.execute("UPDATE crews SET linear_itr='Однофамилец ИТР',linear_itr_person_id=?", (people[1]['id'],))
            db.commit()
        self.assertEqual(len({row['itr_group_key'] for row in self.group_table()}), 2)

    def test_itr_group_place_shift_and_responsible_are_atomic_across_crews(self):
        self.apply()
        rows = self.group_table()
        self.assertEqual(len({row['crew_id'] for row in rows}), 2)
        with self.app.app_context():
            db = self.module.get_db()
            site = db.execute('SELECT id FROM subobjects LIMIT 1').fetchone()[0]
        payload = {**self.group_payload(rows), 'subobject_id': site}
        self.assertEqual(self.write('/api/staffing/groups/assignments', payload, self.viewer).status_code, 403)
        self.assertEqual(self.admin.put('/api/staffing/groups/assignments', json=payload).status_code, 403)
        bad = {**payload, 'expected_tokens': {**payload['expected_tokens'], str(rows[-1]['id']): 'stale'}}
        self.assertEqual(self.write('/api/staffing/groups/assignments', bad).status_code, 409)
        self.assertTrue(all(row['assignment_id'] is None for row in self.group_table()))
        self.assertEqual(self.write('/api/staffing/groups/assignments', payload, self.foreman).status_code, 403)
        self.assertEqual(self.write('/api/staffing/groups/assignments', payload).status_code, 200)
        rows = self.group_table()
        self.assertTrue(all(row['subobject_id'] == site for row in rows))
        self.assertEqual(self.write('/api/staffing/groups/shifts', {**self.group_payload(rows), 'shift': '2 смена'}).status_code, 200)
        rows = self.group_table()
        self.assertTrue(all(row['employee_shift'] == '2 смена' and row['subobject_id'] == site for row in rows))
        targets = [rows[0], rows[-1]]
        self.assertEqual(self.write('/api/staffing/groups/responsible', {
            **self.group_payload(targets, True), 'field': 'linear_itr', 'value': 'Назначенный ИТР'}).status_code, 200)
        updated = self.group_table()
        self.assertEqual(sum(row['linear_itr_name'] == 'Назначенный ИТР' for row in updated), 2)
        self.assertEqual(sum(row['linear_itr_override'] is None for row in updated), 1)
        targets = [row for row in updated if row['id'] in {r['id'] for r in targets}]
        self.assertEqual(self.write('/api/staffing/groups/responsible', {
            **self.group_payload(targets, True), 'field': 'linear_itr', 'value': None}).status_code, 200)
        self.assertTrue(all(row['linear_itr_override'] is None for row in self.group_table()))

    def test_itr_group_rejects_changed_default_and_membership_before_any_write(self):
        self.apply()
        rows = self.group_table()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE crews SET linear_itr='Изменённый ИТР' WHERE id=?", (rows[-1]['crew_id'],))
            db.commit()
        payload = {**self.group_payload(rows, True), 'field': 'brigadier', 'value': 'Бригадир'}
        self.assertEqual(self.write('/api/staffing/groups/responsible', payload).status_code, 409)
        self.assertTrue(all(row['brigadier_override'] is None for row in self.group_table()))
        self.assertEqual(self.write('/api/staffing/groups/shifts', {**self.group_payload(rows), 'shift': '2 смена'}).status_code, 409)
        rows = self.group_table()
        wrong_crews = {str(row['id']): rows[0]['crew_id'] for row in rows}
        self.assertEqual(self.write('/api/staffing/groups/responsible', {
            **self.group_payload(rows, True), 'expected_crews': wrong_crews, 'field': 'brigadier', 'value': 'Бригадир'}).status_code, 409)
        self.assertTrue(all(row['brigadier_override'] is None and row['employee_shift'] == '1 смена' for row in self.group_table()))

    def test_people_cache_revalidates_same_import_and_enforces_roles(self):
        self.apply()
        url = '/api/staffing/people'
        first = self.admin.get(url)
        token = first.headers['ETag']
        self.assertEqual(self.admin.get(url, headers={'If-None-Match': token}).status_code, 304)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE staffing_people SET profession=? WHERE id=?',
                       ('Уточнённая должность', first.get_json()['people'][0]['id']))
            db.commit()
        changed = self.admin.get(url, headers={'If-None-Match': token})
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.headers['ETag'], token)
        denied = self.viewer.get(url, headers={'If-None-Match': token})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.headers['Cache-Control'], 'no-store')
        self.assertNotIn('ETag', denied.headers)

    def test_import_backup_idempotency_and_prefilled_projection(self):
        result = self.apply()
        self.assertTrue((self.module.DATABASE_PATH.parent / 'backups' / result['backup']).exists())
        self.assertTrue(self.apply()['already_imported'])
        data = self.table().get_json()
        self.assertEqual(len(data['rows']), 3)
        self.assertEqual(len(data['crews']), 2)
        self.assertTrue(all(r['contractor'] == r['employer'] == 'ЛГСС' for r in data['rows']))
        self.assertTrue(all(r['gsp_profession'] == r['linear_itr'] == r['brigadier_name'] == '' for r in data['rows']))
        self.assertTrue(all(r['object_name'] is None and r['subobject_name'] is None for r in data['rows']))
        self.assertEqual(len(self.table(self.foreman).get_json()['rows']), 0)
        self.assertEqual(self.table(self.viewer).status_code, 403)

    def test_summary_and_lazy_crew_details_preserve_filters_and_access(self):
        self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("DELETE FROM crew_members WHERE worker_id IN (SELECT id FROM workers WHERE personnel_no='70003')")
            db.commit()
        url = '/api/staffing?date=2026-09-12&shift=all'
        full = self.admin.get(url).get_json()
        summary = self.admin.get(url + '&view=summary').get_json()
        self.assertEqual(summary['rows'], [])
        self.assertEqual(summary['crews'], full['crews'])
        self.assertEqual(len(summary['index']), len(full['rows']))
        for entry, row in zip(summary['index'], full['rows']):
            self.assertEqual(entry['id'], row['id'])
            self.assertEqual(entry['number'], row['number'])
            self.assertEqual(entry['employee_shift'], row['employee_shift'])
            self.assertEqual(entry['category'], row['category'])
            self.assertEqual(entry['employer'], row['employer'])
            self.assertIn(row['employer'] or '', entry['search_fields'])
            self.assertIn(row['full_name'], entry['search_fields'])
            self.assertIn(row['profession'], entry['search_fields'])
            self.assertNotIn('day_token', entry)
            self.assertNotIn('brigadier_override', entry)
        crew_id = next(c['id'] for c in full['crews'] if c['id'])
        details_url = url + '&crew_id=' + str(crew_id)
        details = self.admin.get(details_url).get_json()
        expected = [row for row in full['rows'] if row['crew_id'] == crew_id]
        self.assertEqual({r['id'] for r in details['rows']}, {r['id'] for r in expected})
        self.assertTrue(all('day_token' in r for r in details['rows']))
        unassigned = self.admin.get(url + '&crew_id=unassigned').get_json()
        self.assertEqual(len(unassigned['rows']), 1)
        self.assertIsNone(unassigned['rows'][0]['crew_id'])
        self.assertEqual(self.foreman.get(details_url).status_code, 403)
        self.assertEqual(self.foreman.get(url + '&view=summary').get_json()['index'], [])
        self.assertEqual(self.foreman.get(url + '&crew_id=unassigned').get_json()['rows'], [])
        self.assertEqual(self.viewer.get(url + '&view=summary').status_code, 403)
        self.assertEqual(self.admin.get(url + '&crew_id=invalid').status_code, 400)
        self.assertEqual(self.admin.get(url + '&crew_id=999999').status_code, 404)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.foreman_id, crew_id))
            db.commit()
        self.assertEqual(len(self.foreman.get(details_url).get_json()['rows']), len(expected))
        self.assertEqual(len(self.foreman.get(url + '&view=summary').get_json()['index']), len(expected))

    def test_category_filter_index_uses_effective_category_before_loading_crews(self):
        self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            worker_id = db.execute("SELECT id FROM workers WHERE personnel_no='70001'").fetchone()[0]
            category_id = db.execute('''INSERT INTO gdlr_categories(name,name_key,active,edit_token,updated_by,updated_at)
                VALUES ('Категория из справочника','категория из справочника',1,'test',?,'now')''', (self.admin_id,)).lastrowid
            db.execute('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                VALUES (?,?,'test',?,'now')''', (worker_id, category_id, self.admin_id))
            db.execute("UPDATE workers SET category='' WHERE personnel_no='70002'")
            db.commit()
        url = '/api/staffing?date=2026-09-12&shift=all'
        full = self.admin.get(url).get_json()['rows']
        index = self.admin.get(url + '&view=summary').get_json()['index']
        self.assertEqual({r['id']: r['category'] for r in index}, {r['id']: r['category'] for r in full})
        self.assertEqual(next(r['category'] for r in index if r['id'] == worker_id), 'Категория из справочника')
        self.assertIn('', [r['category'] for r in index])

    def test_brigade_defaults_and_row_override_are_independent(self):
        self.apply()
        data = self.table().get_json()
        crew = next(c for c in data['crews'] if c['name'] == 'Бригада №12_261')
        url = f"/api/staffing/crews/{crew['id']}/details"
        result = self.write(url, {'linear_itr': 'Мастер М.М.', 'brigadier': 'Бригадир Б.Б.', 'expected_token': ''})
        self.assertEqual(result.status_code, 200)
        row = next(r for r in self.table().get_json()['rows'] if r['personnel_no'] == '70001')
        row_url = f"/api/staffing/crews/{crew['id']}/workers/{row['id']}/brigadier"
        edit = self.write(row_url, {'brigadier_override': 'Другой Д.Д.', 'expected_token': None})
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(self.write(row_url, {'brigadier_override': 'Устаревший', 'expected_token': None}).status_code, 409)
        self.assertEqual(self.write(url, {'linear_itr': '', 'brigadier': '', 'expected_token': ''}).status_code, 409)
        self.write(url, {'linear_itr': 'Мастер М.М.', 'brigadier': 'Новый Н.Н.', 'expected_token': result.get_json()['details_token']})
        rows = {r['personnel_no']: r for r in self.table().get_json()['rows']}
        self.assertEqual(rows['70001']['brigadier_name'], 'Другой Д.Д.')
        self.assertEqual(rows['70002']['brigadier_name'], 'Новый Н.Н.')
        self.assertEqual(rows['70001']['linear_itr'], 'Мастер М.М.')
        restored = self.write(row_url, {'brigadier_override': None, 'expected_token': edit.get_json()['row_token']})
        self.assertEqual(restored.get_json()['brigadier_name'], 'Новый Н.Н.')
        self.assertEqual(self.write(url, {'linear_itr': '', 'brigadier': '', 'expected_token': ''}, self.foreman).status_code, 403)
        self.assertEqual(self.admin.put(url, json={}).status_code, 403)

    def test_selected_responsible_is_atomic_scoped_and_keeps_other_values(self):
        self.apply()
        rows = {r['personnel_no']: r for r in self.table().get_json()['rows']}
        first, second, outside = rows['70001'], rows['70002'], rows['70003']
        base = f"/api/staffing/crews/{first['crew_id']}"
        url = base + '/workers/responsible'
        payload = {'field': 'brigadier', 'worker_ids': [first['id']], 'value': 'Выбранный бригадир',
                   'expected_tokens': {str(first['id']): None}}
        for client in (self.foreman, self.viewer):
            self.assertEqual(self.write(url, payload, client).status_code, 403)
        self.assertEqual(self.admin.put(url, json=payload).status_code, 403)
        for patch_values in ({'worker_ids': []}, {'worker_ids': [True]}, {'expected_tokens': {}},
                             {'field': 'invalid'}, {'value': 12}):
            self.assertEqual(self.write(url, {**payload, **patch_values}).status_code, 400)
        self.assertEqual(self.write(url, {**payload, 'brigadier_person_id': 999999}).status_code, 400)
        self.assertEqual(self.write(url, payload).status_code, 200)
        current = {r['id']: r for r in self.table().get_json()['rows']}
        self.assertEqual(current[first['id']]['brigadier_name'], payload['value'])
        self.assertEqual(current[second['id']]['brigadier_name'], '')
        self.assertEqual(current[outside['id']]['brigadier_name'], '')
        # A stale member or a member of another crew rejects the entire selection.
        stale = {**payload, 'worker_ids': [second['id'], first['id']],
                 'expected_tokens': {str(second['id']): None, str(first['id']): None}}
        self.assertEqual(self.write(url, stale).status_code, 409)
        foreign = {**payload, 'worker_ids': [second['id'], outside['id']],
                   'expected_tokens': {str(second['id']): None, str(outside['id']): None}}
        self.assertEqual(self.write(url, foreign).status_code, 409)
        self.assertEqual({r['id']: r for r in self.table().get_json()['rows']}, current)
        person = self.admin.get('/api/staffing/people').get_json()['people'][0]
        itr = {'field': 'linear_itr', 'worker_ids': [first['id'], second['id']],
               'value': person['full_name'], 'linear_itr_person_id': person['id'],
               'expected_tokens': {str(i): current[i]['row_token'] for i in (first['id'], second['id'])}}
        self.assertEqual(self.write(url, itr).get_json()['updated'], 2)
        current = {r['id']: r for r in self.table().get_json()['rows']}
        for worker_id in (first['id'], second['id']):
            self.assertEqual(current[worker_id]['linear_itr_name'], person['full_name'])
            self.assertEqual(current[worker_id]['linear_itr_person_id'], person['id'])
        self.assertEqual(current[first['id']]['brigadier_name'], payload['value'])
        self.assertEqual(current[second['id']]['brigadier_name'], '')
        crew = next(c for c in self.table().get_json()['crews'] if c['id'] == first['crew_id'])
        self.assertEqual(crew['linear_itr'], '')
        self.assertEqual(crew['brigadier'], '')
        restored = {**itr, 'value': None, 'linear_itr_person_id': None,
                    'worker_ids': [first['id']], 'expected_tokens': {str(first['id']): current[first['id']]['row_token']}}
        self.assertEqual(self.write(url, restored).status_code, 200)
        current = {r['id']: r for r in self.table().get_json()['rows']}
        self.assertEqual(current[first['id']]['linear_itr_name'], '')
        self.assertEqual(current[second['id']]['linear_itr_name'], person['full_name'])

    def test_itr_and_brigadier_corrections_inherit_independently(self):
        self.apply()
        rows = {r['personnel_no']: r for r in self.table().get_json()['rows']}
        row = rows['70001']
        base = f"/api/staffing/crews/{row['crew_id']}"
        itr_url = f"{base}/workers/{row['id']}/linear-itr"
        brig_url = f"{base}/workers/{row['id']}/brigadier"
        crew = self.write(base + '/details', {'linear_itr': 'Общий ИТР', 'brigadier': 'Общий бригадир', 'expected_token': ''}).get_json()
        for client in (self.foreman, self.viewer):
            self.assertEqual(self.write(itr_url, {'linear_itr_override': 'Чужой', 'expected_token': None}, client).status_code, 403)
        self.assertEqual(self.admin.put(itr_url, json={'linear_itr_override': 'Без CSRF'}).status_code, 403)
        for payload in ({'expected_token': None}, {'linear_itr_override': 123}, {'linear_itr_override': 'x' * 201}):
            self.assertEqual(self.write(itr_url, payload).status_code, 400)
        itr = self.write(itr_url, {'linear_itr_override': ' Другой ИТР ', 'expected_token': None})
        self.assertEqual(itr.status_code, 200)
        self.assertEqual(itr.get_json()['linear_itr_name'], 'Другой ИТР')
        self.assertEqual(self.write(brig_url, {'brigadier_override': 'Устаревший', 'expected_token': None}).status_code, 409)
        brig = self.write(brig_url, {'brigadier_override': 'Другой бригадир', 'expected_token': itr.get_json()['row_token']}).get_json()
        self.assertEqual(self.write(base + '/details', {'linear_itr': 'Новый ИТР', 'brigadier': 'Новый бригадир', 'expected_token': crew['details_token']}).status_code, 200)
        rows = {r['personnel_no']: r for r in self.table().get_json()['rows']}
        self.assertEqual((rows['70001']['linear_itr_name'], rows['70001']['brigadier_name']), ('Другой ИТР', 'Другой бригадир'))
        self.assertEqual((rows['70002']['linear_itr_name'], rows['70002']['brigadier_name']), ('Новый ИТР', 'Новый бригадир'))
        blank = self.write(itr_url, {'linear_itr_override': '', 'expected_token': brig['row_token']}).get_json()
        self.assertEqual(blank['linear_itr_name'], '')
        restored = self.write(itr_url, {'linear_itr_override': None, 'expected_token': blank['row_token']}).get_json()
        self.assertEqual(restored['linear_itr_name'], 'Новый ИТР')
        row = next(r for r in self.table().get_json()['rows'] if r['personnel_no'] == '70001')
        self.assertIsNone(row['linear_itr_override'])
        self.assertEqual(row['brigadier_name'], 'Другой бригадир')
        self.assertEqual(self.write(brig_url, {'brigadier_override': None, 'expected_token': restored['row_token']}).get_json()['brigadier_name'], 'Новый бригадир')
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM crew_members WHERE worker_id=?', (row['id'],))
            db.commit()
        self.assertEqual(self.write(itr_url, {'linear_itr_override': 'ИТР', 'expected_token': None}).status_code, 409)

    def test_itr_migration_preserves_existing_brigadier_corrections(self):
        self.apply()
        row = self.table().get_json()['rows'][0]
        result = self.write(f"/api/staffing/crews/{row['crew_id']}/workers/{row['id']}/brigadier",
                            {'brigadier_override': 'Сохранённый бригадир', 'expected_token': None}).get_json()
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('ALTER TABLE staffing_row_details DROP COLUMN linear_itr_override')
            db.commit()
            migrate_staffing(db)
            migrate_staffing(db)
            actual = db.execute('SELECT * FROM staffing_row_details WHERE worker_id=?', (row['id'],)).fetchone()
            self.assertEqual(actual['brigadier_override'], 'Сохранённый бригадир')
            self.assertEqual(actual['edit_token'], result['row_token'])
            self.assertIsNone(actual['linear_itr_override'])

    def test_file_people_include_itr_and_bind_exact_selected_record(self):
        self.parsed = parse_attendance(workbook_bytes([{}, {'number': '70002', 'name': 'Иванов Иван Иванович'},
            {'number': '80001', 'name': 'Мастер Иван Иванович', 'qualification': 'Руководители', 'position': 'Прораб'}]), 'people.xlsx')
        self.apply()
        self.assertEqual(len(self.table().get_json()['rows']), 2)
        directory = self.admin.get('/api/staffing/people').get_json()['people']
        self.assertEqual(len(directory), 3)
        self.assertEqual(self.viewer.get('/api/staffing/people').status_code, 403)
        by_number = {p['personnel_no']: p for p in directory}
        crew = self.table().get_json()['crews'][0]
        itr, brig = by_number['80001'], by_number['70002']
        payload = {'linear_itr': itr['full_name'], 'linear_itr_person_id': itr['id'],
                   'brigadier': brig['full_name'], 'brigadier_person_id': brig['id'], 'expected_token': crew['details_token']}
        url = f"/api/staffing/crews/{crew['id']}/details"
        self.assertEqual(self.write(url, {**payload, 'linear_itr': 'Подменённое ФИО'}).status_code, 400)
        self.assertEqual(self.write(url, {**payload, 'linear_itr_person_id': 999999}).status_code, 400)
        self.assertEqual(self.write(url, {**payload, 'linear_itr_person_id': True}).status_code, 400)
        self.assertEqual(self.write(url, payload, self.foreman).status_code, 403)
        self.assertEqual(self.write(url, payload).status_code, 200)
        saved = self.table().get_json()
        self.assertEqual(saved['crews'][0]['linear_itr_person_id'], itr['id'])
        self.assertEqual(saved['crews'][0]['brigadier_person_id'], brig['id'])
        self.assertTrue(all(r['linear_itr_name'] == itr['full_name'] for r in saved['rows']))
        row = saved['rows'][0]
        row_url = f"/api/staffing/crews/{crew['id']}/workers/{row['id']}/linear-itr"
        correction = self.write(row_url, {'linear_itr_override': brig['full_name'], 'linear_itr_person_id': brig['id'], 'expected_token': None})
        self.assertEqual(correction.status_code, 200)
        self.assertEqual(self.table().get_json()['rows'][0]['linear_itr_person_id'], brig['id'])
        restored = self.write(row_url, {'linear_itr_override': None, 'linear_itr_person_id': None, 'expected_token': correction.get_json()['row_token']})
        self.assertEqual(restored.get_json()['linear_itr_name'], itr['full_name'])
        with self.app.app_context():
            db = self.module.get_db()
            migrate_staffing(db)
            self.assertEqual(db.execute('SELECT linear_itr_person_id FROM crews WHERE id=?', (crew['id'],)).fetchone()[0], itr['id'])
        other_shift = self.admin.get('/api/staffing?date=2026-09-14&shift=2%20смена').get_json()
        self.assertEqual(other_shift['crews'][0]['brigadier_person_id'], brig['id'])

    def test_catalog_backfill_does_not_reimport_or_change_crews(self):
        result = self.apply()
        with self.app.app_context():
            db = self.module.get_db()
            before = [tuple(r) for r in db.execute('SELECT * FROM crews ORDER BY id')]
            db.execute('DELETE FROM staffing_people')
            db.commit()
        refreshed = self.apply()
        self.assertTrue(refreshed['already_imported'])
        self.assertEqual(refreshed['id'], result['id'])
        self.assertEqual(refreshed['people_added'], 3)
        self.assertTrue((self.module.DATABASE_PATH.parent / 'backups' / refreshed['backup']).exists())
        with self.app.app_context():
            self.assertEqual(before, [tuple(r) for r in self.module.get_db().execute('SELECT * FROM crews ORDER BY id')])
        ids = [p['id'] for p in self.admin.get('/api/staffing/people').get_json()['people']]
        self.assertEqual(self.apply()['people_added'], 0)
        self.assertEqual(ids, [p['id'] for p in self.admin.get('/api/staffing/people').get_json()['people']])

    def day_rows(self, day='2026-09-13'):
        return self.admin.get('/api/staffing?date=' + day + '&shift=all').get_json()['rows']

    def day_write(self, rows, operation, extra, client=None):
        return self.write(f"/api/staffing/crews/{rows[0]['crew_id']}/{operation}", {
            'date': '2026-09-13', 'worker_ids': [r['id'] for r in rows],
            'expected_tokens': {str(r['id']): r['day_token'] for r in rows}, **extra}, client)

    def test_individual_shifts_bulk_placement_and_move_preserve_sites(self):
        self.apply()
        rows = [r for r in self.day_rows() if r['source_crew']]
        self.assertTrue(all(r['employee_shift'] == '1 смена' for r in rows))
        self.assertEqual(self.day_write(rows, 'assignments', {'subobject_id': 1}).status_code, 200)
        rows = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена'}).status_code, 200)
        rows = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual(self.day_write(rows[:1], 'shifts', {'shift': '2 смена'}).status_code, 200)
        rows = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual({r['employee_shift'] for r in rows}, {'1 смена', '2 смена'})
        self.assertEqual(self.day_write(rows, 'assignments', {'subobject_id': 1}).status_code, 200)
        rows = [r for r in self.day_rows() if r['source_crew']]
        self.assertTrue(all(r['subobject_id'] == 1 for r in rows))
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '2 смена'}).status_code, 200)
        moved = [r for r in self.day_rows() if r['source_crew']]
        self.assertTrue(all(r['employee_shift'] == '2 смена' and r['subobject_id'] == 1 for r in moved))
        self.assertTrue(all(r['employee_shift'] == '1 смена' and r['assignment_id'] is None for r in self.day_rows('2026-09-14')))
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE work_date='2026-09-13'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments WHERE work_date='2026-09-13' AND shift='1 смена'").fetchone()[0], 0)
            self.assertTrue(db.execute("SELECT 1 FROM assignment_events WHERE shift='1 смена' AND after_subobject_id IS NULL").fetchone())
        self.assertEqual(self.day_write(moved, 'assignments', {'subobject_id': None}).status_code, 200)
        cleared = [r for r in self.day_rows() if r['source_crew']]
        self.assertTrue(all(r['employee_shift'] == '2 смена' and r['assignment_id'] is None for r in cleared))

    def clear_selected(self, rows, extra=None, client=None):
        return self.write('/api/staffing/assignments/clear', {
            'date': '2026-09-13', 'worker_ids': [r['id'] for r in rows],
            'expected_tokens': {str(r['id']): r['day_token'] for r in rows},
            'expected_crews': {str(r['id']): r['crew_id'] for r in rows}, **(extra or {})}, client)

    def assigned_two_crews(self):
        self.parsed = parse_attendance(workbook_bytes([{},
            {'number': '70002', 'name': 'Петров Пётр Петрович'},
            {'number': '70003', 'name': 'Сидоров Сидор Сидорович', 'crew': 'Бригада №2'},
            {'number': '70004', 'name': 'Орлов Олег Олегович', 'crew': 'Бригада №2'}]), 'test.xlsx')
        self.apply()
        rows = self.day_rows()
        for row in rows[:3]:
            self.assertEqual(self.day_write([row], 'assignments', {'subobject_id': 1}).status_code, 200)
        return self.day_rows()

    def test_clear_selected_across_crews_preserves_shift_other_people_and_dates(self):
        rows = self.assigned_two_crews()
        # A night assignment without a stored schedule must remain night after clearing.
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET shift='2 смена' WHERE worker_id=?", (rows[0]['id'],))
            db.execute('DELETE FROM staffing_shifts WHERE worker_id=?', (rows[0]['id'],))
            db.commit()
        rows = self.day_rows()
        self.assertEqual(self.day_write([rows[0]], 'assignments', {
            'date': '2026-09-14', 'subobject_id': 2,
            'expected_tokens': {str(rows[0]['id']): self.day_rows('2026-09-14')[0]['day_token']}}).status_code, 200)
        with self.app.app_context():
            before = {table: [tuple(r) for r in self.module.get_db().execute('SELECT * FROM ' + table)]
                      for table in ('crews', 'crew_members', 'staffing_row_details')}
        chosen = [rows[0], rows[2], rows[3]]
        result = self.clear_selected(chosen)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['cleared'], 2)
        fresh = self.day_rows()
        self.assertEqual(fresh[0]['employee_shift'], '2 смена')
        self.assertTrue(all(fresh[i]['assignment_id'] is None for i in (0, 2, 3)))
        self.assertEqual(fresh[1]['day_token'], rows[1]['day_token'])
        self.assertEqual(fresh[3]['day_token'], rows[3]['day_token'])
        self.assertEqual(self.day_rows('2026-09-14')[0]['subobject_id'], 2)
        with self.app.app_context():
            db = self.module.get_db()
            for table, records in before.items():
                self.assertEqual(records, [tuple(r) for r in db.execute('SELECT * FROM ' + table)])
            events = db.execute("SELECT * FROM assignment_events WHERE work_date='2026-09-13' AND after_subobject_id IS NULL").fetchall()
            self.assertEqual({e['worker_id'] for e in events}, {rows[0]['id'], rows[2]['id']})
            self.assertTrue(all(e['changed_by'] == self.admin_id for e in events))
        self.assertEqual(self.clear_selected([fresh[0], fresh[2], fresh[3]]).get_json()['cleared'], 0)

    def test_clear_selected_rejects_stale_membership_roles_and_csrf_atomically(self):
        rows = self.assigned_two_crews()
        chosen = [rows[0], rows[2]]
        for client in (self.viewer, self.foreman):
            self.assertEqual(self.clear_selected(chosen, client=client).status_code, 403)
        self.assertEqual(self.admin.put('/api/staffing/assignments/clear', json={}).status_code, 403)
        self.assertEqual(self.clear_selected(chosen, {'worker_ids': []}).status_code, 400)
        self.assertEqual(self.clear_selected(chosen, {'expected_crews': {}}).status_code, 400)
        self.assertEqual(self.clear_selected(chosen, {'expected_tokens': {}}).status_code, 400)
        self.assertEqual(self.clear_selected(chosen, {'date': 'bad'}).status_code, 400)
        wrong_crews = {str(r['id']): r['crew_id'] for r in chosen}
        wrong_crews[str(chosen[1]['id'])] = chosen[0]['crew_id']
        self.assertEqual(self.clear_selected(chosen, {'expected_crews': wrong_crews}).status_code, 409)
        self.assertEqual(self.day_write([chosen[1]], 'shifts', {'shift': '2 смена'}).status_code, 200)
        self.assertEqual(self.clear_selected(chosen).status_code, 409)
        fresh = self.day_rows()
        self.assertTrue(all(r['assignment_id'] for r in fresh[:3]))
        self.assertEqual(fresh[0]['day_token'], rows[0]['day_token'])
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.foreman_id, fresh[0]['crew_id']))
            db.commit()
        self.assertEqual(self.clear_selected([fresh[0], fresh[2]], client=self.foreman).status_code, 403)
        self.assertEqual(self.clear_selected([fresh[0]], client=self.foreman).status_code, 200)
        self.assertIsNotNone(self.day_rows()[2]['assignment_id'])

    def test_calendar_drill_matches_sites_shifts_and_assignment_employer(self):
        rows = self.assigned_two_crews()
        worker = rows[0]['id']
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("""INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at,crew_id)
                VALUES ('2026-09-13','Ночная смена',2,?,'Другая организация',?,'now',?)""",
                (worker, self.admin_id, rows[0]['crew_id']))
            db.commit()
        def drill(**extra):
            return self.admin.get('/api/staffing', query_string={'date': '2026-09-13', 'shift': 'all',
                'calendar_sites': '2', **extra}).get_json()
        night = drill(calendar_shift='2 смена', calendar_employer='Другая организация')
        self.assertEqual([r['id'] for r in night['rows']], [worker])
        self.assertEqual(night['rows'][0]['subobject_id'], 2)
        self.assertTrue(night['rows'][0]['locked'])
        self.assertEqual(night['calendar_assignment_count'], 1)
        self.assertEqual(drill(calendar_shift='1 смена')['rows'], [])
        self.assertEqual(drill(calendar_employer='')['rows'], [])
        self.assertEqual(drill(date='2026-09-14')['rows'], [])
        total = drill(calendar_sites='1,2,2')
        self.assertEqual(len(total['rows']), 3)
        self.assertEqual(total['calendar_assignment_count'], 4)
        summary = drill(view='summary')
        lazy = drill(crew_id=rows[0]['crew_id'])
        self.assertEqual([r['id'] for r in summary['index']], [r['id'] for r in lazy['rows']])

    def test_calendar_drill_filters_effective_category_before_row_and_count_selection(self):
        rows = self.assigned_two_crews()
        manual, blank, source = (row['id'] for row in rows[:3])
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET category='' WHERE id=?", (blank,))
            db.execute("UPDATE workers SET category='Исходная' WHERE id=?", (source,))
            category_id = db.execute("""INSERT INTO gdlr_categories(name,name_key,active,edit_token,updated_by,updated_at)
                VALUES ('Ручная','ручная',0,'token',?,'now')""", (self.admin_id,)).lastrowid
            db.execute("INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,'token',?,'now')",
                       (manual, category_id, self.admin_id))
            db.commit()
        base = {'date': '2026-09-13', 'shift': 'all', 'calendar_sites': '1', 'calendar_shift': '1 смена'}
        result = self.admin.get('/api/staffing', query_string={**base, 'calendar_category': 'Ручная'}).get_json()
        self.assertEqual([row['id'] for row in result['rows']], [manual])
        self.assertEqual(result['calendar_assignment_count'], 1)
        self.assertEqual(self.admin.get('/api/staffing', query_string={**base, 'calendar_category': ''}).get_json()['calendar_assignment_count'], 1)
        self.assertEqual(self.admin.get('/api/staffing', query_string={**base, 'calendar_category': 'Исходная'}).get_json()['calendar_assignment_count'], 1)
        self.assertEqual(self.foreman.get('/api/staffing', query_string={**base, 'calendar_category': 'Ручная'}).get_json()['rows'], [])
        self.assertEqual(self.admin.get('/api/staffing', query_string={'date': '2026-09-13', 'shift': 'all', 'calendar_category': 'Ручная'}).status_code, 400)
        self.assertEqual(self.admin.get('/api/staffing', query_string={**base, 'calendar_category': 'x' * 201}).status_code, 400)

    def test_assignment_author_is_actual_actor_and_latest_material_change(self):
        self.apply()
        rows = [r for r in self.day_rows() if r['source_crew']]
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=? WHERE id=?', (self.foreman_id, rows[0]['crew_id']))
            db.execute("UPDATE users SET full_name='Иванов Администратор Иванович' WHERE id=?", (self.admin_id,))
            db.execute("UPDATE users SET full_name='Петров Прораб Петрович' WHERE id=?", (self.foreman_id,))
            db.commit()
        first_time, second_time = '2026-09-13T06:10:11Z', '2026-09-13T07:20:21Z'
        with patch('app.datetime') as clock:
            from datetime import datetime
            clock.now.return_value = datetime.fromisoformat(first_time)
            self.assertEqual(self.day_write(rows, 'assignments', {'subobject_id': 1}).status_code, 200)
        first = [r for r in self.day_rows() if r['source_crew']]
        expected = {'user_id': self.admin_id, 'full_name': 'Иванов Администратор Иванович', 'changed_at': first_time}
        self.assertTrue(all(r['assignment_author'] == expected for r in first))
        self.assertTrue(all(r['foreman_user_id'] == self.foreman_id for r in first))
        # Repeating the same location must not claim a different author.
        self.assertEqual(self.day_write(first, 'assignments', {'subobject_id': 1}, self.foreman).status_code, 200)
        self.assertEqual(next(r for r in self.day_rows() if r['id'] == rows[0]['id'])['assignment_author'], expected)
        fresh = [r for r in self.day_rows() if r['source_crew']]
        with patch('app.datetime') as clock:
            clock.now.return_value = datetime.fromisoformat(second_time)
            self.assertEqual(self.day_write(fresh[:1], 'assignments', {'subobject_id': 2}, self.foreman).status_code, 200)
        changed = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual(changed[0]['assignment_author'], {'user_id': self.foreman_id, 'full_name': 'Петров Прораб Петрович', 'changed_at': second_time})
        self.assertEqual(changed[1]['assignment_author'], expected)
        query = '/api/staffing?date=2026-09-13&shift=all&view=summary'
        compact = self.admin.get(query).get_json()
        self.assertEqual(compact['rows'], [])
        by_id = {r['id']: r for r in compact['index']}
        for row in changed:
            self.assertEqual(by_id[row['id']]['assignment_author'], row['assignment_author'])
        # Names can be corrected without losing stable author identity or changing history.
        renamed = self.admin.patch(f'/api/users/{self.admin_id}', headers={'X-CSRF-Token': 'staffing-csrf'},
            json={'full_name': 'Иванов Иван Иванович', 'expected_full_name': expected['full_name']})
        self.assertEqual(renamed.status_code, 200)
        renamed_rows = self.admin.get(query).get_json()['index']
        self.assertEqual(next(r for r in renamed_rows if r['id'] == changed[1]['id'])['assignment_author'],
                         {**expected, 'full_name': 'Иванов Иван Иванович'})
        self.assertEqual(self.viewer.get(query).status_code, 403)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('UPDATE crews SET owner_user_id=?', (self.admin_id,))
            db.commit()
        self.assertEqual(self.foreman.get(query).get_json()['index'], [])
        self.assertEqual(self.day_write(changed[:1], 'assignments', {'subobject_id': None}).status_code, 200)
        self.assertIsNone(next(r for r in self.day_rows() if r['id'] == rows[0]['id'])['assignment_author'])
        self.assertTrue(all(r['assignment_author'] is None for r in self.day_rows('2026-09-14')))

    def test_assignment_author_tracks_legacy_shift_and_never_guesses_missing_history(self):
        self.apply()
        row = next(r for r in self.day_rows() if r['source_crew'])
        url = f"/api/crews/{row['crew_id']}/assignments"
        self.assertEqual(self.write(url, {'date': '2026-09-13', 'shift': '2 смена', 'worker_ids': [row['id']],
            'subobject_id': 2, 'expected_tokens': {str(row['id']): None}}).status_code, 200)
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE assignments SET shift='Ночная смена' WHERE worker_id=?", (row['id'],))
            db.commit()
        query = {'date': '2026-09-13', 'shift': 'all', 'calendar_sites': '2', 'calendar_shift': '2 смена'}
        assigned = self.admin.get('/api/staffing', query_string=query).get_json()['rows'][0]
        self.assertIsNotNone(assigned['assignment_author'])
        with self.app.app_context():
            db = self.module.get_db()
            # Simulate a legacy record with no audit evidence belonging to this incarnation.
            db.execute("UPDATE assignments SET created_at='2099-01-01T00:00:00Z' WHERE worker_id=?", (row['id'],))
            db.commit()
        self.assertIsNone(self.admin.get('/api/staffing', query_string=query).get_json()['rows'][0]['assignment_author'])
        with self.app.app_context():
            db = self.module.get_db()
            db.execute('DELETE FROM assignment_events WHERE worker_id=?', (row['id'],))
            db.commit()
        self.assertIsNone(next(r for r in self.day_rows() if r['id'] == row['id'])['assignment_author'])

    def test_calendar_drill_includes_unimported_inactive_and_preserves_access(self):
        with self.app.app_context():
            db = self.module.get_db()
            worker = db.execute("INSERT INTO workers(full_name,personnel_no,active) VALUES ('Архивный сотрудник','archive-calendar',0)").lastrowid
            db.execute("""INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at)
                VALUES ('2026-09-13','1 смена',1,?,'',?,'now')""", (worker, self.admin_id))
            db.commit()
        query = {'date': '2026-09-13', 'shift': 'all', 'calendar_sites': '1', 'calendar_employer': ''}
        data = self.admin.get('/api/staffing', query_string=query).get_json()
        self.assertIsNone(data['import'])
        self.assertEqual([r['id'] for r in data['rows']], [worker])
        self.assertTrue(data['rows'][0]['locked'])
        self.assertEqual(self.foreman.get('/api/staffing', query_string=query).get_json()['rows'], [])
        self.assertEqual(self.viewer.get('/api/staffing', query_string=query).status_code, 403)
        self.assertEqual(self.app.test_client().get('/api/staffing', query_string=query).status_code, 401)
        for extra in ({'calendar_sites': ''}, {'calendar_sites': '1,no'}, {'calendar_sites': '-1'},
                      {'calendar_shift': 'none'}, {'shift': '1 смена'}):
            self.assertEqual(self.admin.get('/api/staffing', query_string={**query, **extra}).status_code, 400)

    def test_shift_batch_conflicts_roll_back_and_enforce_access(self):
        self.apply()
        rows = [r for r in self.day_rows() if r['source_crew']]
        for client in (self.foreman, self.viewer):
            self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена'}, client).status_code, 403)
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': 'утро'}).status_code, 400)
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена', 'date': 'bad'}).status_code, 400)
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена', 'expected_tokens': {}}).status_code, 400)
        self.assertEqual(self.admin.put(f"/api/staffing/crews/{rows[0]['crew_id']}/shifts", json={}).status_code, 403)
        self.day_write(rows[1:], 'shifts', {'shift': '2 смена'})
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена'}).status_code, 409)
        fresh = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual(fresh[0]['employee_shift'], '1 смена')
        self.assertEqual(fresh[1]['employee_shift'], '2 смена')
        self.assertEqual(self.day_write(fresh, 'assignments', {'subobject_id': 999999}).status_code, 400)
        self.assertTrue(all(r['assignment_id'] is None for r in self.day_rows()))

    def test_existing_shift_is_derived_and_two_assignments_never_overwritten(self):
        self.apply()
        rows = [r for r in self.day_rows() if r['source_crew']]
        row = rows[0]
        old_url = f"/api/crews/{row['crew_id']}/assignments"
        payload = {'date': '2026-09-13', 'shift': '2 смена', 'worker_ids': [row['id']], 'subobject_id': 1, 'expected_tokens': {str(row['id']): None}}
        self.assertEqual(self.write(old_url, payload).status_code, 200)
        fresh = [r for r in self.day_rows() if r['source_crew']]
        self.assertEqual(fresh[0]['employee_shift'], '2 смена')
        self.assertEqual(self.day_write(rows, 'shifts', {'shift': '1 смена'}).status_code, 409)
        self.assertEqual(self.write(old_url, {**payload, 'shift': '1 смена', 'subobject_id': 2}).status_code, 200)
        both = [r for r in self.day_rows() if r['source_crew']]
        self.assertTrue(both[0]['shift_conflict'])
        self.assertTrue(both[0]['locked'])
        self.assertEqual(self.day_write(both, 'shifts', {'shift': '1 смена'}).status_code, 409)
        self.assertEqual(self.clear_selected(both).status_code, 409)
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignments WHERE worker_id=?', (row['id'],)).fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_shifts').fetchone()[0], 0)

    def test_conflicting_identity_prevents_all_changes(self):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute("INSERT INTO workers(full_name,personnel_no) VALUES ('Другой человек','70001')")
            db.commit()
            self.assertTrue(import_conflicts(db, self.parsed))
            with self.assertRaises(ImportProblem):
                apply_attendance(db, self.parsed, self.admin_id, self.module.utc_now)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_imports').fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT id FROM workers WHERE personnel_no='70002'").fetchone())

    def test_preview_is_bound_to_file_and_admin(self):
        def upload(client, route, content, token=''):
            return client.post('/api/staffing/import/' + route, data={'file': (io.BytesIO(content), 'source.xlsx'),
                'preview_token': token}, headers={'X-CSRF-Token': 'staffing-csrf'})
        preview = upload(self.admin, 'preview', self.content)
        self.assertEqual(preview.status_code, 200)
        token = preview.get_json()['preview_token']
        self.assertEqual(upload(self.foreman, 'preview', self.content).status_code, 403)
        self.assertEqual(upload(self.admin, 'apply', workbook_bytes([{}]), token).status_code, 400)
        self.assertEqual(upload(self.admin, 'apply', self.content, 'invalid').status_code, 400)
        self.assertEqual(upload(self.admin, 'apply', self.content, token).status_code, 200)
