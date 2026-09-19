"""Monthly plans preserve decimals, source control totals and reviewed versions."""
from io import BytesIO
from pathlib import Path
from functools import wraps
import os
import unittest
from uuid import uuid4
from xml.sax.saxutils import escape
from zipfile import ZipFile

from flask import abort, g
from smg_parser import NODES, number, parse_workbook


def workbook(value='1.25', missing=False, title='Свод', duplicate=False,
             months=('сентябрь','октябрь','ноябрь'), scope='15'):
    rows = []
    for start in ([2, 103] if duplicate else [2]):
        headers=''.join(f'<c r="{col}{start}" t="inlineStr"><is><t>План\n{month}</t></is></c>' for col,month in zip(('Q','Y','AA'),months))
        rows.append(f'<row r="{start}"><c r="A{start}" t="inlineStr"><is><t>Наименование людских ресурсов ППС-{scope}</t></is></c>{headers}</row>')
        for i,n in enumerate(NODES):
            r = start+i+1
            # Blank cells remain blank, including absent formula cache values.
            numeric = '' if n['group'] or (missing and not n['group']) else f'<v>{value}</v>'
            cells=''.join(f'<c r="{col}{r}">{numeric}</c>' for col in ('Q','Y','AA')[:len(months)])
            rows.append(f'<row r="{r}"><c r="A{r}" t="inlineStr"><is><t>{escape(n["label"])}</t></is></c>{cells}</row>')
    output=BytesIO()
    with ZipFile(output,'w') as z:
        z.writestr('xl/workbook.xml',f'<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{title}" r:id="s1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="s1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet><sheetData>'+''.join(rows)+'</sheetData></worksheet>')
    return output.getvalue()


class SmgParserTest(unittest.TestCase):
    def test_decimal_blank_and_hierarchy(self):
        result=parse_workbook(workbook(),'ППС-15.xlsx',2026)['blocks'][0]
        self.assertEqual(result['scope'],'ППС-15')
        month=result['months'][0]
        self.assertEqual(month['period'],'2026-09-01')
        self.assertFalse(month['errors']);self.assertFalse(month['warnings'])
        self.assertEqual(sum(v=='1.25' for v in month['values'].values()),75)
        self.assertEqual(sum(v is None for v in month['values'].values()),23)

    def test_empty_and_invalid_values_block_import(self):
        for content in (workbook(missing=True),workbook('-1'),workbook('NaN'),workbook('abc')):
            self.assertTrue(parse_workbook(content,'x.xlsx',2026)['blocks'][0]['months'][0]['errors'])
        self.assertEqual(number('0'),'0');self.assertIsNone(number(None))

    def test_bad_file_year_and_sheet(self):
        for content,year in [(b'bad',2026),(workbook(title='Явка'),2026),(workbook(),1900)]:
            with self.assertRaises(ValueError):parse_workbook(content,'x.xlsx',year)

    def test_duplicate_scope_requires_review(self):
        result=parse_workbook(workbook(duplicate=True),'x.xlsx',2026)
        self.assertTrue(all(b['duplicate'] for b in result['blocks']))

    def test_three_consecutive_months_and_year_rollover(self):
        result=parse_workbook(workbook(months=('декабрь','январь','февраль')),'x.xlsx',2026)['blocks'][0]
        self.assertFalse(result['errors'])
        self.assertEqual([m['period'] for m in result['months']],['2026-12-01','2027-01-01','2027-02-01'])
        for months in [('сентябрь',),('сентябрь','ноябрь','декабрь'),('ноябрь','январь','февраль')]:
            self.assertTrue(parse_workbook(workbook(months=months),'x.xlsx',2026)['blocks'][0]['errors'])


