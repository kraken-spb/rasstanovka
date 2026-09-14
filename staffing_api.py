"""Flat attendance-backed placement, with crew defaults and row corrections."""
import json
import secrets
from datetime import date, datetime
from user_smu_access import can_crew, require_crew, require_workers, worker_clause, legacy_foreman, require_all

from flask import abort, g, jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from attendance_status import attendance_states, register_attendance_routes
from day_inheritance import freshness_states
from contractor_api import placement_company, placement_company_sql
from performed_work import performed_work_states, register_performed_work_routes
from staffing_import import removal_import_summary, ImportProblem, apply_attendance, import_conflicts, natural_key, parse_attendance
from staffing_import import active_import_ids_sql, active_members_sql
from staffing_shifts import canonical_shift, day_states, register_shift_routes, responsibility_states, validate_group_snapshot


def assignment_authors(db, day, rows, end_day=None):
    """Resolve the latest matching audit event, never the brigade's owner."""
    ids = sorted({row['id'] for row in rows if row['assignment_id']})
    if not ids:
        return {}
    events = db.execute(f"""SELECT e.*,u.full_name actor_name FROM assignment_events e
        JOIN (SELECT MAX(id) id FROM assignment_events WHERE work_date BETWEEN ? AND ?
              AND worker_id IN ({','.join('?' for _ in ids)})
              GROUP BY work_date,worker_id,CASE WHEN shift='Ночная смена' THEN '2 смена' ELSE shift END) latest ON latest.id=e.id
        LEFT JOIN users u ON u.id=e.changed_by""", [day, end_day or day, *ids]).fetchall()
    latest = {(e['work_date'], e['worker_id'], canonical_shift(e['shift'])): e for e in events}
    authors = {}
    for row in rows:
        row_day = row['work_date'] if 'work_date' in row.keys() else day
        event = latest.get((row_day, row['id'], canonical_shift(row['assignment_shift'])))
        if not row['assignment_id'] or not event or event['after_subobject_id'] != row['subobject_id'] or event['crew_id'] != row['assignment_crew_id']:
            continue
        # An older event must not be attributed to a subsequently recreated legacy assignment.
        try:
            changed = datetime.fromisoformat(event['changed_at'])
            created = datetime.fromisoformat(row['assignment_created_at'])
            if not changed.tzinfo or not created.tzinfo or changed < created:
                continue
        except (TypeError, ValueError):
            continue
        authors[row['assignment_id']] = {'user_id': event['changed_by'], 'full_name': event['actor_name'], 'changed_at': event['changed_at']}
    return authors


