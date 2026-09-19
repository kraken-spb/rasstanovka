"""Local ticket text extraction. Unknown layouts remain explicit review items."""
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import date
from ticket_airports import translate_airports


MONTHS = {word: i for i, words in enumerate([
    'jan january янв января', 'feb february фев февраля', 'mar march мар марта',
    'apr april апр апреля', 'may мая май', 'jun june июн июня', 'jul july июл июля',
    'aug august авг августа', 'sep sept september сен сент сентября',
    'oct october окт октября', 'nov november ноя ноября', 'dec december дек декабря'], 1)
    for word in words.split()}
DATE = r'\d{1,2}\s*(?:[./]\s*\d{1,2}\s*[./]\s*20\d{2}|[A-Za-zА-Яа-яЁё]+\.?\s*20\d{2})'
TIME = r'\d{1,2}[:\ue092]\d{2}'


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def clean_point(value):
    value=clean(value)
    parts=re.sub(r'/[A-Z]{2}$','',value).split()
    if len(parts)==2 and parts[0]==parts[1]:return parts[0]
    return value


def date_value(value):
    s = clean(value).lower()
    match = re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](20\d{2})', s)
    if match:
        day, month, year = map(int, match.groups())
    else:
        match = re.fullmatch(r'(\d{1,2})\s*([a-zа-яё]+)\.?\s*(20\d{2})', s)
        if not match or match[2] not in MONTHS: return ''
        day, month, year = int(match[1]), MONTHS[match[2]], int(match[3])
    try:return date(year, month, day).isoformat()
    except ValueError:return ''


def birth_date_value(value):
    """Full, explicit birth dates only; never infer a century or a US date order."""
    s=clean(value).lower()
    match=re.fullmatch(r'((?:19|20)\d{2})-(\d{2})-(\d{2})',s)
    if match:
        year,month,day=map(int,match.groups())
    else:
        match=re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})',s)
        if match:
            day,month,year=map(int,match.groups())
        else:
            match=re.fullmatch(r'(\d{1,2})\s*([a-zа-яё]+)\.?\s*((?:19|20)\d{2})',s)
            if not match or match[2] not in MONTHS:return ''
            day,month,year=int(match[1]),MONTHS[match[2]],int(match[3])
    try:return date(year,month,day).isoformat()
    except ValueError:return ''


def passenger_birth_date(text, warnings):
    label=r'(?:Дата\s+рождения|Date\s+of\s+birth|D\.?O\.?B\.?|Tug[‘’\x27ʻ]?ilgan\s+sanasi)'
    # Adjacent labelled value only: ticket issue/flight/passport dates are not DOB.
    value=r'((?:19|20)\d{2}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s*[A-Za-zА-Яа-яЁё]+\.?\s*(?:19|20)\d{2})'
    matches=re.findall(r'(?<!\w)'+label+r'(?:\s*[/|]\s*'+label+r')*\s*[:：-]?\s*'+value+r'(?!\d)',text,re.I)
    dates={birth_date_value(value) for value in matches}
    if len(dates)==1 and '' not in dates:return dates.pop()
    if matches:
        warnings.append('Дата рождения в билете неоднозначна или неполная; при сопоставлении она не используется.')
    return ''


def run(command, timeout=45):
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode: raise ValueError('Не удалось прочитать документ. Проверьте файл.')
    return result.stdout.decode('utf-8', errors='replace')


def extract(path):
    """OCR only pages without usable text; all work remains inside the server."""
    from pypdf import PdfReader
    path = Path(path)
    pages, methods = [], []
    if path.suffix.lower() == '.pdf':
        reader = PdfReader(path)
        if reader.is_encrypted: raise ValueError('PDF защищён паролем. Загрузите незашифрованную копию.')
        if not 1 <= len(reader.pages) <= 12: raise ValueError('Допустимо до 12 страниц в одном файле.')
        extracted = run(['pdftotext','-raw','-enc','UTF-8',str(path),'-']).split('\f')
        for index, page in enumerate(reader.pages):
            text = extracted[index] if index < len(extracted) else ''
            controls = sum(ord(c)<32 and c not in '\n\r\t' for c in text)
            good = len(text.strip()) >= 80 and controls <= 2 and 'VVXLQJ' not in text
            if good:
                pages.append(text);methods.append('text');continue
            with tempfile.TemporaryDirectory(prefix='ticket-ocr-') as directory:
                prefix = str(Path(directory)/'page')
                run(['pdftoppm','-f',str(index+1),'-l',str(index+1),'-singlefile','-scale-to','2400','-png',str(path),prefix])
                pages.append(run(['tesseract',prefix+'.png','stdout','-l','eng+rus+uzb','--psm','3'], 60))
                methods.append('ocr')
    else:
        from PIL import Image
        with Image.open(path) as source:
            if source.width*source.height > 30_000_000:raise ValueError('Изображение слишком большое.')
            source.verify()
        pages.append(run(['tesseract',str(path),'stdout','-l','eng+rus+uzb','--psm','3'],60));methods.append('ocr')
    return pages, methods