@unittest.skipUnless(os.getenv('CREW_POSTGRES_TEST_ENV'),'Select isolated PostgreSQL explicitly.')
class SmgPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_workforce_api import WorkforceApiTest
        WorkforceApiTest.setUpClass()

    def setUp(self):
        from test_workforce_api import WorkforceApiTest
        from smg_api import register_smg_routes
        self.f=WorkforceApiTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.app.config['SECRET_KEY']='isolated-test-key'
        def roles_required(*roles):
            def decorator(fn):
                @wraps(fn)
                def wrapper(*a,**kw):
                    if g.user['role'] not in roles:abort(403)
                    return fn(*a,**kw)
                return wrapper
            return decorator
        register_smg_routes(self.f.app,lambda:self.f.db,roles_required)

    def preview(self,role='admin',content=None):
        return self.f.client.post('/api/smg/preview',data={'file':(BytesIO(content or workbook()),'ППС-15.xlsx'),'year':'2099'},headers={'Test-Role':role})

    def payload(self,preview):
        return dict(token=preview.json['token'],block=0,acknowledge=True,request_key=str(uuid4()))

    def test_preview_roles_and_no_writes(self):
        count=self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0]
        self.assertEqual(self.preview().status_code,200)
        self.assertEqual(count,self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0])
        self.assertEqual(self.preview('hr_viewer').status_code,403)
        self.assertEqual(self.f.client.get('/api/smg/plans',headers={'Test-Role':'hr_viewer'}).status_code,200)
        self.assertEqual(self.f.client.get('/api/smg/plans',headers={'Test-Role':'rotation'}).status_code,403)

    def test_versions_decimal_idempotence_and_stale_preview(self):
        daily=self.f.db.native('SELECT count(*) FROM daily_staffing_plans').fetchone()[0]
        bindings=self.f.db.native('SELECT count(*) FROM employee_gdlr').fetchone()[0]
        body=self.payload(self.preview());stale=dict(body,request_key=str(uuid4()))
        first=self.f.client.post('/api/smg/apply',json=body)
        self.assertEqual(first.status_code,201,first.data)
        again=self.f.client.post('/api/smg/apply',json=body)
        self.assertEqual(again.json['id'],first.json['id']);self.assertTrue(again.json['repeated'])
        self.assertEqual(self.f.client.post('/api/smg/apply',json=stale).status_code,409)
        second=self.f.client.post('/api/smg/apply',json=self.payload(self.preview(content=workbook('2.75'))))
        self.assertEqual(second.status_code,201,second.data)
        self.assertEqual(second.json['version'],first.json['version']+1)
        old=self.f.client.get('/api/smg/plans?version='+first.json['id']).json
        self.assertEqual(len(old['months']),3)
        for month in old['months']:
            self.assertEqual(set(month['values'].values()),{'1.25'})
        self.assertEqual(len(old['hierarchy']),98)
        self.assertEqual(daily,self.f.db.native('SELECT count(*) FROM daily_staffing_plans').fetchone()[0])
        self.assertEqual(bindings,self.f.db.native('SELECT count(*) FROM employee_gdlr').fetchone()[0])

    def test_overlapping_horizons_are_atomic_and_history_is_a_snapshot(self):
        body=self.payload(self.preview());first=self.f.client.post('/api/smg/apply',json=body)
        stale=self.payload(self.preview())
        later=self.preview(content=workbook('3.5',months=('октябрь','ноябрь','декабрь')))
        response=self.f.client.post('/api/smg/apply',json=self.payload(later))
        self.assertEqual(response.status_code,201,response.data)
        before=self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0]
        self.assertEqual(self.f.client.post('/api/smg/apply',json=stale).status_code,409)
        self.assertEqual(before,self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0])
        old=self.f.client.get('/api/smg/plans?version='+first.json['id']).json
        self.assertEqual([m['period'] for m in old['months']],['2099-09-01','2099-10-01','2099-11-01'])
        self.assertTrue(all(set(m['values'].values())=={'1.25'} for m in old['months']))
        self.assertEqual(len([v for v in old['versions'] if v['id'] in (first.json['id'],response.json['id'])]),2)

    def test_whole_pps_only_and_incomplete_horizon_rejected(self):
        self.assertEqual(self.preview(content=workbook(scope='15.1')).status_code,400)
        before=self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0]
        body=self.payload(self.preview(content=workbook(months=('сентябрь',))))
        self.assertEqual(self.f.client.post('/api/smg/apply',json=body).status_code,400)
        self.assertEqual(before,self.f.db.native('SELECT count(*) FROM smg_plan_versions').fetchone()[0])

    def test_quick_filters_use_latest_month_without_double_counting_editions(self):
        first=self.f.client.post('/api/smg/apply',json=self.payload(self.preview()))
        self.assertEqual(first.status_code,201)
        second=self.f.client.post('/api/smg/apply',json=self.payload(self.preview(
            content=workbook('3.5',months=('октябрь','ноябрь','декабрь')))))
        self.assertEqual(second.status_code,201)
        response=self.f.client.get('/api/smg/plans?latest=1',headers={'Test-Role':'hr_viewer'})
        self.assertEqual(response.status_code,200)
        months=next(e['months'] for e in response.json['editions'] if e['scope']=='ППС-15')
        months=[m for m in months if m['period'].startswith('2099-')]
        self.assertEqual([m['period'] for m in months],['2099-09-01','2099-10-01','2099-11-01','2099-12-01'])
        self.assertEqual([set(m['values'].values()) for m in months],[{'1.25'},{'3.5'},{'3.5'},{'3.5'}])
        self.assertEqual(self.f.client.get('/api/smg/plans?latest=1',headers={'Test-Role':'rotation'}).status_code,403)

    def test_invalid_preview_token_and_revoked_actor(self):
        body=self.payload(self.preview(content=workbook('-1')))
        self.assertEqual(self.f.client.post('/api/smg/apply',json=body).status_code,400)
        body=self.payload(self.preview());body['token']+='x'
        self.assertEqual(self.f.client.post('/api/smg/apply',json=body).status_code,400)
        body=self.payload(self.preview())
        self.f.db.native('UPDATE users SET active=0 WHERE id=%s',(self.f.users['admin']['id'],))
        self.assertEqual(self.f.client.post('/api/smg/apply',json=body).status_code,403)
