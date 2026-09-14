"""Branded, paginated A4 cards with explicit worker-to-work correspondence."""

from collections import deque
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from svglib.svglib import svg2rlg


ROOT = Path(__file__).resolve().parent
NAVY = colors.HexColor('#153D57')
INK = colors.HexColor('#1E303B')
MUTED = colors.HexColor('#526672')
LINE = colors.HexColor('#CDD8DF')
PALE = colors.HexColor('#EFF5F8')
NIGHT = colors.HexColor('#4A5265')
FONT = 'PositionCardsSans'
BOLD = 'PositionCardsSans-Bold'
MARGIN = 28
PAGE_WIDTH, PAGE_HEIGHT = A4
WIDTH = PAGE_WIDTH - 2 * MARGIN
BOTTOM = PAGE_HEIGHT - 43
PADDING = 6
LEFT_WIDTH = WIDTH * .46
RIGHT_WIDTH = WIDTH - LEFT_WIDTH
EPSILON = .01


@lru_cache(maxsize=1)
def _assets():
    """Use packaged DejaVu fonts and the original vector brand mark."""
    pdfmetrics.registerFont(TTFont(FONT, str(ROOT / 'fonts' / 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont(BOLD, str(ROOT / 'fonts' / 'DejaVuSans-Bold.ttf')))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD, italic=FONT, boldItalic=BOLD)
    logo = svg2rlg(str(ROOT / 'static' / 'lgss-logo.svg'))
    if logo is None:
        raise ValueError('Не удалось прочитать логотип для PDF.')
    return logo


def _text(value):
    return '' if value is None else str(value)


def _safe(value):
    return escape(_text(value)).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')


def _p(text, *, size=9.5, bold=False, color=INK, leading=None, after=0):
    """Only this private helper accepts markup; all source fields are escaped."""
    return Paragraph(text, ParagraphStyle(
        'card', fontName=BOLD if bold else FONT, fontSize=size,
        leading=leading or size * 1.27, textColor=color, spaceAfter=after,
        splitLongWords=True, allowWidows=1, allowOrphans=1,
    ))


def _height(paragraph, width):
    return paragraph.wrap(width, 1000000)[1]


def _queue_height(items, width):
    return sum(_height(item, width) + item.getSpaceAfter() for item in items)


def _take(items, width, height):
    """Consume paragraphs within a cell, splitting a single oversized paragraph.

    Ordinary employee entries stay together. A very long individual entry can
    split, so unusually long names or professions cannot cause a LayoutError.
    Paragraph.split preserves escaped text and ReportLab's line boundaries.
    """
    pending = deque(items)
    taken = []
    used = 0
    while pending:
        item = pending[0]
        item_height = _height(item, width)
        if item_height <= height - used + EPSILON:
            pending.popleft()
            taken.append((item, item_height))
            used += item_height + item.getSpaceAfter()
            continue
        if taken:
            break
        parts = item.split(width, max(0, height - used))
        if not parts:
            break
        pending.popleft()
        first = parts[0]
        first_height = _height(first, width)
        if first_height > height - used + EPSILON:
            raise ValueError('Фрагмент PDF превышает доступную высоту страницы.')
        taken.append((first, first_height))
        used += first_height
        pending.extendleft(reversed(parts[1:]))
        break
    # A final paragraph's spacing is cosmetic and must not extend its cell.
    return taken, pending, min(used, height)


def _worker(worker):
    details = [_safe(worker.get('profession'))]
    if _text(worker.get('personnel_no')):
        details.append('Таб. № ' + _safe(worker['personnel_no']))
    details = ' / '.join(detail for detail in details if detail)
    return _p(
        '<b>' + _safe(worker.get('full_name')) + '</b>'
        + ('<br/><font size="8.5" color="#526672">' + details + '</font>' if details else ''),
        size=9.5, leading=11.6, after=3,
    )


def _page_list(pages):
    ordered = sorted(pages)
    if not ordered:
        raise ValueError('У продолжения PDF отсутствует исходная страница.')
    return str(ordered[0]) if len(ordered) == 1 else f'{ordered[0]}-{ordered[-1]}'


class _CardsRenderer:
    def __init__(self, stream, *, day_label, filter_summary):
        self.logo = _assets()
        self.canvas = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
        self.canvas.setTitle('Карточки позиций за ' + _text(day_label))
        self.canvas.setAuthor('АО «Ленгазспецстрой»')
        self.canvas.setSubject('Расстановка сотрудников и выполняемые работы')
        self.day_label = day_label
        self.filter_summary = filter_summary
        self.card = None
        self.card_number = 0
        self.card_page = 0
        self.page = 0
        self.y = 0
        self.shift = None
        self.group = None
        self.shift_count = 0
        self.group_top = 0
        self.card_body_top = 0

    def _paragraph(self, paragraph, x, top, width):
        height = _height(paragraph, width)
        paragraph.drawOn(self.canvas, x, PAGE_HEIGHT - top - height)
        return height

    def _rect(self, top, height, color, *, x=MARGIN, width=WIDTH):
        self.canvas.setFillColor(color)
        self.canvas.rect(x, PAGE_HEIGHT - top - height, width, height, stroke=0, fill=1)

    def _line(self, top):
        self.canvas.setStrokeColor(LINE)
        self.canvas.setLineWidth(.45)
        self.canvas.line(MARGIN, PAGE_HEIGHT - top, MARGIN + WIDTH, PAGE_HEIGHT - top)

    def _card_context(self, compact=False, size=8.5):
        size = size if compact else 9.5
        context = []
        if _text(self.card.get('stage')):
            stage = _text(self.card['stage'])
            context.append(('' if stage.casefold().startswith(('этап ', 'этап:')) or stage.casefold() == 'этап' else 'Этап: ') + _safe(stage))
        if _text(self.card.get('object_name')):
            context.append(_safe(self.card['object_name']))
        metadata = _p(' / '.join(context), size=size, color=MUTED) if context else None
        rows = []
        for label, key in (('СМУ', 'department'), ('Ответственное лицо', 'responsible_name')):
            value = _safe(self.card.get(key)) or 'Не указано'
            rows.append((_p('<b>' + label + ':</b> ' + value, size=size),) if compact else
                        (_p(label, bold=True), _p(value)))
        position = _safe(self.card.get('subobject_name')) or 'Позиция не указана'
        position_p = _p(f'<b>{self.card_number:02d} / {position}</b>', size=size + 1 if compact else 12, color=NAVY)
        return metadata, rows, position_p

    def _context_height(self, content):
        metadata, rows, position = content
        height = (_height(metadata, WIDTH) + 5) if metadata else 0
        for row in rows:
            height += (_height(row[0], WIDTH) if len(row) == 1 else
                       max(_height(row[0], 139), _height(row[1], WIDTH - 151))) + 3
        return height + _height(position, WIDTH - 20) + 19

    def _new_page(self, *, repeat_group=False):
        if self.page:
            self.canvas.showPage()
        self.page += 1
        self.card_page += 1
        c = self.canvas
        c.saveState()
        c.translate(MARGIN, PAGE_HEIGHT - 50)
        c.scale(174 / self.logo.width, 174 / self.logo.width)
        renderPDF.draw(self.logo, c, 0, 0)
        c.restoreState()
        self._paragraph(_p('АО «Ленгазспецстрой»', size=8.5, color=MUTED), MARGIN, 53, 260)
        self._paragraph(_p(_safe(self.day_label), size=11, bold=True, color=NAVY), PAGE_WIDTH - 135, 29, 107)
        self._rect(67, 1, NAVY)
        title = 'Карточка позиции' + (' / продолжение' if self.card_page > 1 else '')
        self.y = 75 + self._paragraph(_p(title, size=20, bold=True, color=NAVY), MARGIN, 75, WIDTH) + 4
        content = self._card_context()
        if self._context_height(content) > 300:
            content = self._card_context(compact=True)
            if self._context_height(content) > 440:
                content = self._card_context(compact=True, size=7.5)
        metadata, rows, position_p = content
        if metadata:
            self.y += self._paragraph(metadata, MARGIN, self.y, WIDTH) + 5
        for row in rows:
            if len(row) == 1:
                self.y += self._paragraph(row[0], MARGIN, self.y, WIDTH) + 3
            else:
                row_height = max(_height(row[0], 139), _height(row[1], WIDTH - 151))
                self._paragraph(row[0], MARGIN, self.y, 139)
                self._paragraph(row[1], MARGIN + 151, self.y, WIDTH - 151)
                self.y += row_height + 3
        self.y += 3
        band_height = _height(position_p, WIDTH - 20) + 10
        self._rect(self.y, band_height, PALE)
        self._paragraph(position_p, MARGIN + 10, self.y + 5, WIDTH - 20)
        self.y += band_height + 6
        if _text(self.filter_summary):
            self.y += self._paragraph(_p(_safe(self.filter_summary), size=8, color=MUTED), MARGIN, self.y, WIDTH) + 6
        self.card_body_top = self.y
        self._line(PAGE_HEIGHT - 35)
        c.setFillColor(MUTED)
        c.setFont(FONT, 8)
        c.drawRightString(PAGE_WIDTH - MARGIN, 22, 'Страница ' + str(self.page))
        if repeat_group:
            self._shift_header()
            self._group_header()

    def _shift_header(self):
        label = _text(self.shift.get('label'))
        heading = {'День': 'ДНЕВНАЯ СМЕНА', 'Ночь': 'НОЧНАЯ СМЕНА'}.get(label, label.upper())
        paragraph = _p(_safe(heading) + f' / {self.shift_count} чел.', size=10, bold=True, color=colors.white)
        height = _height(paragraph, WIDTH - 18) + 7
        self._rect(self.y, height, NIGHT if label == 'Ночь' else NAVY)
        self._paragraph(paragraph, MARGIN + 9, self.y + 3, WIDTH - 18)
        self.y += height + 4

    def _fresh_row_height(self):
        label = _text(self.shift.get('label'))
        heading = {'День': 'ДНЕВНАЯ СМЕНА', 'Ночь': 'НОЧНАЯ СМЕНА'}.get(label, label.upper())
        shift_height = _height(_p(_safe(heading) + f' / {self.shift_count} чел.', size=10, bold=True), WIDTH - 18) + 11
        return BOTTOM - self.card_body_top - shift_height - self._group_header_height() - 2 * PADDING - 12

    def _group_header_height(self):
        return max(_height(item, WIDTH / 2 - 12) for item in self._responsible_paragraphs()) + 11 + self._column_header_height()

    def _responsible_paragraphs(self):
        return [
            _p('<b>' + label + '</b><br/>' + (_safe(self.group.get(key)) or 'Не указан'))
            for label, key in (('Линейный ИТР', 'linear_itr'), ('Бригадир', 'brigadier'))
        ]

    def _column_paragraphs(self):
        crew = _safe(self.group.get('crew_name')) or 'Без бригады'
        return (_p(crew + ' / сотрудники', size=9, bold=True),
                _p('Выполняемые работы и объёмы', size=9, bold=True))

    def _column_header_height(self):
        left, right = self._column_paragraphs()
        return max(_height(left, LEFT_WIDTH - 12), _height(right, RIGHT_WIDTH - 12)) + 8

    def _group_header(self):
        paragraphs = self._responsible_paragraphs()
        height = max(_height(item, WIDTH / 2 - 12) for item in paragraphs)
        for index, paragraph in enumerate(paragraphs):
            self._paragraph(paragraph, MARGIN + index * WIDTH / 2 + 6, self.y + 2, WIDTH / 2 - 12)
        self.y += height + 7
        self._line(self.y)
        self.y += 4
        height = self._column_header_height()
        self._rect(self.y, height, PALE)
        for paragraph, x, width in zip(self._column_paragraphs(), (MARGIN, MARGIN + LEFT_WIDTH), (LEFT_WIDTH, RIGHT_WIDTH)):
            self._paragraph(paragraph, x + 6, self.y + 4, width - 12)
        self.y += height
        self.group_top = self.y
        if BOTTOM - self.y < 48:
            raise ValueError('Контекст карточки слишком велик для страницы A4.')

    def _draw_cell(self, items, x, top):
        for paragraph, height in items:
            paragraph.drawOn(self.canvas, x, PAGE_HEIGHT - top - height)
            top += height + paragraph.getSpaceAfter()

    def _work(self, work, number):
        original_workers = [_worker(worker) for worker in work.get('workers', [])]
        original_description = _p(_safe(work.get('description')) or 'Не заполнено', size=10)
        workers = deque(original_workers)
        description = deque([original_description])
        worker_pages, description_pages = set(), set()
        continued = False
        left_width, right_width = LEFT_WIDTH - 2 * PADDING, RIGHT_WIDTH - 2 * PADDING
        description_height = _height(original_description, right_width)
        while workers or description:
            label_height = 12
            available = BOTTOM - self.y - 2 * PADDING - label_height
            # Keep a normal work text intact. A text taller than a full page is
            # split below; its work number identifies every continuation.
            required_description = _queue_height(description, right_width) if description else description_height
            fresh_height = self._fresh_row_height()
            if available < 24 or (self.y > self.group_top + EPSILON and available < required_description <= fresh_height):
                self._new_page(repeat_group=True)
                available = BOTTOM - self.y - 2 * PADDING - label_height
            if workers:
                left, remaining_workers, left_height = _take(workers, left_width, available)
            else:
                repeat = original_workers if _queue_height(original_workers, left_width) <= available else [
                    _p(f'Исполнители работы {number}: см. стр. {_page_list(worker_pages)}.', size=9, color=MUTED)
                ]
                left, unused, left_height = _take(repeat, left_width, available)
                remaining_workers = workers
                if unused:
                    raise ValueError('Не помещается ссылка на исполнителей работы.')
            if description:
                right, remaining_description, right_height = _take(description, right_width, available)
            else:
                repeat = [original_description] if description_height <= available else [
                    _p(f'Текст работы {number}: см. стр. {_page_list(description_pages)}.', size=9, color=MUTED)
                ]
                right, unused, right_height = _take(repeat, right_width, available)
                remaining_description = description
                if unused:
                    raise ValueError('Не помещается ссылка на текст работы.')
            if (workers and not left) or (description and not right):
                if abs(self.y - self.group_top) < EPSILON:
                    raise ValueError('Не удалось разместить текст работы на странице A4.')
                self._new_page(repeat_group=True)
                continue
            row_height = max(left_height, right_height) + 2 * PADDING + label_height
            top = self.y + PADDING
            label = f'Работа {number}' + (' / продолжение' if continued else '')
            for x, width in ((MARGIN, LEFT_WIDTH), (MARGIN + LEFT_WIDTH, RIGHT_WIDTH)):
                self._paragraph(_p(label, size=8, color=MUTED), x + PADDING, top, width - 2 * PADDING)
            self._draw_cell(left, MARGIN + PADDING, top + label_height)
            self._draw_cell(right, MARGIN + LEFT_WIDTH + PADDING, top + label_height)
            if workers:
                worker_pages.add(self.page)
            if description:
                description_pages.add(self.page)
            workers, description = remaining_workers, remaining_description
            self.y += row_height
            self._line(self.y)
            if workers or description:
                self._new_page(repeat_group=True)
                continued = True

    def render_card(self, card, number):
        self.card = card
        self.card_number = number
        self.card_page = 0
        self.shift = self.group = None
        self._new_page()
        for shift in card.get('shifts', []):
            groups = [group for group in shift.get('groups', []) if any(work.get('workers') for work in group.get('works', []))]
            if not groups:
                continue
            self.shift = shift
            self.shift_count = sum(len(work.get('workers', [])) for group in groups for work in group.get('works', []))
            self.group = groups[0]
            if self.y + self._group_header_height() + 70 > BOTTOM:
                self._new_page()
            self._shift_header()
            for index, group in enumerate(groups):
                self.group = group
                if index and self.y + self._group_header_height() + 48 > BOTTOM:
                    self._new_page()
                    self._shift_header()
                elif index:
                    self.y += 7
                self._group_header()
                for work_number, work in enumerate((work for work in group.get('works', []) if work.get('workers')), 1):
                    self._work(work, work_number)
            self.y += 8


def build_position_cards_pdf(cards, *, day_label, filter_summary='') -> bytes:
    """Return A4 portrait cards, starting every position on a separate page.

    Counts are assignment counts for each shift. The caller provides filtered,
    authorized data; this renderer neither queries nor changes application data.
    Empty shifts are omitted. Missing values remain explicit and unembellished.
    """
    stream = BytesIO()
    renderer = _CardsRenderer(stream, day_label=day_label, filter_summary=filter_summary)
    for number, card in enumerate(cards, 1):
        renderer.render_card(card, number)
    renderer.canvas.save()
    return stream.getvalue()
