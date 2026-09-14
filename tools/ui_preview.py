"""Disposable local UI fixture. Never connects to the Docker/production database."""
import os
import secrets
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="crew-ui-") as temporary:
        os.environ["DATABASE_PATH"] = str(Path(temporary) / "ui.db")
        os.environ["SECRET_KEY"] = secrets.token_hex(32)
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "ui-test-password-123"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import app
        app.app.config["TEMPLATES_AUTO_RELOAD"] = True
        with app.app.app_context():
            db = app.get_db()
            owner = db.execute(
                "INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES (?,?,?,?,?)",
                ("foreman", app.generate_password_hash("ui-test-password-123"), "Тестовый прораб", "foreman", app.utc_now()),
            ).lastrowid
            db.execute("INSERT INTO users(username,password_hash,full_name,role,created_at) VALUES (?,?,?,?,?)",
                       ('superadmin', app.generate_password_hash('ui-test-password-123'), 'Супер-администратор', 'super_admin', app.utc_now()))
            for name in ("Бригада монтажа", "Бригада сварки"):
                db.execute("INSERT INTO crews(name,owner_user_id,created_at) VALUES (?,?,?)", (name, owner, app.utc_now()))
            db.commit()
            if os.environ.get("UI_STAFFING_FILE"):
                from staffing_import import apply_attendance, parse_attendance
                source = Path(os.environ["UI_STAFFING_FILE"])
                parsed = parse_attendance(source.read_bytes(), source.name)
                apply_attendance(db, parsed, owner, app.utc_now)
        app.app.run(host="127.0.0.1", port=18088, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
