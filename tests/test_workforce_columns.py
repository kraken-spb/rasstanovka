"""The registry and its Excel copy expose the same configurable fields."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from user_preferences import WORKFORCE_COLUMNS
from workforce_registry_export import HEADERS


class ColumnHeaders(HTMLParser):
    def __init__(self):
        super().__init__()
        self.columns = []
        self.current = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'th' and 'data-column' in attrs:
            self.current = {**attrs, 'label': ''}

    def handle_data(self, text):
        if self.current is not None:
            self.current['label'] += text

    def handle_endtag(self, tag):
        if tag == 'th' and self.current is not None:
            self.columns.append(self.current)
            self.current = None


class WorkforceColumnsTest(unittest.TestCase):
    def test_excel_fields_can_all_be_shown_hidden_and_resized(self):
        parser = ColumnHeaders()
        parser.feed((Path(__file__).resolve().parents[1] / 'templates/workforce.html').read_text(encoding='utf-8'))
        columns = parser.columns
        self.assertEqual([column['label'] for column in columns], list(HEADERS))
        self.assertEqual({column['data-column'] for column in columns}, WORKFORCE_COLUMNS)
        self.assertEqual(len(columns), len(WORKFORCE_COLUMNS))
        self.assertTrue(all(64 <= int(column['data-width']) <= 800 for column in columns))
        fields = {column['data-column']: column['data-edit-field'] for column in columns if 'data-edit-field' in column}
        self.assertEqual(fields, {'project':'project_code', 'department': 'smu_id', 'division':'division_id', 'employer': 'employer_id',
                                 'profession': 'profession_code', 'category': 'category_id',
                                 'employment': 'employment_code', 'accommodation':'accommodation_code', 'stage': 'stage_code',
                                 'citizenship':'citizenship_code','origin_city':'origin_code',
                                 'stage_date':'stage_date','arrival_date':'arrival_date',
                                 'forecast_departure_date':'forecast_departure_date',
                                 'movement_direction':'movement_direction','movement_basis':'movement_basis',
                                 'planned_date':'planned_date','rotation_schedule':'rotation_schedule_id',
                                 'leave_start_date':'leave_start_date','leave_end_date':'leave_end_date','next_arrival_date':'next_arrival_date'})
        self.assertEqual({c['data-column'] for c in columns if c.get('data-edit-type')=='date'},
                         {'stage_date','arrival_date','forecast_departure_date','planned_date','leave_start_date','leave_end_date','next_arrival_date'})
        keys = [c['data-column'] for c in columns]
        self.assertEqual(keys.index('leave_start_date') + 1, keys.index('leave_end_date'))
