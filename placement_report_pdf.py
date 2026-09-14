from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Paginated PDF with an embedded Cyrillic font and the same report data as the UI."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle, PageBreak


pdfmetrics.registerFont(TTFont('PlacementSans', str(Path(__file__).parent / 'fonts' / 'DejaVuSans.ttf')))


def build_pdf(data, include_people=True):
    stream = BytesIO()
    day = '.'.join(reversed(data['date'].split('-')))
    style = ParagraphStyle('body', fontName='PlacementSans', fontSize=8, leading=11, spaceAfter=3)
    title = ParagraphStyle('title', parent=style, fontSize=18, leading=23, spaceAfter=8)
    heading = ParagraphStyle('heading', parent=style, fontSize=11, leading=15, spaceBefore=12, spaceAfter=6, keepWithNext=True)
    small = ParagraphStyle('note', parent=style, fontSize=7, leading=10, textColor=colors.HexColor('#52666a'))
    number = ParagraphStyle('number', parent=style, alignment=TA_CENTER)
    def p(text, fmt=style):
        return Paragraph(escape(str(text if text is not None else '')).replace('\n', '<br/>'), fmt)
    page_size = landscape(A4)
    width = page_size[0] - 28 * mm
    document = SimpleDocTemplate(stream, pagesize=page_size, rightMargin=14*mm, leftMargin=14*mm,
                                topMargin=14*mm, bottomMargin=16*mm,
                                title='Отчёт по расстановке за ' + day, author='Расстановка')
    def table(rows, widths, repeat_rows=1, commands=()):
        result = LongTable(rows, colWidths=widths, repeatRows=repeat_rows, hAlign='LEFT', splitInRow=1)
        result.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,repeat_rows-1), colors.HexColor('#e4f3ee')),
            ('ROWBACKGROUNDS', (0,repeat_rows), (-1,-1), [colors.white, colors.HexColor('#f5f8f7')]),
            ('LINEBELOW', (0,repeat_rows-1), (-1,repeat_rows-1), .6, colors.HexColor('#14796e')),
            ('LINEBELOW', (0,repeat_rows), (-1,-1), .25, colors.HexColor('#d9e3e0')),
            ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6), ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ] + list(commands)))
        return result
    filters = data['filters']
    pps = filter_label(filters['pps'],'Все ППС','Без ППС')
    category = filter_label(filters['category'],'Все категории ГДЛР','Без категории')
    totals = data['totals']
    story = [p('Отчёт по расстановке', title), p(day + ' · ' + pps + ' · ' + category),
             p(f"Всего: {totals['total']} · Расставлены: {totals['assigned']} · Не расставлены: {totals['unassigned']} · Неявка: {totals['absent']}", heading),
             p(data['note'], small), Spacer(1, 5*mm)]
    labels = {'assigned': 'Расставлены', 'unassigned': 'Не расставлены', 'absent': 'Неявка'}
    companies = data['contractors']
    batches = [companies[i:i+5] for i in range(0, len(companies), 5)] or [[]]
    for batch_index, batch in enumerate(batches):
        if batch_index:
            story.extend([PageBreak(), p('Отчёт по расстановке · ' + day, title)])
        story.append(p('Компании-подрядчики по столбцам' + (f' · часть {batch_index + 1} из {len(batches)}' if len(batches) > 1 else ''), ParagraphStyle('matrix-heading', parent=heading, keepWithNext=False)))
        summary = [[p(v) for v in ['ППС', 'Категория ГДЛР', 'Статус', *[c or 'Подрядчик не указан' for c in batch], 'Общий итог']]]
        for group in data['groups']:
            for index, (key, label) in enumerate({**labels, 'total': 'Всего'}.items()):
                summary.append([p(group['pps'] or 'Без ППС'),
                                p(group['category'] or 'Без категории'), p(label),
                                *[p(group['companies'].get(c, {}).get(key, 0), number) for c in batch], p(group[key], number)])
        summary.append([p('Итого'), p(''), p('Все сотрудники'),
                        *[p(sum(g['companies'].get(c, {}).get('total', 0) for g in data['groups']), number) for c in batch], p(totals['total'], number)])
        story.append(table(summary, [width*.12, width*.24, width*.18] + [width*.46/(len(batch)+1)]*(len(batch)+1)))
    for group in data['groups'] if include_people else []:
        story.append(Spacer(1, 5*mm))
        details = [[p((group['pps'] or 'Без ППС') + ' / ' + (group['category'] or 'Без категории')), '', '', ''],
                   [p(v) for v in ('ФИО / профессия','Таб. №','Статус','Назначение')]]
        for person in sorted(group['people'], key=lambda r: (list(labels).index(r['status']), r['full_name'].casefold(), r['id'])):
            placements = '\n'.join(a['shift'] + ': ' + a['object_name'] + ' / ' + a['subobject_name'] for a in person['assignments'])
            status = person['attendance_status'] if person['status'] == 'absent' else 'Расставлен' if person['assigned'] else 'Не расставлен'
            details.append([p(person['full_name'] + '\n' + person['profession'] + '\n' + (person['contractor'] or 'Подрядчик не указан')), p(person['personnel_no']),
                            p(status), p(placements or 'Назначения нет')])
        story.append(table(details, [width*.31,width*.13,width*.18,width*.38], repeat_rows=2, commands=[('SPAN',(0,0),(-1,0))]))
    if not data['groups']:
        story.append(p('По выбранным условиям сотрудников нет.', heading))
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('PlacementSans', 8)
        canvas.setFillColor(colors.HexColor('#52666a'))
        canvas.drawString(14*mm, 9*mm, 'Расстановка · ' + day)
        canvas.drawRightString(page_size[0]-14*mm, 9*mm, 'Страница ' + str(doc.page))
        canvas.restoreState()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
