"""Tests for the versioned-ToS acceptance machinery.

Covers core.auth helpers (record_tos_acceptance, get_latest_tos_acceptance,
user_needs_tos_reaccept) + the /api/auth/accept-tos endpoint + the
tos_* fields surfaced via /api/auth/me + the signup-side recording hook.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_tos_acceptance -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_test_env(db_path: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from shared import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts",
              "market_states", "live_mode_confirmations", "tos_acceptances"]:
        try:
            conn.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(email="alice@x.com"):
    from shared import auth as core_auth
    user_id = core_auth.create_user(email=email, password="longenoughpassword")
    token = core_auth.create_jwt(
        user_id=user_id, email=email, email_verified=True, is_admin=False,
    )
    return user_id, token


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Unit tests: helpers ───────────────────────────────────────────────────


class HelperTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foundation_tos_test_")
        self.db_path = os.path.join(self.tmpdir, "g.db")
        self._saved_env = dict(os.environ)
        _setup_test_env(self.db_path)
        self.user_id, _ = _seed_user("h1@x.com")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_no_acceptance_returns_none(self):
        from shared import auth as core_auth
        self.assertIsNone(core_auth.get_latest_tos_acceptance(self.user_id))

    def test_record_then_get_latest(self):
        from shared import auth as core_auth
        core_auth.record_tos_acceptance(self.user_id, "2026-05-22", ip="1.2.3.4")
        self.assertEqual(core_auth.get_latest_tos_acceptance(self.user_id), "2026-05-22")

    def test_latest_is_most_recent(self):
        from shared import auth as core_auth
        core_auth.record_tos_acceptance(self.user_id, "1.0.0")
        core_auth.record_tos_acceptance(self.user_id, "1.1.0")
        core_auth.record_tos_acceptance(self.user_id, "2.0.0")
        self.assertEqual(core_auth.get_latest_tos_acceptance(self.user_id), "2.0.0")

    def test_needs_reaccept_true_when_no_acceptance(self):
        from shared import auth as core_auth
        self.assertTrue(core_auth.user_needs_tos_reaccept(self.user_id))

    def test_needs_reaccept_false_when_current_matches(self):
        from shared import auth as core_auth
        core_auth.record_tos_acceptance(self.user_id, core_auth.CURRENT_TOS_VERSION)
        self.assertFalse(core_auth.user_needs_tos_reaccept(self.user_id))

    def test_needs_reaccept_true_when_old_version(self):
        from shared import auth as core_auth
        core_auth.record_tos_acceptance(self.user_id, "ancient-version-1999")
        self.assertTrue(core_auth.user_needs_tos_reaccept(self.user_id))

    def test_audit_trail_preserves_history(self):
        from shared import auth as core_auth
        core_auth.record_tos_acceptance(self.user_id, "1.0.0", ip="1.1.1.1")
        core_auth.record_tos_acceptance(self.user_id, "2.0.0", ip="2.2.2.2")
        conn = sqlite3.connect(self.db_path)
        rows = list(conn.execute(
            "SELECT tos_version, ip FROM tos_acceptances WHERE user_id=? ORDER BY id",
            (self.user_id,),
        ))
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ("1.0.0", "1.1.1.1"))
        self.assertEqual(rows[1], ("2.0.0", "2.2.2.2"))

    def test_record_failure_does_not_raise(self):
        # Pass a bad db_path → should log but not crash
        from shared import auth as core_auth
        try:
            core_auth.record_tos_acceptance(self.user_id, "x", db_path="/nonexistent/g.db")
        except Exception as e:
            self.fail(f"record_tos_acceptance raised: {e}")


# ─── /api/auth/accept-tos endpoint ────────────────────────────────────────


class AcceptTosEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_tos_api_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.api_auth import router as auth_router

        app = FastAPI()
        app.include_router(auth_router)
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
        from shared import auth as core_auth
        # Reset rate limiters between tests
        for rl in [core_auth.login_limiter, core_auth.signup_limiter,
                   core_auth.verification_resend_lim, core_auth.password_reset_req_lim,
                   core_auth.password_reset_apply_lim, core_auth.verification_apply_lim]:
            rl._attempts.clear()
        self.user_id, self.token = _seed_user("alice@x.com")

    def test_accept_current_version_success(self):
        from shared import auth as core_auth
        resp = self.client.post(
            "/api/auth/accept-tos",
            json={"version": core_auth.CURRENT_TOS_VERSION},
            headers=_bearer(self.token),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        # DB row was written
        latest = core_auth.get_latest_tos_acceptance(self.user_id)
        self.assertEqual(latest, core_auth.CURRENT_TOS_VERSION)

    def test_accept_outdated_version_rejected_400(self):
        resp = self.client.post(
            "/api/auth/accept-tos",
            json={"version": "stale-version-from-cached-page"},
            headers=_bearer(self.token),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current one", resp.json()["detail"])

    def test_accept_without_bearer_returns_401(self):
        from shared import auth as core_auth
        resp = self.client.post(
            "/api/auth/accept-tos",
            json={"version": core_auth.CURRENT_TOS_VERSION},
        )
        self.assertEqual(resp.status_code, 401)

    def test_repeat_acceptance_idempotent_appends_row(self):
        from shared import auth as core_auth
        for _ in range(3):
            self.client.post(
                "/api/auth/accept-tos",
                json={"version": core_auth.CURRENT_TOS_VERSION},
                headers=_bearer(self.token),
            )
        conn = sqlite3.connect(self._db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM tos_acceptances WHERE user_id=?",
            (self.user_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(n, 3)


# ─── /api/auth/me exposes ToS state ────────────────────────────────────────


class MeEndpointTosFieldsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_tos_me_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.api_auth import router as auth_router

        app = FastAPI()
        app.include_router(auth_router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self):
        _truncate_all(self._db_path)
        from shared import auth as core_auth
        for rl in [core_auth.login_limiter, core_auth.signup_limiter]:
            rl._attempts.clear()
        self.user_id, self.token = _seed_user("me@x.com")

    def test_me_includes_tos_fields_when_not_accepted(self):
        from shared import auth as core_auth
        resp = self.client.get("/api/auth/me", headers=_bearer(self.token))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["tos_version_accepted"])
        self.assertEqual(body["tos_version_current"], core_auth.CURRENT_TOS_VERSION)
        self.assertTrue(body["tos_needs_reaccept"])

    def test_me_after_accept_shows_no_reaccept_needed(self):
        from shared import auth as core_auth
        self.client.post(
            "/api/auth/accept-tos",
            json={"version": core_auth.CURRENT_TOS_VERSION},
            headers=_bearer(self.token),
        )
        resp = self.client.get("/api/auth/me", headers=_bearer(self.token))
        body = resp.json()
        self.assertEqual(body["tos_version_accepted"], core_auth.CURRENT_TOS_VERSION)
        self.assertFalse(body["tos_needs_reaccept"])


# ─── Signup records acceptance ─────────────────────────────────────────────


class SignupRecordsTosTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_tos_signup_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.api_auth import router as auth_router

        app = FastAPI()
        app.include_router(auth_router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self):
        _truncate_all(self._db_path)
        from shared import auth as core_auth
        for rl in [core_auth.login_limiter, core_auth.signup_limiter]:
            rl._attempts.clear()

    def test_signup_records_acceptance(self):
        from shared import auth as core_auth
        with patch("api.auth.email_sender.send_verify_email") as mock_email, \
             patch("api.auth.provisioner_client.initialize_user_dir"), \
             patch("api.auth.provisioner_client.enqueue_provision"):
            mock_email.return_value = {"ok": True}
            resp = self.client.post("/api/auth/signup", json={
                "email": "newuser@x.com",
                "password": "verylongpassword123",
                "accepted_terms": True,
                "accepted_risk_acknowledgment": True,
            })
        self.assertEqual(resp.status_code, 201)
        user_id = resp.json()["user"]["id"]
        latest = core_auth.get_latest_tos_acceptance(user_id)
        self.assertEqual(latest, core_auth.CURRENT_TOS_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
