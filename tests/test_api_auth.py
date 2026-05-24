"""Integration tests for api/auth.py — the 11 SaaS auth endpoints.

Uses FastAPI's TestClient. Each test mounts a fresh global.db in a
temp directory and mocks all Postmark calls. Tests the full flow:
signup → JWT → me → verify → change-password → etc.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_auth -v
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
    """Configure all module-level env vars + materialize the schema."""
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["POSTMARK_SERVER_TOKEN"] = "test-postmark-token-not-real"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    # Force core.auth to re-read env (it's already imported, so patch attributes)
    from core import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    # Reset rate limiters between test classes
    for rl in [core_auth.login_limiter, core_auth.signup_limiter,
               core_auth.verification_resend_lim, core_auth.password_reset_req_lim,
               core_auth.password_reset_apply_lim, core_auth.verification_apply_lim]:
        rl._attempts.clear()


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states"]:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


class AuthAPITestBase(unittest.TestCase):
    """Builds a FastAPI app with just the auth router for each test class."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_api_test_")
        cls._db_path = os.path.join(cls._tmpdir, "test_global.db")
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path)

        # Build a minimal FastAPI app with just our router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.auth import router as auth_router

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
        # Reset rate limiters between tests too
        from core import auth as core_auth
        for rl in [core_auth.login_limiter, core_auth.signup_limiter,
                   core_auth.verification_resend_lim, core_auth.password_reset_req_lim,
                   core_auth.password_reset_apply_lim, core_auth.verification_apply_lim]:
            rl._attempts.clear()


# ─── Signup ────────────────────────────────────────────────────────────────


class SignupEndpointTests(AuthAPITestBase):

    def _post_signup(self, **overrides):
        body = {"email": "new@example.com", "password": "verylongpassword123",
                "accepted_terms": True, "accepted_risk_acknowledgment": True}
        body.update(overrides)
        with patch("api.auth.email_sender.send_verify_email") as mock_send:
            mock_send.return_value = {"ok": True, "message_id": "fake", "submitted_at": None}
            return self.client.post("/api/auth/signup", json=body), mock_send

    def test_happy_path(self):
        resp, mock_send = self._post_signup()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["email"], "new@example.com")
        self.assertFalse(data["user"]["email_verified"])
        self.assertFalse(data["user"]["is_admin"])
        # Verification email was sent
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[0], "new@example.com")

    def test_password_too_short_rejected(self):
        resp, _ = self._post_signup(password="short")
        self.assertEqual(resp.status_code, 422)  # Pydantic validation error

    def test_invalid_email_rejected(self):
        resp, _ = self._post_signup(email="not-an-email")
        self.assertEqual(resp.status_code, 422)

    def test_duplicate_email_returns_409(self):
        self._post_signup()
        resp, _ = self._post_signup()  # same email
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already exists", resp.json()["detail"].lower())

    def test_duplicate_email_case_insensitive(self):
        self._post_signup(email="Same@Example.com")
        resp, _ = self._post_signup(email="same@example.com")
        self.assertEqual(resp.status_code, 409)

    def test_terms_required(self):
        resp, _ = self._post_signup(accepted_terms=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("terms", resp.json()["detail"].lower())

    def test_signup_creates_user_in_db(self):
        self._post_signup()
        from core.auth import get_user_by_email
        u = get_user_by_email("new@example.com")
        self.assertIsNotNone(u)
        self.assertEqual(u.id, 1)

    def test_jwt_is_valid_immediately_after_signup(self):
        from core.auth import decode_jwt
        resp, _ = self._post_signup()
        token = resp.json()["token"]
        claims = decode_jwt(token)
        self.assertEqual(claims["email"], "new@example.com")
        self.assertFalse(claims["email_verified"])

    def test_signup_email_failure_does_not_block(self):
        """Postmark error → signup still succeeds (user can resend later)."""
        body = {"email": "user@example.com", "password": "verylongpassword123",
                "accepted_terms": True, "accepted_risk_acknowledgment": True}
        with patch("api.auth.email_sender.send_verify_email") as mock_send:
            mock_send.return_value = {"ok": False, "error": "postmark down"}
            resp = self.client.post("/api/auth/signup", json=body)
        self.assertEqual(resp.status_code, 201)
        self.assertIn("token", resp.json())


# ─── Login ─────────────────────────────────────────────────────────────────


class LoginEndpointTests(AuthAPITestBase):

    def _create_user(self, email="user@example.com", password="verylongpassword123"):
        from core.auth import create_user
        return create_user(email, password)

    def test_happy_path(self):
        self._create_user()
        resp = self.client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "verylongpassword123",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.json())

    def test_wrong_password(self):
        self._create_user()
        resp = self.client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "wrong-password-here",
        })
        self.assertEqual(resp.status_code, 401)

    def test_unknown_email_returns_same_401_as_wrong_password(self):
        self._create_user(email="known@example.com")
        resp1 = self.client.post("/api/auth/login", json={
            "email": "known@example.com", "password": "wrong",
        })
        resp2 = self.client.post("/api/auth/login", json={
            "email": "unknown@example.com", "password": "anything",
        })
        self.assertEqual(resp1.status_code, 401)
        self.assertEqual(resp2.status_code, 401)
        # Same message — no enumeration
        self.assertEqual(resp1.json()["detail"], resp2.json()["detail"])

    def test_login_updates_last_login(self):
        uid = self._create_user()
        self.client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "verylongpassword123",
        })
        from core.auth import get_user_by_id
        u = get_user_by_id(uid)
        self.assertIsNotNone(u.last_login_at)

    def test_bcrypt_hash_migrates_to_argon2_on_login(self):
        """Operator-style bcrypt hash should auto-migrate to argon2."""
        from core import auth as core_auth
        from passlib.hash import bcrypt
        # Manually insert a user with a bcrypt hash (simulating
        # operator's legacy hash from .env)
        bcrypt_hash = bcrypt.hash("legacy-password-12345")
        with core_auth.db_connect() as conn:
            conn.execute(
                "INSERT INTO users (email, password_hash, email_verified, is_admin) "
                "VALUES (?, ?, 1, 1)",
                ("legacy@example.com", bcrypt_hash),
            )
            conn.commit()

        # Login with the bcrypt hash works
        resp = self.client.post("/api/auth/login", json={
            "email": "legacy@example.com", "password": "legacy-password-12345",
        })
        self.assertEqual(resp.status_code, 200)

        # Hash should now be argon2 (migrated on successful login)
        from core.auth import get_user_by_email, get_user_password_hash
        u = get_user_by_email("legacy@example.com")
        new_hash = get_user_password_hash(u.id)
        self.assertTrue(new_hash.startswith("$argon2"))

    def test_deleted_user_cannot_login(self):
        uid = self._create_user(email="deleted@example.com")
        from core.auth import soft_delete_user
        soft_delete_user(uid)
        resp = self.client.post("/api/auth/login", json={
            "email": "deleted@example.com", "password": "verylongpassword123",
        })
        self.assertEqual(resp.status_code, 401)


