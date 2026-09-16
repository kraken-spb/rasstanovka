"""Compact position report: consecutive positions, exact work text and rosters."""
from collections import defaultdict, deque
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
import re

from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle
from svglib.svglib import svg2rlg

ROOT = Path(__file__).resolve().parent
NAVY = colors.HexColor('#153D57')
INK = colors.HexColor('#1E303B')
MUTED = colors.HexColor('#526672')
LINE = colors.HexColor('#CDD8DF')
PALE = colors.HexColor('#EFF5F8')
POSITION_FILL = colors.HexColor('#D8E6EF')
POSITION_BLUE = colors.HexColor('#28556E')
FONT, BOLD = 'PositionCardsSans', 'PositionCardsSans-Bold'
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 22
WIDTH = PAGE_WIDTH - MARGIN * 2
BOTTOM = PAGE_HEIGHT - 30
CONTENT_TOP = 60
WORK_WIDTH, PEOPLE_WIDTH = WIDTH * .55, WIDTH * .45
PAD = 4
BLOCK_PAD = 2
EPSILON = .01
CATEGORY_LABELS = {
    'Арматурщик': 'Арм.',
    'Бетонщик': 'Бет.',
    'Монтажник СиЖБК': 'Монт. СиЖБК',
    'Монтажник ТТ': 'Монт. ТТ',
    'Прочие монтажники': 'Проч. монт.',
    'Прочие основные рабочие': 'Проч. осн. раб.',
    'Сварщик АиПАМ': 'Св. АиПАМ',
    'Сварщик МК': 'Св. МК',
    'Сварщик ТТ': 'Св. ТТ',
    'Электромонтажник': 'Эл.монт.',
}


@lru_cache(maxsize=1)
def _assets():
    pdfmetrics.registerFont(TTFont(FONT, str(ROOT / 'fonts/DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont(BOLD, str(ROOT / 'fonts/DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD, italic=FONT, boldItalic=BOLD)
    logo = svg2rlg(str(ROOT / 'static/lgss-logo.svg'))
    if logo is None:
        raise ValueError('Не удалось прочитать логотип для PDF.')
    return logo


def _text(value):
    return '' if value is None else str(value)


def _safe(value):
    return escape(_text(value)).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')


def _inline(value):
    return escape(' '.join(_text(value).split()))


def _p(text, *, size=9, bold=False, color=INK, align=TA_LEFT):
    return Paragraph(text, ParagraphStyle('position', fontName=BOLD if bold else FONT,
        fontSize=size, leading=size * 1.25, textColor=color, alignment=align,
        splitLongWords=True, allowWidows=1, allowOrphans=1))


def _height(item, width):
    return item.wrap(width, 1000000)[1]


def _roster_grid(people, width, columns=3):
    """Read left to right, then down; each employee occupies their own cell."""
    columns = min(columns, max(1, len(people)))
    rows = []
    for start in range(0, len(people), columns):
        cells = [_p(name, size=8.5) for name in people[start:start + columns]]
        rows.append(cells + [''] * (columns - len(cells)))
    table = Table(rows, colWidths=[width / columns] * columns, hAlign='LEFT',
                  splitByRow=1, splitInRow=0)
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), .5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), .5),
        *([('LINEBEFORE', (1, 0), (-1, -1), .3, LINE)] if columns > 1 else []),
    ]))
    return table


def _take(items, width, height):
    pending, result, used = deque(items), [], 0
    while pending:
        item = pending[0]
        size = _height(item, width)
        if size <= height - used + EPSILON:
            pending.popleft()
            result.append((item, size))
            used += size
            continue
        if result:
            break
        parts = item.split(width, max(0, height - used))
        if not parts:
            break
        first_height = _height(parts[0], width)
        if first_height > height - used + EPSILON:
            raise ValueError('Текст не помещается в доступную область PDF.')
        pending.popleft()
        result.append((parts[0], first_height))
        pending.extendleft(reversed(parts[1:]))
        used += first_height
        break
    return result, pending, used


