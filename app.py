import hmac
import hashlib
import json
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from user_activity import migrate_user_activity, start_session, end_session, session_valid, register_user_activity


BASE_DIR = Path(__file__).resolve().parent
DATABASE_BACKEND = os.getenv('DATABASE_BACKEND', 'sqlite')
if DATABASE_BACKEND not in ('sqlite', 'postgres'):
    raise RuntimeError('DATABASE_BACKEND must explicitly be sqlite or postgres.')
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "placement.db"))
SECRET_KEY = os.getenv("SECRET_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY должен содержать не менее 32 символов.")
if len(ADMIN_PASSWORD) < 10:
    raise RuntimeError("ADMIN_PASSWORD должен содержать не менее 10 символов.")

app = Flask(__name__)
app.json.ensure_ascii = False
if DATABASE_BACKEND == 'postgres':
    from postgres_db import ConcurrentChange

    @app.errorhandler(ConcurrentChange)
    def concurrent_change(_error):
        return jsonify({'error': 'Данные изменены другим пользователем. Обновите значения и повторите действие.'}), 409
app.config.update(
    WORKFORCE_ENABLED=DATABASE_BACKEND == 'postgres',
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    MAX_CONTENT_LENGTH=21_000_000,
    SESSION_COOKIE_NAME=os.getenv('SESSION_COOKIE_NAME', 'crew_staging_session' if os.getenv('APP_ENVIRONMENT') == 'staging' else 'session'),
)
from security import browser_headers
app.after_request(browser_headers)
from api_errors import register_api_errors
register_api_errors(app)
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

ASSET_VERSIONS = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    for path in (BASE_DIR / 'static').iterdir() if path.is_file()
}


def asset_url(filename):
    return url_for('static', filename=filename, v=ASSET_VERSIONS[filename])


app.jinja_env.globals['asset_url'] = asset_url


@app.after_request
def cache_policy(response):
    if request.endpoint == 'static':
        filename = (request.view_args or {}).get('filename')
        version = ASSET_VERSIONS.get(filename)
        if version and request.args.get('v') == version and response.status_code in (200, 304):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response
    # Authenticate and compute the current representation before validating a cached copy.
    memory_only = request.endpoint in ('workforce_people', 'workforce_person')
    if (request.method in ('GET', 'HEAD') and response.status_code == 200
            and (memory_only or request.path in ('/api/reference', '/api/staffing/people', '/api/workforce/reference'))):
        identity = f"{g.user['id']}:{g.user['role']}:".encode('utf-8')
        response.set_etag(hashlib.sha256(identity + response.get_data()).hexdigest())
        # Personnel responses may be reused explicitly in this tab's memory only.
        response.headers['Cache-Control'] = 'no-store' if memory_only else 'private, no-cache, max-age=0, must-revalidate'
        response.vary.add('Cookie')
        response.make_conditional(request)
    else:
        response.headers['Cache-Control'] = 'no-store'
    return response

LOGIN_ATTEMPTS = defaultdict(list)
LOGIN_WINDOW_SECONDS = 600
LOGIN_LIMIT = 5


def utc_now():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_db():
    if "db" not in g:
        if DATABASE_BACKEND == 'postgres':
            from postgres_db import PostgresConnection
            g.db = PostgresConnection()
            return g.db
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        from staffing_history import HistoryConnection
        g.db = sqlite3.connect(DATABASE_PATH, timeout=15, factory=HistoryConnection)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 15000")
    return g.db


@app.teardown_appcontext
def close_db(_error):
    database = g.pop("db", None)
    if database is not None:
        database.close()


