"""Closed report versions freeze identity, scope and facts independently of live operations."""
import json
import os
import sqlite3
import unittest
from datetime import date, timedelta
from uuid import uuid4
from pathlib import Path

from flask import Flask, abort

import test_placement_report as fixtures
from report_closure import capture_report, migrate_report_closure, register_report_closure
from user_smu_access import profile


class ReportClosureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PlacementReportTest.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.PlacementReportTest.tearDownClass()

    def setUp(self):
        self.f = fixtures.PlacementReportTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.module = self.f.module
        self.app = Flask(__name__)
        self.app.secret_key = 'closure-tests-at-least-thirty-two-characters'
        self.app.before_request(self.module.csrf_protection)
        self.app.teardown_appcontext(self.module.close_db)
        self.authorization_calls = []
        register_report_closure(self.app, self.module.get_db, self.module.roles_required,
                                self.module.utc_now, authorize_scope=self.authorize)
        with self.app.app_context():
            db = self.module.get_db()
            migrate_report_closure(db)
            db.commit()
        self.scope = {'kind': 'all'}
        self.day = '2026-09-13'
        self.url = '/api/placement-report/closure'
        self.admin = self.client(self.f.admin)

    def authorize(self, db, scope, *, write):
        """An explicit test policy: selected admins can access a complete selected SMU."""
        self.authorization_calls.append((scope, write))
        actor, rights = profile(db)
        if write and actor['role'] not in ('admin', 'super_admin'):
            abort(403)
        if actor['role'] == 'viewer' and not write:
            return
        if rights and rights['mode'] == 'selected':
            if scope['kind'] != 'department' or scope['department'] not in json.loads(rights['departments_json']):
                abort(403)
        elif rights is None and actor['role'] not in ('admin', 'super_admin'):
            abort(403)

    def client(self, user=None):
        client = self.app.test_client()
        if user:
            with client.session_transaction() as session:
                session['user_id'] = user
                session['csrf_token'] = 'closure-csrf'
        return client

    def get(self, scope=None, client=None, **args):
        scope = scope or self.scope
        return (client or self.admin).get(self.url, query_string={
            'date': self.day, 'scope': scope['kind'],
            **({'department': scope['department']} if 'department' in scope else {}), **args})

    def payload(self, state=None, **extra):
        state = state or self.get().get_json()
        return {'date': self.day, 'scope': state['scope'], 'expected_token': state['expected_token'],
                'idempotency_key': uuid4().hex, **extra}

    def post(self, action, payload=None, client=None):
        return (client or self.admin).post(self.url + '/' + action,
            json=payload or self.payload(), headers={'X-CSRF-Token': 'closure-csrf'})

    def close(self, scope=None):
        state = self.get(scope).get_json()
        response = self.post('check', self.payload(state))
        self.assertEqual(response.status_code, 200, response.data)
        state = self.get(scope).get_json()
        response = self.post('close', self.payload(state))
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def mutate(self, sql, parameters=()):
        with self.app.app_context():
            db = self.module.get_db()
            db.execute(sql, parameters)
            db.commit()

    def version(self, version_id, client=None, suffix=''):
        return (client or self.admin).get(f'{self.url}/versions/{version_id}{suffix}')

    def test_draft_checked_closed_and_totals_match_live_report_without_source_writes(self):
        with self.app.app_context():
            db = self.module.get_db()
            before = [tuple(r) for r in db.execute('SELECT * FROM assignments ORDER BY id')]
        draft = self.get().get_json()
        self.assertEqual(draft['status'], 'draft')
        self.assertFalse(draft['ready_to_close'])
        self.assertEqual(draft['current']['totals'], self.f.get().get_json()['totals'])
        self.assertEqual(self.post('close', self.payload(draft)).status_code, 409)
        self.assertEqual(self.post('check', self.payload(draft)).status_code, 200)
        checked = self.get().get_json()
        self.assertEqual(checked['status'], 'checked')
        self.assertTrue(checked['ready_to_close'])
        closed = self.post('close', self.payload(checked)).get_json()
        self.assertEqual(closed['version'], 1)
        state = self.get().get_json()
        self.assertEqual(state['status'], 'closed')
        self.assertFalse(state['divergent'])
        self.assertFalse(state['ready_to_close'])
        report = self.version(closed['id']).get_json()['report']
        self.assertEqual(report, draft['current'])
        person = next(p for group in report['groups'] for p in group['people'] if p['id'] == self.f.people[0])
        self.assertEqual(len(person['assignments']), 2)
        self.assertEqual(person['crew_id'], self.f.crew)
        self.assertEqual(person['department'], 'СМУ 15')
        self.assertEqual(person['assignments'][0]['assignment_employer'], 'ЛГСС')
        self.assertIsNone(person['assignments'][0]['author'])
        with self.app.app_context():
            db = self.module.get_db()
            self.assertEqual(before, [tuple(r) for r in db.execute('SELECT * FROM assignments ORDER BY id')])

    def test_live_changes_leave_original_immutable_and_require_reasoned_correction(self):
        first = self.close()
        old = self.version(first['id']).get_json()
        self.mutate("UPDATE workers SET full_name='Новое имя',department='СМУ 20',pps='Другой ППС' WHERE id=?", (self.f.people[0],))
        self.mutate('DELETE FROM assignments WHERE worker_id=?', (self.f.people[0],))
        state = self.get().get_json()
        self.assertEqual(state['status'], 'closed')
        self.assertTrue(state['divergent'])
        self.assertEqual(self.version(first['id']).get_json(), old)
        self.assertEqual(self.post('close', self.payload(state, reason='Изменение расстановки')).status_code, 409)
        self.assertEqual(self.post('check', self.payload(state)).status_code, 200)
        state = self.get().get_json()
        self.assertEqual(self.post('close', self.payload(state)).status_code, 400)
        response = self.post('close', self.payload(state, reason='Уточнены сотрудник и назначения'))
        self.assertEqual(response.status_code, 200, response.data)
        second = response.get_json()
        self.assertEqual(second['version'], 2)
        self.assertEqual(second['previous_version_id'], first['id'])
        self.assertEqual(self.version(first['id']).get_json(), old)
        self.assertFalse(self.get().get_json()['divergent'])
        self.assertEqual(len(self.get().get_json()['versions']), 2)
        with self.app.app_context():
            db = self.module.get_db()
            for table in ('report_closure_checks', 'report_closure_versions'):
                for operation in (f"UPDATE {table} SET fingerprint='changed'", f'DELETE FROM {table}'):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(operation)
                    db.rollback()

    def test_stale_source_preview_check_and_same_values_with_new_token_are_rejected(self):
        draft = self.get().get_json()
        self.mutate("UPDATE assignments SET edit_token='new-revision'")
        self.assertEqual(self.post('check', self.payload(draft)).status_code, 409)
        self.assertEqual(self.post('check').status_code, 200)
        checked = self.get().get_json()
        self.mutate("UPDATE staffing_attendance SET edit_token='attendance-revision'")
        self.assertEqual(self.post('close', self.payload(checked)).status_code, 409)
        self.assertFalse(self.get().get_json()['ready_to_close'])
        self.assertEqual(self.get().get_json()['status'], 'draft')

    def test_stale_check_and_actor_bound_previews_cannot_close(self):
        first_payload = self.payload()
        self.assertEqual(self.post('check', first_payload).status_code, 200)
        old = self.get().get_json()
        self.assertEqual(self.post('check', self.payload(old)).status_code, 200)
        self.assertEqual(self.post('close', self.payload(old)).status_code, 409)
        with self.app.app_context():
            db = self.module.get_db()
            second = db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES ('other-admin','hash','Другой','admin','now')").lastrowid
            db.commit()
        self.assertEqual(self.post('close', self.payload(), client=self.client(second)).status_code, 409)

    def test_idempotent_check_close_and_conflicting_key(self):
        payload = self.payload()
        first = self.post('check', payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.post('check', payload).get_json(), first.get_json())
        changed = {**payload, 'expected_token': '0' * 64}
        self.assertEqual(self.post('check', changed).status_code, 409)
        payload = self.payload()
        closed = self.post('close', payload)
        self.assertEqual(closed.status_code, 200)
        self.mutate("UPDATE workers SET profession='Новая профессия' WHERE id=?", (self.f.people[0],))
        self.assertEqual(self.post('close', payload).get_json(), closed.get_json())
        self.assertEqual(len(self.get().get_json()['versions']), 1)

    def test_snapshot_scope_controls_read_even_after_workers_move(self):
        scope = {'kind': 'department', 'department': 'СМУ 15'}
        saved = self.close(scope)
        original = self.version(saved['id']).get_json()
        self.assertEqual(original['report']['totals']['total'], 3)
        self.mutate("INSERT INTO user_smu_access VALUES (?,'selected',?,'scope',?,'now')",
                    (self.f.admin, json.dumps(['СМУ 15'], ensure_ascii=False), self.f.admin))
        self.mutate("UPDATE workers SET department='СМУ 19' WHERE department='СМУ 15'")
        self.assertEqual(self.version(saved['id']).get_json(), original)
        self.assertEqual(self.get().status_code, 403)
        self.assertEqual(self.get(scope).get_json()['current']['totals']['total'], 0)
        self.mutate('UPDATE user_smu_access SET departments_json=? WHERE user_id=?',
                    (json.dumps(['СМУ 19'], ensure_ascii=False), self.f.admin))
        self.assertEqual(self.version(saved['id']).status_code, 403)
        self.assertEqual(self.version(saved['id'], suffix='/pdf').status_code, 403)
        self.assertEqual(self.version(saved['id'], suffix='/export').status_code, 403)

    def test_access_csrf_future_date_and_arbitrary_snapshots_fail_closed(self):
        self.assertEqual(self.get(client=self.client()).status_code, 401)
        self.assertEqual(self.admin.post(self.url + '/check', json=self.payload()).status_code, 403)
        self.assertEqual(self.post('check', client=self.client(self.f.viewer)).status_code, 403)
        self.assertEqual(self.post('check', client=self.client(self.f.foreman)).status_code, 403)
        self.assertEqual(self.get(date='bad').status_code, 400)
        self.assertEqual(self.get(date=(date.today() + timedelta(days=2)).isoformat()).status_code, 400)
        self.assertEqual(self.post('check', self.payload(snapshot={'totals': {'total': 0}})).status_code, 400)
        self.assertEqual(self.post('check', self.payload(scope={'kind': 'department'})).status_code, 400)
        self.assertEqual(self.post('check', self.payload(idempotency_key='small')).status_code, 400)
        self.assertEqual(self.version(999999).status_code, 404)

    def test_scope_revocation_after_preview_and_idempotent_retry_is_enforced(self):
        payload = self.payload()
        self.assertEqual(self.post('check', payload).status_code, 200)
        self.mutate("INSERT INTO user_smu_access VALUES (?,'selected','[]','revoked',?,'now')", (self.f.admin, self.f.admin))
        self.assertEqual(self.post('check', payload).status_code, 403)
        self.assertEqual(self.post('close', payload).status_code, 403)

    def test_stored_pdf_and_json_export_need_no_live_report_queries(self):
        saved = self.close()
        self.mutate('DELETE FROM assignments')
        pdf = self.version(saved['id'], suffix='/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b'%PDF-'))
        self.assertEqual(pdf.headers['Cache-Control'], 'no-store')
        exported = self.version(saved['id'], suffix='/export')
        self.assertEqual(exported.mimetype, 'application/json')
        self.assertIn('attachment', exported.headers['Content-Disposition'])
        self.assertEqual(json.loads(exported.data), self.version(saved['id']).get_json())

    def test_assignment_author_and_closing_actor_names_are_frozen(self):
        self.mutate("UPDATE assignments SET created_at='2026-09-13T08:00:00Z' WHERE worker_id=?", (self.f.people[0],))
        self.mutate('''INSERT INTO assignment_events
            (crew_id,worker_id,work_date,shift,after_subobject_id,changed_by,changed_at)
            VALUES (?,?,'2026-09-13','1 смена',?,?,'2026-09-13T08:01:00Z')''',
            (self.f.crew, self.f.people[0], self.f.site, self.f.foreman))
        saved = self.close()
        before = self.version(saved['id']).get_json()
        person = next(p for group in before['report']['groups'] for p in group['people'] if p['id'] == self.f.people[0])
        author = next(a['author'] for a in person['assignments'] if a['assignment_shift'] == '1 смена')
        self.assertEqual(author['user_id'], self.f.foreman)
        self.assertEqual(author['full_name'], 'Прораб')
        self.mutate("UPDATE users SET full_name='Изменённое имя пользователя' WHERE id IN (?,?)", (self.f.foreman, self.f.admin))
        self.assertEqual(self.version(saved['id']).get_json(), before)
        self.assertTrue(self.get().get_json()['divergent'])

    def test_capture_requires_transaction_and_keeps_one_database_snapshot(self):
        with self.app.app_context():
            db = self.module.get_db()
            with self.assertRaises(RuntimeError):
                capture_report(db, self.day, self.scope)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN')
            db.execute('SELECT id FROM workers LIMIT 1').fetchone()
            with sqlite3.connect(self.module.DATABASE_PATH) as other:
                other.execute("UPDATE workers SET full_name='Изменение после начала снимка' WHERE id=?", (self.f.people[0],))
                other.execute('DELETE FROM assignments WHERE worker_id=?', (self.f.people[0],))
            report = capture_report(db, self.day, self.scope)
            db.rollback()
        person = next(p for group in report['groups'] for p in group['people'] if p['id'] == self.f.people[0])
        self.assertEqual(person['full_name'], 'Сотрудник 0')
        self.assertEqual(len(person['assignments']), 2)
        self.assertEqual(report['totals']['assigned'], 1)


@unittest.skipUnless(os.getenv('REPORT_CLOSURE_POSTGRES_ENV'), 'Explicit isolated PostgreSQL test environment required')
class ReportClosurePostgresTest(unittest.TestCase):
    """Opt-in integration against a disposable operations clone, never staging.

    Run this class alone in a fresh process so the application gets its reviewed
    PostgreSQL configuration before import. No migrations are run by this test.
    """
    @classmethod
    def setUpClass(cls):
        import importlib
        from psycopg.conninfo import conninfo_to_dict
        for line in Path(os.environ['REPORT_CLOSURE_POSTGRES_ENV']).read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
        name = conninfo_to_dict(os.environ['DATABASE_URL'])['dbname']
        if not name.startswith('workforce_operations_test_'):
            raise RuntimeError('Only the explicitly designated disposable operations clone is allowed.')
        cls.module = importlib.import_module('app')
        if cls.module.DATABASE_BACKEND != 'postgres':
            raise RuntimeError('Run the PostgreSQL integration class alone in a fresh process.')

    def test_scope_options_preserve_archived_scope_without_disclosing_it_to_other_smu(self):
        from report_closure_access import authorize_report_scope, register_report_scope_options
        app = Flask(__name__)
        app.secret_key = self.module.SECRET_KEY
        app.before_request(self.module.csrf_protection)
        app.teardown_appcontext(self.module.close_db)
        register_report_closure(app, self.module.get_db, self.module.roles_required,
                                self.module.utc_now, authorize_scope=authorize_report_scope)
        register_report_scope_options(app, self.module.get_db, self.module.roles_required)
        suffix = uuid4().hex
        archived = 'Архивное СМУ ' + suffix
        destination = 'Новое СМУ ' + suffix
        scope = {'kind': 'department', 'department': archived}
        stamp = '2026-09-13T08:00:00Z'
        with app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                actors = []
                for label, department in [('archive', archived), ('destination', destination)]:
                    actor = db.execute('''INSERT INTO users(username,password_hash,full_name,role,created_at)
                        VALUES (?,?,'Доступ к истории','admin',?)''',
                        ('scope-' + label + '-' + suffix, 'test-only-no-password', stamp)).lastrowid
                    actors.append(actor)
                    db.execute("INSERT INTO user_smu_access VALUES (?,'selected',?,?,?,?)",
                        (actor, json.dumps([department], ensure_ascii=False), suffix, actor, stamp))
                worker = db.execute('''INSERT INTO workers(full_name,personnel_no,department)
                    VALUES ('Сотрудник архивного СМУ',?,?)''', ('scope-' + suffix, archived)).lastrowid
                site = db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 1').fetchone()[0]
                db.execute('''INSERT INTO assignments(work_date,shift,worker_id,subobject_id,foreman_user_id,
                    employer,created_at,edit_token) VALUES ('2026-09-13','1 смена',?,?,?,'',?,?)''',
                    (worker, site, actors[0], stamp, suffix))

        def client_for(actor):
            client = app.test_client()
            with client.session_transaction() as session:
                session['user_id'] = actor
                session['csrf_token'] = 'scope-test-csrf'
            return client

        owner, other = (client_for(actor) for actor in actors)
        base = '/api/placement-report/closure'

        def scopes(client):
            response = client.get(base + '/scopes')
            self.assertEqual(response.status_code, 200, response.data)
            return response.get_json()['departments']

        self.assertEqual(app.test_client().get(base + '/scopes').status_code, 401)
        self.assertEqual(scopes(owner), [archived])
        self.assertNotIn(archived, scopes(other))
        saved = None
        for action in ('check', 'close'):
            preview = owner.get(base, query_string={'date': '2026-09-13', 'scope': 'department', 'department': archived})
            self.assertEqual(preview.status_code, 200, preview.data)
            self.assertEqual(preview.get_json()['current']['totals']['total'], 1)
            response = owner.post(base + '/' + action, json={
                'date': '2026-09-13', 'scope': scope, 'expected_token': preview.get_json()['expected_token'],
                'idempotency_key': uuid4().hex}, headers={'X-CSRF-Token': 'scope-test-csrf'})
            self.assertEqual(response.status_code, 200, response.data)
            saved = response.get_json()
        with app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE workers SET department=? WHERE id=?', (destination, worker))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM workers WHERE department=?', (archived,)).fetchone()[0], 0)
        self.assertEqual(scopes(owner), [archived])
        self.assertEqual(scopes(other), [destination])
        version_url = base + '/versions/' + str(saved['id'])
        frozen = owner.get(version_url)
        self.assertEqual(frozen.status_code, 200, frozen.data)
        self.assertEqual(frozen.get_json()['report']['groups'][0]['people'][0]['department'], archived)
        self.assertEqual(other.get(version_url).status_code, 403)
        with app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE user_smu_access SET departments_json='[]' WHERE user_id=?", (actors[0],))
        self.assertEqual(scopes(owner), [])
        self.assertEqual(owner.get(version_url).status_code, 403)

    def test_postgres_closure_storage_exports_and_immutable_permissions(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from report_closure_access import authorize_report_scope
        self.app = Flask(__name__)
        self.app.secret_key = self.module.SECRET_KEY
        self.app.before_request(self.module.csrf_protection)
        self.app.teardown_appcontext(self.module.close_db)
        suffix = uuid4().hex
        scope = {'kind': 'department', 'department': 'Тест закрытия ' + suffix}

        register_report_closure(self.app, self.module.get_db, self.module.roles_required,
                                self.module.utc_now, authorize_scope=authorize_report_scope)
        stamp = '2026-09-13T08:00:00Z'
        with self.app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                actor = db.execute('''INSERT INTO users(username,password_hash,full_name,role,created_at)
                    VALUES (?,?,'Проверка отчёта','admin',?)''', ('closure-' + suffix, 'test-only-no-password', stamp)).lastrowid
                crew = db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES ('Тест отчёта',?,?)", (actor, stamp)).lastrowid
                category = db.execute('SELECT id,name FROM gdlr_categories WHERE active=1 AND staffing_allowed=1 ORDER BY id LIMIT 1').fetchone()
                site = db.execute('SELECT id FROM subobjects ORDER BY id LIMIT 1').fetchone()[0]
                worker = db.execute('''INSERT INTO workers(full_name,personnel_no,department,pps,category,profession)
                    VALUES ('Снимок сотрудника',?,?,'ППС тест',?,'Монтажник')''',
                    ('closure-' + suffix, scope['department'], category['name'])).lastrowid
                db.execute('INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,?,?,?)',
                           (worker, category['id'], suffix, actor, stamp))
                db.execute('INSERT INTO manual_employees(worker_id,created_by,created_at,request_key,payload_hash) VALUES (?,?,?,?,?)',
                           (worker, actor, stamp, suffix, suffix))
                db.execute('INSERT INTO crew_members(crew_id,worker_id) VALUES (?,?)', (crew, worker))
                for shift in ('1 смена', '2 смена'):
                    db.execute('''INSERT INTO assignments(work_date,shift,worker_id,subobject_id,crew_id,foreman_user_id,
                        employer,created_at,edit_token) VALUES ('2026-09-13',?,?,?,?,?,'ЛГСС',?,?)''',
                        (shift, worker, site, crew, actor, stamp, suffix))
                    db.execute('''INSERT INTO assignment_events(crew_id,worker_id,work_date,shift,after_subobject_id,changed_by,changed_at)
                        VALUES (?,?,'2026-09-13',?,?,?,?)''', (crew, worker, shift, site, actor, stamp))
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = actor
            session['csrf_token'] = 'pg-closure-csrf'
        url = '/api/placement-report/closure'

        def state():
            response = client.get(url, query_string={'date': '2026-09-13', 'scope': 'department', 'department': scope['department']})
            self.assertEqual(response.status_code, 200, response.data)
            return response.get_json()

        def payload(current, reason=''):
            return {'date': '2026-09-13', 'scope': scope, 'expected_token': current['expected_token'],
                    'idempotency_key': uuid4().hex, **({'reason': reason} if reason else {})}

        def post(action, data, expected=200):
            response = client.post(url + '/' + action, json=data, headers={'X-CSRF-Token': 'pg-closure-csrf'})
            self.assertEqual(response.status_code, expected, response.data)
            return response.get_json()

        draft = state()
        self.assertEqual(draft['current']['totals'], dict(total=1, assigned=1, unassigned=0, absent=0))
        person = draft['current']['groups'][0]['people'][0]
        self.assertTrue(person['worker_uuid'])
        self.assertEqual(len(person['assignments']), 2)
        self.assertTrue(all(a['author']['user_id'] == actor for a in person['assignments']))
        post('check', {**payload(draft), 'scope': {'kind': 'all'}}, expected=400)
        check_payload = payload(draft)
        check = post('check', check_payload)
        self.assertEqual(post('check', check_payload), check)
        close_payload = payload(state())
        closed = post('close', close_payload)
        self.assertEqual(post('close', close_payload), closed)
        version_url = url + '/versions/' + str(closed['id'])
        original = client.get(version_url).get_json()
        with self.app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE workers SET full_name='Исправленное имя' WHERE id=?", (worker,))
        changed = state()
        self.assertTrue(changed['divergent'])
        self.assertEqual(client.get(version_url).get_json(), original)
        post('close', payload(changed, 'Уточнение'), expected=409)
        post('check', payload(changed))
        next_version = post('close', payload(state(), 'Уточнение имени'))
        self.assertEqual(next_version['version'], 2)
        self.assertEqual(next_version['previous_version_id'], closed['id'])
        self.assertEqual(client.get(version_url).get_json(), original)
        self.assertTrue(client.get(version_url + '/pdf').data.startswith(b'%PDF-'))
        self.assertEqual(client.get(version_url + '/export').get_json(), original)
        with self.app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("INSERT INTO user_smu_access VALUES (?,'selected',?,?,?,?)",
                           (actor, json.dumps([scope['department']], ensure_ascii=False), suffix, actor, stamp))
                db.execute("UPDATE workers SET department='Другое тестовое СМУ' WHERE id=?", (worker,))
        self.assertEqual(client.get(version_url).get_json(), original)
        self.assertEqual(client.get(url, query_string={'date': '2026-09-13', 'scope': 'all'}).status_code, 403)
        with self.app.app_context():
            db = self.module.get_db()
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE user_smu_access SET departments_json=? WHERE user_id=?',
                           (json.dumps(['Другое тестовое СМУ'], ensure_ascii=False), actor))
        self.assertEqual(client.get(version_url).status_code, 403)
        self.assertEqual(client.get(version_url + '/pdf').status_code, 403)
        self.assertEqual(client.get(version_url + '/export').status_code, 403)
        post('close', close_payload, expected=403)
        with self.app.app_context():
            db = self.module.get_db()
            for table in ('report_closure_checks', 'report_closure_versions'):
                self.assertEqual(db.native("SELECT has_table_privilege(current_user,%s,'UPDATE')", (table,)).fetchone()[0], False)
                for statement in (f"UPDATE {table} SET fingerprint='bad'", f'DELETE FROM {table}', f'TRUNCATE {table}'):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        db.native(statement)
                    db.rollback()
        owner_url = os.environ['ADMIN_DATABASE_URL']
        self.assertEqual(conninfo_to_dict(owner_url)['dbname'], conninfo_to_dict(os.environ['DATABASE_URL'])['dbname'])
        with psycopg.connect(owner_url, autocommit=True) as owner:
            for table in ('report_closure_checks', 'report_closure_versions'):
                for statement in (f"UPDATE {table} SET fingerprint='bad'", f'DELETE FROM {table}', f'TRUNCATE {table} CASCADE'):
                    with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
                        owner.execute(statement)


if __name__ == '__main__':
    unittest.main()
