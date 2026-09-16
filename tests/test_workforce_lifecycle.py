from datetime import date
import unittest

from workforce_identity import legacy_outstaff_key, match_worker, source_groups
from workforce_lifecycle import merge_sources


def source(sheet, fields=None, service='rotation', row=2, section=''):
    return {'filename': service + '.xlsx', 'sheet': sheet, 'row': row, 'service': service,
            'fields': {'name': 'Тестовый Работник', **(fields or {})}, 'mapping_notes': [], 'section': section}


class WorkforceLifecycleTest(unittest.TestCase):
    def test_confirmed_fact_precedes_future_ticket_and_new_cycle_precedes_old_presence(self):
        records = [source('Неявка', {'leave_start': '2026-09-10', 'leave_end': '2026-09-20'}),
                   source('ПВП', {'ticket_arrival': '2026-09-25'}, 'recruitment'),
                   source('Явка', {'arrival': '2026-07-01'})]
        merged = merge_sources(records, date(2026, 9, 15))
        self.assertEqual(merged['stage'], 'stage.leave')
        self.assertTrue(merged['confirmed'])
        self.assertEqual(merged['planned_date'], '2026-09-22')

    def test_pvp_priority_negative_presence_and_no_invented_actual_date(self):
        records = [source('ПВП', {'transit_arrival': '2026-09-10'}),
                   source('ПВП', {'ticket_arrival': '2026-09-10', 'accommodation': 'Хостел'}, 'recruitment')]
        merged = merge_sources(records, date(2026, 9, 15))
        self.assertEqual(merged['chosen'][0], 'recruitment.xlsx')
        records.append(source('Заезд', {'notes': 'Не прибыл 12.09.2026'}, row=3))
        merged = merge_sources(records, date(2026, 9, 15))
        self.assertFalse(merged['confirmed'])
        self.assertEqual(merged['stage'], 'stage.inbound')
        self.assertIsNone(merged['event_date'])

    def test_ticket_basis_requires_purchase_section_not_just_date(self):
        record = source('ПВП', {'ticket_arrival': '2026-09-10'}, 'recruitment')
        self.assertIsNone(merge_sources([record], date(2026, 9, 15))['basis_code'])
        record['section'] = 'Покупка билетов'
        self.assertEqual(merge_sources([record], date(2026, 9, 15))['basis_code'], 'basis.ticket')
        record['fields'].update(tab='123', employment_type='внешний аутстаффинг')
        self.assertEqual(merge_sources([record], date(2026, 9, 15))['employment_code'], 'employment.staff')

    def test_weak_names_are_not_auto_merged_and_conflicting_tabs_require_review(self):
        worker = {'id': 1, 'full_name': 'Тестовый Работник', 'personnel_no': '123', 'personnel_is_internal': False,
                  'employer': 'ЛГСС', 'birth_date': '1990-01-01', 'phone': ''}
        index = {'by_tab': {'123': worker}, 'by_outstaff': {}, 'by_name': {'тестовый работник': [worker]}}
        record = source('ПВП', {'employer': 'ЛГСС'}, 'recruitment')
        self.assertTrue(match_worker([record], index)['issue'])
        record['fields'].update(tab='456', dob='1990-01-01')
        self.assertTrue(match_worker([record], index)['issue'])
        record['fields']['tab'] = '123'
        self.assertEqual(match_worker([record], index)['worker']['id'], 1)
        record['fields']['dob'] = '1991-01-01'
        self.assertTrue(match_worker([record], index)['issue'])

    def test_persisted_outstaff_identity_is_reused_and_source_names_alone_stay_separate(self):
        worker = {'id': 1, 'full_name': 'Тестовый Работник', 'personnel_no': 'OUT-1', 'personnel_is_internal': True,
                  'employer': 'ЛГСС', 'birth_date': None, 'phone': ''}
        record = source('Аутстаффинг', {'employer': 'ЛГСС'})
        index = {'by_tab': {}, 'by_outstaff': {legacy_outstaff_key(worker['full_name'],worker['employer']): worker},
                 'by_name': {'тестовый работник': [worker]}}
        self.assertEqual(match_worker([record], index)['worker']['id'], 1)
        rows = [source('ПВП'), source('Заезд')]
        self.assertEqual(source_groups(rows), [[0], [1]])

    def test_phone_or_alias_cannot_override_conflicting_birth_date(self):
        worker = {'id': 1, 'full_name': 'Тестовый Работник', 'personnel_no': 'OUT-1', 'personnel_is_internal': True,
                  'employer': 'ЛГСС', 'birth_date': '1990-01-01', 'phone': '+79000000001'}
        record = source('Аутстаффинг', {'employer': 'ЛГСС', 'dob': '1991-01-01', 'phone': '89000000001'})
        for aliases in ({}, {legacy_outstaff_key(worker['full_name'], worker['employer']): worker}):
            index = {'by_tab': {}, 'by_outstaff': aliases, 'by_name': {'тестовый работник': [worker]}}
            result = match_worker([record], index)
            self.assertIsNone(result['worker'])
            self.assertEqual(result['candidates'], [worker])
            self.assertIn('дата рождения', result['issue'])


if __name__ == '__main__':
    unittest.main()