def init_db():
    if DATABASE_BACKEND == 'postgres':
        from postgres_db import get_pool
        get_pool()
        return
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")
    from user_roles import migrate_user_roles
    migrate_user_roles(db)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'foreman', 'viewer', 'hr_viewer', 'rotation', 'recruitment')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            personnel_no TEXT NOT NULL UNIQUE COLLATE NOCASE,
            contractor TEXT NOT NULL DEFAULT '',
            employer TEXT NOT NULL DEFAULT '',
            profession TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS subobjects (
            id INTEGER PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            UNIQUE(object_id, name)
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY,
            work_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            subobject_id INTEGER NOT NULL REFERENCES subobjects(id),
            worker_id INTEGER NOT NULL REFERENCES workers(id),
            employer TEXT NOT NULL DEFAULT '',
            foreman_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            UNIQUE(work_date, shift, worker_id)
        );
        CREATE TABLE IF NOT EXISTS staffing_plans (
            id INTEGER PRIMARY KEY,
            work_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            subobject_id INTEGER NOT NULL REFERENCES subobjects(id) ON DELETE CASCADE,
            planned_count INTEGER NOT NULL CHECK(planned_count >= 0),
            created_by INTEGER NOT NULL REFERENCES users(id),
            updated_at TEXT NOT NULL,
            UNIQUE(work_date, shift, subobject_id)
        );
        CREATE TABLE IF NOT EXISTS crews (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            UNIQUE(owner_user_id, name)
        );
        CREATE TABLE IF NOT EXISTS daily_staffing_plans (
            subobject_id INTEGER NOT NULL REFERENCES subobjects(id),
            work_date TEXT NOT NULL,
            planned_count INTEGER NOT NULL CHECK(planned_count >= 0),
            updated_by INTEGER NOT NULL REFERENCES users(id),
            updated_at TEXT NOT NULL,
            edit_token TEXT NOT NULL,
            PRIMARY KEY(subobject_id, work_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_plans_date ON daily_staffing_plans(work_date);
        CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS crew_members (
            crew_id INTEGER NOT NULL REFERENCES crews(id),
            worker_id INTEGER NOT NULL UNIQUE REFERENCES workers(id),
            PRIMARY KEY(crew_id, worker_id)
        );
        CREATE TABLE IF NOT EXISTS assignment_events (
            id INTEGER PRIMARY KEY,
            crew_id INTEGER REFERENCES crews(id),
            worker_id INTEGER NOT NULL REFERENCES workers(id),
            work_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            before_subobject_id INTEGER REFERENCES subobjects(id),
            after_subobject_id INTEGER REFERENCES subobjects(id),
            changed_by INTEGER NOT NULL REFERENCES users(id),
            changed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_assignments_date ON assignments(work_date);
        CREATE INDEX IF NOT EXISTS idx_assignments_dashboard
            ON assignments(work_date, shift, subobject_id, employer);
        CREATE INDEX IF NOT EXISTS idx_subobjects_object ON subobjects(object_id);
        CREATE INDEX IF NOT EXISTS idx_staffing_plans_date_shift
            ON staffing_plans(work_date, shift, subobject_id);
        """
    )
    migrate_user_activity(db)
    from staffing_import import migrate_staffing
    migrate_staffing(db)
    from staffing_shifts import migrate_crewless_assignments
    migrate_crewless_assignments(db)
    from gdlr_api import migrate_gdlr
    migrate_gdlr(db)
    from contractor_api import migrate_contractors
    migrate_contractors(db)
    from employer_api import migrate_employers
    migrate_employers(db)
    from location_api import migrate_locations
    migrate_locations(db)
    from attendance_status import migrate_attendance_status
    migrate_attendance_status(db)
    from performed_work import migrate_performed_work
    migrate_performed_work(db)
    from work_types import migrate_work_types
    migrate_work_types(db)
    from selected_transfer import migrate_selected_transfer
    migrate_selected_transfer(db)
    from staffing_history import migrate_history
    migrate_history(db)
    from employee_removal import migrate_employee_removals
    migrate_employee_removals(db)
    from day_inheritance import migrate_day_inheritance
    migrate_day_inheritance(db)
    from outstaff_api import migrate_outstaff
    migrate_outstaff(db)
    from user_preferences import migrate_preferences
    migrate_preferences(db)
    from user_profile import migrate_user_profile
    migrate_user_profile(db)
    from manual_employees import migrate_manual_employees
    migrate_manual_employees(db)
    from user_smu_access import migrate_smu_access
    migrate_smu_access(db)
    from smu_api import migrate_smu_catalog
    migrate_smu_catalog(db)
    from pps_api import migrate_pps_catalog
    migrate_pps_catalog(db)
    from telegram_api import migrate_telegram
    migrate_telegram(db)
    from placement_verification import migrate_verification
    migrate_verification(db)
    # Additive migration: existing assignments retain their date, owner and site.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(assignments)")}
    if "crew_id" not in columns:
        db.execute("ALTER TABLE assignments ADD COLUMN crew_id INTEGER REFERENCES crews(id)")
    if "edit_token" not in columns:
        db.execute("ALTER TABLE assignments ADD COLUMN edit_token TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS idx_assignments_crew_date ON assignments(crew_id, work_date)")
    if not db.execute("SELECT 1 FROM schema_versions WHERE version = 2").fetchone():
        db.execute("""INSERT INTO daily_staffing_plans(subobject_id, work_date, planned_count,
                         updated_by, updated_at, edit_token)
                      SELECT subobject_id, work_date, SUM(planned_count), MIN(created_by), MAX(updated_at), lower(hex(randomblob(16)))
                      FROM staffing_plans GROUP BY subobject_id, work_date""")
        db.execute("INSERT INTO schema_versions(version) VALUES (2)")
    admin_username = os.getenv("ADMIN_USERNAME", "admin").strip()
    admin_password = ADMIN_PASSWORD
    db.execute(
        """INSERT OR IGNORE INTO users
           (username, password_hash, full_name, role, created_at)
           VALUES (?, ?, ?, 'admin', ?)""",
        (admin_username, generate_password_hash(admin_password), "Администратор", utc_now()),
    )
    if (not db.execute("SELECT 1 FROM schema_versions WHERE version=3").fetchone()
            and db.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 0):
        seed_path = BASE_DIR / "seed.json"
        if seed_path.exists():
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
            for object_item in seed.get("objects", []):
                db.execute("INSERT OR IGNORE INTO objects(name) VALUES (?)", (object_item["name"],))
                object_id = db.execute(
                    "SELECT id FROM objects WHERE name = ? COLLATE NOCASE", (object_item["name"],)
                ).fetchone()["id"]
                for subobject in object_item.get("subobjects", []):
                    db.execute(
                        "INSERT OR IGNORE INTO subobjects(object_id, name) VALUES (?, ?)",
                        (object_id, subobject),
                    )
            for worker in seed.get("workers", []):
                db.execute(
                    """INSERT OR IGNORE INTO workers
                       (full_name, personnel_no, contractor, employer, profession)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        worker["full_name"], worker["personnel_no"], worker.get("contractor", ""),
                        worker.get("employer", ""), worker.get("profession", ""),
                    ),
                )
    # An intentionally emptied location catalog must stay empty after restart.
    db.execute("INSERT OR IGNORE INTO schema_versions(version) VALUES (3)")
    db.commit()
    from contractor_api import populate_contractors
    populate_contractors(db)


def current_user():
    user_id = session.get("user_id")
    if not user_id or not session_valid(get_db(), user_id):
        return None
    return get_db().execute(
        "SELECT id, username, full_name, role, active FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user["active"]:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется вход"}), 401
            return redirect(url_for("login"))
        g.user = user
        from user_roles import HR_VIEWER, hr_request_allowed
        if user['role'] == HR_VIEWER and not hr_request_allowed(request.endpoint, request.method):
            return jsonify({'error': 'Для этой роли доступен только просмотр данных.'}), 403
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    if 'admin' in roles:
        roles = (*roles, 'super_admin')
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(*args, **kwargs):
            from user_roles import HR_VIEWER, WORKFORCE_SERVICE_ROLES, WORKFORCE_LEGACY_READ_ENDPOINTS
            # login_required has already checked the read-only endpoint allowlist.
            service_read = (g.user['role'] in WORKFORCE_SERVICE_ROLES and request.method in ('GET', 'HEAD')
                            and request.endpoint in WORKFORCE_LEGACY_READ_ENDPOINTS)
            if g.user["role"] not in roles and g.user['role'] != HR_VIEWER and not service_read:
                return jsonify({"error": "Недостаточно прав"}), 403
            return view(*args, **kwargs)

        if DATABASE_BACKEND == 'postgres':
            from postgres_retry import staffing_transaction_retry
            return staffing_transaction_retry(wrapped, get_db)
        return wrapped

    return decorator


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.before_request
def csrf_protection():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Сессия устарела. Обновите страницу."}), 403
            return "Недействительный CSRF-токен", 403


