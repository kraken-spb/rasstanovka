import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PlacementAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            "DATABASE_PATH": str(Path(cls.temp.name) / "test.db"),
            "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "test-password-123",
            "SECRET_KEY": "test-secret-key-that-is-long-enough-123",
        }):
            cls.module = importlib.import_module("app")
            cls.previous_path = cls.module.DATABASE_PATH
            cls.module.DATABASE_PATH = Path(cls.temp.name) / "test.db"
            with cls.module.app.app_context():
                cls.module.init_db()
                db = cls.module.get_db()
                db.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'",
                           (cls.module.generate_password_hash("test-password-123"),))
                db.commit()
        cls.module.app.config.update(TESTING=True)
        cls.client = cls.module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.module.DATABASE_PATH = cls.previous_path
        cls.temp.cleanup()

    def test_protected_and_full_workflow(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        response = self.client.post("/login", data={"username": "admin", "password": "test-password-123"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            headers = {"X-CSRF-Token": session["csrf_token"]}
            owner = session["user_id"]
        reference = self.client.get("/api/reference").get_json()
        self.assertGreaterEqual(len(reference["objects"]), 1)
        worker = reference["workers"][0]["id"]
        site = reference["subobjects"][0]["id"]
        crew = self.client.post("/api/crews", json={"name": "Тестовая бригада", "owner_user_id": owner}, headers=headers)
        self.assertEqual(crew.status_code, 201)
        crew_id = crew.get_json()["id"]
        self.assertEqual(self.client.post(f"/api/crews/{crew_id}/members", json={"worker_ids": [worker]}, headers=headers).status_code, 200)
        payload = {"date": "2026-09-11", "shift": "1 смена", "subobject_id": site,
                   "worker_ids": [worker], "expected_tokens": {str(worker): None}}
        assigned = self.client.put(f"/api/crews/{crew_id}/assignments", json=payload, headers=headers)
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.get_json()["changed"], 1)
        retry = self.client.put(f"/api/crews/{crew_id}/assignments", json=payload, headers=headers)
        self.assertEqual(retry.get_json()["changed"], 0)
        plan = self.client.put("/api/daily-plans", json={
            "date": "2026-09-11", "subobject_id": site, "planned_count": 3, "expected_token": None,
        }, headers=headers)
        self.assertEqual(plan.status_code, 200)
        calendar = self.client.get("/api/calendar?start=2026-09-11&days=1").get_json()
        self.assertEqual(sum(row["day_count"] for row in calendar["facts"]), 1)
        self.assertEqual(sum(row["planned_count"] for row in calendar["plans"]), 3)
        dates = self.client.get("/api/activity-dates").get_json()
        self.assertEqual(dates["rows"][0]["work_date"], "2026-09-11")
        account = self.client.post("/api/users", json={
            "username": "foreman", "full_name": "Тестовый Прораб", "password": "password-123", "role": "foreman",
        }, headers=headers)
        self.assertEqual(account.status_code, 201)
        self.assertEqual(self.client.post("/api/plans", json={}, headers=headers).status_code, 410)


if __name__ == "__main__":
    unittest.main()
