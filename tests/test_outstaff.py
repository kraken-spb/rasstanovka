import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from outstaff_api import parse_outstaff, filtered_rows, ImportProblem


def workbook(records=None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Аустаффинг'
    sheet.append(['№', 'Ф.И.О.', 'Должность', 'Вид работ', 'СМУ', 'Контрагент', 'Категория ГДЛР ЛГСС ', 'ВИД', 'Дата заезда', 'Дата выезда'])
    for index, record in enumerate(records or [{}], 1):
        row = dict(number=index, name='Тестов Иван Иванович', profession='Монтажник 5 разряда', kind='Монтажник',
                   department='СМУ 15.1', vendor='Тестовый подрядчик', category='Монтажник', staff_type='внешний аутстаффинг',
                   arrival='2026-09-01', departure='')
        row.update(record)
        sheet.append(list(row.values()))
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class OutstaffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'DATABASE_PATH': str(Path(cls.bootstrap.name) / 'bootstrap.db'),
                                    'ADMIN_PASSWORD': 'outstaff-test-password-123',
                                    'SECRET_KEY': 'outstaff-test-secret-at-least-thirty-two-characters'}):
            cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(setattr, self.module, 'DATABASE_PATH', self.module.DATABASE_PATH)
        self.module.DATABASE_PATH = Path(temp.name) / 'outstaff.db'
        self.client = self.module.app.test_client()
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.cat = db.execute('''INSERT INTO gdlr_categories(name,name_key,staffing_allowed,edit_token,updated_by,updated_at)
                VALUES ('Монтажник','монтажник',1,'category-token',?,'now')''', (self.admin,)).lastrowid
            db.execute('''INSERT INTO workers(full_name,personnel_no,department,category,profession)
                VALUES ('Образец категории','DEPT-1','Строительно-монтажный участок № 15.1','Монтажник','Монтажник 5 разряда')''')
            db.commit()
        with self.client.session_transaction() as session:
            session['user_id'] = self.admin
            session['csrf_token'] = 'outstaff-csrf-token'
        self.content = workbook()

    def post(self, action, *, filters=None, decisions=None, token='', content=None, client=None, csrf=True):
        return (client or self.client).post('/api/outstaff/' + action, data={
            'file': (io.BytesIO(content or self.content), 'перевахтовка.xlsx'), 'filters': json.dumps(filters or []),
            'decisions': json.dumps(decisions or {}), 'token': token},
            headers={'X-CSRF-Token': 'outstaff-csrf-token'} if csrf else {})

    def apply(self, **kwargs):
        preview = self.post('preview', **kwargs)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        return self.post('apply', token=preview.get_json()['token'], **kwargs)

    def test_real_headers_filters_dates_and_empty_values(self):
        parsed = parse_outstaff(workbook([{}, {'name': 'Другой', 'departure': '2026-09-12'}]), 'file.xlsx')
        self.assertEqual(parsed['sheet'], 'Аустаффинг')
        rows = filtered_rows(parsed, [{'column': 9, 'op': 'empty'}, {'column': 4, 'op': 'in', 'values': ['СМУ 15.1']}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(filtered_rows(parsed, [{'column': 9, 'op': 'before', 'values': ['2026-09-12']}])), 1)
        for filters in ({}, [{'column': 100, 'op': 'empty'}], [{'column': 4, 'op': 'in', 'values': []}]):
            with self.assertRaises(ImportProblem): filtered_rows(parsed, filters)

    def test_import_shared_staffing_idempotency_source_preservation(self):
        preview = self.post('preview').get_json()
        self.assertEqual(preview['unresolved'], 0)
        self.assertEqual(preview['rows'][0]['mapped_department'], 'Строительно-монтажный участок № 15.1')
        for _ in range(2):
            response = self.post('apply', token=preview['token'])
            self.assertEqual(response.status_code, 200, response.get_json())
        data = self.client.get('/api/employees?scope=outstaff&date=2026-09-13').get_json()
        self.assertEqual(len(data['rows']), 1)
        row = data['rows'][0]
        self.assertTrue(row['personnel_no'].startswith('OUT-'))
        self.assertEqual(row['category_id'], self.cat)
        self.assertEqual(row['outstaff']['source_category'], 'Монтажник')
        self.assertEqual(row['employer'], 'Тестовый подрядчик')
        self.assertEqual(row['contractor'], 'ЛГСС')
        self.assertNotIn('Тестовый подрядчик', [item['name'] for item in self.client.get('/api/contractors').get_json()['rows']])
        self.assertIsNotNone(row['crew_id'])
        staffing = self.client.get('/api/staffing?date=2026-09-13&shift=all').get_json()
        self.assertEqual([r['id'] for r in staffing['rows']], [row['id']])
        self.assertFalse(staffing['rows'][0]['locked'])
        with self.module.app.app_context():
            db = self.module.get_db()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM staffing_imports').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outstaff_import_rows').fetchone()[0], 1)

    def test_column_filters_inspect_preview_and_apply_select_same_rows(self):
        self.content = workbook([
            {'name': 'Оставить Первый', 'vendor': 'Подрядчик А'},
            {'name': 'Оставить Второй', 'vendor': 'Подрядчик Б'},
            {'name': 'Уехавший', 'vendor': 'Подрядчик А', 'departure': '2026-09-12'},
            {'name': 'Другой подрядчик', 'vendor': 'Подрядчик В'},
        ])
        inspected = self.post('inspect').get_json()
        columns = {column['name'].strip(): column for column in inspected['columns']}
        self.assertEqual(inspected['total'], 4)
        self.assertEqual(columns['Контрагент']['values'], ['Подрядчик А', 'Подрядчик Б', 'Подрядчик В'])
        self.assertIn('', columns['Дата выезда']['values'])
        filters = [
            {'column': columns['Контрагент']['index'], 'op': 'in', 'values': ['Подрядчик А', 'Подрядчик Б']},
            {'column': columns['Дата выезда']['index'], 'op': 'empty', 'values': []},
        ]
        preview = self.post('preview', filters=filters).get_json()
        self.assertEqual(preview['selected'], 2)
        self.assertEqual({row['full_name'] for row in preview['rows']}, {'Оставить Первый', 'Оставить Второй'})
        self.assertEqual(self.post('apply', filters=[], token=preview['token']).status_code, 409)
        applied = self.post('apply', filters=filters, token=preview['token'])
        self.assertEqual(applied.status_code, 200, applied.get_json())
        people = self.client.get('/api/employees?scope=outstaff&date=2026-09-13').get_json()['rows']
        self.assertEqual({row['full_name'] for row in people}, {'Оставить Первый', 'Оставить Второй'})

    def test_manual_mapping_and_ambiguous_evidence(self):
        self.content = workbook([{'category': 'Неизвестная', 'profession': 'Новая должность', 'department': 'ВЖГ 15.1'}])
        preview = self.post('preview').get_json()
        self.assertEqual(preview['unresolved'], 1)
        self.assertEqual(self.post('apply', token=preview['token']).status_code, 400)
        decision = {'categories': {'Неизвестная': self.cat}, 'departments': {'ВЖГ 15.1': 'Строительно-монтажный участок № 15.1'}}
        self.assertEqual(self.apply(decisions=decision).status_code, 200)
        learned = self.post('preview', content=workbook([{'name': 'Новый Человек', 'category': 'Неизвестная', 'profession': 'Другая должность'}])).get_json()
        self.assertEqual(learned['rows'][0]['category_id'], self.cat)

    def test_blank_category_uses_unique_profession_and_conflicts_stay_manual(self):
        self.content = workbook([{'category': '', 'kind': ''}])
        self.assertEqual(self.post('preview').get_json()['rows'][0]['category_id'], self.cat)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute('''INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                VALUES ('Сварщик','сварщик','second',?,'now')''', (self.admin,))
            db.execute('''INSERT INTO workers(full_name,personnel_no,category,profession)
                VALUES ('Другой образец','DEPT-2','Сварщик','Монтажник 5 разряда')''')
            db.commit()
        self.assertIsNone(self.post('preview').get_json()['rows'][0]['category_id'])

    def test_stale_review_tampering_and_roles(self):
        preview = self.post('preview').get_json()
        self.assertEqual(self.post('apply', token=preview['token'], filters=[{'column': 9, 'op': 'empty'}]).status_code, 409)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE gdlr_categories SET edit_token='changed'")
            db.commit()
        self.assertEqual(self.post('apply', token=preview['token']).status_code, 409)
        self.assertEqual(self.post('preview', csrf=False).status_code, 403)
        self.assertEqual(self.post('inspect', client=self.module.app.test_client()).status_code, 403)
        self.assertEqual(self.module.app.test_client().get('/api/employees?scope=outstaff').status_code, 401)
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE users SET role='foreman' WHERE id=?", (self.admin,)); db.commit()
        self.assertEqual(self.post('preview').status_code, 403)

    def test_name_collision_requires_explicit_identity_and_duplicate_rows_rejected(self):
        self.content = workbook([{'name': 'Образец категории'}])
        preview = self.post('preview').get_json()
        self.assertEqual(preview['unresolved'], 1)
        self.assertTrue(preview['rows'][0]['needs_identity'])
        identity = preview['rows'][0]['identity_candidates'][0]['id']
        self.assertEqual(self.apply(decisions={'identities': {'2': identity}}).status_code, 200)
        self.assertEqual(len(self.client.get('/api/employees?scope=outstaff').get_json()['rows']), 1)
        self.assertEqual(self.post('preview', content=workbook([{}, {}])).get_json()['unresolved'], 2)

    def test_reimport_preserves_manual_category_and_crew(self):
        self.assertEqual(self.apply().status_code, 200)
        before = self.client.get('/api/employees?scope=outstaff').get_json()['rows'][0]
        self.content = workbook([{'category': 'Другое исходное значение', 'profession': 'Другая должность'}])
        self.assertEqual(self.apply().status_code, 200)
        after = self.client.get('/api/employees?scope=outstaff').get_json()['rows'][0]
        for field in ('id', 'personnel_no', 'crew_id', 'category_id', 'profession'):
            self.assertEqual(before[field], after[field])
        self.assertEqual(after['outstaff']['source_category'], 'Другое исходное значение')

    def test_union_does_not_duplicate_attendance_members(self):
        self.assertEqual(self.apply().status_code, 200)
        worker = self.client.get('/api/employees?scope=outstaff').get_json()['rows'][0]['id']
        with self.module.app.app_context():
            db = self.module.get_db()
            batch = db.execute('''INSERT INTO staffing_imports(sha256,filename,imported_by,imported_at,selected_count,summary_json)
                VALUES ('fixture','Явка.xlsx',?,'now',1,'{}')''', (self.admin,)).lastrowid
            db.execute("INSERT INTO staffing_import_members VALUES (?,?,2,'Бригада')", (batch, worker)); db.commit()
        data = self.client.get('/api/staffing?date=2026-09-13&shift=all').get_json()
        self.assertEqual(len(data['rows']), 1)


if __name__ == '__main__':
    unittest.main()
