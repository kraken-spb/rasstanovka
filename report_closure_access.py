"""Authorization against immutable report scopes, independent of worker moves."""
import json
from flask import abort, g, jsonify
from werkzeug.exceptions import Forbidden
from workforce_core import READERS


def authorize_report_scope(db, scope, *, write=False):
    actor=db.execute('SELECT id,role,active FROM users WHERE id=?',(g.user['id'],)).fetchone()
    if not actor or not actor['active']:
        abort(403)
    access=db.execute('SELECT mode,departments_json FROM user_smu_access WHERE user_id=?',(actor['id'],)).fetchone()
    global_access=actor['role'] in ('super_admin','hr_viewer') or (
        access['mode']=='all' if access else actor['role'] in ('admin','viewer'))
    departments=json.loads(access['departments_json']) if access and access['mode']=='selected' else []
    if scope['kind']=='all':
        if not global_access:
            abort(403,description='Полная версия отчёта доступна только при доступе ко всем СМУ.')
        if write:
            abort(400,description='Для проверки и закрытия выберите конкретное СМУ.')
    elif not global_access and scope['department'] not in departments:
        abort(403,description='Нет доступа к СМУ сохранённого отчёта.')
    if write and actor['role'] not in ('admin','super_admin'):
        abort(403,description='Закрытие отчёта выполняет администратор участка.')
    return actor


def register_report_scope_options(app,get_db,roles_required):
    @app.get('/api/placement-report/closure/scopes')
    @roles_required(*READERS)
    def placement_report_closure_scopes():
        db=get_db()
        with db:
            db.execute('BEGIN')
            names={r['department'] for r in db.execute("SELECT DISTINCT department FROM workers WHERE department<>''").fetchall()}
            for row in db.execute('SELECT DISTINCT scope_json FROM report_closure_versions UNION SELECT DISTINCT scope_json FROM report_closure_checks').fetchall():
                saved=json.loads(row['scope_json'])
                if saved['kind']=='department':
                    names.add(saved['department'])
            available=[]
            for name in sorted(names):
                try:
                    authorize_report_scope(db,{'kind':'department','department':name})
                except Forbidden:
                    continue
                available.append(name)
        return jsonify(departments=available)
