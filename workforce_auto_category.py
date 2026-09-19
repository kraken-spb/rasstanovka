"""Preview and atomic application of category suggestions for an explicit selection."""
from flask import abort, jsonify, request
from uuid import uuid4

from workforce_bulk import ids_from, lock_people, snapshots
from workforce_core import EDITORS, audit, digest, remember, replay, text_value


def preview(db, ids):
    from workforce_category_matching import suggestions
    rows = snapshots(db, ids)
    people = suggestions(db, rows)
    return {'people': people, 'token': digest([rows, people]),
            'matched': sum(row['status'] == 'matched' for row in people)}


def register_auto_category_routes(app, database, roles_required):
    @app.post('/api/workforce/bulk/category-auto/prepare')
    @roles_required(*EDITORS)
    def workforce_auto_category_prepare():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != {'ids'}:
            abort(400, description='Передайте выбранных сотрудников.')
        ids = ids_from(data['ids'])
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            lock_people(db, ids)
            result = preview(db, ids)
        return jsonify(result)

    @app.post('/api/workforce/bulk/category-auto/apply')
    @roles_required(*EDITORS)
    def workforce_auto_category_apply():
        data = request.get_json(silent=True)
        fields = {'ids', 'token', 'reason', 'request_key'}
        if not isinstance(data, dict) or set(data) - fields or (fields - {'reason'}) - set(data):
            abort(400, description='Сначала выполните автоматический подбор ГДЛР.')
        ids = ids_from(data['ids'])
        if not isinstance(data['token'], str) or len(data['token']) != 64:
            abort(400, description='Обновите результат подбора.')
        reason = text_value(data.get('reason', ''), 'Причина изменения', 10000)
        db = database()
        with db:
            db.execute('BEGIN IMMEDIATE')
            actor = lock_people(db, ids)
            previous = replay(db, actor, 'auto-category', data)
            if previous is not None:
                return jsonify(previous)
            current = preview(db, ids)
            if current['token'] != data['token']:
                abort(409, description='Сотрудники или основания подбора изменились. Повторите подбор ГДЛР.')
            before = {row['id']: row for row in snapshots(db, ids)}
            matched = [row for row in current['people'] if row['status'] == 'matched']
            for row in matched:
                inserted = db.native('''INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at)
                    VALUES (%s,%s,%s,%s,now()::text) ON CONFLICT(worker_id) DO NOTHING RETURNING worker_id''',
                    (row['id'], row['category_id'], uuid4().hex, actor['id'])).fetchone()
                if not inserted:
                    abort(409, description='Категория уже назначена другим пользователем. Повторите подбор ГДЛР.')
            if matched:
                db.native('''UPDATE workforce_profiles SET edit_token=gen_random_uuid(),updated_by=%s,updated_at=now()
                    WHERE worker_id=ANY(%s)''', (actor['id'], [row['id'] for row in matched]))
            after = {row['id']: row for row in snapshots(db, [row['id'] for row in matched])} if matched else {}
            for row in matched:
                audit(db, actor, 'auto_category', 'profile', row['id'], before[row['id']],
                      {**after[row['id']], 'category_match_basis': row['basis']}, worker_id=row['id'], reason=reason)
            result = {'changed': len(matched), 'skipped': len(ids) - len(matched)}
            remember(db, actor, 'auto-category', data, result)
        return jsonify(result)
