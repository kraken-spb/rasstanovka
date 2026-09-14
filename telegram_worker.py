"""Single Telegram long-polling process; no public endpoint and no session/CSRF bypass."""
import logging
import sys
import time

from app import app, get_db
from telegram_api import config_path, read_config, telegram_call, TelegramError
from telegram_report_bot import process_update


def poll_once(call=telegram_call):
    with app.app_context():
        db=get_db();config=read_config(db)
        if not config: return False
        state=db.execute('SELECT next_offset FROM telegram_state WHERE bot_id=?',(config['bot_id'],)).fetchone()
        updates=call(config['token'],'getUpdates',{'offset':state['next_offset'] if state else 0,'timeout':20,'limit':20,'allowed_updates':['message']})
        with db:
            db.execute('INSERT INTO telegram_state(bot_id,polled_at) VALUES (?,?) ON CONFLICT(bot_id) DO UPDATE SET polled_at=excluded.polled_at,error=\'\'',(config['bot_id'],int(time.time())))
        for update in updates:
            if type(update.get('update_id')) is not int: continue
            if read_config(db)!=config: break
            try:
                process_update(db,config,update,call=call)
            except Exception as error:
                # Exceptions from urllib can contain the bot token. Log only safe class names.
                logging.warning('Telegram request failed: %s',type(error).__name__)
            finally:
                with db:
                    db.execute('UPDATE telegram_state SET next_offset=MAX(next_offset,?) WHERE bot_id=?',(update['update_id']+1,config['bot_id']))
        return True


def main():
    if '--healthcheck' in sys.argv:
        with app.app_context():
            db=get_db(); config=read_config(db)
            state=db.execute('SELECT polled_at FROM telegram_state WHERE bot_id=?',(config['bot_id'],)).fetchone() if config else None
            return 0 if not config or (state and time.time()-state['polled_at']<120) else 1
    import fcntl
    with app.app_context(): path=config_path(get_db()).with_name('.telegram-worker.lock')
    with path.open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            logging.error('Another Telegram worker is already running');return 1
        while True:
            try:
                if not poll_once():time.sleep(3)
            except Exception as error:
                safe=str(error) if isinstance(error,TelegramError) else 'Ошибка обработки Telegram. Проверьте подключение.'
                logging.warning('Telegram polling failed: %s',type(error).__name__)
                with app.app_context():
                    db=get_db()
                    try: config=read_config(db)
                    except TelegramError: config=None
                    if config:
                        with db:db.execute('INSERT INTO telegram_state(bot_id,error) VALUES (?,?) ON CONFLICT(bot_id) DO UPDATE SET error=excluded.error',(config['bot_id'],safe))
                time.sleep(10)


if __name__=='__main__':sys.exit(main())
