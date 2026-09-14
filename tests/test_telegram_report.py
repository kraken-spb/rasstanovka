import hashlib
import json
import time
import unittest
from datetime import date
from unittest.mock import patch

import test_placement_report as report_fixture
from telegram_api import config_path, read_config, write_config, telegram_call, TelegramError
from telegram_report_bot import parse_report, process_update


TOKEN='123456789:'+('x'*35)
CONFIG={'token':TOKEN,'username':'placement_test_bot','bot_id':123456789}


class TelegramReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): report_fixture.PlacementReportTest.setUpClass()
    @classmethod
    def tearDownClass(cls): report_fixture.PlacementReportTest.tearDownClass()
    def setUp(self):
        self.fixture=report_fixture.PlacementReportTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.module=self.fixture.module
        self.user=self.fixture.foreman
        self.client=self.fixture.client(self.user)
        with self.client.session_transaction() as session:session['csrf_token']='telegram-test'
        self.headers={'X-CSRF-Token':'telegram-test'}
        self.calls=[]
        with self.module.app.app_context():write_config(self.module.get_db(),TOKEN,CONFIG['username'])

    def call(self,token,method,payload=None,document=None):
        self.assertEqual(token,TOKEN)
        self.calls.append((method,payload,document))
        return {'message_id':1}

    def update(self,text,number=1,telegram_id=500,chat_type='private'):
        return {'update_id':number,'message':{'from':{'id':telegram_id},'chat':{'id':telegram_id,'type':chat_type},'text':text}}

    def handle(self,update,render=None):
        with self.module.app.app_context():
            db=self.module.get_db()
            process_update(db,CONFIG,update,call=self.call,**({'render':render} if render else {}))

    def link(self):
        response=self.client.post('/api/telegram/link',json={},headers=self.headers)
        self.assertEqual(response.status_code,200)
        token=response.get_json()['url'].split('link_')[1]
        self.handle(self.update('/start link_'+token,0))
        self.calls.clear()
        return token

    def test_expiring_single_use_link_and_csrf(self):
        self.assertEqual(self.client.post('/api/telegram/link',json={}).status_code,403)
        self.assertEqual(self.fixture.client().post('/api/telegram/link',json={},headers=self.headers).status_code,403)
        token=self.link()
        self.assertTrue(self.client.get('/api/telegram').get_json()['linked'])
        self.handle(self.update('/start link_'+token,1,telegram_id=501))
        self.assertIn('недействительна',self.calls[-1][1]['text'])
        with self.module.app.app_context():
            db=self.module.get_db()
            self.assertEqual(db.execute('SELECT telegram_user_id FROM telegram_links').fetchone()[0],500)
            expired='e'*32
            db.execute('INSERT INTO telegram_link_codes VALUES (?,?,?,?)',(hashlib.sha256(expired.encode()).hexdigest(),self.user,CONFIG['bot_id'],1));db.commit()
        self.handle(self.update('/start link_'+expired,2,telegram_id=501))
        self.assertIn('недействительна',self.calls[-1][1]['text'])

    def test_private_only_unlinked_denied_and_duplicate_updates_not_resent(self):
        self.handle(self.update('/report 13.09.2026',1))
        self.assertEqual([c[0] for c in self.calls],['sendMessage'])
        self.calls.clear();self.handle(self.update('/report',2,chat_type='group'));self.assertFalse(self.calls)
        self.link()
        update=self.update('/report 13.09.2026',3)
        self.handle(update);self.handle(update)
        self.assertEqual([c[0] for c in self.calls],['sendDocument'])
        self.assertTrue(self.calls[0][2].startswith(b'%PDF-'))
        self.assertEqual(self.calls[0][1]['chat_id'],500)
        self.assertIn('Всего: 3',self.calls[0][1]['caption'])
        self.assertIn('Неявка: 1',self.calls[0][1]['caption'])

    def test_filters_current_smu_permissions_and_full_pdf(self):
        self.link();captured=[]
        def render(data,include_people):captured.append((data,include_people));return b'%PDF-fixture'
        self.handle(self.update('/report_full 13.09.2026 ППС15 Электромонтажники'),render)
        data,full=captured[0]
        self.assertTrue(full);self.assertEqual(data['totals'],{'total':3,'assigned':1,'unassigned':1,'absent':1})
        with self.module.app.app_context():
            db=self.module.get_db()
            db.execute("INSERT INTO user_smu_access(user_id,mode,departments_json,edit_token,updated_by,updated_at) VALUES (?,'selected','[\"СМУ 19\"]','t',?,'now')",(self.user,self.fixture.admin))
            db.execute('DELETE FROM telegram_requests');db.commit()
        captured.clear();self.handle(self.update('/report 13.09.2026',2),render)
        self.assertEqual(captured[0][0]['totals']['total'],2)
        self.assertFalse(captured[0][1])

    def test_revocation_before_delivery_and_unlink_stop_reports(self):
        self.link()
        def revoke(data,include_people):
            db=self.module.get_db();db.execute('UPDATE users SET active=0 WHERE id=?',(self.user,));db.commit();return b'%PDF-test'
        self.handle(self.update('/report 13.09.2026'),revoke)
        self.assertFalse(any(call[0]=='sendDocument' for call in self.calls))
        self.calls.clear();self.handle(self.update('/report',2))
        self.assertIn('Сначала привяжите',self.calls[-1][1]['text'])
        with self.module.app.app_context():
            db=self.module.get_db();db.execute('UPDATE users SET active=1 WHERE id=?',(self.user,));db.commit()
        self.client.delete('/api/telegram/link',headers=self.headers)
        self.assertFalse(self.client.get('/api/telegram').get_json()['linked'])

    def test_config_is_super_admin_only_and_token_never_returned(self):
        self.assertEqual(self.client.post('/api/telegram/config',json={'token':TOKEN},headers=self.headers).status_code,403)
        with self.module.app.app_context():
            db=self.module.get_db();db.execute("UPDATE users SET role='super_admin' WHERE id=?",(self.user,));db.commit()
        results=[{'is_bot':True,'id':CONFIG['bot_id'],'username':CONFIG['username']},{'url':''}]
        with patch('telegram_api.telegram_call',side_effect=results):
            response=self.client.post('/api/telegram/config',json={'token':TOKEN},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertNotIn(TOKEN,response.get_data(as_text=True)+self.client.get('/api/telegram').get_data(as_text=True))
        with self.module.app.app_context():
            db=self.module.get_db();self.assertEqual(read_config(db),CONFIG)
            self.assertNotIn(TOKEN,'\n'.join(db.iterdump()))
        with patch('telegram_api.telegram_call',side_effect=[results[0],{'url':'https://another.example/hook'}]):
            self.assertEqual(self.client.post('/api/telegram/config',json={'token':TOKEN},headers=self.headers).status_code,400)

    def test_command_dates_and_invalid_inputs(self):
        today=date(2026,9,13)
        self.assertEqual(parse_report('Отчёт за вчера',today),('2026-09-12',None,None,False))
        self.assertEqual(parse_report('/report_full 2026-09-13 без_ппс без_категории',today),('2026-09-13','','',True))
        with self.assertRaises(ValueError):parse_report('/report 31.02.2026',today)
        with self.assertRaises(ValueError):parse_report('/report ППС155',today)

    def test_transport_error_hides_secret_url(self):
        from urllib.error import URLError
        with patch('telegram_api.urllib.request.urlopen',side_effect=URLError('https://api.telegram.org/bot'+TOKEN)):
            with self.assertRaises(TelegramError) as caught:telegram_call(TOKEN,'getMe')
        self.assertNotIn(TOKEN,str(caught.exception))

    def test_worker_checkpoint_survives_repoll_and_pauses_without_token(self):
        from telegram_worker import poll_once
        self.link()
        update=self.update('/report 13.09.2026',44)
        offsets=[]
        def transport(token,method,payload=None,document=None):
            if method=='getUpdates':offsets.append(payload['offset']);return [update]
            return self.call(token,method,payload,document)
        self.assertTrue(poll_once(transport));self.assertTrue(poll_once(transport))
        self.assertEqual(offsets,[0,45])
        self.assertEqual(sum(c[0]=='sendDocument' for c in self.calls),1)
        with self.module.app.app_context():config_path(self.module.get_db()).unlink()
        self.assertFalse(poll_once(transport))


if __name__=='__main__':unittest.main()
