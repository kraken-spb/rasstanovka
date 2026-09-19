"""PPS-linked organizational units; SMU names and parents have one owner."""
import unicodedata

from flask import abort, jsonify, request
from workforce_core import ADMINS, READERS, actor_scope, audit, plain, text_value


def add_division_filter(query, clauses, args):
    selected=sorted(set(query.getlist('division'))-{''})
    if not selected:return
    try:
        values=[int(v) for v in selected if v!='__none__']
        if len(selected)>100 or any(v<=0 or v>=2**63 for v in values):raise ValueError
    except ValueError:abort(400, description='Выберите подразделения из справочника.')
    expression='p.division_id=ANY(%s::bigint[])'
    if '__none__' in selected:expression='('+expression+' OR p.division_id IS NULL)'
    clauses.append(expression);args.append(values)


def division_rows(db):
    return plain(db.native('''SELECT d.*,p.name pps_name,p.active pps_active,
        count(w.id) employee_count FROM workforce_divisions d
        LEFT JOIN pps_catalog p ON p.id=d.pps_id
        LEFT JOIN workforce_profiles wp ON wp.division_id=d.id
        LEFT JOIN workers w ON w.id=wp.worker_id AND w.active=1
        GROUP BY d.id,p.name,p.active ORDER BY p.name NULLS LAST,d.name,d.id''').fetchall())


def register_division_routes(app, database, roles_required):
    def editor(db):
        actor, scope, _ = actor_scope(db, write=True)
        if actor['role'] not in ADMINS or scope != 'TRUE':
            abort(403, description='Справочник подразделений изменяет администратор с доступом ко всем СМУ.')
        return actor

    def payload():
        body=request.get_json(silent=True)
        if not isinstance(body,dict):
            abort(400, description='Передайте данные подразделения.')
        return body

    def values(db, body, old=None):
        name=' '.join(text_value(body.get('name'), 'Название подразделения', 500, True).split())
        if any(unicodedata.category(c).startswith('C') for c in name):
            abort(400, description='Название содержит служебные символы.')
        parent=body.get('pps_id')
        if parent is not None:
            if type(parent) is not int or not 0 < parent < 2**63:
                abort(400, description='Выберите ППС из справочника.')
            pps=db.native('SELECT active FROM pps_catalog WHERE id=%s FOR SHARE',(parent,)).fetchone()
            if not pps or (not pps['active'] and (not old or parent != old['pps_id'])):
                abort(400, description='Выберите действующий ППС.')
        if type(body.get('active',True)) is not bool:
            abort(400, description='Укажите доступность подразделения.')
        duplicate=db.native('SELECT id FROM workforce_divisions WHERE pps_id IS NOT DISTINCT FROM %s AND name_key=log_casefold(%s) AND id<>%s',
            (parent,name,old['id'] if old else 0)).fetchone()
        if duplicate:abort(409, description='В этом ППС уже есть такое подразделение.')
        return name,parent,body.get('active',True),text_value(body.get('notes',''),'Примечание',5000)

    def checked(db, identity, body):
        row=plain(db.native('SELECT * FROM workforce_divisions WHERE id=%s FOR UPDATE',(identity,)).fetchone())
        if not row:abort(404, description='Подразделение не найдено.')
        if str(body.get('expected_token')) != str(row['edit_token']):
            abort(409, description='Подразделение уже изменено. Обновите справочник.')
        if row['smu_id'] is not None:
            abort(409, description='Это СМУ. Изменяйте название и привязку к ППС в справочнике СМУ.')
        return row

    @app.get('/api/workforce/divisions')
    @roles_required(*READERS)
    def get_divisions():
        db=database()
        actor,scope,_=actor_scope(db)
        # Catalogs contain no personal data. Counts follow the existing catalog convention.
        return jsonify(rows=division_rows(db),pps=plain(db.native('SELECT id,name,active FROM pps_catalog ORDER BY name').fetchall()),
            editable=actor['role'] in ADMINS and scope=='TRUE')

    @app.post('/api/workforce/divisions')
    @roles_required(*ADMINS)
    def create_division():
        db=database();body=payload()
        with db:
            db.execute('BEGIN IMMEDIATE');actor=editor(db)
            db.native('LOCK TABLE workforce_divisions IN SHARE ROW EXCLUSIVE MODE')
            name,parent,active,notes=values(db,body)
            row=plain(db.native('''INSERT INTO workforce_divisions(name,name_key,pps_id,active,notes,updated_by)
                VALUES (%s,log_casefold(%s),%s,%s,%s,%s) RETURNING *''',(name,name,parent,active,notes,actor['id'])).fetchone())
            audit(db,actor,'create','division',str(row['id']),None,row)
        return jsonify(row),201

    @app.patch('/api/workforce/divisions/<int:identity>')
    @roles_required(*ADMINS)
    def update_division(identity):
        db=database();body=payload()
        with db:
            db.execute('BEGIN IMMEDIATE');actor=editor(db)
            db.native('LOCK TABLE workforce_divisions IN SHARE ROW EXCLUSIVE MODE')
            old=checked(db,identity,body);name,parent,active,notes=values(db,body,old)
            row=plain(db.native('''UPDATE workforce_divisions SET name=%s,name_key=log_casefold(%s),pps_id=%s,
                active=%s,notes=%s,edit_token=gen_random_uuid(),updated_by=%s,updated_at=now() WHERE id=%s RETURNING *''',
                (name,name,parent,active,notes,actor['id'],identity)).fetchone())
            audit(db,actor,'update','division',str(identity),old,row)
        return jsonify(row)

    @app.delete('/api/workforce/divisions/<int:identity>')
    @roles_required(*ADMINS)
    def delete_division(identity):
        db=database();body=payload()
        with db:
            db.execute('BEGIN IMMEDIATE');actor=editor(db);old=checked(db,identity,body)
            if db.native('SELECT 1 FROM workforce_profiles WHERE division_id=%s LIMIT 1',(identity,)).fetchone():
                abort(409, description='К подразделению привязаны сотрудники. Можно отключить его для новых назначений.')
            db.native('DELETE FROM workforce_divisions WHERE id=%s',(identity,))
            audit(db,actor,'delete','division',str(identity),old,None)
        return jsonify(deleted=identity)