def _short_name(name):
    parts = _text(name).split()
    # Preserve multipart names whose surname cannot safely be inferred.
    if len(parts) not in (2, 3) or not all(re.fullmatch(r'[А-ЯЁа-яёA-Za-z-]+', p) for p in parts):
        return ' '.join(parts)
    if any(p.casefold() in {'угли', 'улы', 'уулу', 'кызы', 'оглы', 'гызы'} for p in parts):
        return ' '.join(parts)
    return parts[0] + ' ' + ''.join(p[0] + '.' for p in parts[1:])


def _name_labels(cards):
    identities = defaultdict(set)
    workers = [w for c in cards for s in c.get('shifts', []) for g in s.get('groups', [])
               for work in g.get('works', []) for w in work.get('workers', [])]
    for worker in workers:
        identities[_short_name(worker.get('full_name'))].add(
            (_text(worker.get('full_name')), _text(worker.get('personnel_no'))))
    labels = {}
    for worker in workers:
        identity = (_text(worker.get('full_name')), _text(worker.get('personnel_no')))
        short = _short_name(identity[0])
        if len(identities[short]) == 1:
            labels[identity] = short
        else:
            same_name = sum(name == identity[0] for name, number in identities[short])
            labels[identity] = identity[0] + (' (таб. ' + identity[1] + ')' if same_name > 1 and identity[1] else '')
    return labels


def _display_groups(shift):
    """Keep brigade rosters separate; merge only matching responsibility and work."""
    result = {}
    for group in shift.get('groups', []):
        key = (group.get('linear_itr') or '', group.get('brigadier') or '', group.get('crew_name') or '', group.get('crew_id'))
        target = result.setdefault(key, {'linear_itr': key[0], 'brigadier': key[1], 'crew_name': key[2], 'works': {}})
        for work in group.get('works', []):
            if not work.get('workers'):
                continue
            description = work.get('description') or ''
            target['works'].setdefault(description,
                {'description': description, 'workers': []})['workers'].extend(work['workers'])
    return [{**group, 'works': list(group['works'].values())} for group in result.values() if group['works']]


def _natural(value):
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', _text(value)))


def _summary_matrix(cards):
    """Count the same authorized assignments as the detail, separately by shift."""
    rows, employers, shifts = [], set(), set()
    totals = defaultdict(int)
    for card in cards:
        counts = defaultdict(int)
        categories = defaultdict(lambda: defaultdict(int))
        for shift in card.get('shifts', []):
            for group in shift.get('groups', []):
                for work in group.get('works', []):
                    for worker in work.get('workers', []):
                        employer = _text(worker.get('employer'))
                        key = (employer, shift['label'])
                        counts[key] += 1
                        categories[_text(worker.get('category'))][key] += 1
                        totals[key] += 1
                        employers.add(employer)
                        shifts.add(shift['label'])
        if counts:
            rows.append({'card': card, 'counts': dict(counts), 'categories': [
                {'category': category, 'counts': dict(categories[category])}
                for category in sorted(categories, key=lambda name: (not bool(name), _natural(name)))]})
    return {'rows': rows, 'totals': dict(totals),
            'employers': sorted(employers, key=lambda name: (not bool(name), _natural(name))),
            'shifts': [shift for shift in ('День', 'Ночь') if shift in shifts]}


