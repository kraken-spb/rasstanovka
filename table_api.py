from filter_values import argument as filter_argument, values as filter_values, matches as filter_matches, label as filter_label
"""Daily plans and a shared day/night fact projection for the calendar tables."""
import secrets
from datetime import date, timedelta

from flask import abort, g, jsonify, request

from attendance_status import calendar_absences
from report_queries import CATEGORY_SQL, calendar_facts


def register_table_routes(app, get_db, roles_required, utc_now):
    @app.get("/api/calendar")
    @roles_required("admin", "foreman", "viewer")
    def calendar():
        try:
            start = date.fromisoformat(request.args.get("start", date.today().isoformat()))
            days = int(request.args.get("days", "7"))
            if days < 1 or days > 31:
                raise ValueError
            end = start + timedelta(days=days - 1)
        except (ValueError, OverflowError):
            abort(400, description="Выберите период от 1 до 31 дня.")
        dates = [(start + timedelta(days=index)).isoformat() for index in range(days)]
        category = filter_argument('category')
        if category is not None and len(category) > 200:
            abort(400, description='Категория не должна превышать 200 символов.')
        db = get_db()
        db.execute('BEGIN')
        category_sql = CATEGORY_SQL
        facts = calendar_facts(db, dates[0], dates[-1], category=category)
        plans = [dict(row) for row in db.execute(
            """SELECT p.*, s.object_id FROM daily_staffing_plans p
               JOIN subobjects s ON s.id = p.subobject_id WHERE p.work_date BETWEEN ? AND ?""",
            (dates[0], dates[-1]),
        )]
        categories = sorted((row[0] for row in db.execute(
            "SELECT DISTINCT " + category_sql + " FROM workers w LEFT JOIN employee_gdlr eg ON eg.worker_id=w.id "
            "LEFT JOIN gdlr_categories gc ON gc.id=eg.category_id UNION SELECT name FROM gdlr_categories"
        )), key=str.casefold)
        return jsonify({"dates": dates, "facts": facts, "plans": plans, "categories": categories,
                        "absences": calendar_absences(db, dates[0], dates[-1], category)})

    @app.put("/api/daily-plans")
    @roles_required("admin")
    def set_daily_plan():
        payload = request.get_json(silent=True) or {}
        try:
            work_date = date.fromisoformat(str(payload.get("date", ""))).isoformat()
        except ValueError:
            abort(400, description="Укажите дату плана.")
        site = payload.get("subobject_id")
        count = payload.get("planned_count")
        if "planned_count" not in payload:
            abort(400, description="Укажите численность или пустую ячейку.")
        if type(site) is not int or (count is not None and (type(count) is not int or not 0 <= count <= 100000)):
            abort(400, description="Введите целое число от 0 до 100000 или оставьте ячейку пустой.")
        if "expected_token" not in payload:
            abort(400, description="Обновите таблицу плана.")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM subobjects WHERE id = ?", (site,)).fetchone():
                abort(404, description="Подобъект не найден.")
            old = db.execute("SELECT * FROM daily_staffing_plans WHERE subobject_id = ? AND work_date = ?", (site, work_date)).fetchone()
            if (old and old["planned_count"] == count) or (old is None and count is None):
                return jsonify({"planned_count": count, "edit_token": old["edit_token"] if old else None})
            if payload["expected_token"] != (old["edit_token"] if old else None):
                abort(409, description="Эту ячейку уже изменили. Обновите таблицу перед повторным вводом.")
            token = secrets.token_urlsafe(16) if count is not None else None
            if count is None:
                db.execute("DELETE FROM daily_staffing_plans WHERE subobject_id = ? AND work_date = ?", (site, work_date))
            else:
                db.execute(
                    """INSERT INTO daily_staffing_plans VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(subobject_id, work_date) DO UPDATE SET planned_count = excluded.planned_count,
                          updated_by = excluded.updated_by, updated_at = excluded.updated_at, edit_token = excluded.edit_token""",
                    (site, work_date, count, g.user["id"], utc_now(), token),
                )
        return jsonify({"planned_count": count, "edit_token": token})
