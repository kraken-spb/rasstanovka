"""Exact airport labels for ticket previews; retain original values for review.

Codes in the September ticket corpus were checked against the source itineraries.
IKT/OVB: https://corp.uzairways.com/sites/default/files/2025-10/1.%D0%93%D0%B5%D0%BD%D0%B5%D1%80%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D0%B5%20%D0%B3%D1%80%D1%83%D0%B7%D1%8B%20%D0%BC%D0%B5%D0%B6%D0%B4%D1%83%D0%BD%D0%B0%D1%80%D0%BE%D0%B4%D0%BD%D1%8B%D0%B5.pdf
TAS: https://www.uzairways.com/ru/taxonomy/term/10
NMA: https://www.uzairways.com/ru/taxonomy/term/170
BSZ (formerly FRU): https://airport.kg/ru/news/15
IRK is intentionally NOT an alias for IKT.
"""
from copy import deepcopy
import re

# IATA -> Russian city. Airport-specific project links still distinguish codes.
CITIES = {
    'IKT':'Иркутск', 'TAS':'Ташкент', 'OVB':'Новосибирск',
    'SVX':'Екатеринбург', 'NMA':'Наманган', 'BSZ':'Бишкек', 'FRU':'Бишкек',
    'EVN':'Ереван', 'DME':'Москва', 'SVO':'Москва', 'ASF':'Астрахань',
    'OSS':'Ош', 'SKD':'Самарканд', 'KJA':'Красноярск', 'ALA':'Алматы',
    'VOG':'Волгоград', 'KUF':'Самара', 'CIT':'Шымкент', 'FEG':'Фергана',
}
ENGLISH = {
    'IRKUTSK':'Иркутск', 'TASHKENT':'Ташкент', 'TASHKENT INTERNATIONAL':'Ташкент',
    'NAMANGAN':'Наманган', 'NOVOSIBIRSK':'Новосибирск', 'YEKATERINBURG':'Екатеринбург',
    'BISHKEK':'Бишкек', 'MOSCOW':'Москва', 'YEREVAN':'Ереван',
    'ASTRAKHAN':'Астрахань', 'OSH':'Ош', 'SAMARKAND':'Самарканд',
    'KRASNOYARSK':'Красноярск', 'ALMATY':'Алматы', 'VOLGOGRAD':'Волгоград',
    'SAMARA':'Самара', 'SHYMKENT':'Шымкент', 'FERGANA':'Фергана',
}

def airport(value):
    """Return (city, explicit IATA code) only for exact known values."""
    raw=' '.join((value or '').split()).strip()
    upper=raw.upper()
    if upper in CITIES:return CITIES[upper],upper
    if upper in ENGLISH:return ENGLISH[upper],''
    if upper in {s.upper() for s in CITIES.values()}:return raw.title(),''
    match=re.fullmatch(r'(.+?)\s+\(?([A-Za-z]{3})\)?(?:\s*,\s*.*)?',raw)
    if match:
        prefix,code=match[1].strip(),match[2].upper()
        city=CITIES.get(code)
        if city and (prefix.casefold()==city.casefold() or ENGLISH.get(prefix.upper())==city):
            return city,code
    return None

def translate_airports(ticket):
    if ticket.get('transport')!='air':return ticket
    result=deepcopy(ticket)
    warnings=result.setdefault('warnings',[])
    for segment in result.get('segments',[]):
        for field in ('origin','destination'):
            current=segment.get(field,'')
            raw=segment.get(field+'_original',current)
            match=airport(raw)
            if match and current in (raw,match[0]):
                city,code=match
                if city!=raw:segment[field+'_original']=raw
                if code:segment[field+'_iata']=code
                segment[field]=city
            elif re.fullmatch('[A-Za-z]{3}',current or '') and not match:
                warning='Код аэропорта '+current.upper()+' отсутствует в справочнике. Укажите город по билету.'
                if warning not in warnings:warnings.append(warning)
    return result

def matches_point(value,reference):
    """None means no airport-specific evidence; caller keeps existing matching."""
    a,b=airport(value),airport(reference)
    if not a or not b:return None
    if a[1] and b[1]:
        canonical=lambda code:'BSZ' if code=='FRU' else code
        return canonical(a[1])==canonical(b[1])
    # A city-wide project link covers its airports; an airport-specific link does
    # not infer a missing airport from the city alone.
    if b[1] and not a[1]:return False
    return a[0]==b[0]
