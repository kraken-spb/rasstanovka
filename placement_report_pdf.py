"""Paginated PDF with an embedded Cyrillic font and the same report data as the UI."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle


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
    width = A4[0] - 28 * mm
    document = SimpleDocTemplate(stream, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
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
    pps = 'Все ППС' if filters['pps'] is None else filters['pps'] or 'Без ППС'
    category = 'Все категории ГДЛР' if filters['category'] is None else filters['category'] or 'Без категории'
    totals = data['totals']
    story = [p('Отчёт по расстановке', title), p(day + ' · ' + pps + ' · ' + category),
             p(f"Всего: {totals['total']} · Расставлены: {totals['assigned']} · Не расставлены: {totals['unassigned']} · Неявка: {totals['absent']}", heading),
             p(data['note'], small), Spacer(1, 5*mm)]
    labels = {'assigned': 'Расставлены', 'unassigned': 'Не расставлены', 'absent': 'Неявка'}
    summary = [[p(v) for v in ('ППС','Категория ГДЛР','Статус','Человек')]]
    for group in data['groups']:
        for index, (key, label) in enumerate(labels.items()):
            summary.append([p((group['pps'] or 'Без ППС') if index == 0 else ''),
                            p((group['category'] or 'Без категории') if index == 0 else ''),
                            p(label), p(group[key], number)])
    summary.append([p('Итого'), p(''), p('Все сотрудники'), p(totals['total'], number)])
    story.append(table(summary, [width*.16,width*.40,width*.28,width*.16], commands=[
        ('NOSPLIT', (0,1+index*3), (-1,3+index*3)) for index in range(len(data['groups']))]))
    for group in data['groups'] if include_people else []:
        story.append(Spacer(1, 5*mm))
        details = [[p((group['pps'] or 'Без ППС') + ' / ' + (group['category'] or 'Без категории')), '', '', ''],
                   [p(v) for v in ('ФИО / профессия','Таб. №','Статус','Назначение')]]
        for person in sorted(group['people'], key=lambda r: (list(labels).index(r['status']), r['full_name'].casefold(), r['id'])):
            placements = '\n'.join(a['shift'] + ': ' + a['object_name'] + ' / ' + a['subobject_name'] for a in person['assignments'])
            status = person['attendance_status'] if person['status'] == 'absent' else 'Расставлен' if person['assigned'] else 'Не расставлен'
            details.append([p(person['full_name'] + '\n' + person['profession']), p(person['personnel_no']),
                            p(status), p(placements or 'Назначения нет')])
        story.append(table(details, [width*.31,width*.13,width*.18,width*.38], repeat_rows=2, commands=[('SPAN',(0,0),(-1,0))]))
    if not data['groups']:
        story.append(p('По выбранным условиям сотрудников нет.', heading))
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('PlacementSans', 8)
        canvas.setFillColor(colors.HexColor('#52666a'))
        canvas.drawString(14*mm, 9*mm, 'Расстановка · ' + day)
        canvas.drawRightString(A4[0]-14*mm, 9*mm, 'Страница ' + str(doc.page))
        canvas.restoreState()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