def register_staffing_routes(app, get_db, roles_required, utc_now):
    register_shift_routes(app, get_db, roles_required, utc_now)
    register_attendance_routes(app, get_db, roles_required, utc_now)
    register_performed_work_routes(app, get_db, roles_required, utc_now)
    def person_binding(payload, field, name):
        person_id = payload.get(field + '_person_id')
        if person_id is None:
            return None
        if type(person_id) is not int:
            abort(400, description="Выберите сотрудника из файла.")
        person = get_db().execute('SELECT full_name FROM staffing_people WHERE id=?', (person_id,)).fetchone()
        if not person or person['full_name'] != name:
            abort(400, description="ФИО не соответствует выбранному сотруднику. Выберите его заново.")
        return person_id

    @app.get('/api/staffing/people')
    @roles_required('admin', 'foreman')
    def staffing_people():
        db = get_db()
        batch = db.execute('SELECT id,filename FROM staffing_imports ORDER BY id DESC LIMIT 1').fetchone()
        people = db.execute(f"""SELECT p.*,
            CASE WHEN m.person_id IS NULL THEN 'file' ELSE 'manual' END source_kind
            FROM staffing_people p LEFT JOIN manual_staffing_people m ON m.person_id=p.id
            WHERE p.import_id IN ({active_import_ids_sql()}) OR m.person_id IS NOT NULL
            ORDER BY p.full_name,p.personnel_no,p.id""").fetchall() if batch else []
        return jsonify({'import_id': batch['id'] if batch else None, 'filename': batch['filename'] if batch else '',
                        'people': [dict(p) for p in people]})

    def access(crew_id):
        row = get_db().execute("SELECT * FROM crews WHERE id=?", (crew_id,)).fetchone()
        if row is None:
            abort(404, description="Бригада не найдена.")
        if not can_crew(get_db(), crew_id):
            abort(403, description="Эта бригада закреплена за другим прорабом.")
        return row

    @app.get("/api/staffing")
    @roles_required("admin", "foreman")
    def staffing_table():
        try:
            day = date.fromisoformat(request.args.get("date", "")).isoformat()
        except ValueError:
            abort(400, description="Укажите дату расстановки.")
        shift = request.args.get("shift")
        if shift not in {"1 смена", "2 смена", "all"}:
            abort(400, description="Выберите смену.")
        db = get_db()
        batch = db.execute("SELECT * FROM staffing_imports ORDER BY id DESC LIMIT 1").fetchone()
        calendar_sites = None
        if 'calendar_sites' in request.args:
            try:
                calendar_sites = sorted(set(int(value) for value in request.args['calendar_sites'].split(',')))
                if not calendar_sites or len(calendar_sites) > 10000 or min(calendar_sites) <= 0:
                    raise ValueError
            except ValueError:
                abort(400, description='Укажите подобъекты из сводной таблицы.')
            if shift != 'all' or request.args.get('calendar_shift', 'all') not in ('all', '1 смена', '2 смена'):
                abort(400, description='Выберите смену для перехода из сводной таблицы.')
        if 'calendar_category' in request.args and calendar_sites is None:
            abort(400, description='Категория доступна только при переходе из сводной таблицы.')
        calendar_category = request.args.get('calendar_category') if calendar_sites is not None and 'calendar_category' in request.args else None
        if calendar_category is not None and len(calendar_category) > 200:
            abort(400, description='Категория не должна превышать 200 символов.')
        has_outstaff = db.execute('SELECT 1 FROM outstaff_members LIMIT 1').fetchone() is not None
        has_manual = db.execute('SELECT 1 FROM manual_employees LIMIT 1').fetchone() is not None
        has_restored = db.execute('SELECT 1 FROM employee_restorations LIMIT 1').fetchone() is not None
        if not batch and not has_outstaff and not has_manual and not has_restored and calendar_sites is None:
            return jsonify({"import": None, "crews": [], "rows": [], "index": []})
        source = f'''({active_members_sql()}
            UNION ALL SELECT om.worker_id,om.source_row,'' FROM outstaff_members om
            WHERE NOT EXISTS (SELECT 1 FROM ({active_members_sql()}) current WHERE current.worker_id=om.worker_id)
            UNION ALL SELECT me.worker_id,0,'' FROM manual_employees me
            WHERE NOT EXISTS (SELECT 1 FROM ({active_members_sql()}) current WHERE current.worker_id=me.worker_id)
              AND NOT EXISTS (SELECT 1 FROM outstaff_members om WHERE om.worker_id=me.worker_id)
            UNION ALL SELECT DISTINCT er.worker_id,0,'' FROM employee_restorations er
            WHERE NOT EXISTS (SELECT 1 FROM ({active_members_sql()}) current WHERE current.worker_id=er.worker_id)
              AND NOT EXISTS (SELECT 1 FROM outstaff_members om WHERE om.worker_id=er.worker_id)
              AND NOT EXISTS (SELECT 1 FROM manual_employees me WHERE me.worker_id=er.worker_id)) sm JOIN workers w ON w.id=sm.worker_id'''
        assignment_join = """a.worker_id=w.id AND a.work_date=?
            AND ((?='all' AND a.id=(SELECT MIN(aa.id) FROM assignments aa WHERE aa.worker_id=w.id AND aa.work_date=a.work_date))
                OR (CASE WHEN a.shift='Ночная смена' THEN '2 смена' ELSE a.shift END)=?)"""
        where = 'w.active=1'
        clause, params = '', [day, shift, shift]
        if calendar_sites is not None:
            # Drill through the actual assignments, including people outside the latest import.
            source = 'workers w LEFT JOIN staffing_import_members sm ON sm.worker_id=w.id AND sm.import_id=?'
            match = f"aa.work_date=? AND aa.subobject_id IN ({','.join('?' for _ in calendar_sites)})"
            match_params = [day, *calendar_sites]
            calendar_shift = request.args.get('calendar_shift', 'all')
            if calendar_shift != 'all':
                match += " AND (CASE WHEN aa.shift='Ночная смена' THEN '2 смена' ELSE aa.shift END)=?"
                match_params.append(calendar_shift)
            else:
                match += " AND aa.shift IN ('1 смена','2 смена','Ночная смена')"
            if 'calendar_employer' in request.args:
                match += ' AND (' + placement_company_sql('aa.employer') + ')=?'
                match_params.append(request.args['calendar_employer'])
            assignment_join = 'a.id=(SELECT MIN(aa.id) FROM assignments aa WHERE aa.worker_id=w.id AND ' + match + ')'
            where = "a.id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM staffing_attendance att WHERE att.worker_id=w.id AND att.work_date=a.work_date AND att.status<>'Явка')"
            params = [batch['id'] if batch else None, *match_params]
            if calendar_category is not None:
                where += " AND COALESCE(gc.name,w.category,'')=?"
                params.append(calendar_category)
        access_clause, access_params = worker_clause(db)
        clause = ' AND (' + access_clause + ')'
        params.extend(access_params)
        if "crew_id" in request.args:
            crew_id = request.args["crew_id"]
            if crew_id == "unassigned":
                clause += " AND m.crew_id IS NULL"
            else:
                try:
                    crew_id = int(crew_id)
                except ValueError:
                    abort(400, description="Укажите номер бригады.")
                access(crew_id)
                clause += " AND m.crew_id=?"
                params.append(crew_id)
        rows = db.execute(f"""
            SELECT w.id,w.active,w.full_name,w.personnel_no,COALESCE(ct.name,w.contractor) contractor,ct.id contractor_id,ct.name_key contractor_key,ew.edit_token contractor_token,w.employer,w.profession,
                COALESCE(gc.name,w.category) category,ec.category_id,ec.edit_token category_binding_token,
                w.category source_category,w.gsp_profession,w.department,w.pps,m.crew_id,sm.source_row,sm.source_crew,
                c.name crew_name,c.linear_itr,c.brigadier,c.details_token,c.owner_user_id,
                c.linear_itr_person_id crew_linear_itr_person_id,c.brigadier_person_id crew_brigadier_person_id,
                d.brigadier_override,d.linear_itr_override,d.edit_token row_token,
                d.linear_itr_person_id,d.brigadier_person_id,
                a.id assignment_id,a.subobject_id,a.edit_token,a.crew_id assignment_crew_id,a.foreman_user_id,
                a.shift assignment_shift,a.created_at assignment_created_at,
                s.name subobject_name,s.object_id,o.name object_name
            FROM {source}
            LEFT JOIN employee_contractors ew ON ew.worker_id=w.id
            LEFT JOIN contractors ct ON ct.id=ew.contractor_id
            LEFT JOIN employee_gdlr ec ON ec.worker_id=w.id
            LEFT JOIN gdlr_categories gc ON gc.id=ec.category_id
            LEFT JOIN crew_members m ON m.worker_id=w.id LEFT JOIN crews c ON c.id=m.crew_id
            LEFT JOIN staffing_row_details d ON d.worker_id=w.id
            LEFT JOIN assignments a ON {assignment_join}
            LEFT JOIN subobjects s ON s.id=a.subobject_id LEFT JOIN objects o ON o.id=s.object_id
            WHERE {where}""" + clause, params).fetchall()
        daily = day_states(db, day, [row['id'] for row in rows]) if shift == 'all' else {}
        authors = assignment_authors(db, day, rows)
        attendance = attendance_states(db, day, [row['id'] for row in rows])
        freshness = freshness_states(db, day, [row['id'] for row in rows])
        works = performed_work_states(db, day, [row['id'] for row in rows])
        extra = {}
        if calendar_sites is not None:
            site_set = set(calendar_sites)
            companies = {row['id']: (row['contractor'], row['contractor_key']) for row in rows}
            extra['calendar_assignment_count'] = sum(
                1 for current in daily.values() for assignment in current['assignments']
                if assignment['subobject_id'] in site_set
                and assignment['shift'] in ('1 смена', '2 смена', 'Ночная смена')
                and (calendar_shift == 'all' or ('2 смена' if assignment['shift'] == 'Ночная смена' else assignment['shift']) == calendar_shift)
                and ('calendar_employer' not in request.args or
                    placement_company(assignment['employer'], *companies[assignment['worker_id']]) == request.args['calendar_employer']))
        groups = responsibility_states(db, [row['id'] for row in rows])
        result, crews = [], {}
        for row in rows:
            item = dict(row)
            item.update(attendance[row['id']])
            item['freshness'] = freshness[row['id']]
            item.update(groups[row['id']])
            item['assignment_author'] = authors.get(row['assignment_id'])
            if shift == 'all':
                item.update({k: v for k, v in daily[row['id']].items() if k != 'assignments'})
            item["locked"] = bool(row["assignment_id"] and (
                row["assignment_crew_id"] not in (None, row["crew_id"]) or (
                    row["assignment_crew_id"] is None and legacy_foreman(db)
                    and row["foreman_user_id"] != g.user["id"])))
            item['locked'] = item['locked'] or not row['active'] or item.get('shift_conflict', False)
            item.update(works.get((row['id'], item.get('employee_shift') if shift == 'all' else canonical_shift(shift)),
                                  {'performed_work': '', 'performed_work_token': None}))
            item["brigadier_name"] = row["brigadier_override"] if row["brigadier_override"] is not None else row["brigadier"] or ""
            item["linear_itr_name"] = row["linear_itr_override"] if row["linear_itr_override"] is not None else row["linear_itr"] or ""
            result.append(item)
            key = row["crew_id"]
            if key not in crews:
                crews[key] = {"id": key, "name": row["crew_name"] or "Вне состава бригады", "count": 0,
                              "linear_itr": row["linear_itr"] or "", "brigadier": row["brigadier"] or "",
                              "linear_itr_person_id": row['crew_linear_itr_person_id'], "brigadier_person_id": row['crew_brigadier_person_id'],
                              "details_token": row["details_token"], "assigned": 0,
                              "can_edit_whole": bool(key and can_crew(db, key, whole=True))}
            crews[key]["count"] += 1
            crews[key]["assigned"] += int(bool(row["assignment_id"]))
        ordered = sorted(crews.values(), key=lambda c: natural_key(c["name"]))
        order = {c["id"]: i for i, c in enumerate(ordered)}
        result.sort(key=lambda r: (order[r["crew_id"]], r["full_name"].casefold(), r["personnel_no"]))
        for n, row in enumerate(result, 1):
            row["number"] = n
        info = {"id": batch["id"], "filename": batch["filename"], "imported_at": batch["imported_at"], "sheet": "Явка"} if batch else None
        if info:
            active = db.execute(f'SELECT source_label,filename FROM staffing_imports WHERE id IN ({active_import_ids_sql()}) ORDER BY id').fetchall()
            if len(active) > 1:
                info['filename'] = ' + '.join(item['source_label'] or item['filename'] for item in active)
        if has_outstaff:
            if info is None: info = {'id': None, 'filename': 'Аутстафф', 'sheet': 'Аутстафф'}
            info['has_outstaff'] = True
        if has_manual:
            if info is None: info = {'id': None, 'filename': 'Добавленные вручную сотрудники', 'sheet': ''}
            info['has_manual'] = True
        if has_restored and info is None:
            info = {'id': None, 'filename': 'Восстановленные сотрудники', 'sheet': ''}
        if batch and g.user["role"] in ('admin', 'super_admin'):
            info["summary"] = json.loads(batch["summary_json"])
        if request.args.get("view") == "summary":
            # Keep global filtering available without loading editable worker details.
            fields = ("full_name", "personnel_no", "profession", "category", "department", "employer", "pps",
                      "crew_name", "object_name", "subobject_name", "linear_itr_name", "brigadier_name")
            index = [{**{key: row.get(key) for key in ("id", "crew_id", "number", "department", "employer", "category", "pps",
                       "assignment_id", "assignment_author", "attendance_status", "attendance_token", "employee_shift", "itr_group_key", "itr_group_label", "group_token", "freshness")},
                      "search_fields": [row.get(key) or "" for key in fields]} for row in result]
            return jsonify({"import": info, "crews": ordered, "rows": [], "index": index, **extra})
        return jsonify({"import": info, "crews": ordered, "rows": result, **extra})

    @app.put("/api/staffing/crews/<int:crew_id>/details")
    @roles_required("admin", "foreman")
    def save_crew_details(crew_id):
        payload = request.get_json(silent=True) or {}
        values = {}
        for field in ("linear_itr", "brigadier"):
            value = payload.get(field)
            if not isinstance(value, str) or len(value) > 200:
                abort(400, description="ФИО должно быть текстом не длиннее 200 символов.")
            values[field] = value.strip()
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            crew = access(crew_id)
            require_crew(db, crew_id, whole=True)
            if "expected_token" not in payload or payload["expected_token"] != crew["details_token"]:
                abort(409, description="Данные бригады изменены в другом окне. Обновите таблицу.")
            token = secrets.token_urlsafe(16)
            bindings = [person_binding(payload, field, values[field]) if field + '_person_id' in payload or values[field] != crew[field]
                        else crew[field + '_person_id'] for field in ('linear_itr', 'brigadier')]
            db.execute("UPDATE crews SET linear_itr=?,brigadier=?,details_token=?,linear_itr_person_id=?,brigadier_person_id=? WHERE id=?",
                       (values["linear_itr"], values["brigadier"], token, *bindings, crew_id))
        return jsonify({**values, "details_token": token})

    @app.put("/api/staffing/crews/<int:crew_id>/workers/<int:worker_id>/brigadier", defaults={"field": "brigadier"})
    @app.put("/api/staffing/crews/<int:crew_id>/workers/<int:worker_id>/linear-itr", defaults={"field": "linear_itr"})
    @roles_required("admin", "foreman")
    def save_row_responsible(crew_id, worker_id, field):
        payload = request.get_json(silent=True) or {}
        # Field names come only from the two fixed routes above.
        override = field + "_override"
        value = payload.get(override)
        if override not in payload or (value is not None and (not isinstance(value, str) or len(value) > 200)):
            abort(400, description="Укажите ФИО не длиннее 200 символов или верните значение бригады.")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            crew = access(crew_id)
            require_workers(db, [worker_id])
            if not db.execute("SELECT 1 FROM crew_members WHERE crew_id=? AND worker_id=?", (crew_id, worker_id)).fetchone():
                abort(409, description="Сотрудник больше не состоит в этой бригаде.")
            old = db.execute("SELECT edit_token FROM staffing_row_details WHERE worker_id=?", (worker_id,)).fetchone()
            if "expected_token" not in payload or payload["expected_token"] != (old[0] if old else None):
                abort(409, description="Ответственные в строке уже изменены. Обновите таблицу.")
            token = secrets.token_urlsafe(16)
            value = value.strip() if value is not None else None
            person_id = person_binding(payload, field, value)
            db.execute(f"""INSERT INTO staffing_row_details(worker_id,{override},edit_token,updated_by,updated_at,{field}_person_id)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
                        {override}=excluded.{override},edit_token=excluded.edit_token,
                        {field}_person_id=excluded.{field}_person_id,
                        updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                       (worker_id, value, token, g.user["id"], utc_now(), person_id))
        return jsonify({"row_token": token, override: value,
                        field + "_name": crew[field] if value is None else value})

    @app.put('/api/staffing/crews/<int:crew_id>/workers/responsible')
    @app.put('/api/staffing/groups/responsible', defaults={'crew_id': None})
    @roles_required('admin', 'foreman')
    def save_selected_responsible(crew_id):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            abort(400, description='Некорректные данные назначения.')
        field = payload.get('field')
        if field not in ('linear_itr', 'brigadier'):
            abort(400, description='Выберите линейного ИТР или бригадира.')
        ids = payload.get('worker_ids')
        if not isinstance(ids, list) or not ids or len(ids) > (10000 if crew_id is None else 1000) or any(type(i) is not int or i <= 0 for i in ids):
            abort(400, description='Выберите сотрудников.')
        ids = sorted(set(ids))
        expected = payload.get('expected_tokens')
        if not isinstance(expected, dict) or any(str(i) not in expected for i in ids):
            abort(400, description='Обновите таблицу перед изменением.')
        value = payload.get('value')
        if 'value' not in payload or (value is not None and (not isinstance(value, str) or len(value) > 200)):
            abort(400, description='Укажите ФИО не длиннее 200 символов или верните значение бригады.')
        value = value.strip() if value is not None else None
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            if crew_id is not None:
                access(crew_id)
            else:
                validate_group_snapshot(db, ids, payload)
            actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
            if not actor or not actor['active'] or actor['role'] not in ('super_admin', 'admin', 'foreman'):
                abort(403, description='Нет права изменять ответственных.')
            marks = ','.join('?' for _ in ids)
            members = db.execute(f'''SELECT w.id,d.edit_token,m.crew_id,c.owner_user_id FROM workers w
                LEFT JOIN crew_members m ON m.worker_id=w.id
                LEFT JOIN crews c ON c.id=m.crew_id
                LEFT JOIN staffing_row_details d ON d.worker_id=w.id
                WHERE w.active=1 AND w.id IN ({marks})''', ids).fetchall()
            if len(members) != len(ids):
                abort(409, description='Состав бригады изменился. Обновите таблицу.')
            require_workers(db, ids)
            expected_crews = payload.get('expected_crews')
            if crew_id is None and (not isinstance(expected_crews, dict) or any(
                    str(i) not in expected_crews or not (
                        type(expected_crews[str(i)]) is int or
                        (field == 'linear_itr' and expected_crews[str(i)] is None)) for i in ids)):
                abort(400, description='Обновите состав бригад перед назначением.')
            for member in members:
                if member['crew_id'] != (crew_id if crew_id is not None else expected_crews[str(member['id'])]):
                    abort(409, description='Состав бригады изменился. Обновите таблицу.')
            if any(expected[str(member['id'])] != member['edit_token'] for member in members):
                abort(409, description='Ответственные изменены в другом окне. Обновите таблицу.')
            person_id = person_binding(payload, field, value) if value is not None else None
            # The field is restricted to the two names above; validate the whole batch before writing.
            override = field + '_override'
            for worker_id in ids:
                db.execute(f'''INSERT INTO staffing_row_details(worker_id,{override},edit_token,updated_by,updated_at,{field}_person_id)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
                    {override}=excluded.{override},edit_token=excluded.edit_token,
                    {field}_person_id=excluded.{field}_person_id,
                    updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                    (worker_id, value, secrets.token_urlsafe(16), g.user['id'], utc_now(), person_id))
        return jsonify({'updated': len(ids)})

    def uploaded():
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".xlsx"):
            raise ImportProblem("Выберите файл XLSX.")
        parsed = parse_attendance(upload.read(), upload.filename)
        parsed['source_label'] = request.form.get('source_label', '')
        if parsed['source_label'] not in ('', 'ППС15', 'ППС19'):
            raise ImportProblem('Выберите ППС15 или ППС19.')
        return parsed

    @app.post("/api/staffing/import/preview")
    @roles_required("admin")
    def preview_import():
        try:
            parsed = uploaded()
            issues = import_conflicts(get_db(), parsed)
            signer = URLSafeTimedSerializer(app.secret_key, salt="attendance-import")
            token = signer.dumps({"sha256": parsed["sha256"], "user_id": g.user["id"], 'source_label': parsed['source_label']})
            return jsonify({"summary": removal_import_summary(get_db(), parsed), "groups": parsed["groups"], "issues": issues,
                            "filename": parsed["filename"], "preview_token": token})
        except ImportProblem as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/staffing/import/apply")
    @roles_required("admin")
    def apply_import():
        require_all(get_db())
        try:
            parsed = uploaded()
            signer = URLSafeTimedSerializer(app.secret_key, salt="attendance-import")
            signed = signer.loads(request.form.get("preview_token", ""), max_age=900)
            if signed != {"sha256": parsed["sha256"], "user_id": g.user["id"], 'source_label': parsed['source_label']}:
                raise ImportProblem("Файл изменился. Проверьте импорт заново.")
            return jsonify(apply_attendance(get_db(), parsed, g.user["id"], utc_now, authorize=require_all))
        except (ImportProblem, BadSignature) as error:
            return jsonify({"error": str(error) if isinstance(error, ImportProblem) else "Предпросмотр устарел. Проверьте файл заново."}), 400
