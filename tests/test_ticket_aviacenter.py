import unittest
from ticket_parser import parse
from ticket_matching import name_evidence, recommend

SAMPLE = '''ЭЛЕКТРОННЫЙ БИЛЕТ (МАРШРУТ/КВИТАНЦИЯ)
ООО «Агентство АВИА ЦЕНТР»
Дата : 07.09.2026
Фамилия : KRIUCHKOV VADIM DMITRIEVICH
ОТПРВ/НАЗН : IKT - ASF
Номер билета : 5550000000001
ОТ/ДО РЕЙС КЛ ДАТА ОТПР ПРИБ СТ
Иркутск IKT
Москва SVO , терм. B
SU1443 r 15.09.2026 09:50 10:55 ОК RCOENR 1P23KG
Рейс выполняется а/к Aeroflot(SU) В пути 6ч. 5мин.
Москва SVO , терм. B
Астрахань ASF
SU1642 r 15.09.2026 13:20 17:00 ОК RCOENR 1P23KG
'''

class AviacenterTest(unittest.TestCase):
    def test_two_segments_and_departure_not_issue_date(self):
        value=parse([SAMPLE],filename='Другой человек.pdf')
        self.assertEqual(value['passenger'],'KRIUCHKOV VADIM DMITRIEVICH')
        self.assertEqual(value['ticket_number'],'5550000000001')
        self.assertEqual(value['template'],'aviacenter')
        self.assertEqual(len(value['segments']),2)
        self.assertEqual([s['flight'] for s in value['segments']],['SU1443','SU1642'])
        self.assertEqual(value['segments'][0]['origin'],'Иркутск')
        self.assertEqual(value['segments'][0]['origin_original'],'Иркутск IKT')
        self.assertEqual(value['segments'][1]['destination'],'Астрахань')
        self.assertEqual([s['departure_date'] for s in value['segments']],['2026-09-15']*2)
        self.assertEqual([s['arrival_time'] for s in value['segments']],['10:55','17:00'])
        self.assertEqual([s['arrival_date'] for s in value['segments']],['',''])
        self.assertFalse(any('выбранный при загрузке год' in w for w in parse([SAMPLE],travel_year=2027)['warnings']))

    def test_missing_and_multiple_identities_remain_for_review(self):
        value=parse([SAMPLE.replace('KRIUCHKOV VADIM DMITRIEVICH','')])
        self.assertEqual(value['passenger'],'')
        value=parse([SAMPLE+'\nФамилия : IVANOV IVAN IVANOVICH'])
        self.assertEqual(value['passenger'],'')
        self.assertEqual(value['ticket_number'],'')

    def test_passport_transliteration_is_exact_and_ties_remain(self):
        self.assertEqual(name_evidence('KRIUCHKOV VADIM DMITRIEVICH','Крючков Вадим Дмитриевич')[0],100)
        self.assertEqual(name_evidence('KRIUCHKOV VADIM DMITRIEVICH','Крюков Вадим Дмитриевич')[0],0)
        ticket=parse([SAMPLE])
        workers=[{'id':i,'full_name':'Крючков Вадим Дмитриевич','department':'Тест','personnel_no':str(i)} for i in (1,2)]
        self.assertFalse(recommend(ticket,workers,{})[0]['recommended'])
