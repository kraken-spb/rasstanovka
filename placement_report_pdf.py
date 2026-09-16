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
    selected_authors = filter_values(filters.get('author'))
    author_labels = data.get('options', {}).get('author_labels', {})
    author_caption = ' · Кто расставил: ' + ', '.join(author_labels.get(value, 'Автор не определён' if value == 'unknown' else 'Пользователь №' + value) for value in selected_authors) if selected_authors else ''
    totals = data['totals']
    story = [p('Отчёт по расстановке', title), p(day + ' · ' + pps + ' · ' + category + author_caption),
             p(f"Всего: {totals['total']} · Расставлены: {totals['assigned']} · Не расставлены: {totals['unassigned']} · Неявка: {totals['absent']}", heading),
             p(data['note'], small), Spacer(1, 5*mm)]
    labels = {'assigned': 'Расставлены', 'unassigned': 'Не расставлены', 'absent': 'Неявка'}
    trend = data.get('dynamics')
    if trend:
        metric = trend.get('metric', 'assigned')
        metric_label = {**labels, 'total': 'Всего сотрудников'}[metric]
        first = '.'.join(reversed(trend['start'].split('-')))
        period = first + ' - ' + day
        story.insert(2, p('Период: ' + period + ' · Показатель: ' + metric_label, heading))
        story.insert(3, p('Карточки итогов приведены на конец периода. Списки ФИО, если включены, относятся к выбранному показателю на ' + day + '.', small))
        comparison_day = '.'.join(reversed(trend['comparison_date'].split('-')))
        story.insert(4, p('Изменение за день: ' + day + ' минус ' + comparison_day + '.', small))
        hierarchy = []
        for group in trend['categories']:
            hierarchy.append((group['category'] or 'Без категории', group['counts'], group['changes'], True))
            if trend.get('pps_details'):
                hierarchy.extend(('    ' + (child['pps'] or 'Без ППС'), child['counts'], child['changes'], False) for child in group['pps'])
        hierarchy.append(('Итого', trend['totals'], trend['changes'], True))
        dates = trend['dates']
        for offset in range(0, len(dates), 7):
            if offset:
                story.extend([PageBreak(), p('Динамика расстановки · ' + period, title)])
            batch = dates[offset:offset+7]
            story.append(p(metric_label + ' по категориям ГДЛР' + (' / ППС' if trend.get('pps_details') else ''), heading))
            summary = [[p('Категория ГДЛР / ППС'), *[p('.'.join(reversed(value.split('-')[1:])), number) for value in batch], p('Изменение за день, чел.', number)]]
            commands = []
            for name, counts, changes, parent in hierarchy:
                delta = changes[metric]
                summary.append([p(name), *[p(value, number) for value in counts[metric][offset:offset+7]], p(f'{delta:+d}' if delta else '0', number)])
                if parent:
                    commands.append(('BACKGROUND', (0, len(summary)-1), (-1, len(summary)-1), colors.HexColor('#eaf3ef')))
            story.append(table(summary, [width*.32] + [width*.56/len(batch)]*len(batch) + [width*.12], commands=commands))
        if include_people:
            story.extend([Spacer(1, 5*mm), p('Списки сотрудников на ' + day, heading)])
    companies = data['contractors']
    batches = [] if trend else [companies[i:i+5] for i in range(0, len(companies), 5)] or [[]]
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
    detail_groups = sorted(data['groups'], key=lambda group: (group['category'].casefold(), group['pps'])) if trend else data['groups']
    if trend:
        detail_groups = [{**group, 'people': [person for person in group['people'] if metric == 'total' or person['status'] == metric]} for group in detail_groups]
        detail_groups = [group for group in detail_groups if group['people']]
        if include_people and not detail_groups:
            story.append(p('На конец периода по выбранному показателю сотрудников нет.', small))
    for group in detail_groups if include_people else []:
        story.append(Spacer(1, 5*mm))
        details = [[p((group['pps'] or 'Без ППС') + ' / ' + (group['category'] or 'Без категории')), '', '', ''],
                   [p(v) for v in ('ФИО / профессия','Таб. №','Статус','Назначение')]]
        for person in sorted(group['people'], key=lambda r: (list(labels).index(r['status']), r['full_name'].casefold(), r['id'])):
            placements = '\n'.join(a['shift'] + ': ' + a['object_name'] + ' / ' + a['subobject_name'] for a in person['assignments'])
            status = person['attendance_status'] if person['status'] == 'absent' else 'Расставлен' if person['assigned'] else 'Не расставлен'
            details.append([p(person['full_name'] + '\n' + person['profession'] + '\n' + (person['contractor'] or 'Подрядчик не указан')), p(person['personnel_no']),
                            p(status), p(placements or 'Назначения нет')])
        story.append(table(details, [width*.31,width*.13,width*.18,width*.38], repeat_rows=2, commands=[('SPAN',(0,0),(-1,0))]))
    if not data['groups'] and (not trend or not trend['categories']):
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
