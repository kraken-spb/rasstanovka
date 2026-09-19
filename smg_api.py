"""Reviewed, versioned monthly SMG imports; no writes to personnel or daily plans."""
from decimal import Decimal
import json
from uuid import UUID, uuid4, uuid5
import re

from flask import abort, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from smg_parser import parse_workbook
from workforce_core import plain


def hierarchy(db):
    return plain(db.native('''SELECT n.*,c.name category_name FROM gdlr_hierarchy_nodes n
        LEFT JOIN gdlr_categories c ON c.id=n.category_id ORDER BY n.sort_order''').fetchall())


def latest(db, scope, period):
    return db.native('''SELECT * FROM smg_plan_versions WHERE scope=%s AND period=%s
        ORDER BY version DESC LIMIT 1''', (scope, period)).fetchone()


def token_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='smg-reviewed-horizon-v2')


def current_values(db, version):
    return {r['node_id']: str(r['value']) for r in db.native(
        'SELECT node_id,value FROM smg_plan_values WHERE version_id=%s', (version,))} if version else {}


def edition_months(db, row):
    edition_id = row['source'].get('edition_id')
    rows = db.native('''SELECT id,period,version,source FROM smg_plan_versions
        WHERE created_by=%s AND source->>'edition_id'=%s ORDER BY period''',
        (row['created_by'], edition_id)).fetchall() if edition_id else [row]
    return plan_months(db, rows)


def plan_months(db, rows):
    values = {}
    if rows:
        for r in db.native('SELECT version_id,node_id,value FROM smg_plan_values WHERE version_id=ANY(%s::uuid[])',
                           ([str(r['id']) for r in rows],)):
            values.setdefault(str(r['version_id']), {})[r['node_id']] = str(r['value'])
    return [dict(id=str(r['id']), period=str(r['period']), version=r['version'],
                 values=values.get(str(r['id']), {}), breakdown=plan_breakdown(r['source'])) for r in rows]


def plan_breakdown(source):
    objects = source.get('objects', []) if source.get('format') == 'son-v1' else []
    if not objects:
        return None
    totals = {}
    for kind in ('staff', 'rental', 'outstaff', 'total'):
        totals[kind] = {}
        keys = set().union(*(o['values'][kind] for o in objects))
        for key in keys:
            values = [o['values'][kind].get(key) for o in objects]
            totals[kind][key] = None if any(v is None for v in values) else str(sum((Decimal(v) for v in values), Decimal(0)))
    return dict(values=totals, objects=[{k:o[k] for k in ('scope','label','values')} for o in objects])


