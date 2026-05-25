"""Tests for core/auth.py TOTP helpers + api/totp.py endpoints + the
2FA-aware login flow in api/auth.py.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_totp -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ─── Shared setup ──────────────────────────────────────────────────────────


def _setup_test_env(db_path: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["POSTMARK_SERVER_TOKEN"] = "test-token"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from shared import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts",
              "market_states", "totp_recovery_codes",
              "live_mode_confirmations", "tos_acceptances"]:
        try:
            conn.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(email="alice@x.com", password="longenoughpassword"):
    from shared import auth as core_auth
    user_id = core_auth.create_user(email=email, password=password, is_admin=False)
    return user_id


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ─── core/auth.py unit tests ───────────────────────────────────────────────


class TotpHelperTests(unittest.TestCase):
    """Pure-function tests on the core/auth.py helpers — no FastAPI."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_totp_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)

    def setUp(self):
        _truncate_all(self._db_path)
        self.user_id = _seed_user()

    # ── generate / verify ──────────────────────────────────────────────

    def test_generate_secret_is_base32(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        # base32 alphabet = A-Z, 2-7
        self.assertGreaterEqual(len(secret), 16)
        self.assertTrue(all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret))

    def test_provisioning_uri_format(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        uri = core_auth.totp_provisioning_uri(secret, account_label="alice@x.com")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("Foundation", uri)
        self.assertIn("alice", uri)
        self.assertIn(f"secret={secret}", uri)

    def test_verify_correct_code(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        current_code = pyotp.TOTP(secret).now()
        self.assertTrue(core_auth.verify_totp_code(secret, current_code))

    def test_verify_wrong_code(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        self.assertFalse(core_auth.verify_totp_code(secret, "000000"))

    def test_verify_malformed_codes(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        self.assertFalse(core_auth.verify_totp_code(secret, ""))
        self.assertFalse(core_auth.verify_totp_code(secret, "abcdef"))
        self.assertFalse(core_auth.verify_totp_code(secret, "12345"))   # too short
        self.assertFalse(core_auth.verify_totp_code(secret, "1234567")) # too long
        self.assertFalse(core_auth.verify_totp_code("", "123456"))

    # ── enrollment lifecycle ──────────────────────────────────────────

    def test_user_has_totp_false_initially(self):
        from shared import auth as core_auth
        self.assertFalse(core_auth.user_has_totp(self.user_id))

    def test_stash_pending_secret_does_not_enable(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        # Pending secret is set but user is NOT enabled yet
        self.assertFalse(core_auth.user_has_totp(self.user_id))
        self.assertEqual(core_auth.get_user_totp_secret(self.user_id), secret)

    def test_confirm_enrollment_flips_enabled(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        codes = core_auth.confirm_totp_enrollment(
            self.user_id, pyotp.TOTP(secret).now())
        self.assertTrue(core_auth.user_has_totp(self.user_id))
        self.assertEqual(len(codes), 10)
        # Each code is in xxxx-xxxx format
        for c in codes:
            self.assertEqual(len(c), 9)
            self.assertEqual(c[4], "-")

    def test_confirm_with_bad_code_raises(self):
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        with self.assertRaises(core_auth.AuthError):
            core_auth.confirm_totp_enrollment(self.user_id, "000000")
        # Still not enrolled
        self.assertFalse(core_auth.user_has_totp(self.user_id))

    def test_stash_refuses_when_already_enrolled(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        # Try to stash a new pending secret while enrolled
        with self.assertRaises(core_auth.AuthError):
            core_auth.stash_pending_totp_secret(self.user_id, core_auth.generate_totp_secret())

    def test_disable_clears_everything(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        core_auth.disable_totp(self.user_id)
        self.assertFalse(core_auth.user_has_totp(self.user_id))
        self.assertIsNone(core_auth.get_user_totp_secret(self.user_id))
        self.assertEqual(core_auth.count_active_recovery_codes(self.user_id), 0)

    def test_disable_idempotent(self):
        from shared import auth as core_auth
        core_auth.disable_totp(self.user_id)
        core_auth.disable_totp(self.user_id)  # no error

    # ── recovery codes ────────────────────────────────────────────────

    def _enroll(self):
        """Helper: enroll the seeded user and return (secret, recovery_codes)."""
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        codes = core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        return secret, codes

    def test_recovery_code_verifies_once(self):
        from shared import auth as core_auth
        _, codes = self._enroll()
        first = codes[0]
        self.assertTrue(core_auth.consume_recovery_code(self.user_id, first))
        # Second use fails
        self.assertFalse(core_auth.consume_recovery_code(self.user_id, first))

    def test_recovery_code_accepts_formatting_variants(self):
        from shared import auth as core_auth
        _, codes = self._enroll()
        # Code is "abcd-efgh" — accept "ABCDEFGH", "abcdefgh", "abcd efgh"
        first = codes[1]
        canonical = first.replace("-", "")
        self.assertTrue(core_auth.consume_recovery_code(self.user_id, canonical.upper()))

    def test_recovery_code_wrong_fails(self):
        from shared import auth as core_auth
        self._enroll()
        self.assertFalse(core_auth.consume_recovery_code(self.user_id, "wxyz-0000"))

    def test_count_active_decrements_on_use(self):
        from shared import auth as core_auth
        _, codes = self._enroll()
        self.assertEqual(core_auth.count_active_recovery_codes(self.user_id), 10)
        core_auth.consume_recovery_code(self.user_id, codes[0])
        self.assertEqual(core_auth.count_active_recovery_codes(self.user_id), 9)

    def test_regenerate_wipes_old_codes(self):
        from shared import auth as core_auth
        _, original = self._enroll()
        new = core_auth.regenerate_recovery_codes(self.user_id)
        self.assertEqual(len(new), 10)
        # Old codes no longer work
        self.assertFalse(core_auth.consume_recovery_code(self.user_id, original[0]))
        # New codes do work
        self.assertTrue(core_auth.consume_recovery_code(self.user_id, new[0]))

    def test_regenerate_raises_when_not_enrolled(self):
        from shared import auth as core_auth
        with self.assertRaises(core_auth.AuthError):
            core_auth.regenerate_recovery_codes(self.user_id)

    def test_recovery_codes_unique(self):
        from shared import auth as core_auth
        _, codes = self._enroll()
        self.assertEqual(len(set(codes)), 10)


# ─── api/totp.py endpoint tests ────────────────────────────────────────────


class TotpEndpointsTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_totp_api_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.totp import router as totp_router
        from shared.api_auth import router as auth_router
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(totp_router)
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
        # Reset rate limiter between tests
        core_auth.login_limiter._attempts.clear()
        self.password = "longenoughpassword"
        self.user_id = _seed_user("alice@x.com", self.password)
        self.token = core_auth.create_jwt(
            user_id=self.user_id, email="alice@x.com",
            email_verified=True, is_admin=False,
        )


class StatusEndpointTests(TotpEndpointsTestBase):

    def test_status_disabled_initially(self):
        r = self.client.get("/api/auth/totp/status", headers=_bearer(self.token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["recovery_codes_remaining"], 0)

    def test_status_unauth(self):
        r = self.client.get("/api/auth/totp/status")
        self.assertEqual(r.status_code, 401)


class EnrollFlowTests(TotpEndpointsTestBase):

    def test_full_enrollment_flow(self):
        import pyotp
        # Step 1: start
        r = self.client.post(
            "/api/auth/totp/enroll/start",
            json={"current_password": self.password},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("provisioning_uri", body)
        self.assertIn("secret_base32", body)
        self.assertTrue(body["qr_png_data_url"].startswith("data:image/png;base64,"))
        secret = body["secret_base32"]

        # Step 2: confirm with valid code
        r = self.client.post(
            "/api/auth/totp/enroll/confirm",
            json={"code": pyotp.TOTP(secret).now()},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["recovery_codes"]), 10)

        # Status now shows enabled
        r = self.client.get("/api/auth/totp/status", headers=_bearer(self.token))
        self.assertTrue(r.json()["enabled"])
        self.assertEqual(r.json()["recovery_codes_remaining"], 10)

    def test_start_refuses_wrong_password(self):
        r = self.client.post(
            "/api/auth/totp/enroll/start",
            json={"current_password": "wrongpassword"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 401)

    def test_confirm_wrong_code_400(self):
        # Start to get pending secret
        self.client.post(
            "/api/auth/totp/enroll/start",
            json={"current_password": self.password},
            headers=_bearer(self.token),
        )
        r = self.client.post(
            "/api/auth/totp/enroll/confirm",
            json={"code": "000000"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 400)

    def test_confirm_without_start_400(self):
        r = self.client.post(
            "/api/auth/totp/enroll/confirm",
            json={"code": "123456"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 400)

    def test_start_409_when_already_enrolled(self):
        import pyotp
        from shared import auth as core_auth
        # Enroll out-of-band
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        r = self.client.post(
            "/api/auth/totp/enroll/start",
            json={"current_password": self.password},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 409)


class DisableEndpointTests(TotpEndpointsTestBase):

    def _enroll(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        codes = core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        return secret, codes

    def test_disable_with_totp_code_succeeds(self):
        import pyotp
        from shared import auth as core_auth
        secret, _ = self._enroll()
        r = self.client.post(
            "/api/auth/totp/disable",
            json={"current_password": self.password, "code": pyotp.TOTP(secret).now()},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(core_auth.user_has_totp(self.user_id))

    def test_disable_with_recovery_code_succeeds(self):
        from shared import auth as core_auth
        _, codes = self._enroll()
        r = self.client.post(
            "/api/auth/totp/disable",
            json={"current_password": self.password, "code": codes[0]},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(core_auth.user_has_totp(self.user_id))

    def test_disable_wrong_password(self):
        import pyotp
        secret, _ = self._enroll()
        r = self.client.post(
            "/api/auth/totp/disable",
            json={"current_password": "wrong", "code": pyotp.TOTP(secret).now()},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 401)

    def test_disable_wrong_code(self):
        self._enroll()
        r = self.client.post(
            "/api/auth/totp/disable",
            json={"current_password": self.password, "code": "000000"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 401)

    def test_disable_when_not_enrolled_is_idempotent(self):
        r = self.client.post(
            "/api/auth/totp/disable",
            json={"current_password": self.password, "code": "000000"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)


class RegenerateEndpointTests(TotpEndpointsTestBase):

    def _enroll(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        codes = core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        return secret, codes

    def test_regenerate_returns_new_codes(self):
        import pyotp
        secret, original = self._enroll()
        r = self.client.post(
            "/api/auth/totp/regenerate-codes",
            json={"current_password": self.password, "code": pyotp.TOTP(secret).now()},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 200)
        new = r.json()["recovery_codes"]
        self.assertEqual(len(new), 10)
        # No overlap with originals (cryptographically near-certain)
        self.assertEqual(len(set(original) & set(new)), 0)

    def test_regenerate_refuses_recovery_code(self):
        # Regenerate should require TOTP, not recovery — that'd be circular
        _, codes = self._enroll()
        r = self.client.post(
            "/api/auth/totp/regenerate-codes",
            json={"current_password": self.password, "code": codes[0]},
            headers=_bearer(self.token),
        )
        # Recovery code looks like xxxx-xxxx — fails the min_length=6 max=8
        # validation OR fails TOTP verification. Either way: not 200.
        self.assertIn(r.status_code, (400, 401, 422))

    def test_regenerate_when_not_enrolled_400(self):
        r = self.client.post(
            "/api/auth/totp/regenerate-codes",
            json={"current_password": self.password, "code": "123456"},
            headers=_bearer(self.token),
        )
        self.assertEqual(r.status_code, 400)


# ─── api/auth.py 2FA-aware login flow tests ────────────────────────────────


class TotpLoginFlowTests(TotpEndpointsTestBase):

    def _enroll(self):
        import pyotp
        from shared import auth as core_auth
        secret = core_auth.generate_totp_secret()
        core_auth.stash_pending_totp_secret(self.user_id, secret)
        codes = core_auth.confirm_totp_enrollment(self.user_id, pyotp.TOTP(secret).now())
        return secret, codes

    def test_login_without_totp_unchanged(self):
        # User without 2FA: /login returns token directly
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("token", body)
        self.assertIn("user", body)
        self.assertNotIn("totp_required", body)

    def test_login_with_totp_returns_partial(self):
        self._enroll()
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["totp_required"])
        self.assertIn("partial_token", body)
        self.assertNotIn("token", body)
        self.assertEqual(body["recovery_codes_remaining"], 10)

    def test_partial_token_rejected_by_normal_endpoints(self):
        self._enroll()
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        partial = r.json()["partial_token"]
        # Try to use the partial token as a session token
        r2 = self.client.get("/api/auth/me", headers=_bearer(partial))
        self.assertEqual(r2.status_code, 401)

    def test_login_totp_completes_with_valid_code(self):
        import pyotp
        secret, _ = self._enroll()
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        partial = r.json()["partial_token"]
        r2 = self.client.post(
            "/api/auth/login/totp",
            json={"partial_token": partial, "code": pyotp.TOTP(secret).now()},
        )
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertIn("token", body)
        self.assertTrue(body["user"]["totp_enabled"])

    def test_login_totp_accepts_recovery_code(self):
        _, codes = self._enroll()
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        partial = r.json()["partial_token"]
        r2 = self.client.post(
            "/api/auth/login/totp",
            json={"partial_token": partial, "code": codes[0]},
        )
        self.assertEqual(r2.status_code, 200)
        # Recovery code consumed
        from shared import auth as core_auth
        self.assertEqual(core_auth.count_active_recovery_codes(self.user_id), 9)

    def test_login_totp_rejects_wrong_code(self):
        self._enroll()
        r = self.client.post(
            "/api/auth/login",
            json={"email": "alice@x.com", "password": self.password},
        )
        partial = r.json()["partial_token"]
        r2 = self.client.post(
            "/api/auth/login/totp",
            json={"partial_token": partial, "code": "000000"},
        )
        self.assertEqual(r2.status_code, 401)

    def test_login_totp_rejects_session_token_replay(self):
        # A full session JWT must NOT be accepted at /login/totp — it'd
        # let any session upgrade itself
        r = self.client.post(
            "/api/auth/login/totp",
            json={"partial_token": self.token, "code": "123456"},
        )
        self.assertEqual(r.status_code, 401)

    def test_me_surfaces_totp_enabled(self):
        # Before enrollment
        r = self.client.get("/api/auth/me", headers=_bearer(self.token))
        self.assertFalse(r.json()["totp_enabled"])
        # After
        self._enroll()
        r = self.client.get("/api/auth/me", headers=_bearer(self.token))
        self.assertTrue(r.json()["totp_enabled"])


# ─── Schema migration ──────────────────────────────────────────────────────


class SchemaMigrationTests(unittest.TestCase):
    """Verify init_global_db is idempotent for the new TOTP columns +
    safely adds them to an existing DB that doesn't have them."""

    def test_adds_columns_to_existing_db(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import init_global_db
        tmpdir = tempfile.mkdtemp(prefix="foundation_migrate_test_")
        db = os.path.join(tmpdir, "g.db")
        try:
            # Materialize a DB with the OLD users-table shape (no totp cols)
            conn = sqlite3.connect(db)
            conn.execute("""
                CREATE TABLE users (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    email           TEXT NOT NULL,
                    password_hash   TEXT NOT NULL,
                    email_verified  INTEGER DEFAULT 0,
                    is_admin        INTEGER DEFAULT 0,
                    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login_at   DATETIME,
                    deleted_at      DATETIME,
                    recovery_email  TEXT
                )
            """)
            conn.commit()
            conn.close()

            # Run the migration — should add the new columns without error
            init_global_db.init_global_db(db, verbose=False)

            conn = sqlite3.connect(db)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            self.assertIn("totp_secret", cols)
            self.assertIn("totp_enrolled_at", cols)
            self.assertIn("totp_recovery_codes", tables)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_migration_is_idempotent(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import init_global_db
        tmpdir = tempfile.mkdtemp(prefix="foundation_migrate_test_")
        db = os.path.join(tmpdir, "g.db")
        try:
            init_global_db.init_global_db(db, verbose=False)
            init_global_db.init_global_db(db, verbose=False)  # second call — no error
            init_global_db.init_global_db(db, verbose=False)  # third — still no error
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
