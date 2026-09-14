"""Preview attendance; --apply explicitly imports after a SQLite backup."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staffing_import import apply_attendance, parse_attendance


def main():
    parser = argparse.ArgumentParser(description="Импорт листа «Явка» для расстановки")
    parser.add_argument("input", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    parsed = parse_attendance(args.input.read_bytes(), args.input.name)
    report = {k: v for k, v in parsed.items() if k not in {"rows", "people"}}
    if args.apply:
        import app
        with app.app.app_context():
            db = app.get_db()
            user = db.execute("SELECT id FROM users WHERE username=? AND role IN ('admin','super_admin') AND active=1",
                              (os.environ.get("ADMIN_USERNAME", "admin"),)).fetchone()
            if user is None:
                raise SystemExit("Не найдена действующая учётная запись администратора.")
            report["result"] = apply_attendance(db, parsed, user["id"], app.utc_now)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