# ─── /me + auth dependency ────────────────────────────────────────────────


class MeEndpointTests(AuthAPITestBase):

    def _signup(self, email="u@x.com", password="verylongpassword123"):
        with patch("api.auth.email_sender.send_verify_email") as mock_send:
            mock_send.return_value = {"ok": True}
            resp = self.client.post("/api/auth/signup", json={
                "email": email, "password": password, "accepted_terms": True, "accepted_risk_acknowledgment": True,
            })
        return resp.json()["token"]

    def test_me_returns_user(self):
        token = self._signup()
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], "u@x.com")

    def test_missing_authorization_header(self):
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token(self):
        resp = self.client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
        self.assertEqual(resp.status_code, 401)

    def test_token_for_deleted_user(self):
        token = self._signup(email="del@x.com")
        from core.auth import get_user_by_email, soft_delete_user
        u = get_user_by_email("del@x.com")
        soft_delete_user(u.id)
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)


# ─── Verify email ──────────────────────────────────────────────────────────


class VerifyEmailEndpointTests(AuthAPITestBase):

    def test_happy_path(self):
        from core.auth import create_user, create_email_verification, get_user_by_id
        uid = create_user("v@x.com", "verylongpassword123")
        token = create_email_verification(uid)
        resp = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(get_user_by_id(uid).email_verified)

    def test_invalid_token(self):
        resp = self.client.post("/api/auth/verify-email",
                                 json={"token": "x" * 30})
        self.assertEqual(resp.status_code, 400)

    def test_token_single_use(self):
        from core.auth import create_user, create_email_verification
        uid = create_user("vv@x.com", "verylongpassword123")
        token = create_email_verification(uid)
        self.client.post("/api/auth/verify-email", json={"token": token})
        # Second attempt fails
        resp = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(resp.status_code, 400)


# ─── Password reset flow ──────────────────────────────────────────────────


class PasswordResetEndpointTests(AuthAPITestBase):

    def test_request_reset_for_existing_email(self):
        from core.auth import create_user
        create_user("r@x.com", "verylongpassword123")
        with patch("api.auth.email_sender.send_password_reset_email") as mock_send:
            mock_send.return_value = {"ok": True}
            resp = self.client.post("/api/auth/request-password-reset",
                                     json={"email": "r@x.com"})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once()

    def test_request_reset_for_unknown_email_returns_ok(self):
        """Anti-enumeration: always 200 regardless of whether email exists."""
        with patch("api.auth.email_sender.send_password_reset_email") as mock_send:
            mock_send.return_value = {"ok": True}
            resp = self.client.post("/api/auth/request-password-reset",
                                     json={"email": "nobody@x.com"})
        self.assertEqual(resp.status_code, 200)
        # But no email was sent (no user found)
        mock_send.assert_not_called()

    def test_apply_reset_changes_password(self):
        from core.auth import create_user, create_password_reset, get_user_password_hash, verify_password
        uid = create_user("a@x.com", "old-password-very-long")
        old_hash = get_user_password_hash(uid)
        token = create_password_reset(uid)
        with patch("api.auth.email_sender.send_password_changed_notification") as mock_notify:
            mock_notify.return_value = {"ok": True}
            resp = self.client.post("/api/auth/reset-password", json={
                "token": token, "new_password": "new-password-very-long-123",
            })
        self.assertEqual(resp.status_code, 200)
        new_hash = get_user_password_hash(uid)
        self.assertNotEqual(old_hash, new_hash)
        self.assertTrue(verify_password("new-password-very-long-123", new_hash))
        mock_notify.assert_called_once()

    def test_apply_reset_with_invalid_token(self):
        resp = self.client.post("/api/auth/reset-password", json={
            "token": "x" * 30, "new_password": "new-password-very-long",
        })
        self.assertEqual(resp.status_code, 400)