app.jinja_env.globals["csrf_token"] = csrf_token


@app.get("/health")
def health():
    if DATABASE_BACKEND == 'postgres':
        get_db().native('SELECT 1')
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("index"))
        return render_template("login.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if len(username) > 200 or len(password) > 1024:
        return render_template('login.html', error='Неверный логин или пароль.'), 401
    key = f"{request.remote_addr}:{username.lower()}"
    now = time.time()
    if DATABASE_BACKEND == 'postgres':
        from security import reserve_login
        allowed = reserve_login(get_db(), app.secret_key, request.remote_addr or '', username)
    else:
        LOGIN_ATTEMPTS[key] = [stamp for stamp in LOGIN_ATTEMPTS[key] if now - stamp < LOGIN_WINDOW_SECONDS]
        allowed = len(LOGIN_ATTEMPTS[key]) < LOGIN_LIMIT
    if not allowed:
        return render_template("login.html", error="Слишком много попыток. Повторите через 10 минут."), 429
    user = get_db().execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    password_valid = check_password_hash(user['password_hash'] if user else DUMMY_PASSWORD_HASH, password)
    if not user or not user["active"] or not password_valid:
        if DATABASE_BACKEND != 'postgres':
            LOGIN_ATTEMPTS[key].append(now)
        return render_template("login.html", error="Неверный логин или пароль."), 401
    LOGIN_ATTEMPTS.pop(key, None)
    if DATABASE_BACKEND == 'postgres':
        from security import clear_login
        clear_login(get_db(), app.secret_key, request.remote_addr or '', username)
    end_session(get_db())
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    start_session(get_db(), user["id"], successful_login=True)
    csrf_token()
    return redirect(url_for("index"))


@app.post("/logout")
@login_required
def logout():
    end_session(get_db())
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    from user_preferences import staffing_preferences
    return render_template("index.html", user=dict(g.user), today=datetime.now(timezone(timedelta(hours=3))).date().isoformat(),
                           staffing_preferences=staffing_preferences(get_db(), g.user["id"]),
                           workforce_enabled=DATABASE_BACKEND == 'postgres')


@app.get("/api/reference")
@login_required
def reference():
    db = get_db()
    objects = [dict(row) for row in db.execute('''SELECT o.id,o.name,o.stage_id,s.name stage_name
        FROM objects o LEFT JOIN location_stages s ON s.id=o.stage_id ORDER BY o.name''')]
    stages = [dict(row) for row in db.execute('SELECT id,name FROM location_stages ORDER BY id')]
    subobjects = [dict(row) for row in db.execute(
        "SELECT id, object_id, name FROM subobjects ORDER BY object_id, name"
    )]
    if request.args.get('scope') == 'locations':
        return jsonify({'objects': objects, 'subobjects': subobjects, 'stages': stages})
    workers = [dict(row) for row in db.execute(
        """SELECT id, full_name, personnel_no, contractor, employer, profession
           FROM workers WHERE active = 1 ORDER BY full_name"""
    )]
    return jsonify({"objects": objects, "subobjects": subobjects, "workers": workers, "stages": stages})


@app.get("/api/dashboard")
@login_required
def dashboard():
    return jsonify({"error": "Сводка перенесена в календарную таблицу. Обновите страницу."}), 410


@app.get("/api/activity-dates")
@login_required
def activity_dates():
    from user_smu_access import explicit_scope, worker_clause
    clause = "" if g.user["role"] != "foreman" else " WHERE foreman_user_id = ? OR crew_id IN (SELECT id FROM crews WHERE owner_user_id = ?)"
    params = () if not clause else (g.user["id"], g.user["id"])
    if g.user['role'] != 'viewer' and explicit_scope(get_db()):
        access, params = worker_clause(get_db())
        clause = ' WHERE worker_id IN (SELECT w.id FROM workers w WHERE ' + access + ')'
    rows = [dict(row) for row in get_db().execute(
        "SELECT work_date, COUNT(*) people_count FROM assignments" + clause
        + " GROUP BY work_date ORDER BY work_date DESC LIMIT 14", params
    )]
    return jsonify({"rows": rows})


@app.get("/api/assignments")
@login_required
def assignments():
    from user_smu_access import explicit_scope, worker_clause
    work_date = request.args.get("date", date.today().isoformat())
    clause = "" if g.user["role"] != "foreman" else " AND (a.foreman_user_id = ? OR a.crew_id IN (SELECT id FROM crews WHERE owner_user_id = ?))"
    params = [work_date] if not clause else [work_date, g.user["id"], g.user["id"]]
    if g.user['role'] != 'viewer' and explicit_scope(get_db()):
        access, access_params = worker_clause(get_db())
        clause, params = ' AND (' + access + ')', [work_date, *access_params]
    rows = [dict(row) for row in get_db().execute(
        """SELECT a.id, a.work_date, a.shift, a.employer, a.foreman_user_id,
                  w.full_name worker_name, w.personnel_no, w.profession,
                  s.name subobject_name, o.name object_name, u.full_name foreman_name
           FROM assignments a
           JOIN workers w ON w.id = a.worker_id
           JOIN subobjects s ON s.id = a.subobject_id
           JOIN objects o ON o.id = s.object_id
           JOIN users u ON u.id = a.foreman_user_id
           WHERE a.work_date = ?""" + clause + " ORDER BY a.shift, o.name, s.name, w.full_name",
        params,
    )]
    return jsonify({"rows": rows})


@app.post("/api/assignments")
@roles_required("admin", "foreman")
def create_assignments():
    return jsonify({"error": "Откройте бригаду и укажите сотрудникам место работы. Обновите страницу."}), 410


@app.delete("/api/assignments/<int:assignment_id>")
@roles_required("admin", "foreman")
def delete_assignment(assignment_id):
    from user_smu_access import explicit_scope, require_workers
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    row = db.execute("SELECT worker_id, foreman_user_id, crew_id FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not row:
        return jsonify({"error": "Назначение не найдено."}), 404
    if row["crew_id"] is not None:
        return jsonify({"error": "Измените место работы в списке бригады. Обновите страницу."}), 409
    if explicit_scope(db):
        require_workers(db, [row['worker_id']])
    elif g.user["role"] not in ('admin', 'super_admin') and row["foreman_user_id"] != g.user["id"]:
        return jsonify({"error": "Можно отменять только собственные назначения."}), 403
    db.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
    db.commit()
    return jsonify({"deleted": assignment_id})


@app.route("/api/plans", methods=["GET", "POST"])
@app.delete("/api/plans/<int:plan_id>")
@login_required
def retired_plans(plan_id=None):
    return jsonify({"error": "План задаётся посуточно в новой таблице. Обновите страницу."}), 410


@app.get("/api/users")
@roles_required("admin")
def users():
    rows = [dict(row) for row in get_db().execute(
        """SELECT u.id,u.username,u.full_name,u.role,u.active,u.created_at,COUNT(c.id) crew_count
           FROM users u LEFT JOIN crews c ON c.owner_user_id=u.id GROUP BY u.id ORDER BY u.full_name"""
    )]
    return jsonify({"rows": rows})


@app.post("/api/users")
@roles_required("super_admin")
def create_user():
    from user_roles import USER_ROLES
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    full_name = str(payload.get("full_name", "")).strip()
    password = str(payload.get("password", ""))
    role = str(payload.get("role", "foreman"))
    if len(username) < 3 or not full_name or len(password) < 10 or role not in USER_ROLES:
        return jsonify({"error": "Проверьте поля. Пароль должен содержать не менее 10 символов."}), 400
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not actor or not actor['active'] or actor['role'] != 'super_admin':
            db.rollback()
            return jsonify({'error': 'Выдавать доступ может только супер-администратор.'}), 403
        cursor = db.execute(
            "INSERT INTO users(username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), full_name, role, utc_now()),
        )
        get_db().commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Такой логин уже используется."}), 409
    return jsonify({"id": cursor.lastrowid}), 201


@app.patch("/api/users/<int:user_id>")
@roles_required("super_admin")
def update_user(user_id):
    from user_roles import USER_ROLES
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Некорректные данные пользователя."}), 400
    if user_id == g.user["id"] and payload.get("active") is False:
        return jsonify({"error": "Нельзя отключить собственную учётную запись."}), 400
    fields = []
    params = []
    if 'full_name' in payload:
        name = payload['full_name']
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            return jsonify({'error': 'Укажите ФИО не длиннее 200 символов.'}), 400
        if 'expected_full_name' not in payload:
            return jsonify({'error': 'Обновите список пользователей перед изменением ФИО.'}), 400
        fields.append('full_name = ?')
        params.append(name.strip())
    if "active" in payload:
        if type(payload['active']) is not bool:
            return jsonify({"error": "Укажите состояние учётной записи."}), 400
        fields.append("active = ?")
        params.append(1 if payload["active"] else 0)
    if 'role' in payload:
        if not isinstance(payload['role'], str) or payload['role'] not in USER_ROLES:
            return jsonify({'error': 'Выберите роль из списка.'}), 400
        if 'expected_role' not in payload:
            return jsonify({'error': 'Обновите список пользователей перед сменой роли.'}), 400
        fields.append('role = ?')
        params.append(payload['role'])
    if payload.get("password"):
        if len(str(payload["password"])) < 10:
            return jsonify({"error": "Пароль должен содержать не менее 10 символов."}), 400
        fields.append("password_hash = ?")
        params.append(generate_password_hash(str(payload["password"])))
    if not fields:
        return jsonify({"error": "Нет изменений."}), 400
    db = get_db()
    with db:
        db.execute('BEGIN IMMEDIATE')
        actor = db.execute('SELECT role,active FROM users WHERE id=?', (g.user['id'],)).fetchone()
        if not actor or actor['role'] != 'super_admin' or not actor['active']:
            return jsonify({'error': 'Изменять учётные записи может только супер-администратор.'}), 403
        user = db.execute('SELECT role,active,full_name FROM users WHERE id=?', (user_id,)).fetchone()
        if user is None:
            return jsonify({"error": "Пользователь не найден."}), 404
        if 'role' in payload and payload['expected_role'] != user['role']:
            return jsonify({'error': 'Роль уже изменена в другом окне. Обновите список.'}), 409
        if 'full_name' in payload and payload['expected_full_name'] != user['full_name']:
            return jsonify({'error': 'ФИО уже изменено в другом окне. Обновите список.'}), 409
        next_role, next_active = payload.get('role', user['role']), payload.get('active', user['active'])
        if user['role'] == 'super_admin' and user['active'] and (next_role != 'super_admin' or not next_active):
            others = db.execute("SELECT COUNT(*) FROM users WHERE role='super_admin' AND active=1 AND id<>?", (user_id,)).fetchone()[0]
            if not others:
                return jsonify({'error': 'Должен остаться хотя бы один действующий супер-администратор.'}), 409
        if user['role'] == 'admin' and user['active'] and (next_role != 'admin' or not next_active):
            others = db.execute("SELECT COUNT(*) FROM users WHERE role IN ('admin','super_admin') AND active=1 AND id<>?", (user_id,)).fetchone()[0]
            if not others:
                return jsonify({'error': 'Должен остаться хотя бы один действующий администратор.'}), 409
        db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", [*params, user_id])
        if payload.get('password') or next_role != user['role'] or not next_active:
            db.execute('UPDATE user_sessions SET ended_at=? WHERE user_id=? AND ended_at IS NULL',
                       (int(time.time()), user_id))
    return jsonify({"updated": user_id})


from crew_api import register_crew_routes
from gdlr_api import register_gdlr_routes
from contractor_api import register_contractor_routes
from backup_api import register_backup_routes
from logs_api import register_log_routes
from location_api import register_location_routes
from table_api import register_table_routes
from staffing_api import register_staffing_routes
from personnel_dashboard import register_personnel_dashboard
from staffing_export import register_staffing_export_route

from outstaff_api import register_outstaff_routes
register_outstaff_routes(app, get_db, roles_required, utc_now)
from user_preferences import register_preferences
register_preferences(app, get_db, roles_required, utc_now)
from user_profile import register_user_profile
register_user_profile(app, get_db, roles_required, utc_now)
from user_smu_access import register_smu_access
register_smu_access(app, get_db, roles_required, utc_now)
register_crew_routes(app, get_db, roles_required, utc_now)
register_gdlr_routes(app, get_db, roles_required, utc_now)
register_contractor_routes(app, get_db, roles_required, utc_now)
from employer_api import register_employer_routes
register_employer_routes(app, get_db, roles_required, utc_now)
from smu_api import register_smu_routes
register_smu_routes(app, get_db, roles_required, utc_now)
from pps_api import register_pps_routes
register_pps_routes(app, get_db, roles_required, utc_now)
register_backup_routes(app, get_db, roles_required)
register_location_routes(app, get_db, roles_required, utc_now)
register_log_routes(app, get_db, roles_required)
register_table_routes(app, get_db, roles_required, utc_now)
register_staffing_routes(app, get_db, roles_required, utc_now)
from work_types import register_work_types
register_work_types(app, get_db, roles_required, utc_now)
from staffing_history import register_history
register_history(app, get_db, roles_required, utc_now)
from day_inheritance import register_day_inheritance
register_day_inheritance(app, get_db, roles_required, utc_now)
register_personnel_dashboard(app, get_db, roles_required)
register_user_activity(app, get_db, login_required, roles_required)
register_staffing_export_route(app, get_db, roles_required)
from position_cards import register_position_cards_route
register_position_cards_route(app, get_db, roles_required)
from placement_verification import register_verification
register_verification(app, get_db, roles_required, utc_now)
from placement_report import register_placement_report
register_placement_report(app, get_db, roles_required)
from telegram_api import register_telegram
register_telegram(app, get_db, roles_required)
from selected_transfer import register_selected_transfer
register_selected_transfer(app, get_db, roles_required, utc_now)
from workforce_api import register_workforce_routes
register_workforce_routes(app, get_db, roles_required)
if os.environ.get('DATABASE_BACKEND') == 'postgres':
    from ticket_import import register_ticket_import
    register_ticket_import(app, get_db, roles_required)
    from rotation_summary import register_rotation_summary
    register_rotation_summary(app, get_db, roles_required)
    from smg_api import register_smg_routes
    register_smg_routes(app, get_db, roles_required)
    from workforce_operations import register_workforce_operations
    register_workforce_operations(app, get_db, roles_required)
    from report_closure import register_report_closure
    from report_closure_access import authorize_report_scope, register_report_scope_options
    register_report_scope_options(app, get_db, roles_required)
    register_report_closure(app, get_db, roles_required, utc_now, authorize_scope=authorize_report_scope)

with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
