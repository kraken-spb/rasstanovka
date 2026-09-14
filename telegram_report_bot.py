"""Telegram commands reuse the website report and the linked user's current rights."""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import g

from placement_report import report_data
from placement_report_pdf import build_pdf
from telegram_api import read_config, telegram_call, TelegramError


HELP = ('Отчёт по расстановке: расставлены, не расставлены и отдельно неявки.\n\n'
        '/report — сводка за сегодня\n'
        '/report 13.09.2026 — за указанную дату\n'
        '/report 13.09.2026 ППС15 Электромонтажник — с фильтрами\n'
        '/report_full — PDF со списками ФИО\n'
        '/categories — категории ГДЛР\n'
        '/unlink — отключить привязку\n\n'
        'Можно указать дату ГГГГ-ММ-ДД. Для пустых значений: без_ппс и без_категории. '
        'Даты «сегодня» и «вчера» считаются по Москве.')
KEYBOARD = {'keyboard':[['Отчёт за сегодня','Отчёт за вчера'],['Отчёт с ФИО','Помощь']], 'resize_keyboard':True}


def moscow_today():
    return datetime.now(timezone(timedelta(hours=3))).date()


def parse_report(text, today=None):
    today = today or moscow_today()
    text = text.strip()
    shortcuts = {'Отчёт за сегодня':'/report','Отчёт за вчера':'/report вчера','Отчёт с ФИО':'/report_full'}
    words = shortcuts.get(text,text).split()
    if not words or words[0].split('@')[0] not in ('/report','/report_full'):
        raise ValueError('Используйте /report или кнопку «Отчёт за сегодня».')
    full = words.pop(0).split('@')[0]=='/report_full'
    day = today
    if words and words[0].casefold() in ('сегодня','вчера'):
        day=today-timedelta(days=int(words.pop(0).casefold()=='вчера'))
    elif words and re.match(r'^\d',words[0]):
        raw=words.pop(0)
        try:
            day=datetime.strptime(raw,'%d.%m.%Y' if '.' in raw else '%Y-%m-%d').date()
        except ValueError:
            raise ValueError('Укажите существующую дату: 13.09.2026 или 2026-09-13.') from None
    pps = None
    if words and words[0].casefold() in ('ппс15','ппс19','без_ппс','все_ппс'):
        pps={'ппс15':'ППС15','ппс19':'ППС19','без_ппс':'','все_ппс':None}[words.pop(0).casefold()]
    elif words and words[0].casefold().startswith('ппс'):
        raise ValueError('Укажите ППС15, ППС19, без_ппс или все_ппс.')
    category = ' '.join(words) if words else None
    if category is not None and category.casefold() in ('без_категории','все_категории'):
        category='' if category.casefold()=='без_категории' else None
    return day.isoformat(), pps, category, full


def linked_user(db, config, telegram_id):
    return db.execute('''SELECT u.* FROM telegram_links t JOIN users u ON u.id=t.user_id
        WHERE t.bot_id=? AND t.telegram_user_id=? AND t.chat_id=? AND u.active=1
        AND u.role IN ('super_admin','admin','foreman','viewer')''',
        (config['bot_id'],telegram_id,telegram_id)).fetchone()


def access_stamp(db, user_id):
    user=db.execute('SELECT id,role,active FROM users WHERE id=?',(user_id,)).fetchone()
    scope=db.execute('SELECT * FROM user_smu_access WHERE user_id=?',(user_id,)).fetchone()
    link=db.execute('SELECT * FROM telegram_links WHERE user_id=?',(user_id,)).fetchone()
    return [tuple(row) if row else None for row in (user,scope,link)]


def consume_link(db, config, telegram_id, token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}',token): return False
    digest=hashlib.sha256(token.encode()).hexdigest()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            code=db.execute('''SELECT c.* FROM telegram_link_codes c JOIN users u ON u.id=c.user_id
                WHERE c.token_hash=? AND c.bot_id=? AND c.expires_at>? AND u.active=1''',
                (digest,config['bot_id'],int(time.time()))).fetchone()
            if not code: return False
            db.execute('''INSERT INTO telegram_links(user_id,bot_id,telegram_user_id,chat_id,linked_at)
                VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET bot_id=excluded.bot_id,
                telegram_user_id=excluded.telegram_user_id,chat_id=excluded.chat_id,linked_at=excluded.linked_at''',
                (code['user_id'],config['bot_id'],telegram_id,telegram_id,int(time.time())))
            db.execute('DELETE FROM telegram_link_codes WHERE token_hash=?',(digest,))
    except sqlite3.IntegrityError:
        return False
    return True