def parse(pages, filename='', methods=None, travel_year=None):
    text='\n'.join(pages).replace('\u00a0',' ').replace('\ue092',':').replace('\ue088','-')
    lines=[clean(x) for x in text.splitlines() if clean(x)]
    text='\n'.join(lines)
    text=re.sub(r'([А-ЯЁ]{3,}-)\n([А-ЯЁ]{3,})(?=\n)',r'\1\2',text)
    result={'passenger':'','ticket_number':'','transport':'','segments':[], 'warnings':[],
            'filename':filename,'methods':methods or ['text']*len(pages),'template':'unknown'}
    warnings=result['warnings']
    def find(pattern,flags=re.I):
        m=re.search(pattern,text,flags);return clean(m[1]) if m else ''
    def segment(origin='',destination='',dep='',arr='',dt='',at='',flight=''):
        return {'origin':clean_point(origin),'destination':clean_point(destination),'departure_date':date_value(dep),
                'arrival_date':date_value(arr),'departure_time':clean(dt),'arrival_time':clean(at),
                'flight':clean(flight),'coach':'','seat':'','timezone_note':'Как указано в билете; часовой пояс требует проверки'}
    def number(s):return re.sub(r'[\s-]','',s)
    if 'Chiptaraqami' in text or re.search(r'Ticket\s*number\s*/\s*Chipta',text,re.I):
        result['template']='uzair';result['transport']='air'
        result['passenger']=find(r'Passenger\s*/[^:\n]+:\s*(.+?)(?:\bMr\b|\bMs\b|\bMrs\b|Mr\(|Ms\(|\(ADT\)|\n)')
        result['ticket_number']=number(find(r'Ticket\s*number\s*/[^:\n]+:\s*([\d -]{13,20})'))
        # A row has an explicit flight, departure/arrival dates and times.
        for m in re.finditer(r'([A-ZА-Я][A-ZА-Я /-]{3,100})\s+(HY\s*\d{2,5})\s+('+TIME+r')\s+('+DATE+r')\s+('+TIME+r')\s+('+DATE+r')',text):
            previous=text[:m.start()].splitlines()
            origin=next((line for line in reversed(previous) if re.fullmatch(r'[A-Z][A-Z /-]{3,80}',line) and line not in {'FROM','QAYERDAN'}),'')
            result['segments'].append(segment(origin,m[1],m[4],m[6],m[3],m[5],m[2]))
    elif 'АВИА ЦЕНТР' in text and 'ОТПРВ/НАЗН' in text:
        result['template']='aviacenter';result['transport']='air'
        result['passenger']=find(r'^Фамилия[ \t]*:[ \t]*([^\n]+)',re.M|re.I)
        result['ticket_number']=find(r'^Номер билета[ \t]*:[ \t]*(\d{13})\b',re.M|re.I)
        point=r'([^\n]+?\s+[A-Z]{3}(?:\s*,[^\n]*)?)'
        route=r'^'+point+r'\n'+point+r'\n([A-Z0-9]{2}\s*\d{2,5})\s+[A-Za-z]\s+('+DATE+r')\s+('+TIME+r')\s+('+TIME+r')'
        for m in re.finditer(route,text,re.M):
            row=segment(m[1],m[2],m[4],'',m[5],m[6],m[3])
            result['segments'].append(row)
        warnings.append('Дата прибытия не указана отдельно. Проверьте её для каждого участка маршрута; время сохранено как в билете.')
        if len(set(re.findall(r'^Фамилия[ \t]*:[ \t]*([^\n]+)',text,re.M|re.I)))>1:
            result['passenger']='';result['ticket_number']=''
            warnings.append('В файле несколько пассажиров. Выберите данные одного билета вручную.')
    elif 'КОНТРОЛЬНЫЙ КУПОН' in text:
        result['template']='rail';result['transport']='rail'
        result['ticket_number']=number(find(r'(?:КОНТРОЛЬНЫЙ КУПОН №|E-ticket number)\s*([\d ]{14,25})'))
        result['passenger']=find(r'Valid\s*\n([^\n]+)') or find(r'\n([А-ЯЁ]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)')
        year=find(r'Год совершения поездки:\s*(20\d{2})')
        if year:
            m=re.search(r'Class\s*(\d{2}\.\d{2})\s*('+TIME+r')\s*([^\n]+)\s*\n->\s*\n([^\n]+)\s*(\d{2}\.\d{2})\s*('+TIME+r')',text)
            if m: result['segments'].append(segment(m[3],m[4],m[1]+'.'+year,m[5]+'.'+year,m[2],m[6],find(r'Поезд\s*\nTrain\s*\n([^\n]+)')))
        else:
            m=re.search('('+TIME+r').*?('+DATE+r')\s*\n([^\n]+).*?('+TIME+r').*?('+DATE+r')\s*\n([^\n]+)',text,re.S)
            if m:result['segments'].append(segment(m[3],m[6],m[2],m[5],m[1],m[4],find(r'ПОЕЗД\s*\n([^\n]+)')))
        if result['segments']:
            result['segments'][0]['coach']=find(r'(?:Вагон\s*\nCoach|ВАГОН)\s*\n([^\n]+)')
            result['segments'][0]['seat']=find(r'(?:Место\s*\nSeat|МЕСТО)\s*\n([^\n]+)')
    elif 'Пассажиры / Passengers' in text:
        result['template']='s7';result['transport']='air'
        m=re.search(r'\n(?:Mr |Ms |Mrs )(.+?)\s+(\S+)\s+(\d{13})',text)
        if m:result.update(passenger=m[1],ticket_number=m[3])
        # S7 itinerary omits the year: retain dates as a review note, never use issue year.
        warnings.append('В маршруте S7 год поездки может быть не указан. Уточните даты по оригиналу.')
        for m in re.finditer(r'(S7\s+\d+)\s*\nS7 Airlines\s*\n[^\n]+\s*\n(\d{1,2}\s+[а-я]+)\s*\n('+TIME+r')\s*\n(.*?)\s*\n(\d{1,2}\s+[а-я]+)\s*\n('+TIME+r')\s*\n(.*?)(?=\n(?:Эконом|Бизнес))',text,re.S):
            row=segment(re.sub(r'\nТерминал:.*','',m[4]),re.sub(r'\nТерминал:.*','',m[7]),m[2]+' '+str(travel_year) if travel_year else '',m[5]+' '+str(travel_year) if travel_year else '',dt=m[3],at=m[6],flight=m[1]);row['date_note']=m[2]+' → '+m[5];result['segments'].append(row)
    elif 'Бронирование (PNR)' in text:
        result['template']='tutu';result['transport']='air'
        m=re.search(r'Тип пассажира\s*\n([A-Z -]+?)\s+(\d{13})',text)
        if m:result.update(passenger=m[1],ticket_number=number(m[2]))
        for m in re.finditer(r'Вылет\s*('+TIME+r')\s*([A-Z]{3})\s*('+DATE+r').*?Прилёт\s*('+TIME+r')\s*([A-Z]{3})\s*('+DATE+r')',text,re.S):
            result['segments'].append(segment(m[2],m[5],m[3],m[6],m[1],m[4],find(r'Рейс\s*\n([^\n]+)')))
    elif re.search(r'ФАМИЛИЯ\s+И\s+ИМЯ\s+ПАССАЖИРА',text):
        result['template']='agency';result['transport']='air'
        result['passenger']=find(r'ПАССАЖИРА\s*/\s*ДОКУМЕНТ\s*\n([^/\n]+)')
        m=re.search(r'Статус\s*\n([^\n]+)\s*\n(\d{13})',text)
        if m:result['ticket_number']=m[2]
        a=re.search(r'ВЫЛЕТ\s*:\s*('+DATE+r')\s*Г\s*\.\s*('+TIME+r')(.*?)Рейс',text,re.S|re.I)
        b=re.search(r'ПРИЛ[ЕЁ]Т\s*:\s*('+DATE+r')\s*Г\s*\.\s*('+TIME+r')(.*?)ВАЖНО',text,re.S|re.I)
        if a and b:
            origin=re.findall(r'\b[A-Z]{3}\b',a[3]);dest=re.findall(r'\b[A-Z]{3}\b',b[3])
            result['segments'].append(segment(origin[-1] if origin else '',dest[-1] if dest else '',a[1],b[1],a[2],b[2],m[1] if m else ''))
    elif 'Passenger / Пассажир:' in text:
        result['template']='ural';result['transport']='air'
        result['passenger']=find(r'Passenger / Пассажир:\s*([^\n]+)')
        result['ticket_number']=find(r'Ticket number / Номер билета:\s*(\d{13})')
        for m in re.finditer(r'([A-Za-z -]+)\n([А-Яа-яЁё -]+)\n([A-Za-z -]+)\n([А-Яа-яЁё -]+)\nU6\n(\d+)\n\S+ (\d{2} [A-Za-z]+)\n\d{2}\n[а-я.]+\n('+TIME+r') ('+TIME+r')',text):
            row=segment(m[2],m[4],m[6]+' '+str(travel_year) if travel_year else '',dt=m[7],at=m[8],flight='U6 '+m[5])
            # Arrival day is absent: the user must confirm it, even when same-day seems likely.
            row['date_note']=m[6]+'; дата прибытия не указана';result['segments'].append(row)
        warnings.append('Проверьте год отправления и дату прибытия: в маршрутной таблице они указаны не полностью.')
    elif 'SCAT Airlines' in text and 'Фамилия\n' in text:
        result['template']='scat';result['transport']='air'
        result['passenger']=find(r'Фамилия\n([^\n]+)')+' '+find(r'Имя\n([^\n]+)')
        result['ticket_number']=number(find(r'\nБилет\n([\d-]+)'))
        m=re.search(r'\n([^\n,]+), (\d{2} [а-я]+) — ([^\n,]+), (\d{2} [а-я]+), в пути.*?\n('+TIME+r')\n[^\n]+\n('+TIME+r')',text)
        if m:result['segments'].append(segment(m[1],m[3],m[2]+' '+str(travel_year) if travel_year else '',m[4]+' '+str(travel_year) if travel_year else '',m[5],m[6],find(r'\n(DV-\d+)')))
        warnings.append('Год поездки отсутствует в строке маршрута: проверьте выбранный год.')
    elif 'Продавец этого билета' in text:
        result['template']='aviasales';result['transport']='air'
        result['passenger']=find(r'ПАССАЖИР / ДОКУМЕНТ\n([^/\n]+)')
        result['ticket_number']=find(r'ЭЛЕКТРОННЫЙ БИЛЕТ\n(\d{13})')
        for m in re.finditer(r'([^\n]+)\s+\(([A-Z]{3})\)\n('+TIME+r')\n('+DATE+r')\n([A-Z0-9]{2}-\d+)[^\n]*\n[^\n]*\n('+TIME+r')\n('+DATE+r')\n([^\n]+)\n[^\n]+\(([A-Z]{3})\)',text):
            result['segments'].append(segment(m[1],m[8],m[4],m[7],m[3],m[6],m[5]))
    elif 'Агент: Uzairways.online' in text:
        result['template']='uz-online';result['transport']='air'
        result['passenger']=find(r'фамилия\):\s*([^\n]+)')
        result['ticket_number']=find(r'Номер билета:\s*(\d{13})')
        for m in re.finditer(r'('+TIME+r')\n('+DATE+r')\n([^\n]+)\n('+TIME+r')\n('+DATE+r')\n([^\n]+)',text):
            result['segments'].append(segment(m[3],m[6],m[2],m[5],m[1],m[4],find(r'\n(HY \d+)')))
    else:
        # Recognize identifiers without inventing relationships in an unknown layout.
        result['passenger']=find(r'ПАССАЖИР\s*/\s*ДОКУМЕНТ\s*\n([^/\n]+)')
        result['ticket_number']=number(find(r'(?:Номер билета:|ЭЛЕКТРОННЫЙ БИЛЕТ)\s*\n([\d -]{13,20})'))
        warnings.append('Формат билета требует проверки. Заполните нераспознанные поля по оригиналу.')
    if travel_year and any(s.get('date_note') for s in result['segments']):warnings.append('Для дат без года использован выбранный при загрузке год: '+str(travel_year))
    if not result['segments']:result['segments']=[segment()]
    passenger_lines=re.findall(r'(?:Passenger\s*/[^:\n]+:\s*|\n(?:Mr |Ms |Mrs ))([A-Za-zА-Яа-яЁё -]+)',text)
    if len({clean(s).lower() for s in passenger_lines})>1:
        warnings.append('В файле несколько записей пассажиров. Проверьте, что сохраняете данные только выбранного билета.')
        result['passenger']='';result['ticket_number']=''
    if not result['passenger']:warnings.append('Не удалось уверенно прочитать ФИО пассажира.')
    if not result['ticket_number']:warnings.append('Проверьте номер билета.')
    if any(not r['departure_date'] or not r['arrival_date'] for r in result['segments']):warnings.append('Даты поездки требуют заполнения или проверки.')
    if 'ocr' in (methods or []):warnings.append('Часть данных распознана с изображения. Сверьте с оригиналом.')
    result['birth_date']=passenger_birth_date(text,warnings) if result['passenger'] else ''
    return translate_airports(result)
