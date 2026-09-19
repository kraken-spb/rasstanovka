import unittest
from copy import deepcopy

from ticket_matching import recommend
from ticket_parser import parse, passenger_birth_date


class TicketIdentityTest(unittest.TestCase):
    def ticket(self, **values):
        return {'passenger':'IVAN IVANOV', 'segments':[{'origin':'Казань','destination':'Москва',
                'departure_date':'2026-09-10','arrival_date':'2026-09-11'}], **values}

    def worker(self, key, birthday='', day='2026-09-11', direction='arrival'):
        return {'id':key,'full_name':'Иванов Иван','department':'СМУ','personnel_no':str(key),
                'birth_date':birthday,'project_code':'moscow','plans':[{'direction':direction,
                'planned_date':day,'source':'Дата заезда из карточки'}]}

    def test_labelled_birth_dates_require_full_year_and_preserve_trip_dates(self):
        for label in ['Дата рождения','Date of birth','DOB','Tug‘ilgan sanasi','Дата рождения / Date of birth']:
            for value in ['04.05.1980','04/05/1980','04May1980','4 мая 1980','1980-05-04']:
                with self.subTest(label=label,value=value):
                    self.assertEqual(passenger_birth_date(label+':\n'+value,[]),'1980-05-04')
        for text in ['Дата выдачи: 04.05.1980','Flight date: 04.05.1980','Дата рождения: 04.05.80',
                     'Дата рождения: 31.02.1980','Дата рождения: 04.05.1980\nDOB: 05.04.1980']:
            self.assertEqual(passenger_birth_date(text,[]),'')
        value=parse(['Passenger / Пассажир: IVANOV IVAN\nTicket number / Номер билета: 1234567890123\nДата рождения: 04.05.1980'])
        self.assertEqual(value['birth_date'],'1980-05-04')
        self.assertEqual(value['segments'][0]['departure_date'],'')

    def test_dob_match_beats_closer_route_date_but_not_unrelated_name(self):
        workers=[self.worker(1,'1981-05-04'),self.worker(2,'1980-05-04','2026-09-20'),self.worker(3)]
        workers.append({**self.worker(4,'1980-05-04'),'full_name':'Петров Пётр'})
        ranked=recommend(self.ticket(birth_date='1980-05-04'),workers,{'moscow':['Москва']})
        self.assertEqual([r['id'] for r in ranked],[2,3,1])
        self.assertTrue(ranked[0]['recommended'])
        self.assertEqual(ranked[-1]['birth_date_match'],'conflict')
        self.assertIn('НЕ совпадает',' '.join(ranked[-1]['reasons']))

    def test_conflict_is_never_recommended_even_for_single_candidate(self):
        ranked=recommend(self.ticket(birth_date='1980-05-04'),[self.worker(1,'1981-05-04')],{'moscow':['Москва']})
        self.assertFalse(ranked[0]['recommended'])

    def test_equal_birthdays_use_dates_then_keep_a_real_tie(self):
        workers=[self.worker(1,'1980-05-04','2026-09-21'),self.worker(2,'1980-05-04')]
        ranked=recommend(self.ticket(birth_date='1980-05-04'),workers,{'moscow':['Москва']})
        self.assertEqual(ranked[0]['id'],2)
        self.assertEqual(ranked[0]['date_difference'],0)
        self.assertIn('11.09.2026',' '.join(ranked[0]['reasons']))
        workers[0]['plans']=deepcopy(workers[1]['plans'])
        self.assertFalse(recommend(self.ticket(birth_date='1980-05-04'),workers,{'moscow':['Москва']})[0]['recommended'])

    def test_departure_uses_departure_day_and_missing_route_does_not_invent_direction(self):
        ticket=self.ticket();ticket['segments'][0].update(origin='Москва',destination='Казань')
        workers=[self.worker(1,day='2026-09-10',direction='departure'),self.worker(2,day='2026-09-11',direction='departure')]
        ranked=recommend(ticket,workers,{'moscow':['Москва']})
        self.assertEqual((ranked[0]['id'],ranked[0]['date_difference'],ranked[0]['direction']),(1,0,'departure'))
        self.assertEqual(recommend(ticket,workers,{})[0]['direction'],'')

    def test_multiple_passengers_do_not_share_a_birth_date(self):
        value=parse(['Passenger / Пассажир: IVANOV IVAN\nDOB: 04.05.1980\nPassenger / Пассажир: PETROV PETR'])
        self.assertEqual(value['passenger'],'')
        self.assertEqual(value['birth_date'],'')
