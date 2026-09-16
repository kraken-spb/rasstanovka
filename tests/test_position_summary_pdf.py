import copy
from collections import Counter
import unittest
from unittest.mock import patch

from position_cards_pdf import _Report, _assets, _summary_matrix, _summary_table, build_position_cards_pdf
from test_compact_position_pdf import card, worker


def staffed_card(name, day, night=()):
    result = card(name, workers=[{**worker('Работник ' + str(i), str(i)), 'employer': employer}
                                for i, employer in enumerate(day)])
    if night:
        shift = copy.deepcopy(result['shifts'][0])
        shift['label'] = 'Ночь'
        shift['groups'][0]['works'][0]['workers'] = [
            {**worker('Работник ' + str(i), str(i)), 'employer': employer} for i, employer in enumerate(night)]
        result['shifts'].append(shift)
    return result


class PositionSummaryPdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _assets()

    def test_detail_starts_on_page_after_summary_without_blank_page(self):
        for count in (1, 70):
            with self.subTest(positions=count):
                cards = [staffed_card('Позиция ' + str(i), ['ЛГСС']) for i in range(count)]
                summary_pages, detail_pages = set(), set()
                original_draw, original_header = _Report._draw, _Report._card_header

                def draw(report, item, *args):
                    if report.summary_mode and hasattr(item, '_cellvalues'):
                        summary_pages.add(report.page)
                    return original_draw(report, item, *args)

                def header(report, *args, **kwargs):
                    detail_pages.add(report.page)
                    return original_header(report, *args, **kwargs)

                with patch.object(_Report, '_draw', draw), patch.object(_Report, '_card_header', header):
                    result = build_position_cards_pdf(cards, day_label='15.09.2026')
                self.assertTrue(result.startswith(b'%PDF-'))
                self.assertTrue(summary_pages)
                self.assertTrue(detail_pages)
                self.assertFalse(summary_pages & detail_pages)
                self.assertEqual(min(detail_pages), max(summary_pages) + 1)
                if count == 1:
                    self.assertEqual(summary_pages, {1})
                    self.assertEqual(detail_pages, {2})
                else:
                    self.assertGreater(len(summary_pages), 1)

    def test_counts_keep_position_employer_and_shift_boundaries(self):
        cards = [staffed_card('Позиция А', ['ЛГСС', 'ЛГСС', 'УПМА'], ['ЛГСС']),
                 staffed_card('Позиция Б', ['', 'УПМА'], ['УПМА'])]
        before = copy.deepcopy(cards)
        matrix = _summary_matrix(cards)
        self.assertEqual(matrix['employers'], ['ЛГСС', 'УПМА', ''])
        self.assertEqual(matrix['shifts'], ['День', 'Ночь'])
        self.assertEqual(matrix['rows'][0]['counts'], {('ЛГСС', 'День'): 2, ('УПМА', 'День'): 1, ('ЛГСС', 'Ночь'): 1})
        self.assertEqual(matrix['rows'][1]['counts'], {('', 'День'): 1, ('УПМА', 'День'): 1, ('УПМА', 'Ночь'): 1})
        self.assertEqual(matrix['totals'], {('ЛГСС', 'День'): 2, ('УПМА', 'День'): 2,
                                          ('ЛГСС', 'Ночь'): 1, ('', 'День'): 1, ('УПМА', 'Ночь'): 1})
        self.assertEqual(sum(matrix['totals'].values()), 7)
        self.assertEqual(cards, before)

    def test_single_shift_does_not_add_empty_night_columns(self):
        matrix = _summary_matrix([staffed_card('Позиция', ['ЛГСС'])])
        self.assertEqual(matrix['shifts'], ['День'])
        table = _summary_table(matrix, matrix['employers'])
        self.assertEqual(len(table._cellvalues[0]), 3)

    def test_table_headers_repeat_and_totals_stay_with_last_position(self):
        cards = [staffed_card('Позиция ' + str(i), ['ЛГСС', 'УПМА'], ['ЛГСС']) for i in range(30)]
        matrix = _summary_matrix(cards)
        pending = _summary_table(matrix, matrix['employers'])
        seen_positions, final_rows = [], []

        def cell_text(cell):
            items = cell if isinstance(cell, (list, tuple)) else [cell]
            return ''.join(item.getPlainText() if hasattr(item, 'getPlainText') else str(item) for item in items)

        pages = 0
        while pending is not None:
            pending.wrap(551, 1000000)
            chunks = pending.split(551, 260)
            self.assertTrue(chunks)
            part = chunks[0]
            self.assertEqual(cell_text(part._cellvalues[0][0]), 'Позиция / категория ГДЛР')
            self.assertEqual(cell_text(part._cellvalues[1][1]), 'День')
            self.assertEqual(cell_text(part._cellvalues[1][2]), 'Ночь')
            body = part._cellvalues[2:]
            names = [cell_text(row[0]) for row in body]
            seen_positions.extend(name for name in names if name.startswith('Позиция '))
            if any('Всего по СМУ' in name for name in names):
                self.assertGreaterEqual(len(body), 2)
                final_rows = body
            pending = chunks[1] if len(chunks) > 1 else None
            pages += 1
            self.assertLess(pages, 20)
        self.assertGreater(pages, 1)
        self.assertEqual(len(seen_positions), 30)
        self.assertEqual(len(set(seen_positions)), 30)
        self.assertEqual([cell_text(cell) for cell in final_rows[-1][1:]], ['30', '30', '30', '—', '60', '30'])

    def test_summary_precedes_detail_and_wide_employers_keep_panel_totals(self):
        employers = [f'Работодатель {i}' for i in range(7)]
        cards = [staffed_card('Позиция', employers, employers)]
        panels, events = [], []
        original_draw = _Report._draw
        original_card = _Report.render_card

        def draw(report, item, *args):
            if report.summary_mode and hasattr(item, '_cellvalues'):
                panels.append(item)
                events.append('summary')
            return original_draw(report, item, *args)

        def detail(report, item):
            events.append('detail')
            return original_card(report, item)

        with patch.object(_Report, '_draw', draw), patch.object(_Report, 'render_card', detail):
            result = build_position_cards_pdf(cards, day_label='15.09.2026')
        self.assertTrue(result.startswith(b'%PDF-'))
        self.assertEqual(events, ['summary', 'summary', 'detail'])
        self.assertEqual(len(panels), 2)
        actual = Counter()
        for panel in panels:
            for cell in panel._cellvalues[0]:
                for item in cell if isinstance(cell, (list, tuple)) else [cell]:
                    if hasattr(item, 'getPlainText') and item.getPlainText() in employers:
                        actual[item.getPlainText()] += 1
        self.assertEqual(actual, Counter(employers))

    def test_category_rows_keep_employer_shift_counts_and_position_total(self):
        position = staffed_card('Общая позиция', ['ЛГСС', 'ЛГСС', 'УПМА'], ['ЛГСС'])
        day_workers = position['shifts'][0]['groups'][0]['works'][0]['workers']
        for person, category in zip(day_workers, ['Сварщик МК', 'Бетонщик', 'Сварщик МК']):
            person['category'] = category
        position['shifts'][1]['groups'][0]['works'][0]['workers'][0]['category'] = 'Сварщик МК'
        matrix = _summary_matrix([position])
        categories = {row['category']: row['counts'] for row in matrix['rows'][0]['categories']}
        self.assertEqual(categories, {'Бетонщик': {('ЛГСС', 'День'): 1},
            'Сварщик МК': {('ЛГСС', 'День'): 1, ('УПМА', 'День'): 1, ('ЛГСС', 'Ночь'): 1}})
        summed = Counter()
        for counts in categories.values():
            summed.update(counts)
        self.assertEqual(dict(summed), matrix['rows'][0]['counts'])
        table = _summary_table(matrix, matrix['employers'])
        self.assertEqual(table._cellvalues[3][0].getPlainText(), 'ГДЛР: Бетонщик')
        self.assertEqual(table._cellvalues[4][0].getPlainText(), 'ГДЛР: Сварщик МК')

    def test_single_unknown_category_is_visible_without_duplicate_count_row(self):
        matrix = _summary_matrix([staffed_card('Позиция', ['ЛГСС'])])
        table = _summary_table(matrix, matrix['employers'])
        self.assertEqual(len(table._cellvalues), 4)
        self.assertIn('ГДЛР: не указана', table._cellvalues[2][0].getPlainText())

    def test_category_breakdown_stays_with_position_across_pages(self):
        cards = [staffed_card('Позиция ' + str(i), ['ЛГСС'] * 3) for i in range(12)]
        for position in cards:
            workers = position['shifts'][0]['groups'][0]['works'][0]['workers']
            for person, category in zip(workers, ['Бетонщик', 'Монтажник ТТ', 'Сварщик МК']):
                person['category'] = category
        matrix = _summary_matrix(cards)
        pending = _summary_table(matrix, matrix['employers'])
        seen = 0
        while pending is not None:
            pending.wrap(551, 1000000)
            chunks = pending.split(551, 260)
            self.assertTrue(chunks)
            names = []
            for row in chunks[0]._cellvalues[2:]:
                cell = row[0]
                items = cell if isinstance(cell, (list, tuple)) else [cell]
                names.append(''.join(item.getPlainText() for item in items))
            self.assertTrue(names[0].startswith('Позиция '))
            for index, name in enumerate(names):
                if name.startswith('Позиция '):
                    self.assertEqual(names[index + 1:index + 4],
                                     ['ГДЛР: Бетонщик', 'ГДЛР: Монтажник ТТ', 'ГДЛР: Сварщик МК'])
                    seen += 1
            pending = chunks[1] if len(chunks) > 1 else None
            self.assertLessEqual(seen, len(cards))
        self.assertEqual(seen, len(cards))


if __name__ == '__main__':
    unittest.main()