def _summary_table(matrix, employers, *, partial=False):
    shifts = matrix['shifts']
    shift_count = len(shifts)
    left_width = WIDTH * (.54 if len(employers) <= 2 else .43)
    number_width = (WIDTH - left_width) / ((len(employers) + 1) * shift_count)
    column_count = 1 + (len(employers) + 1) * shift_count

    def heading(text):
        return _p(_inline(text), size=7.5, bold=True, color=colors.white, align=TA_CENTER)

    top = [heading('Позиция / категория ГДЛР')]
    sub = ['']
    spans = [('SPAN', (0, 0), (0, 1))]
    for index, employer in enumerate([*employers, None]):
        start = 1 + index * shift_count
        title = ('Итого по блоку' if partial else 'Всего') if employer is None else (employer or 'Не указан')
        top.extend([heading(title), *([''] * (shift_count - 1))])
        sub.extend(heading(shift) for shift in shifts)
        if shift_count > 1:
            spans.append(('SPAN', (start, 0), (start + shift_count - 1, 0)))

    def numbers(counts, total=False, grand_total=False):
        values = [counts.get((employer, shift), 0) for employer in employers for shift in shifts]
        values.extend(sum(counts.get((employer, shift), 0) for employer in employers) for shift in shifts)
        return [_p(str(value) if value else '—', size=8, bold=total or index >= len(employers) * shift_count,
                   color=colors.white if grand_total else (NAVY if value else MUTED), align=TA_CENTER)
                for index, value in enumerate(values)]

    data = [top, sub]
    position_styles = []
    for row in matrix['rows']:
        card = row['card']
        context = list(dict.fromkeys(value for value in (card.get('stage'), card.get('object_name')) if value))
        position = '<b>' + (_inline(card.get('subobject_name')) or 'Позиция не указана') + '</b>'
        category_rows = row['categories']
        metadata = _inline(' / '.join(context))
        if len(category_rows) == 1:
            metadata += (' · ' if metadata else '') + 'ГДЛР: ' + (_inline(category_rows[0]['category']) or 'не указана')
        if metadata:
            position += '<br/><font size="7" color="#526672">' + metadata + '</font>'
        start = len(data)
        data.append([_p(position, size=8, color=NAVY), *numbers(row['counts'], total=True)])
        if len(category_rows) > 1:
            for category in category_rows:
                label = 'ГДЛР: ' + (_inline(category['category']) or 'не указана')
                data.append([_p(label, size=7.5), *numbers(category['counts'])])
            position_styles.extend([
                ('LEFTPADDING', (0, start + 1), (0, len(data) - 1), 12),
                ('TOPPADDING', (0, start + 1), (-1, len(data) - 1), 1.5),
                ('BOTTOMPADDING', (0, start + 1), (-1, len(data) - 1), 1.5),
            ])
        position_styles.extend([
            ('BACKGROUND', (0, start), (-1, start), POSITION_FILL),
            ('LINEABOVE', (0, start), (-1, start), .7, NAVY),
            ('NOSPLIT', (0, start), (-1, len(data) - 1)),
        ])
    data.append([_p('Всего по показанным работодателям' if partial else 'Всего по СМУ', size=7.5, bold=True, color=colors.white),
                 *numbers(matrix['totals'], total=True, grand_total=True)])
    table = Table(data, colWidths=[left_width] + [number_width] * (column_count - 1),
                  repeatRows=2, hAlign='LEFT', splitByRow=1, splitInRow=0)
    table.setStyle(TableStyle([
        *spans,
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('BACKGROUND', (0, 0), (-1, 1), NAVY),
        ('ROWBACKGROUNDS', (0, 2), (-1, -2), [colors.white, colors.HexColor('#F8FAFB')]),
        ('BACKGROUND', (0, -1), (-1, -1), NAVY),
        ('GRID', (0, 0), (-1, -1), .35, LINE),
        *position_styles,
        ('NOSPLIT', (0, len(data) - 2), (-1, len(data) - 1)),
    ]))
    return table