def process_update(db, config, update, call=telegram_call, render=build_pdf):
    """Claim updates before delivery; an uncertain send is never silently repeated."""
    update_id=update.get('update_id')
    if type(update_id) is not int: return
    with db:
        claimed=db.execute("INSERT OR IGNORE INTO telegram_requests VALUES (?,?,NULL,?,'received')",
                           (config['bot_id'],update_id,int(time.time()))).rowcount
    if not claimed: return
    message=update.get('message') or {}
    chat=message.get('chat') or {}; sender=message.get('from') or {}
    telegram_id=sender.get('id'); text=message.get('text','')
    if (chat.get('type')!='private' or type(telegram_id) is not int or telegram_id<=0 or sender.get('is_bot')
            or chat.get('id')!=telegram_id or not isinstance(text,str) or len(text)>1024):
        with db: db.execute("UPDATE telegram_requests SET state='ignored' WHERE bot_id=? AND update_id=?",(config['bot_id'],update_id))
        return
    def say(value, keyboard=False):
        payload={'chat_id':telegram_id,'text':value}
        if keyboard: payload['reply_markup']=KEYBOARD
        return call(config['token'],'sendMessage',payload)
    try:
        command=text.split(maxsplit=1)[0].split('@')[0] if text.strip() else ''
        if command=='/start' and ' ' in text:
            argument=text.split(maxsplit=1)[1].strip()
            ok=argument.startswith('link_') and consume_link(db,config,telegram_id,argument[5:])
            say('Telegram привязан к вашей учётной записи сайта.\n\n'+HELP if ok else
                'Ссылка недействительна или истекла. Получите новую ссылку на сайте в отчёте по расстановке.',ok)
        else:
            user=linked_user(db,config,telegram_id)
            if not user:
                say('Сначала привяжите Telegram: войдите на report-bi.ru → Сводная таблица → Отчёт по расстановке → Telegram → Привязать мой Telegram.')
            elif command=='/unlink':
                with db: db.execute('DELETE FROM telegram_links WHERE user_id=?',(user['id'],))
                say('Привязка отключена. Получение отчётов закрыто до новой привязки на сайте.')
            elif command in ('/help','/start') or text=='Помощь':
                say(HELP,True)
            else:
                with db:
                    db.execute('UPDATE telegram_requests SET user_id=? WHERE bot_id=? AND update_id=?',(user['id'],config['bot_id'],update_id))
                recent=db.execute('''SELECT 1 FROM telegram_requests WHERE bot_id=? AND user_id=? AND update_id<>?
                    AND state='done' AND received_at>? LIMIT 1''',(config['bot_id'],user['id'],update_id,int(time.time())-5)).fetchone()
                if recent:
                    say('Подождите несколько секунд перед следующим запросом отчёта.')
                else:
                    g.user=user
                    db.execute('BEGIN')
                    stamp=access_stamp(db,user['id'])
                    if command=='/categories':
                        data=report_data(db,moscow_today().isoformat());db.rollback()
                        lines=['Категории ГДЛР:']+[c or 'без_категории' for c in data['options']['categories']]
                        chunks=['']
                        for line in lines:
                            if len(chunks[-1])+len(line)>3500: chunks.append('')
                            chunks[-1]+=line+'\n'
                        for chunk in chunks: say(chunk)
                    else:
                        try: day,pps,category,full=parse_report(text)
                        except ValueError as error:
                            db.rollback();say(str(error));return
                        data=report_data(db,day,pps=pps)
                        if category is not None:
                            matches=[c for c in data['options']['categories'] if c.casefold()==category.casefold()]
                            if len(matches)!=1:
                                db.rollback();say('Категория не найдена. Список доступен по команде /categories.');return
                            data=report_data(db,day,pps=pps,category=matches[0])
                        db.rollback()
                        pdf=render(data,include_people=full)
                        if len(pdf)>49*1024*1024:
                            say('PDF слишком большой. Уточните ППС или категорию либо запросите /report без списка ФИО.');return
                        if access_stamp(db,user['id'])!=stamp or read_config(db)!=config:
                            say('Права доступа или подключение бота изменились. Запросите отчёт заново.');return
                        totals=data['totals']
                        caption=('Отчёт по расстановке · '+'.'.join(reversed(day.split('-')))+'\n'+
                            (pps if pps else 'Без ППС' if pps=='' else 'Все ППС')+' · '+
                            (data['filters']['category'] or 'Без категории' if category is not None else 'Все категории ГДЛР')+'\n'+
                            f"Всего: {totals['total']} · Расставлены: {totals['assigned']} · Не расставлены: {totals['unassigned']} · Неявка: {totals['absent']}")
                        call(config['token'],'sendDocument',{'chat_id':telegram_id,'caption':caption},document=pdf)
        with db: db.execute("UPDATE telegram_requests SET state='done' WHERE bot_id=? AND update_id=?",(config['bot_id'],update_id))
    except Exception:
        db.rollback()
        with db: db.execute("UPDATE telegram_requests SET state='error' WHERE bot_id=? AND update_id=?",(config['bot_id'],update_id))
        try: say('Не удалось подтвердить отправку отчёта. Повторите запрос, если файл не получен.')
        except TelegramError: pass
        raise
