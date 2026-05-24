"""Integration tests for api/demo.py — public-no-auth demo login.

The endpoint mints a short-lived JWT for the configured demo user
without requiring credentials. Tests:
  - Happy path: returns token + is_demo claim
  - Missing demo user → 503
  - Rate limiting (20/hr per IP)
  - Token can decode + claims include is_demo=true

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_demo -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_test_env(db_path: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["DEMO_USER_ID"] = "2"
    # Keep tests fast — the default production limit (100/hr/IP) would
    # mean the rate-limit test iterates 100 times.
    os.environ["DEMO_LOGIN_RATE_LIMIT"]      = "5"
    os.environ["DEMO_LOGIN_RATE_WINDOW_SEC"] = "3600"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from core import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    # Rebind the demo module's DEMO_USER_ID since it captured at import
    from api import demo as demo_mod
    demo_mod.DEMO_USER_ID = 2


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states"]:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_demo_user(db_path: str) -> int:
    """Insert a user at id=2 (the demo user). Returns the id."""
    from core import auth as core_auth
    # Burn id=1 with a throwaway user so create_user gives us id=2 for demo
    core_auth.create_user(email="placeholder@x.com", password="longenoughpassword")
    return core_auth.create_user(email="demo@foundationbots.com", password="longenoughpassword")


class DemoTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_demo_test_")
        cls._db_path = os.path.join(cls._tmpdir, "test_global.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.demo import router as demo_router

        app = FastAPI()
        app.include_router(demo_router)
        cls.app = app
        cls.client = TestClient(app)

        from api import demo as demo_mod
        cls.demo_mod = demo_mod

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self):
        _truncate_all(self._db_path)
        # Clear rate limiter between tests so they're independent
        self.demo_mod.demo_login_limiter._attempts.clear()


# ─── Happy path ────────────────────────────────────────────────────────────


class DemoLoginHappyTests(DemoTestBase):

    def test_returns_token_and_is_demo_true(self):
        demo_id = _seed_demo_user(self._db_path)
        self.assertEqual(demo_id, 2)
        r = self.client.post("/api/demo/login")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("token", body)
        self.assertTrue(body["is_demo"])
        self.assertEqual(body["user"]["id"], 2)
        self.assertEqual(body["user"]["email"], "demo@foundationbots.com")
        self.assertFalse(body["user"]["is_admin"])

    def test_token_decodes_with_is_demo_claim(self):
        _seed_demo_user(self._db_path)
        r = self.client.post("/api/demo/login")
        token = r.json()["token"]
        # Decode using the same module that issued it
        from core import auth as core_auth
        claims = core_auth.decode_jwt(token)
        self.assertEqual(claims["sub"], "2")
        self.assertTrue(claims["is_demo"])
        self.assertEqual(claims["email"], "demo@foundationbots.com")

    def test_token_has_short_expiry(self):
        """Demo tokens should expire WAY before a regular signup token."""
        _seed_demo_user(self._db_path)
        r = self.client.post("/api/demo/login")
        token = r.json()["token"]
        from core import auth as core_auth
        claims = core_auth.decode_jwt(token)
        ttl_sec = claims["exp"] - claims["iat"]
        # Default DEMO_TOKEN_HOURS=24 → 86400s. Allow generous slop +/- 60s.
        self.assertAlmostEqual(ttl_sec, 24 * 3600, delta=120)
        # And definitely shorter than the 7-day regular JWT_EXPIRY_DAYS
        self.assertLess(ttl_sec, 7 * 86400)


# ─── Missing demo user ─────────────────────────────────────────────────────


class MissingDemoUserTests(DemoTestBase):

    def test_503_when_demo_user_not_seeded(self):
        # No demo user in the table → 503
        r = self.client.post("/api/demo/login")
        self.assertEqual(r.status_code, 503)
        self.assertIn("unavailable", r.json()["detail"].lower())


# ─── Rate limiting ─────────────────────────────────────────────────────────


class DemoRateLimitTests(DemoTestBase):

    def test_rate_limits_after_configured_attempts(self):
        """Demo limiter blocks the (LIMIT+1)th request from a given IP.

        Reads the limit dynamically from the daemon's configured
        max_attempts so this test stays green when the operator tunes
        DEMO_LOGIN_RATE_LIMIT via env.
        """
        _seed_demo_user(self._db_path)
        limit = self.demo_mod.demo_login_limiter.max_attempts
        # First `limit` requests succeed
        for i in range(limit):
            r = self.client.post("/api/demo/login")
            self.assertEqual(r.status_code, 200, f"attempt {i+1}/{limit} failed: {r.text}")
        # (limit+1)th gets rate-limited
        r = self.client.post("/api/demo/login")
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", {k.lower() for k in r.headers.keys()})

    def test_env_override_tunes_limit(self):
        """Setting DEMO_LOGIN_RATE_LIMIT in env should adjust the limit
        after a module reload — operators tune via .env without rebuild."""
        _seed_demo_user(self._db_path)
        # Lower the limit way down, reload the module so it re-reads env
        os.environ["DEMO_LOGIN_RATE_LIMIT"] = "3"
        os.environ["DEMO_LOGIN_RATE_WINDOW_SEC"] = "3600"
        try:
            import importlib
            from api import demo as demo_mod
            importlib.reload(demo_mod)
            self.assertEqual(demo_mod.demo_login_limiter.max_attempts, 3)
        finally:
            # Restore original env + reload back to the test default
            os.environ.pop("DEMO_LOGIN_RATE_LIMIT", None)
            os.environ.pop("DEMO_LOGIN_RATE_WINDOW_SEC", None)
            import importlib
            from api import demo as demo_mod
            importlib.reload(demo_mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
