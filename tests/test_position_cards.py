import json
import unittest
from unittest.mock import patch

import test_staffing_export as export_tests


class PositionCardsTest(unittest.TestCase):
    setUpClass = classmethod(export_tests.StaffingExportTest.setUpClass.__func__)
    tearDownClass = classmethod(export_tests.StaffingExportTest.tearDownClass.__func__)

    def setUp(self):
        self.fixture = export_tests.StaffingExportTest()
        self.fixture.module = self.module
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.module = self.fixture.module
        self.admin = self.fixture.admin
        self.url = '/api/staffing/position-cards/pdf'
        with self.module.app.app_context():
            db = self.module.get_db()
            for worker, department in [(self.fixture.visible_worker, 'СМУ А'),
                                       (self.fixture.legacy_worker, 'СМУ А'),
                                       (self.fixture.hidden_worker, 'СМУ Б')]:
                db.execute('UPDATE workers SET department=? WHERE id=?', (department, worker))
            db.execute('UPDATE smu_catalog SET site_chief_user_id=? WHERE name=?',
                       (self.fixture.foreman_id, 'СМУ А'))
            db.execute("UPDATE users SET full_name='Ответственный А' WHERE id=?", (self.fixture.foreman_id,))
            db.execute('INSERT INTO staffing_performed_work VALUES (?,?,?,?,?,?,?)',
                       ('2026-09-13', self.fixture.visible_worker, '1 смена',
                        'Сварка <труб> & контроль\nВыполнено 10 стыков.', 'work', self.fixture.admin_id, 'now'))
            db.commit()

    def export(self, client=None, **filters):
        return (client or self.admin).get(self.url,
            query_string={'date': '2026-09-13', 'shift': 'all', **filters})

    def population(self, cards):
        return [worker['full_name'] for card in cards for shift in card['shifts']
                for group in shift['groups'] for work in group['works'] for worker in work['workers']]

    @patch('position_cards.build_position_cards_pdf', return_value=b'%PDF-fixture')
    def test_snapshot_grouping_responsibility_and_history(self, render):
        with self.module.app.app_context():
            db = self.module.get_db()
            before = [tuple(r) for r in db.execute('SELECT * FROM assignments ORDER BY id')]
        response = self.export()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.headers['X-Export-Count'], '4')
        self.assertEqual(response.headers['X-Export-Card-Count'], '3')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.mimetype, 'application/pdf')
        cards = render.call_args.args[0]
        card = next(card for card in cards if card['department'] == 'СМУ А')
        self.assertEqual(card['responsible_name'], 'Ответственный А')
        self.assertEqual([s['label'] for s in card['shifts']], ['День', 'Ночь'])
        group = card['shifts'][0]['groups'][0]
        self.assertEqual(group['linear_itr'], 'ИТР вручную')
        self.assertEqual(group['brigadier'], 'Бригадир вручную')
        self.assertEqual(group['works'][0]['description'], 'Сварка <труб> & контроль\nВыполнено 10 стыков.')
        self.assertIn('Неактивный сотрудник', self.population(cards))
        self.assertEqual(render.call_args.kwargs['day_label'], '13.09.2026')
        with self.module.app.app_context():
            after = [tuple(r) for r in self.module.get_db().execute('SELECT * FROM assignments ORDER BY id')]
        self.assertEqual(before, after)

    @patch('position_cards.build_position_cards_pdf', return_value=b'%PDF-fixture')
    def test_filters_compose_and_empty_shifts_are_omitted(self, render):
        response = self.export(department=['СМУ А', 'СМУ Б'], contractor=['Подрядчик', 'Другой'],
                               category='Ручная ГДЛР', shift=['1 смена', '2 смена'])
        self.assertEqual(response.status_code, 200)
        cards = render.call_args.args[0]
        self.assertEqual(self.population(cards), ['=Формула'])
        self.assertEqual([s['label'] for s in cards[0]['shifts']], ['День'])
        self.assertEqual(self.export(department='СМУ Б', contractor='Подрядчик').status_code, 404)
        self.assertEqual(self.export(category='').status_code, 404)
        self.assertEqual(self.export(query='Несуществующая позиция').status_code, 404)

    @patch('position_cards.build_position_cards_pdf', return_value=b'%PDF-fixture')
    def test_authorization_legacy_and_explicit_scope(self, render):
        response = self.export(self.fixture.foreman)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(self.population(render.call_args.args[0])),
                         {'=Формула', 'Ночной сотрудник', 'Неактивный сотрудник'})
        with self.module.app.app_context():
            db = self.module.get_db()
            for user_id in (self.fixture.admin_id, self.fixture.foreman_id):
                db.execute('INSERT INTO user_smu_access VALUES (?,?,?,?,?,?)',
                           (user_id, 'selected', json.dumps(['СМУ Б'], ensure_ascii=False),
                            'scope', self.fixture.admin_id, 'now'))
            db.commit()
        for client in (self.admin, self.fixture.foreman):
            self.assertEqual(self.export(client).status_code, 200)
            self.assertEqual(self.population(render.call_args.args[0]), ['Чужой сотрудник'])
            self.assertEqual(self.export(client, department='СМУ А').status_code, 404)

    @patch('position_cards.build_position_cards_pdf', return_value=b'%PDF-fixture')
    def test_overrides_and_different_responsibles_do_not_merge(self, render):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("UPDATE workers SET department='СМУ А' WHERE id=?", (self.fixture.hidden_worker,))
            db.execute("UPDATE staffing_row_details SET linear_itr_override='' WHERE worker_id=?", (self.fixture.visible_worker,))
            db.commit()
        self.assertEqual(self.export(shift='1 смена', department='СМУ А').status_code, 200)
        groups = render.call_args.args[0][0]['shifts'][0]['groups']
        self.assertEqual(len(groups), 2)
        self.assertEqual({group['linear_itr'] for group in groups}, {'', 'Чужой ИТР'})

    def test_permissions_validation_and_empty_date(self):
        self.assertEqual(self.module.app.test_client().get(self.url).status_code, 401)
        self.assertEqual(self.export(self.fixture.viewer).status_code, 403)
        for filters in ({'date': '2026-02-30'}, {'date': '20260913'}, {'shift': 'invalid'},
                        {'department': 'x' * 501}, {'contractor': ['x'] * 101}):
            self.assertEqual(self.export(**filters).status_code, 400)
        self.assertEqual(self.export(date='2026-09-14').status_code, 404)

    def test_real_pdf_response(self):
        response = self.export(department='СМУ А')
        self.assertEqual(response.status_code, 200, response.data[:500])
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertGreater(len(response.data), 20000)


if __name__ == '__main__':
    unittest.main()
