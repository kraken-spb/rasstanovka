import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DailyTableTest(unittest.TestCase):
    """Isolated API tests; no imported TestCase classes or live database access."""

    @classmethod
    def setUpClass(cls):
        cls.bootstrap = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            "DATABASE_PATH": str(Path(cls.bootstrap.name) / "bootstrap.db"),
            "ADMIN_PASSWORD": "table-test-password-123",
            "SECRET_KEY": "table-test-secret-key-at-least-thirty-two-characters",
        }):
            cls.module = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.bootstrap.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous_path = self.module.DATABASE_PATH
        previous_testing = self.module.app.config["TESTING"]
        self.addCleanup(setattr, self.module, "DATABASE_PATH", previous_path)
        self.addCleanup(self.module.app.config.__setitem__, "TESTING", previous_testing)
        self.module.DATABASE_PATH = Path(temporary.name) / "tables.db"
        self.module.app.config["TESTING"] = True
        with self.module.app.app_context():
            self.module.init_db()
            db = self.module.get_db()
            self.admin_id = db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()["id"]
            self.role_ids = {}
            for role in ("foreman", "viewer"):
                self.role_ids[role] = db.execute(
                    "INSERT INTO users(username, password_hash, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                    (f"table_{role}", "unused-test-hash", f"Table {role}", role, self.module.utc_now()),
                ).lastrowid
            self.object_id = db.execute("INSERT INTO objects(name) VALUES ('Table test object')").lastrowid
            self.sites = [db.execute(
                "INSERT INTO subobjects(object_id, name) VALUES (?, ?)",
                (self.object_id, f"Table test site {number}"),
            ).lastrowid for number in range(2)]
            db.commit()
        self.admin = self.client_for(self.admin_id)
        self.foreman = self.client_for(self.role_ids["foreman"])
        self.viewer = self.client_for(self.role_ids["viewer"])

    def client_for(self, user_id):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "table-test-csrf-token"
        return client

    def put(self, count, token=None, site=None, work_date="2026-09-11", client=None):
        return (client or self.admin).put("/api/daily-plans", json={
            "date": work_date, "subobject_id": site or self.sites[0],
            "planned_count": count, "expected_token": token,
        }, headers={"X-CSRF-Token": "table-test-csrf-token"})

    def calendar(self, start="2026-09-11", days=7, client=None, category=None):
        query = {"start": start, "days": days}
        if category is not None:
            query['category'] = category
        response = (client or self.admin).get("/api/calendar", query_string=query)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def plan_rows(self):
        with self.module.app.app_context():
            return [dict(row) for row in self.module.get_db().execute(
                "SELECT * FROM daily_staffing_plans ORDER BY subobject_id, work_date"
            )]

    def test_create_update_delete_are_bound_to_date_and_subobject(self):
        created = self.put(3)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.put(8, work_date="2026-09-12").status_code, 200)
        self.assertEqual(self.put(4, site=self.sites[1]).status_code, 200)
        updated = self.put(6, created.get_json()["edit_token"])
        self.assertEqual(updated.status_code, 200)
        plans = {(row["subobject_id"], row["work_date"]): row["planned_count"] for row in self.plan_rows()}
        self.assertEqual(plans, {
            (self.sites[0], "2026-09-11"): 6,
            (self.sites[0], "2026-09-12"): 8,
            (self.sites[1], "2026-09-11"): 4,
        })
        deleted = self.put(None, updated.get_json()["edit_token"])
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(deleted.get_json()["edit_token"])
        self.assertEqual(len(self.plan_rows()), 2)
        self.assertEqual({row["planned_count"] for row in self.plan_rows()}, {4, 8})

    def test_zero_is_a_plan_and_null_removes_the_plan(self):
        self.assertEqual(self.put(None).status_code, 200)
        self.assertEqual(self.plan_rows(), [])
        zero = self.put(0)
        self.assertEqual(zero.status_code, 200)
        self.assertTrue(zero.get_json()["edit_token"])
        returned = self.calendar(days=1)["plans"]
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]["planned_count"], 0)
        self.assertEqual(self.put(None, zero.get_json()["edit_token"]).status_code, 200)
        self.assertEqual(self.calendar(days=1)["plans"], [])

    def test_tokens_allow_identical_retry_and_reject_stale_changes(self):
        created = self.put(2).get_json()
        retry = self.put(2)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["edit_token"], created["edit_token"])
        updated = self.put(5, created["edit_token"])
        self.assertEqual(updated.status_code, 200)
        new_token = updated.get_json()["edit_token"]
        self.assertNotEqual(new_token, created["edit_token"])
        before = self.plan_rows()
        self.assertEqual(self.put(7, created["edit_token"]).status_code, 409)
        self.assertEqual(self.put(None, created["edit_token"]).status_code, 409)
        self.assertEqual(self.plan_rows(), before)
        self.assertEqual(self.put(5, created["edit_token"]).get_json()["edit_token"], new_token)
        self.assertEqual(self.put(None, new_token).status_code, 200)
        self.assertEqual(self.put(None, new_token).status_code, 200)
        self.assertEqual(self.plan_rows(), [])

    def test_invalid_counts_do_not_mutate_existing_plan(self):
        created = self.put(9).get_json()
        before = self.plan_rows()
        for count in (-1, 100001, 1.5, "3", True, [], {}):
            with self.subTest(count=count):
                self.assertEqual(self.put(count, created["edit_token"]).status_code, 400)
                self.assertEqual(self.plan_rows(), before)

    def test_invalid_date_site_and_missing_token_reject(self):
        self.assertEqual(self.put(5, work_date="2026-02-30").status_code, 400)
        self.assertEqual(self.put(5, site=999999).status_code, 404)
        missing = self.admin.put("/api/daily-plans", json={
            "date": "2026-09-11", "subobject_id": self.sites[0], "planned_count": 5,
        }, headers={"X-CSRF-Token": "table-test-csrf-token"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.plan_rows(), [])
        self.assertEqual(self.put(5, work_date="20260911").status_code, 200)
        self.assertEqual(self.plan_rows()[0]["work_date"], "2026-09-11")

    def test_missing_count_cannot_delete_an_existing_plan(self):
        existing = self.put(9).get_json()
        before = self.plan_rows()
        response = self.admin.put("/api/daily-plans", json={
            "date": "2026-09-11", "subobject_id": self.sites[0], "expected_token": existing["edit_token"],
        }, headers={"X-CSRF-Token": "table-test-csrf-token"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.plan_rows(), before)

    def test_legacy_plan_migration_runs_once_and_preserves_assignments(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            db.execute("DELETE FROM schema_versions WHERE version = 2")
            db.executemany(
                """INSERT INTO staffing_plans(work_date, shift, subobject_id, planned_count,
                       created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    ("2026-09-11", "1 смена", self.sites[0], 3, self.admin_id, "2026-09-10T10:00:00Z"),
                    ("2026-09-11", "2 смена", self.sites[0], 4, self.admin_id, "2026-09-10T11:00:00Z"),
                    ("2026-09-12", "1 смена", self.sites[0], 5, self.admin_id, "2026-09-10T12:00:00Z"),
                ],
            )
            worker = db.execute("INSERT INTO workers(full_name, personnel_no) VALUES (?, ?)",
                                ("Migration worker", "table-migration-worker")).lastrowid
            db.execute(
                """INSERT INTO assignments(work_date, shift, subobject_id, worker_id, employer,
                       foreman_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("2026-09-11", "1 смена", self.sites[0], worker, "Original employer",
                 self.role_ids["foreman"], "2026-09-10T12:00:00Z"),
            )
            db.commit()
            original_assignments = [dict(row) for row in db.execute("SELECT * FROM assignments")]
            original_plans = [dict(row) for row in db.execute("SELECT * FROM staffing_plans ORDER BY id")]
            self.module.init_db()
            migrated = [dict(row) for row in db.execute("SELECT * FROM daily_staffing_plans ORDER BY work_date")]
            self.assertEqual([(row["work_date"], row["planned_count"]) for row in migrated],
                             [("2026-09-11", 7), ("2026-09-12", 5)])
            self.assertTrue(all(row["edit_token"] for row in migrated))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_versions WHERE version = 2").fetchone()[0], 1)
            self.module.init_db()
            self.assertEqual([dict(row) for row in db.execute("SELECT * FROM daily_staffing_plans ORDER BY work_date")], migrated)
            self.assertEqual([dict(row) for row in db.execute("SELECT * FROM assignments")], original_assignments)
            self.assertEqual([dict(row) for row in db.execute("SELECT * FROM staffing_plans ORDER BY id")], original_plans)

    def test_csrf_and_roles_protect_writes_while_calendar_is_readable(self):
        payload = {"date": "2026-09-11", "subobject_id": self.sites[0], "planned_count": 4, "expected_token": None}
        self.assertEqual(self.admin.put("/api/daily-plans", json=payload).status_code, 403)
        self.assertEqual(self.admin.put("/api/daily-plans", json=payload,
                                       headers={"X-CSRF-Token": "incorrect-token"}).status_code, 403)
        for client in (self.foreman, self.viewer):
            self.assertEqual(self.put(4, client=client).status_code, 403)
            self.assertEqual(self.calendar(days=1, client=client)["dates"], ["2026-09-11"])
        anonymous = self.module.app.test_client()
        self.assertEqual(anonymous.get("/api/calendar").status_code, 401)
        self.assertEqual(self.plan_rows(), [])

    def test_calendar_combines_period_plans_with_day_night_organization_facts(self):
        facts = [
            ("2026-09-11", "1 смена", "Employer A", self.sites[0]),
            ("2026-09-11", "1 смена", "Employer A", self.sites[0]),
            ("2026-09-11", "2 смена", "Employer A", self.sites[0]),
            ("2026-09-11", "Ночная смена", "Employer B", self.sites[0]),
            ("2026-09-13", "1 смена", "Employer B", self.sites[1]),
            ("2026-09-10", "1 смена", "Outside period", self.sites[0]),
            ("2026-09-14", "2 смена", "Outside period", self.sites[0]),
        ]
        with self.module.app.app_context():
            db = self.module.get_db()
            for number, (work_date, shift, employer, site) in enumerate(facts):
                worker = db.execute("INSERT INTO workers(full_name, personnel_no) VALUES (?, ?)",
                                    (f"Calendar worker {number}", f"calendar-{number}")).lastrowid
                db.execute(
                    """INSERT INTO assignments(work_date, shift, subobject_id, worker_id, employer,
                           foreman_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (work_date, shift, site, worker, employer, self.role_ids["foreman"], self.module.utc_now()),
                )
            db.commit()
        self.assertEqual(self.put(12).status_code, 200)
        self.assertEqual(self.put(7, site=self.sites[1], work_date="2026-09-13").status_code, 200)
        self.assertEqual(self.put(99, work_date="2026-09-14").status_code, 200)
        data = self.calendar(days=3, client=self.viewer)
        self.assertEqual(data["dates"], ["2026-09-11", "2026-09-12", "2026-09-13"])
        by_fact = {(row["work_date"], row["subobject_id"], row["employer"]):
                   (row["day_count"], row["night_count"]) for row in data["facts"]}
        self.assertEqual(by_fact, {
            ("2026-09-11", self.sites[0], "Employer A"): (2, 1),
            ("2026-09-11", self.sites[0], "Employer B"): (0, 1),
            ("2026-09-13", self.sites[1], "Employer B"): (1, 0),
        })
        self.assertTrue(all(row["object_id"] == self.object_id for row in data["facts"]))
        self.assertEqual({(row["work_date"], row["subobject_id"], row["planned_count"])
                          for row in data["plans"]}, {
            ("2026-09-11", self.sites[0], 12), ("2026-09-13", self.sites[1], 7),
        })
        self.assertTrue(all(row["object_id"] == self.object_id for row in data["plans"]))
        self.assertTrue(all(row["updated_by"] == self.admin_id for row in data["plans"]))

    def test_calendar_category_filters_facts_but_not_plans_and_keeps_all_options(self):
        with self.module.app.app_context():
            db = self.module.get_db()
            manual = db.execute("INSERT INTO workers(full_name,personnel_no,category) VALUES ('Manual','manual','Исходная')").lastrowid
            source = db.execute("INSERT INTO workers(full_name,personnel_no,category) VALUES ('Source','source','Исходная')").lastrowid
            blank = db.execute("INSERT INTO workers(full_name,personnel_no,category) VALUES ('Blank','blank','')").lastrowid
            category_id = db.execute("""INSERT INTO gdlr_categories(name,name_key,active,edit_token,updated_by,updated_at)
                VALUES ('Ручная','ручная',0,'token',?,'now')""", (self.admin_id,)).lastrowid
            db.execute("INSERT INTO employee_gdlr(worker_id,category_id,edit_token,updated_by,updated_at) VALUES (?,?,'token',?,'now')",
                       (manual, category_id, self.admin_id))
            db.execute("""INSERT INTO gdlr_categories(name,name_key,edit_token,updated_by,updated_at)
                VALUES ('Пустая категория','пустая категория','token',?,'now')""", (self.admin_id,))
            for worker, shift in ((manual, '1 смена'), (manual, 'Ночная смена'), (source, '1 смена'), (blank, '2 смена')):
                db.execute("""INSERT INTO assignments(work_date,shift,subobject_id,worker_id,employer,foreman_user_id,created_at)
                    VALUES ('2026-09-11',?,?,?,'Employer',?,'now')""", (shift, self.sites[0], worker, self.role_ids['foreman']))
            db.commit()
        self.assertEqual(self.put(9).status_code, 200)
        baseline = self.calendar(days=1, client=self.viewer)
        self.assertEqual({(row['day_count'], row['night_count']) for row in baseline['facts']}, {(2, 2)})
        self.assertEqual(baseline['categories'], ['', 'Исходная', 'Пустая категория', 'Ручная'])
        self.assertEqual(self.calendar(days=1, category='Пустая категория')['facts'], [])
        manual = self.calendar(days=1, category='Ручная')
        self.assertEqual([(row['day_count'], row['night_count']) for row in manual['facts']], [(1, 1)])
        self.assertEqual(manual['plans'], baseline['plans'])
        self.assertEqual(manual['categories'], baseline['categories'])
        source = self.calendar(days=1, category='Исходная')
        self.assertEqual([(row['day_count'], row['night_count']) for row in source['facts']], [(1, 0)])
        blank = self.calendar(days=1, category='')
        self.assertEqual([(row['day_count'], row['night_count']) for row in blank['facts']], [(0, 1)])
        self.assertEqual(self.admin.get('/api/calendar', query_string={'category': 'x' * 201}).status_code, 400)

    def test_calendar_rejects_invalid_or_excessive_periods(self):
        for start, days in (("2026-09-11", 0), ("2026-09-11", 32),
                            ("2026-09-11", "seven"), ("invalid", 7), ("9999-12-31", 2)):
            with self.subTest(start=start, days=days):
                response = self.admin.get("/api/calendar", query_string={"start": start, "days": days})
                self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
