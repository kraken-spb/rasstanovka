"""Private Telegram configuration and one-time website account linking."""
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import abort, g, jsonify, request


TOKEN_RE = re.compile(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}')


class TelegramError(Exception):
    """Sanitized errors must never contain the API URL with its bot token."""


def telegram_call(token, method, payload=None, document=None):
    if method not in {'getMe','getWebhookInfo','getUpdates','sendMessage','sendDocument','setMyCommands'}:
        raise ValueError('Unsupported Telegram method')
    if not TOKEN_RE.fullmatch(token):
        raise TelegramError('Некорректный токен Telegram-бота.')
    if document is None:
        body = json.dumps(payload or {}, ensure_ascii=False).encode()
        content_type = 'application/json'
    else:
        boundary = 'placement-' + secrets.token_hex(16)
        parts = []
        for key, value in (payload or {}).items():
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
        parts.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="placement-report.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode(),
                      document, f'\r\n--{boundary}--\r\n'.encode()])
        body = b''.join(parts); content_type = 'multipart/form-data; boundary=' + boundary
    call = urllib.request.Request('https://api.telegram.org/bot' + token + '/' + method,
                                  data=body, headers={'Content-Type':content_type})
    try:
        with urllib.request.urlopen(call, timeout=35 if method in ('getUpdates','sendDocument') else 12) as response:
            data = json.load(response)
        if not data.get('ok'):
            raise TelegramError('Telegram отклонил запрос. Проверьте токен и доступ бота к чату.')
        return data['result']
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        code = getattr(error, 'code', None)
        messages = {401:'Токен бота недействителен.',403:'Бот заблокирован или не имеет доступа к чату.',
                    409:'Этот бот уже подключён к другому обработчику.',429:'Telegram ограничил частоту запросов. Повторите позже.'}
        raise TelegramError(messages.get(code,'Telegram временно недоступен. Повторите запрос позже.')) from None


def config_path(db):
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    return Path(filename).parent / 'telegram.env'


def read_config(db):
    path = config_path(db)
    if not path.exists():
        return None
    values = dict(line.split('=',1) for line in path.read_text(encoding='utf-8').splitlines() if '=' in line)
    token, username = values.get('TELEGRAM_BOT_TOKEN',''), values.get('TELEGRAM_BOT_USERNAME','')
    if not TOKEN_RE.fullmatch(token) or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):
        raise TelegramError('Настройки Telegram повреждены. Подключите бота заново.')
    return {'token':token, 'username':username, 'bot_id':int(token.split(':')[0])}


def write_config(db, token, username):
    path = config_path(db)
    fd, name = tempfile.mkstemp(prefix='.telegram-',suffix='.env',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as file:
            file.write(f'TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_BOT_USERNAME={username}\n')
            file.flush(); os.fsync(file.fileno())
        os.chmod(name,0o600)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def migrate_telegram(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS telegram_links (
            user_id INTEGER PRIMARY KEY REFERENCES users(id), bot_id INTEGER NOT NULL,
            telegram_user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
            linked_at INTEGER NOT NULL, UNIQUE(bot_id,telegram_user_id));
        CREATE TABLE IF NOT EXISTS telegram_link_codes (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
            bot_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS telegram_state (
            bot_id INTEGER PRIMARY KEY, next_offset INTEGER NOT NULL DEFAULT 0,
            polled_at INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS telegram_requests (
            bot_id INTEGER NOT NULL, update_id INTEGER NOT NULL, user_id INTEGER,
            received_at INTEGER NOT NULL, state TEXT NOT NULL,
            PRIMARY KEY(bot_id,update_id));
    ''')


def register_telegram(app, get_db, roles_required):
    @app.get('/api/telegram')
    @roles_required('admin','foreman','viewer')
    def telegram_status():
        db = get_db()
        try: config = read_config(db)
        except TelegramError as error: return jsonify({'configured':False,'error':str(error),'can_configure':g.user['role']=='super_admin'})
        link = db.execute('SELECT * FROM telegram_links WHERE user_id=? AND bot_id=?',
                          (g.user['id'], config['bot_id'])).fetchone() if config else None
        state = db.execute('SELECT polled_at,error FROM telegram_state WHERE bot_id=?',(config['bot_id'],)).fetchone() if config else None
        response = jsonify({'configured':bool(config),'username':config['username'] if config else None,
            'linked':bool(link),'can_configure':g.user['role']=='super_admin',
            'online':bool(state and time.time()-state['polled_at']<90 and not state['error']),
            'error':state['error'] if state and g.user['role']=='super_admin' else ''})
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/telegram/config')
    @roles_required('super_admin')
    def configure_telegram():
        payload = request.get_json(silent=True)
        token = payload.get('token') if isinstance(payload,dict) else None
        if not isinstance(token,str) or not TOKEN_RE.fullmatch(token.strip()):
            abort(400,description='Вставьте токен, полученный от @BotFather.')
        token=token.strip()
        try:
            bot=telegram_call(token,'getMe')
            if not bot.get('is_bot') or bot.get('id')!=int(token.split(':')[0]) or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',bot.get('username','')):
                raise TelegramError('Telegram не подтвердил учётную запись бота.')
            if telegram_call(token,'getWebhookInfo').get('url'):
                raise TelegramError('Бот уже подключён через webhook. Используйте отдельного бота для отчётов.')
        except TelegramError as error:
            abort(400,description=str(error))
        db=get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor=db.execute('SELECT role,active FROM users WHERE id=?',(g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role']!='super_admin': abort(403)
            write_config(db,token,bot['username'])
            db.execute('DELETE FROM telegram_link_codes')
        return jsonify({'configured':True,'username':bot['username']})

    @app.post('/api/telegram/link')
    @roles_required('admin','foreman','viewer')
    def telegram_link_code():
        db=get_db()
        try: config=read_config(db)
        except TelegramError as error: abort(409,description=str(error))
        if not config: abort(409,description='Бот ещё не подключён. Обратитесь к супер-администратору.')
        token=secrets.token_urlsafe(24)
        with db:
            db.execute('DELETE FROM telegram_link_codes WHERE user_id=? OR expires_at<?',(g.user['id'],int(time.time())))
            db.execute('INSERT INTO telegram_link_codes VALUES (?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),g.user['id'],config['bot_id'],int(time.time())+600))
        response=jsonify({'url':f"https://t.me/{config['username']}?start=link_{token}",'expires_in':600})
        response.headers['Cache-Control']='no-store'
        return response

    @app.delete('/api/telegram/link')
    @roles_required('admin','foreman','viewer')
    def unlink_telegram():
        with get_db() as db:
            db.execute('DELETE FROM telegram_links WHERE user_id=?',(g.user['id'],))
            db.execute('DELETE FROM telegram_link_codes WHERE user_id=?',(g.user['id'],))
        return jsonify({'linked':False})
