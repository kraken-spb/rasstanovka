import unittest
from copy import deepcopy
from ticket_airports import airport, translate_airports, matches_point
from ticket_matching import recommend

class AirportCodesTest(unittest.TestCase):
    def ticket(self,a='IKT',b='TAS'):
        return {'transport':'air','passenger':'Иванов Иван','warnings':[],
                'segments':[{'origin':a,'destination':b,'departure_date':'','arrival_date':''}]}

    def test_codes_and_original_are_preserved_without_mutating_stored_preview(self):
        source=self.ticket();before=deepcopy(source);value=translate_airports(source)
        self.assertEqual(source,before)
        s=value['segments'][0]
        self.assertEqual((s['origin'],s['destination']),('Иркутск','Ташкент'))
        self.assertEqual((s['origin_original'],s['destination_iata']),('IKT','TAS'))
        self.assertEqual(translate_airports(value),value)

    def test_airport_suffix_and_english_names(self):
        self.assertEqual(airport('Москва SVO , терм. B'),('Москва','SVO'))
        self.assertEqual(airport('Иркутск (IKT)'),('Иркутск','IKT'))
        self.assertEqual(airport('IRKUTSK'),('Иркутск',''))
        self.assertEqual(airport('NMA'),('Наманган','NMA'))
        self.assertEqual(airport('BSZ'),('Бишкек','BSZ'))

    def test_unknown_conflicting_and_rail_values_are_not_guessed(self):
        self.assertIsNone(airport('IRK'))
        self.assertIsNone(airport('Ташкент IKT'))
        value=translate_airports(self.ticket('IRK','ZZZ'))
        self.assertEqual(value['segments'][0]['origin'],'IRK')
        self.assertEqual(len(value['warnings']),2)
        self.assertEqual(translate_airports(value),value)
        train=self.ticket();train['transport']='rail'
        self.assertEqual(translate_airports(train),train)

    def test_city_and_airport_project_scope(self):
        self.assertTrue(matches_point('IKT','Иркутск'))
        self.assertTrue(matches_point('Москва SVO , терм. B','SVO'))
        self.assertFalse(matches_point('DME','SVO'))
        self.assertFalse(matches_point('Москва','SVO'))
        self.assertTrue(matches_point('FRU','BSZ'))
        workers=[{'id':1,'full_name':'Иванов Иван','personnel_no':'1','department':'Тест','project_code':'project.test'}]
        ticket=translate_airports(self.ticket())
        self.assertEqual(recommend(ticket,workers,{'project.test':['TAS']})[0]['direction'],'arrival')
        self.assertEqual(recommend(ticket,workers,{'project.test':['Иркутск']})[0]['direction'],'departure')
