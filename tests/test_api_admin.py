"""Integration tests for api/admin.py — operator-only admin endpoints.

Real global.db in a temp dir; provisioner audit log + queue dir mocked
via env overrides. Verifies authorization (admin gates), shape of
responses, and graceful handling of missing files.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_admin -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_test_env(db_path: str, user_data_dir: str, queue_dir: str, audit_log: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["USER_DATA_DIR"] = user_data_dir
    os.environ["PROVISIONER_QUEUE_DIR"] = queue_dir
    os.environ["PROVISIONER_AUDIT_LOG"] = audit_log

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    # Rebind module-level constants that were captured at import time
    from shared import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    from core import provisioner_client as pc
    pc.DEFAULT_USER_DATA_DIR = user_data_dir
    pc.DEFAULT_QUEUE_DIR = queue_dir

    from api import admin as admin_mod
    admin_mod.AUDIT_LOG_PATH = audit_log


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states"]:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(email: str, *, is_admin: bool = False) -> tuple[int, str]:
    """Insert a user, return (user_id, jwt)."""
    from shared import auth as core_auth
    user_id = core_auth.create_user(email=email, password="longenoughpassword", is_admin=is_admin)
    token = core_auth.create_jwt(
        user_id=user_id, email=email, email_verified=True, is_admin=is_admin,
    )
    return user_id, token


def _seed_broker_key(db_path: str, user_id: int, *, scope: str = "trade",
                     last_error: str = None) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO broker_keys (user_id, broker, key_enc, secret_enc, scope, "
        "validated_at, last_error) VALUES (?, 'oanda', 'enc_k', 'enc_s', ?, "
        "CURRENT_TIMESTAMP, ?)",
        (user_id, scope, last_error),
    )
    conn.commit()
    conn.close()


def _seed_event(db_path: str, user_id: int, event_type: str, detail: str = "") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO broker_key_events (user_id, broker, event_type, detail) "
        "VALUES (?, 'oanda', ?, ?)",
        (user_id, event_type, detail),
    )
    conn.commit()
    conn.close()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class AdminAPITestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_admin_test_")
        cls._db_path        = os.path.join(cls._tmpdir, "test_global.db")
        cls._user_data_dir  = os.path.join(cls._tmpdir, "users")
        cls._queue_dir      = os.path.join(cls._tmpdir, "users", "_queue")
        cls._audit_log      = os.path.join(cls._tmpdir, "audit.log")
        os.makedirs(cls._user_data_dir, exist_ok=True)
        os.makedirs(cls._queue_dir,     exist_ok=True)
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path, cls._user_data_dir, cls._queue_dir, cls._audit_log)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)
        cls.app = app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self):
        _truncate_all(self._db_path)
        # Wipe per-user dirs + audit log + queue between tests
        for entry in Path(self._user_data_dir).iterdir():
            if entry.is_dir() and entry.name not in ("_queue",):
                import shutil
                shutil.rmtree(entry)
        for f in Path(self._queue_dir).iterdir():
            f.unlink()
        Path(self._audit_log).unlink(missing_ok=True)

        # Always seed an admin + a regular user — most tests need both
        self.admin_id, self.admin_token = _seed_user("admin@x.com", is_admin=True)
        self.user_id,  self.user_token  = _seed_user("alice@x.com", is_admin=False)


# ─── Authorization gate ────────────────────────────────────────────────────


class AuthorizationTests(AdminAPITestBase):

    def test_unauth_returns_401_users(self):
        r = self.client.get("/api/admin/users")
        self.assertEqual(r.status_code, 401)

    def test_unauth_returns_401_provisioner(self):
        r = self.client.get("/api/admin/provisioner")
        self.assertEqual(r.status_code, 401)

    def test_non_admin_returns_403(self):
        r = self.client.get("/api/admin/users", headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 403)
        self.assertIn("Admin", r.json()["detail"])

    def test_admin_can_list(self):
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)


# ─── GET /api/admin/users ──────────────────────────────────────────────────


class ListUsersTests(AdminAPITestBase):

    def test_returns_all_users_sorted_by_id(self):
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 2)
        self.assertEqual([u["id"] for u in body], [self.admin_id, self.user_id])
        self.assertEqual(body[0]["email"], "admin@x.com")
        self.assertTrue(body[0]["is_admin"])
        self.assertFalse(body[1]["is_admin"])

    def test_broker_summary_reflects_row(self):
        _seed_broker_key(self._db_path, self.user_id, scope="read")
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        users = r.json()
        alice = next(u for u in users if u["email"] == "alice@x.com")
        self.assertTrue(alice["broker"]["connected"])
        self.assertEqual(alice["broker"]["scope"], "read")

    def test_broker_summary_when_no_key(self):
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        users = r.json()
        alice = next(u for u in users if u["email"] == "alice@x.com")
        self.assertFalse(alice["broker"]["connected"])
        self.assertIsNone(alice["broker"]["scope"])

    def test_excludes_soft_deleted_by_default(self):
        # Soft-delete alice
        conn = sqlite3.connect(self._db_path)
        conn.execute("UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (self.user_id,))
        conn.commit()
        conn.close()
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        emails = [u["email"] for u in r.json()]
        self.assertNotIn("alice@x.com", emails)

    def test_include_deleted_flag(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (self.user_id,))
        conn.commit()
        conn.close()
        r = self.client.get(
            "/api/admin/users?include_deleted=true",
            headers=_bearer(self.admin_token),
        )
        emails = [u["email"] for u in r.json()]
        self.assertIn("alice@x.com", emails)

    def test_engine_status_for_user_with_dir(self):
        user_dir = Path(self._user_data_dir) / str(self.user_id)
        user_dir.mkdir()
        (user_dir / ".engine_heartbeat").touch()
        r = self.client.get("/api/admin/users", headers=_bearer(self.admin_token))
        alice = next(u for u in r.json() if u["id"] == self.user_id)
        self.assertTrue(alice["engine"]["config_present"])
        self.assertIsNotNone(alice["engine"]["heartbeat"])
        self.assertLess(alice["engine"]["heartbeat_age_seconds"], 60)


# ─── GET /api/admin/users/{id} ─────────────────────────────────────────────


class UserDetailTests(AdminAPITestBase):

    def test_returns_404_for_unknown(self):
        r = self.client.get("/api/admin/users/999", headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 404)

    def test_includes_recent_broker_events(self):
        _seed_event(self._db_path, self.user_id, "key_added", "scope=trade")
        _seed_event(self._db_path, self.user_id, "mode_flipped_to_live")
        r = self.client.get(
            f"/api/admin/users/{self.user_id}",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["email"], "alice@x.com")
        events = body["broker_events"]
        self.assertEqual(len(events), 2)
        types = [e["event_type"] for e in events]
        # Most recent first (id DESC)
        self.assertEqual(types, ["mode_flipped_to_live", "key_added"])

    def test_caps_events_at_50(self):
        for i in range(60):
            _seed_event(self._db_path, self.user_id, f"event_{i}")
        r = self.client.get(
            f"/api/admin/users/{self.user_id}",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(len(r.json()["broker_events"]), 50)


# ─── GET /api/admin/users/{id}/login-attempts ──────────────────────────────


class LoginAttemptsTests(AdminAPITestBase):

    def _seed_login(self, username, result="SUCCESS"):
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO login_attempts (username, ip, result) VALUES (?, '1.2.3.4', ?)",
            (username, result),
        )
        conn.commit()
        conn.close()

    def test_returns_matching_attempts_case_insensitive(self):
        self._seed_login("alice@x.com", "SUCCESS")
        self._seed_login("ALICE@X.COM", "FAIL")
        self._seed_login("bob@x.com",   "SUCCESS")  # different user
        r = self.client.get(
            f"/api/admin/users/{self.user_id}/login-attempts",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        attempts = r.json()["attempts"]
        self.assertEqual(len(attempts), 2)
        # bob's attempt excluded
        for a in attempts:
            self.assertEqual(a["username"].lower(), "alice@x.com")

    def test_404_for_unknown_user(self):
        r = self.client.get(
            "/api/admin/users/9999/login-attempts",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(r.status_code, 404)


# ─── GET /api/admin/provisioner ────────────────────────────────────────────


class ProvisionerStatusTests(AdminAPITestBase):

    def test_no_audit_log_no_queue(self):
        r = self.client.get("/api/admin/provisioner", headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["audit_log_present"])
        self.assertEqual(body["recent"], [])
        self.assertEqual(body["queue_pending"], [])

    def test_parses_audit_log_jsonl(self):
        records = [
            {"ts": "2026-05-22T19:00:00+00:00", "action": "provision", "user_id": 5, "ok": True,  "detail": "container=ionic-engine-5"},
            {"ts": "2026-05-22T19:01:00+00:00", "action": "teardown",  "user_id": 5, "ok": True,  "detail": "moved"},
            {"ts": "2026-05-22T19:02:00+00:00", "action": "provision", "user_id": 6, "ok": False, "detail": "boom"},
        ]
        with open(self._audit_log, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        r = self.client.get("/api/admin/provisioner", headers=_bearer(self.admin_token))
        body = r.json()
        self.assertTrue(body["audit_log_present"])
        self.assertEqual(len(body["recent"]), 3)
        # Returned in chronological order (oldest first within tail)
        self.assertEqual(body["recent"][0]["user_id"], 5)
        self.assertEqual(body["recent"][2]["ok"], False)

    def test_skips_malformed_lines(self):
        with open(self._audit_log, "w") as f:
            f.write('{"ts":"x","action":"provision","user_id":1,"ok":true}\n')
            f.write('this is not json\n')
            f.write('\n')  # blank
            f.write('{"ts":"y","action":"teardown","user_id":2,"ok":true}\n')
        r = self.client.get("/api/admin/provisioner", headers=_bearer(self.admin_token))
        body = r.json()
        self.assertEqual(len(body["recent"]), 2)

    def test_tail_limit(self):
        with open(self._audit_log, "w") as f:
            for i in range(20):
                f.write(json.dumps({"ts": f"t{i}", "action": "provision", "user_id": i, "ok": True}) + "\n")
        r = self.client.get(
            "/api/admin/provisioner?tail=5",
            headers=_bearer(self.admin_token),
        )
        body = r.json()
        self.assertEqual(len(body["recent"]), 5)
        # Last 5 chronologically
        self.assertEqual(body["recent"][-1]["user_id"], 19)

    def test_queue_pending_lists_unresolved_flags(self):
        (Path(self._queue_dir) / "5.provision").touch()
        (Path(self._queue_dir) / "7.teardown").touch()
        # Random file that's NOT a flag — should be ignored
        (Path(self._queue_dir) / "audit.log").touch()
        r = self.client.get("/api/admin/provisioner", headers=_bearer(self.admin_token))
        pending = r.json()["queue_pending"]
        self.assertEqual(sorted(pending), ["5.provision", "7.teardown"])


# ─── POST /api/admin/users/{id}/restart-engine ─────────────────────────────


class RestartEngineTests(AdminAPITestBase):

    def test_touches_flag_when_dir_exists(self):
        user_dir = Path(self._user_data_dir) / str(self.user_id)
        user_dir.mkdir()
        r = self.client.post(
            f"/api/admin/users/{self.user_id}/restart-engine",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertTrue((user_dir / ".restart_engine").exists())

    def test_no_user_dir_returns_ok_false(self):
        r = self.client.post(
            f"/api/admin/users/{self.user_id}/restart-engine",
            headers=_bearer(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["ok"])
        self.assertIn("No user dir", body["detail"])

    def test_requires_admin(self):
        r = self.client.post(
            f"/api/admin/users/{self.user_id}/restart-engine",
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
