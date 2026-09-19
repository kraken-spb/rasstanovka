"""Explainable candidate ranking; a recommendation never changes identity or presence."""
from datetime import date
import re
from ticket_airports import matches_point

TRANSLIT=dict(zip('абвгдезийклмнопрстуфыэ','abvgdezijklmnoprstufye'))
TRANSLIT.update({'ё':'e','ж':'zh','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ь':'','ю':'yu','я':'ya'})


def name_key(value):
    value=''.join(TRANSLIT.get(c,c) for c in (value or '').lower())
    return re.sub(r'[^a-z0-9]','',value)


def words(value):
    return [name_key(word) for word in re.findall(r'[A-Za-zА-Яа-яЁё]+',value or '')]


def name_evidence(passenger, full_name):
    p,n=words(passenger),words(full_name)
    if len(p)>=2 and len(n)>=2 and sorted(p)==sorted(n): return 100,'ФИО совпадает'
    if len(p)>=2 and len(n)>=2 and sorted(p[:2])==sorted(n[:2]):return 95,'Фамилия и имя совпадают; отчество требует проверки'
    # Exact alternative transliteration, not fuzzy similarity: passport IU/IA/I.
    if re.search('[А-Яа-яЁё]',full_name or ''):
        passport={**TRANSLIT,'ю':'iu','я':'ia','й':'i'}
        alternate=words(''.join(passport.get(c,c) for c in full_name.lower()))
        if len(p)>=2 and len(alternate)>=2 and sorted(p)==sorted(alternate):
            return 100,'ФИО совпадает в паспортной транслитерации'
        if len(p)>=2 and len(alternate)>=2 and sorted(p[:2])==sorted(alternate[:2]):
            return 95,'Фамилия и имя совпадают в паспортной транслитерации; отчество требует проверки'
    # A single PDF word may contain a surname and a first name with no space.
    if len(p)==1 and len(n)>=2 and p[0] in {n[0]+n[1],n[1]+n[0]}:return 90,'Фамилия и имя совпадают после разделения слитного текста'
    if len(p)>=2 and len(n)>=2 and p[0]==n[0] and all(len(a)==1 and b.startswith(a) for a,b in zip(p[1:],n[1:])):
        return 65,'Совпадают фамилия и инициалы; требуется проверка'
    return 0,''


def recommend(ticket, workers, mappings):
    first,last=ticket['segments'][0],ticket['segments'][-1]
    source,destination=first.get('origin_original',first['origin']),last.get('destination_original',last['destination'])
    def point_matches(value,point):
        known=matches_point(value,point) if ticket.get('transport')=='air' else None
        return known if known is not None else bool(value and name_key(value)==name_key(point))
    ranked=[]
    for worker in workers:
        strength,explanation=name_evidence(ticket['passenger'],worker['full_name'])
        if not strength:continue
        points=mappings.get(worker.get('project_code'),[])
        arrival=any(point_matches(destination,p) for p in points)
        departure=any(point_matches(source,p) for p in points)
        direction='arrival' if arrival and not departure else 'departure' if departure and not arrival else ''
        reasons=[explanation];delta=None
        ticket_birth=ticket.get('birth_date') or ''
        worker_birth=worker.get('birth_date') or ''
        birth_match='match' if ticket_birth and ticket_birth==worker_birth else 'conflict' if ticket_birth and worker_birth else 'unknown'
        if birth_match=='match':reasons.append('Дата рождения совпадает: '+date.fromisoformat(ticket_birth).strftime('%d.%m.%Y'))
        elif birth_match=='conflict':reasons.append('Дата рождения НЕ совпадает: в билете '+date.fromisoformat(ticket_birth).strftime('%d.%m.%Y')+', в карточке '+date.fromisoformat(worker_birth).strftime('%d.%m.%Y')+'; требуется ручная проверка')
        elif ticket_birth:reasons.append('Дата рождения в карточке не заполнена; совпадение не подтверждено')
        def closest(direction=None):
            options=[]
            for plan in worker.get('plans',[]):
                if plan.get('direction') not in ('arrival','departure') or (direction and plan['direction']!=direction):continue
                actual=last['arrival_date'] if plan['direction']=='arrival' else first['departure_date']
                if actual and plan.get('planned_date'):
                    difference=abs((date.fromisoformat(actual)-date.fromisoformat(plan['planned_date'])).days)
                    options.append((difference,plan['planned_date'],plan.get('source') or 'Плановая дата поездки'))
            return min(options) if options else None
        if direction:
            reasons.append('Пункт '+('прибытия' if arrival else 'отправления')+' связан с проектом сотрудника')
            nearest=closest(direction)
            if nearest:
                delta,expected,label=nearest
                reasons.append(label+': '+date.fromisoformat(expected).strftime('%d.%m.%Y')+'; отклонение: '+str(delta)+' дн.')
        else:
            reasons.append('Направление поездки требует выбора: нет однозначной связи маршрута с проектом')
            nearest=closest()
            if nearest:
                delta,expected,label=nearest
                reasons.append(label+': '+date.fromisoformat(expected).strftime('%d.%m.%Y')+'; отклонение: '+str(delta)+' дн.; направление не подтверждено')
        ranked.append({'id':worker['id'],'full_name':worker['full_name'],'department':worker['department'],
            'personnel_no':worker['personnel_no'],'project':worker.get('project',''),'direction':direction,
            'birth_date':worker_birth,'birth_date_match':birth_match,
            'reasons':reasons,'name_strength':strength,'date_difference':delta})
    # DOB only ranks name-compatible people; mismatches are never recommended.
    ranked.sort(key=lambda r:({'match':0,'unknown':1,'conflict':2}[r['birth_date_match']],-r['name_strength'],not bool(r['direction']),r['date_difference'] is None,
                              r['date_difference'] if r['date_difference'] is not None else 0,r['id']))
    if ranked:
        top=ranked[0]
        tied=(len(ranked)>1 and bool(top['direction'])==bool(ranked[1]['direction'])
              and all(top[k]==ranked[1][k] for k in ('birth_date_match','name_strength','date_difference')))
        top['recommended']=not tied and top['birth_date_match']!='conflict'
        if tied:top['reasons'].append('Несколько равнозначных кандидатов: выберите сотрудника')
    return ranked[:8]