def register_smg_routes(app, database, roles_required):
    @app.get('/api/smg/plans')
    @roles_required('admin', 'hr_viewer')
    def smg_plans():
        db = database()
        records = plain(db.native('''SELECT v.id,v.scope,v.period,v.version,v.previous_id,v.source - 'objects' AS source,
            v.created_at,u.full_name author FROM smg_plan_versions v JOIN users u ON u.id=v.created_by
            WHERE NOT (v.source ? 'edition_id') OR v.source->>'horizon_start'=v.period::text
            ORDER BY v.created_at DESC,v.id DESC LIMIT 200''').fetchall())
        if request.args.get('latest') == '1':
            # Select each month once, including months retained from older rolling horizons.
            rows = db.native('''SELECT DISTINCT ON(scope,period) id,scope,period,version,source
                FROM smg_plan_versions ORDER BY scope,period,version DESC''').fetchall()
            editions = {}
            for row, month in zip(rows, plan_months(db, rows)):
                editions.setdefault(row['scope'], []).append(month)
            return jsonify(versions=records, hierarchy=hierarchy(db),
                           editions=[dict(scope=scope, months=months) for scope, months in editions.items()])
        selected = request.args.get('version') or (records[0]['id'] if records else None)
        if selected:
            try:
                selected = str(UUID(selected))
            except (ValueError, TypeError):
                abort(400, description='Некорректная версия плана.')
            row = db.native('SELECT * FROM smg_plan_versions WHERE id=%s', (selected,)).fetchone()
            if not row:
                abort(404)
        return jsonify(versions=records, selected=selected, scope=row['scope'] if selected else None,
                       months=edition_months(db, row) if selected else [], hierarchy=hierarchy(db))

    @app.post('/api/smg/preview')
    @roles_required('admin')
    def smg_preview():
        upload = request.files.get('file')
        if not upload or not (upload.filename or '').lower().endswith('.xlsx'):
            abort(400, description='Выберите Excel-файл .xlsx с листом «Свод».')
        try:
            year = int(request.form.get('year', ''))
            parsed = parse_workbook(upload.stream.read(20 * 1024 * 1024 + 1), upload.filename, year)
        except (ValueError, OverflowError) as error:
            abort(400, description=str(error))
        parsed['blocks'] = [b for b in parsed['blocks'] if re.fullmatch(r'ППС-\d+', b['scope'])]
        if not parsed['blocks']:
            abort(400, description='В файле нет общего блока ППС. План задаётся на ППС целиком.')
        db = database()
        for block in parsed['blocks']:
            for month in block['months']:
                old = latest(db, block['scope'], month['period'])
                month['expected_id'] = str(old['id']) if old else None
                month['previous'] = current_values(db, old['id'] if old else None)
        signed = token_serializer().dumps({'actor': g.user['id'], 'document': parsed})
        return jsonify(document=parsed, token=signed, hierarchy=hierarchy(db))

    @app.post('/api/smg/apply')
    @roles_required('admin')
    def smg_apply():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'token', 'block', 'acknowledge', 'request_key'}:
            abort(400, description='Обновите предварительную проверку файла.')
        try:
            parsed = token_serializer().loads(data['token'], max_age=3600)
            if parsed['actor'] != g.user['id']:
                abort(403)
            document = parsed['document']
            index = data['block']
            if type(index) is not int or not 0 <= index < len(document['blocks']):
                raise ValueError
            block = document['blocks'][index]
            months = block['months']
            key = str(UUID(data['request_key']))
        except (BadSignature, SignatureExpired, ValueError, TypeError, KeyError, StopIteration):
            abort(400, description='Проверка устарела или повреждена. Загрузите файл повторно.')
        if (not re.fullmatch(r'ППС-\d+', block['scope']) or block['errors']
                or len(months) != 3 or any(m['errors'] for m in months)):
            abort(400, description='Исправьте ошибки выбранного блока в Excel и загрузите его заново.')
        if (any(m['warnings'] for m in months) or block['duplicate']) and data['acknowledge'] is not True:
            abort(400, description='Подтвердите замечания к выбранному блоку.')
        common_source = {'filename': document['filename'], 'sha256': document['sha256'], 'sheet': 'Свод',
                         'block_row': block['row'], 'block_label': block['label'],
                         'horizon_start': months[0]['period'], 'periods': [m['period'] for m in months],
                         'acknowledged': data['acknowledge']}
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = db.native('SELECT role,active FROM users WHERE id=%s FOR SHARE', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('admin', 'super_admin'):
                abort(403)
            db.native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('smg-request:' + str(g.user['id']) + ':' + key,))
            # One lock per PPS serializes overlapping rolling horizons without deadlocks.
            db.native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('smg-pps:' + block['scope'],))
            repeated = db.native('SELECT * FROM smg_plan_versions WHERE created_by=%s AND request_key=%s', (g.user['id'], key)).fetchone()
            if repeated:
                if (repeated['scope'] != block['scope'] or
                        any(repeated['source'].get(k) != v for k, v in common_source.items())):
                    abort(409, description='Этот ключ уже использован для другого импорта.')
                return jsonify(id=str(repeated['id']), version=repeated['version'], repeated=True)
            previous = [latest(db, block['scope'], m['period']) for m in months]
            # Validate the complete horizon before the first INSERT (including in rollback fixtures).
            if any((str(old['id']) if old else None) != m['expected_id'] for m, old in zip(months, previous)):
                abort(409, description='Один из месяцев уже изменён. Повторите проверку файла; ни один месяц не сохранён.')
            edition_id, saved = str(uuid4()), []
            for i, (month, old) in enumerate(zip(months, previous)):
                source = dict(common_source, edition_id=edition_id, column=month['column'], warnings=month['warnings'])
                version = (old['version'] if old else 0) + 1
                row = db.native('''INSERT INTO smg_plan_versions(scope,period,version,previous_id,source,created_by,request_key)
                    VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING id''',
                    (block['scope'], month['period'], version, old['id'] if old else None,
                     json.dumps(source, ensure_ascii=False), g.user['id'], key if i == 0 else str(uuid5(UUID(key), month['period'])))).fetchone()
                for node_id, value in month['values'].items():
                    if value is not None:
                        db.native('INSERT INTO smg_plan_values(version_id,node_id,value) VALUES (%s,%s,%s)',
                                  (row['id'], node_id, Decimal(value)))
                saved.append(dict(id=str(row['id']), version=version))
        return jsonify(**saved[0], months=3), 201
