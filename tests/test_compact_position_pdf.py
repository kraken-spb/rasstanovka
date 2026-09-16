import copy
from io import BytesIO
import unittest
from unittest.mock import patch

from position_cards_pdf import _Report, _display_groups, _name_labels, _short_name, _roster_grid, build_position_cards_pdf


def worker(name='Петров Пётр Петрович', number='1'):
    return {'full_name': name, 'personnel_no': number, 'profession': 'Профессия с длинным названием'}


def card(name='Позиция', department='СМУ 15.2', workers=None, description='Монтаж трубопровода. Выполнено 10 стыков.'):
    return {'stage': 'Этап 5', 'object_name': 'Группа', 'subobject_name': name,
            'department': department, 'responsible_name': 'Иванов Иван Иванович',
            'shifts': [{'label': 'День', 'groups': [{'linear_itr': 'Сидоров Сидор Сидорович',
                'brigadier': '', 'crew_id': 15, 'crew_name': '015/2', 'works': [
                    {'description': description, 'workers': workers if workers is not None else [worker()]}]}]}]}


class CompactPositionPdfTest(unittest.TestCase):
    def test_multiple_positions_share_a_page_without_empty_night(self):
        cards = [card('Позиция ' + str(i)) for i in range(5)]
        before = copy.deepcopy(cards)
        text = []
        original = _Report._draw

        def draw(report, paragraph, *args):
            if hasattr(paragraph, 'getPlainText'):
                text.append(paragraph.getPlainText())
            return original(report, paragraph, *args)

        with patch.object(_Report, '_new_page', autospec=True, side_effect=_Report._new_page) as pages:
            with patch.object(_Report, '_draw', draw):
                result = build_position_cards_pdf(cards, day_label='14.09.2026')
        self.assertTrue(result.startswith(b'%PDF-'))
        self.assertLess(pages.call_count, len(cards))
        self.assertEqual(cards, before)
        self.assertNotIn('Ночь', ' '.join(text))
        self.assertNotIn('Требует внимания', ' '.join(text))
        self.assertEqual(sum('Бригада: 015/2' in item for item in text), 5)
        for i in range(5):
            self.assertIn('Позиция ' + str(i), ' '.join(text))

    def test_name_collisions_keep_full_names_and_distinguish_namesakes(self):
        workers = [worker('Иванов Иван Иванович', '1'), worker('Иванов Илья Игоревич', '2'),
                   worker('Петров Пётр Петрович', '3'), worker('Петров Пётр Петрович', '4')]
        labels = _name_labels([card(workers=workers)])
        self.assertEqual(labels[('Иванов Иван Иванович', '1')], 'Иванов Иван Иванович')
        self.assertEqual(labels[('Иванов Илья Игоревич', '2')], 'Иванов Илья Игоревич')
        self.assertIn('таб. 3', labels[('Петров Пётр Петрович', '3')])
        self.assertIn('таб. 4', labels[('Петров Пётр Петрович', '4')])
        self.assertEqual(_short_name('Сидоров Сидор Сидорович'), 'Сидоров С.С.')
        self.assertEqual(_short_name('Майрамбек уулу Абдурахман'), 'Майрамбек уулу Абдурахман')

    def test_roster_has_one_employee_per_cell_and_retains_order_on_split(self):
        names = [f'Сотрудник {i:02} Иван Иванович' for i in range(17)]
        grid = _roster_grid(names, 535)
        grid.wrap(535, 1000)
        self.assertEqual(len(grid._cellvalues), 6)
        self.assertEqual(len(grid._cellvalues[0]), 3)
        parts = grid.split(535, 50)
        self.assertEqual(len(parts), 2)
        actual = []
        for part in parts:
            for row in part._cellvalues:
                for cell in row:
                    for item in cell if isinstance(cell, (tuple, list)) else [cell]:
                        if hasattr(item, 'getPlainText'):
                            actual.append(item.getPlainText())
        self.assertEqual(actual, names)

    def test_merge_keeps_exact_work_text_responsibles_and_brigades(self):
        shift = card()['shifts'][0]
        another = copy.deepcopy(shift['groups'][0])
        another['works'][0]['workers'] = [worker('Васильев Василий Васильевич', '2')]
        another['works'].append({'description': 'Другая работа', 'workers': [worker('Соколов Семён Сергеевич', '3')]})
        different_lead = copy.deepcopy(another)
        different_lead['linear_itr'] = 'Другой ИТР'
        different_crew = copy.deepcopy(another)
        different_crew['crew_id'] = 16
        different_crew['crew_name'] = '016'
        namesake_crew = copy.deepcopy(another)
        namesake_crew['crew_id'] = 17
        no_crew = copy.deepcopy(another)
        no_crew['crew_id'] = None
        no_crew['crew_name'] = ''
        shift['groups'].extend([another, different_lead, different_crew, namesake_crew, no_crew])
        before = copy.deepcopy(shift)
        groups = _display_groups(shift)
        self.assertEqual(len(groups), 5)
        self.assertEqual([group['crew_name'] for group in groups], ['015/2', '015/2', '016', '015/2', ''])
        self.assertEqual(len(groups[0]['works']), 2)
        self.assertEqual(len(groups[0]['works'][0]['workers']), 2)
        self.assertEqual(len(groups[2]['works'][0]['workers']), 1)
        self.assertEqual(groups[0]['works'][1]['description'], 'Другая работа')
        self.assertEqual(shift, before)

    def test_same_work_keeps_grouping_and_category_next_to_each_person(self):
        cards = [card(workers=[{**worker('Сидоров Сидор Сидорович', '1'), 'category': 'Сварщик МК'},
                               {**worker('Петров Пётр Петрович', '2'), 'category': 'Монтажник ТТ'},
                               {**worker('Иванов Иван Иванович', '3'), 'category': 'Сварщик МК'}])]
        before = copy.deepcopy(cards)
        works = _display_groups(cards[0]['shifts'][0])[0]['works']
        self.assertEqual(len(works), 1)
        self.assertEqual([w['personnel_no'] for w in works[0]['workers']], ['1', '2', '3'])
        self.assertEqual(works[0]['description'], cards[0]['shifts'][0]['groups'][0]['works'][0]['description'])
        text = []
        original = _Report._draw
        rosters = []
        original_roster = _roster_grid

        def roster(people, *args, **kwargs):
            rosters.extend(people)
            return original_roster(people, *args, **kwargs)

        def draw(report, item, *args):
            if hasattr(item, 'getPlainText'):
                text.append(item.getPlainText())
            return original(report, item, *args)

        with patch.object(_Report, '_draw', draw), patch('position_cards_pdf._roster_grid', roster):
            build_position_cards_pdf(cards, day_label='15.09.2026')
        self.assertEqual(sum(value.startswith('3 чел. · Монтаж трубопровода.') for value in text), 1)
        self.assertTrue(any('Сидоров С.С.' in value and '(Св. МК)' in value for value in rosters))
        self.assertTrue(any('Петров П.П.' in value and '(Монт. ТТ)' in value for value in rosters))
        self.assertTrue(any('Иванов И.И.' in value and '(Св. МК)' in value for value in rosters))
        self.assertEqual(cards, before)

    def test_inline_category_preserves_unknown_names_and_missing_binding(self):
        workers = [{**worker('Петров Пётр Петрович', '1'), 'category': 'Новая <категория> & название'},
                   {**worker('Сидоров Сидор Сидорович', '2'), 'category': ''}]
        report = _Report(BytesIO(), [card(workers=workers)], '15.09.2026', '')
        label = report._worker_label(workers[0])
        self.assertIn('Новая &lt;категория&gt; &amp; название', label)
        self.assertIn('ГДЛР не указана', report._worker_label(workers[1]))
        self.assertNotIn(workers[1]['profession'], report._worker_label(workers[1]))

    def test_missing_brigade_and_markup_in_brigade_name_are_readable(self):
        cards = [card('Без бригады'), card('Именованная бригада'), card('Номерная бригада')]
        cards[0]['shifts'][0]['groups'][0]['crew_name'] = ''
        cards[1]['shifts'][0]['groups'][0]['crew_name'] = '№ 015 <монтаж> & 2'
        cards[2]['shifts'][0]['groups'][0]['crew_name'] = 'Бригада №1012_261'
        text = []
        original = _Report._draw

        def draw(report, item, *args):
            if hasattr(item, 'getPlainText'):
                text.append(item.getPlainText())
            return original(report, item, *args)

        with patch.object(_Report, '_draw', draw):
            build_position_cards_pdf(cards, day_label='15.09.2026')
        self.assertIn('Бригада не указана', ' '.join(text))
        self.assertIn('Бригада: № 015 <монтаж> & 2', ' '.join(text))
        self.assertIn('День · Бригада №1012_261 · ИТР:', ' '.join(text))
        self.assertNotIn('Бригада: Бригада', ' '.join(text))

    def test_long_text_roster_and_continuations_stay_inside_page(self):
        cards = [card(workers=[worker(f'Работник{i} Иван Петрович', str(i)) for i in range(150)],
                      description=('Я\n' * 900) + 'Последняя строка')]
        positions, brigade_pages = [], []
        original = _Report._line
        original_header = _Report._group_header

        def line(report, y):
            positions.append((report.page, y))
            original(report, y)

        def header(report):
            self.assertIn('Бригада: 015/2', report._group_paragraph().getPlainText())
            brigade_pages.append(report.page)
            original_header(report)

        with patch.object(_Report, '_line', line), patch.object(_Report, '_group_header', header):
            result = build_position_cards_pdf(cards, day_label='15.09.2026')
        self.assertTrue(result.startswith(b'%PDF-'))
        self.assertGreater(max(page for page, y in positions), 1)
        self.assertEqual(set(brigade_pages), {page for page, y in positions})
        self.assertLessEqual(max(y for page, y in positions), 812)


if __name__ == '__main__':
    unittest.main()