# ─── Change password ──────────────────────────────────────────────────────


class ChangePasswordEndpointTests(AuthAPITestBase):

    def _signup(self):
        with patch("api.auth.email_sender.send_verify_email") as mock_send:
            mock_send.return_value = {"ok": True}
            resp = self.client.post("/api/auth/signup", json={
                "email": "cp@x.com",
                "password": "current-password-12345",
                "accepted_terms": True, "accepted_risk_acknowledgment": True,
            })
        return resp.json()["token"]

    def test_change_password_happy_path(self):
        token = self._signup()
        with patch("api.auth.email_sender.send_password_changed_notification") as mock_notify:
            mock_notify.return_value = {"ok": True}
            resp = self.client.post("/api/auth/change-password",
                headers={"Authorization": f"Bearer {token}"},
                json={"current_password": "current-password-12345",
                      "new_password": "fresh-new-pass-67890"},
            )
        self.assertEqual(resp.status_code, 200)
        mock_notify.assert_called_once()

    def test_wrong_current_password(self):
        token = self._signup()
        resp = self.client.post("/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "wrong-password",
                  "new_password": "anything-very-long-here"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_same_new_password_rejected(self):
        token = self._signup()
        resp = self.client.post("/api/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "current-password-12345",
                  "new_password": "current-password-12345"},
        )
        self.assertEqual(resp.status_code, 400)


# ─── Account deletion ────────────────────────────────────────────────────


class AccountDeletionTests(AuthAPITestBase):

    def _signup(self, email="del@x.com", password="verylongpassword123"):
        with patch("api.auth.email_sender.send_verify_email") as mock_send:
            mock_send.return_value = {"ok": True}
            resp = self.client.post("/api/auth/signup", json={
                "email": email, "password": password, "accepted_terms": True, "accepted_risk_acknowledgment": True,
            })
        return resp.json()["token"], email, password

    def test_happy_path(self):
        token, email, password = self._signup()
        resp = self.client.request(
            "DELETE", "/api/auth/account",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": password, "confirmation_email": email},
        )
        self.assertEqual(resp.status_code, 200)
        # User can no longer log in
        login_resp = self.client.post("/api/auth/login",
                                       json={"email": email, "password": password})
        self.assertEqual(login_resp.status_code, 401)

    def test_wrong_confirmation_email(self):
        token, email, password = self._signup()
        resp = self.client.request(
            "DELETE", "/api/auth/account",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": password,
                  "confirmation_email": "wrong@x.com"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_wrong_password(self):
        token, email, _ = self._signup()
        resp = self.client.request(
            "DELETE", "/api/auth/account",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "wrong-password",
                  "confirmation_email": email},
        )
        self.assertEqual(resp.status_code, 401)


# ─── Rate limiting smoke test ────────────────────────────────────────────


class RateLimitingTests(AuthAPITestBase):

    def test_signup_rate_limit(self):
        # The signup endpoint also calls provisioner_client.initialize_user_dir
        # which would try to write to /app/data/users/<n>/ — collides with
        # the operator's real bind-mounted dirs in dev. Mock the provisioner
        # calls so this test stays in the auth-rate-limit scope.
        from core import auth as core_auth
        limit = core_auth.signup_limiter.max_attempts
        with patch("api.auth.email_sender.send_verify_email") as mock_send, \
             patch("api.auth.provisioner_client.initialize_user_dir"), \
             patch("api.auth.provisioner_client.enqueue_provision"):
            mock_send.return_value = {"ok": True}
            # `limit` signups should be allowed within the window
            for i in range(limit):
                resp = self.client.post("/api/auth/signup", json={
                    "email": f"u{i}@x.com",
                    "password": "verylongpassword123",
                    "accepted_terms": True, "accepted_risk_acknowledgment": True,
                })
                self.assertEqual(resp.status_code, 201, f"signup #{i+1}/{limit} failed: {resp.text}")
            # The (limit+1)th should be rate-limited
            resp = self.client.post("/api/auth/signup", json={
                "email": "u_overflow@x.com",
                "password": "verylongpassword123",
                "accepted_terms": True, "accepted_risk_acknowledgment": True,
            })
            self.assertEqual(resp.status_code, 429)
            self.assertIn("Retry-After", resp.headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