class _Report:
    def __init__(self, stream, cards, day_label, filter_summary):
        self.logo = _assets()
        self.canvas = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
        self.canvas.setTitle('Расстановка по позициям за ' + day_label)
        self.canvas.setAuthor('АО «Ленгазспецстрой»')
        self.day = day_label
        self.filters = filter_summary
        self.names = _name_labels(cards)
        self.totals = defaultdict(lambda: defaultdict(int))
        for card in cards:
            for shift in card.get('shifts', []):
                self.totals[self._smu(card)][shift['label']] += sum(len(work.get('workers', []))
                    for group in shift.get('groups', []) for work in group.get('works', []))
        self.page = 0
        self.y = 0
        self.card = self.shift = self.group = None
        self.work_number = 0
        self.summary_mode = False
        self.details_started = False

    @staticmethod
    def _smu(card):
        return (card.get('department') or '', card.get('responsible_name') or '')

    def _draw(self, paragraph, x, y, width):
        height = _height(paragraph, width)
        paragraph.drawOn(self.canvas, x, PAGE_HEIGHT - y - height)
        return height

    def _line(self, y):
        self.canvas.setStrokeColor(LINE)
        self.canvas.setLineWidth(.4)
        self.canvas.line(MARGIN, PAGE_HEIGHT - y, MARGIN + WIDTH, PAGE_HEIGHT - y)

    def _counts(self, values):
        return ' · '.join(f'{label}: {values[label]}' for label in ('День', 'Ночь') if values.get(label))

    def _smu_paragraph(self):
        department, responsible = self._smu(self.card)
        return _p('<b>' + (_inline(department) or 'СМУ не указано') + '</b>'
            + ' · ' + self._counts(self.totals[self._smu(self.card)]) + '<br/>'
            + 'Ответственное лицо: <b>' + (_inline(responsible) or 'Не указано') + '</b>', size=9)

    def _smu_header(self):
        self.y += self._draw(self._smu_paragraph(), MARGIN, self.y, WIDTH) + 5

    def _new_page(self, continuation=False, detail_start=False):
        if self.page:
            self.canvas.showPage()
        self.page += 1
        c = self.canvas
        logo_scale = 135 / self.logo.width
        c.saveState()
        c.translate(MARGIN, PAGE_HEIGHT - 8 - self.logo.height * logo_scale)
        c.scale(logo_scale, logo_scale)
        renderPDF.draw(self.logo, c, 0, 0)
        c.restoreState()
        self._draw(_p('АО «ЛЕНГАЗСПЕЦСТРОЙ»', size=9, bold=True, color=NAVY, align=TA_CENTER),
                   MARGIN + 145, 13, WIDTH - 290)
        self._draw(_p('Дата расстановки', size=7, color=MUTED, align=TA_RIGHT),
                   PAGE_WIDTH - MARGIN - 100, 7, 100)
        self._draw(_p(_inline(self.day), size=10, bold=True, color=NAVY, align=TA_RIGHT),
                   PAGE_WIDTH - MARGIN - 100, 18, 100)
        c.setFillColor(NAVY)
        c.rect(MARGIN, PAGE_HEIGHT - 54, WIDTH, 20, stroke=0, fill=1)
        self._draw(_p('Расстановка по позициям', size=12.5, bold=True, color=colors.white, align=TA_CENTER),
                   MARGIN, 36, WIDTH)
        self.y = CONTENT_TOP
        if self.filters:
            self.y += self._draw(_p(_safe(self.filters), size=8, color=MUTED), MARGIN, self.y, WIDTH) + 3
        if self.summary_mode:
            self.y += self._draw(_p('Сводная по позициям · Численность по сменам', size=10, bold=True, color=NAVY),
                                MARGIN, self.y, WIDTH) + 7
        elif detail_start:
            self._detail_heading()
        self._smu_header()
        self.body_top = self.y
        c.setFillColor(MUTED)
        c.setFont(FONT, 7.5)
        c.drawString(MARGIN, 15, 'АО «Ленгазспецстрой»')
        c.drawRightString(PAGE_WIDTH - MARGIN, 15, str(self.page))
        if continuation:
            self._card_header(continued=True)
            self._group_header()

    def render_summary(self, cards):
        sections = {}
        for card in cards:
            sections.setdefault(self._smu(card), []).append(card)
        self.summary_mode = True
        for section in sections.values():
            matrix = _summary_matrix(section)
            if not matrix['rows']:
                continue
            self.card = section[0]
            employers = matrix['employers']
            limit = 4 if len(matrix['shifts']) == 2 else 6
            for start in range(0, len(employers), limit):
                shown = employers[start:start + limit]
                partial = len(employers) > limit
                table = _summary_table(matrix, shown, partial=partial)
                caption = _p(f'Работодатели {start + 1}-{start + len(shown)} из {len(employers)}',
                             size=8, color=MUTED) if partial else None
                caption_height = _height(caption, WIDTH) + 4 if caption else 0
                _height(table, WIDTH)
                first_rows = min(4, len(table._rowHeights))
                needed = sum(table._rowHeights[:first_rows]) + _height(self._smu_paragraph(), WIDTH) + caption_height + 14
                if not self.page or self.y + needed > BOTTOM:
                    self._new_page()
                else:
                    self.y += 9
                    self._smu_header()
                while table is not None:
                    if caption:
                        self.y += self._draw(caption, MARGIN, self.y, WIDTH) + 4
                    chunks = table.split(WIDTH, BOTTOM - self.y)
                    if not chunks:
                        if self.y > self.body_top + caption_height + EPSILON:
                            self._new_page()
                            continue
                        raise ValueError('Строка сводной таблицы слишком велика для листа A4.')
                    self.y += self._draw(chunks[0], MARGIN, self.y, WIDTH)
                    table = chunks[1] if len(chunks) > 1 else None
                    if table is not None:
                        self._new_page()
        self.summary_mode = False
        self.card = self.shift = self.group = None

    def _detail_heading(self):
        self.y += self._draw(_p('Расстановка по бригадам', size=10, bold=True, color=NAVY),
                            MARGIN, self.y, WIDTH) + 7

    def _card_content(self, continued=False):
        context = [value for value in (self.card.get('stage'), self.card.get('object_name')) if value]
        context = list(dict.fromkeys(context))
        name = _inline(self.card.get('subobject_name')) or 'Позиция не указана'
        title = _p('<b>' + name + '</b>' + (' <font size="8">(продолжение)</font>' if continued else ''), size=10, color=colors.white)
        counts = {shift['label']: sum(len(work.get('workers', [])) for group in shift.get('groups', [])
                  for work in group.get('works', [])) for shift in self.card.get('shifts', [])}
        numbers = _p(_safe(self._counts(counts)), size=9, bold=True, color=colors.white)
        metadata = _p(_inline(' / '.join(context)), size=8, color=colors.HexColor('#E4EEF4')) if context else None
        return title, numbers, metadata

    def _card_height(self, continued=False):
        title, numbers, metadata = self._card_content(continued)
        return max(_height(title, WIDTH - 119), _height(numbers, 103)) + 8 + (
            _height(metadata, WIDTH - 8) + 2 if metadata else 0)

    def _card_header(self, continued=False):
        title, numbers, metadata = self._card_content(continued)
        height = self._card_height(continued)
        self.canvas.setFillColor(POSITION_BLUE)
        self.canvas.rect(MARGIN, PAGE_HEIGHT - self.y - height, WIDTH, height, stroke=0, fill=1)
        title_height = max(_height(title, WIDTH - 119), _height(numbers, 103))
        self._draw(title, MARGIN + 4, self.y + 4, WIDTH - 119)
        self._draw(numbers, MARGIN + WIDTH - 107, self.y + 4, 103)
        if metadata:
            self._draw(metadata, MARGIN + 4, self.y + 4 + title_height + 2, WIDTH - 8)
        self.y += height + 2

    def _group_paragraph(self):
        crew_name = ' '.join(_text(self.group.get('crew_name')).split())
        crew_label = (crew_name if re.match(r'^бригада(?:\s|$)', crew_name, re.IGNORECASE)
                      else 'Бригада: ' + crew_name) if crew_name else 'Бригада не указана'
        return _p('<b>' + _inline(self.shift['label']) + '</b>'
            + ' · <b>' + _inline(crew_label) + '</b>'
            + ' · ИТР: ' + (_inline(self.group.get('linear_itr')) or '—')
            + ' · Бригадир: ' + (_inline(self.group.get('brigadier')) or '—'), size=8.5)

    def _group_header(self):
        self.y += self._draw(self._group_paragraph(), MARGIN + PAD, self.y + 2, WIDTH - PAD * 2) + 5
        self.group_top = self.y

    def _draw_items(self, items, x, y):
        for paragraph, height in items:
            paragraph.drawOn(self.canvas, x, PAGE_HEIGHT - y - height)
            y += height

    def _compact_content(self, work):
        people = [self._worker_label(worker) for worker in work['workers']]
        description = self._work_description(work)
        roster = _roster_grid(people, WIDTH - PAD * 2)
        height = _height(description, WIDTH - PAD * 2) + _height(roster, WIDTH - PAD * 2) + BLOCK_PAD * 2 + 2
        return description, roster, height

    def _work_description(self, work):
        return _p('<b>' + str(len(work['workers'])) + ' чел.</b> · '
                  + (_safe(work.get('description')) or 'Работы не заполнены'), size=9)

    def _worker_label(self, worker):
        name = self.names[(_text(worker.get('full_name')), _text(worker.get('personnel_no')))]
        category = _text(worker.get('category'))
        category_label = CATEGORY_LABELS.get(category, category) or 'ГДЛР не указана'
        tag = '(' + _inline(category_label) + ')'
        if category in CATEGORY_LABELS:
            tag = '<nobr>' + tag + '</nobr>'
        return _inline(name) + ' <font size="7" color="#526672">' + tag + '</font>'

    def _fresh_top(self):
        return CONTENT_TOP + (_height(_p(_safe(self.filters), size=8), WIDTH) + 3 if self.filters else 0) + _height(self._smu_paragraph(), WIDTH) + 5

    def _first_row_height(self, work):
        height = self._compact_content(work)[2]
        maximum = BOTTOM - self._fresh_top() - self._card_height(True) - _height(self._group_paragraph(), WIDTH - PAD * 2) - 10
        return height if height <= maximum else 45

    def _work(self, work):
        self.work_number += 1
        label = f'{self.work_number}'
        description, roster, height = self._compact_content(work)
        maximum = BOTTOM - self._fresh_top() - self._card_height(True) - _height(self._group_paragraph(), WIDTH - PAD * 2) - 10
        if height <= maximum:
            if self.y + height > BOTTOM:
                self._new_page(continuation=True)
            top = self.y + BLOCK_PAD
            top += self._draw(description, MARGIN + PAD, top, WIDTH - PAD * 2) + 2
            self._draw(roster, MARGIN + PAD, top, WIDTH - PAD * 2)
            self.y += height
            self._line(self.y)
            return
        people = [self._worker_label(worker) for worker in work['workers']]
        original_people = _roster_grid(people, PEOPLE_WIDTH - PAD * 2, columns=2)
        original_work = self._work_description(work)
        people_pending, work_pending = deque([original_people]), deque([original_work])
        people_pages, work_pages = set(), set()
        continued = False
        pw, ww = PEOPLE_WIDTH - PAD * 2, WORK_WIDTH - PAD * 2
        original_ph, original_wh = _height(original_people, pw), _height(original_work, ww)
        while people_pending or work_pending:
            available = BOTTOM - self.y - PAD * 2
            full_height = max(sum(_height(p, pw) for p in people_pending), sum(_height(p, ww) for p in work_pending))
            fresh = BOTTOM - self.body_top - self._card_height(True) - _height(self._group_paragraph(), WIDTH - PAD * 2) - 10 - PAD * 2
            if available < 24 or (full_height <= fresh and full_height > available):
                self._new_page(continuation=True)
                available = BOTTOM - self.y - PAD * 2
            split_row = continued or full_height > available
            continuation_label = _p('Работа ' + label + (' / продолжение' if continued else ''), size=7.5, color=MUTED)
            label_height = max(_height(continuation_label, ww), _height(continuation_label, pw)) + 2 if split_row else 0
            available -= label_height
            def consume(pending, original, original_height, width, pages, text):
                if pending:
                    return _take(pending, width, available)
                repeated = original if original_height <= available else _p(
                    text + ' ' + label + ': см. стр. ' + str(min(pages)) + '-' + str(max(pages)) + '.', size=8, color=MUTED)
                items, remaining, height = _take([repeated], width, available)
                if remaining:
                    raise ValueError('Не помещается ссылка на продолжение PDF.')
                return items, pending, height
            people_chunk, people_rest, ph = consume(people_pending, original_people, original_ph, pw, people_pages, 'Исполнители работы')
            work_chunk, work_rest, wh = consume(work_pending, original_work, original_wh, ww, work_pages, 'Текст работы')
            if (people_pending and not people_chunk) or (work_pending and not work_chunk):
                if self.y > self.group_top + EPSILON:
                    self._new_page(continuation=True)
                    continue
                raise ValueError('Контекст позиции слишком велик для листа A4.')
            top = self.y + PAD
            if split_row:
                for x, width in ((MARGIN, WORK_WIDTH), (MARGIN + WORK_WIDTH, PEOPLE_WIDTH)):
                    self._draw(continuation_label, x + PAD, top, width - PAD * 2)
            self._draw_items(work_chunk, MARGIN + PAD, top + label_height)
            self._draw_items(people_chunk, MARGIN + WORK_WIDTH + PAD, top + label_height)
            self.y += max(ph, wh) + PAD * 2 + label_height
            self._line(self.y)
            if people_pending:
                people_pages.add(self.page)
            if work_pending:
                work_pages.add(self.page)
            people_pending, work_pending = people_rest, work_rest
            if people_pending or work_pending:
                self._new_page(continuation=True)
                continued = True

    def render_card(self, card):
        previous_smu = self._smu(self.card) if self.card else None
        self.card = card
        new_smu = previous_smu != self._smu(card)
        groups = [(shift, group) for shift in card.get('shifts', []) for group in _display_groups(shift)]
        if not groups:
            return
        self.shift, self.group = groups[0]
        needed = self._card_height() + _height(self._group_paragraph(), WIDTH - PAD * 2) + self._first_row_height(self.group['works'][0]) + 8
        if new_smu:
            needed += _height(self._smu_paragraph(), WIDTH) + 9
        # Detail begins on a fresh page after the complete summary section.
        if not self.details_started or not self.page or self.y + needed > BOTTOM:
            self._new_page(detail_start=not self.details_started)
        else:
            if new_smu:
                self.y += 7
                self._smu_header()
        self.details_started = True
        self._card_header()
        for shift, group in groups:
            self.shift, self.group = shift, group
            if self.y + _height(self._group_paragraph(), WIDTH - PAD * 2) + self._first_row_height(group['works'][0]) + 5 > BOTTOM:
                self._new_page(continuation=True)
            else:
                self._group_header()
            for work in group['works']:
                self._work(work)
        self.y += 7


def build_position_cards_pdf(cards, *, day_label, filter_summary='') -> bytes:
    """Group positions by SMU, flow multiple positions per page, keep work text intact."""
    ordered = sorted(cards, key=lambda c: tuple(_natural(c.get(key)) for key in
                     ('department', 'stage', 'object_name', 'subobject_name')))
    stream = BytesIO()
    report = _Report(stream, ordered, day_label, filter_summary)
    report.render_summary(ordered)
    for card in ordered:
        report.render_card(card)
    report.canvas.save()
    return stream.getvalue()
