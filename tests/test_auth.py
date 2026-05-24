"""Tests for core.auth — password hashing, JWT, DB helpers, rate limiting.

Each test uses an isolated SQLite file (tmp directory) so we never touch
real production data. Run inside the ionic-api container (or any env
with argon2-cffi + python-jose + passlib installed).

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_auth -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add repo root to path so `from core import auth` works whether run
# from repo root or from tests/ dir
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import auth as core_auth  # noqa: E402


# Path to the schema-init script — we need to materialize a fresh
# global.db for each test class
def _init_test_db(path: str) -> None:
    """Run scripts/init_global_db.py against the given path."""
    # Inline the schema rather than shelling out to subprocess — keeps
    # tests fast and avoids cwd surprises
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(path, verbose=False)


class TestSetupBase(unittest.TestCase):
    """Provides a fresh global.db per test class + restores env vars after."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_auth_test_")
        cls._db_path = os.path.join(cls._tmpdir, "test_global.db")
        cls._original_env = dict(os.environ)

        # Provide deterministic test env
        os.environ["GLOBAL_DB_PATH"] = cls._db_path
        os.environ["API_SECRET_KEY"] = "test-secret-key-do-not-use-in-prod-" + "x" * 40
        os.environ["JWT_EXPIRY_DAYS"] = "7"

        # Force module to re-read env (it captures these at import-time)
        # Easiest way: directly patch the module-level constants
        core_auth.GLOBAL_DB_PATH = cls._db_path
        core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
        core_auth.JWT_EXPIRY_DAYS = 7

        _init_test_db(cls._db_path)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._original_env)

    def setUp(self):
        """Truncate all tables between tests to avoid cross-test pollution."""
        conn = sqlite3.connect(self._db_path)
        for table in ["users", "email_verifications", "password_resets",
                      "broker_keys", "broker_key_events", "login_attempts",
                      "market_states"]:
            conn.execute(f"DELETE FROM {table}")
        # Reset autoincrement counters
        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
        conn.close()


# ─── Password hashing ──────────────────────────────────────────────────────


class PasswordHashingTests(TestSetupBase):

    def test_round_trip(self):
        h = core_auth.hash_password("correct-horse-battery-staple")
        self.assertTrue(core_auth.verify_password("correct-horse-battery-staple", h))
        self.assertFalse(core_auth.verify_password("wrong-password-here", h))

    def test_argon2_is_default_scheme(self):
        """New hashes should use argon2, not bcrypt."""
        h = core_auth.hash_password("test-password-12345")
        # passlib argon2 hashes start with $argon2id$ or $argon2i$ etc.
        self.assertTrue(h.startswith("$argon2"))

    def test_bcrypt_hash_still_verifies(self):
        """Existing bcrypt hashes (operator's legacy hash) must still verify."""
        # passlib's bcrypt: prefix $2b$
        from passlib.hash import bcrypt
        legacy_hash = bcrypt.hash("operator-password")
        self.assertTrue(core_auth.verify_password("operator-password", legacy_hash))

    def test_bcrypt_hash_flagged_as_needs_rehash(self):
        """Bcrypt hashes should report needs_update=True so we can migrate."""
        from passlib.hash import bcrypt
        legacy_hash = bcrypt.hash("any-pass")
        self.assertTrue(core_auth.password_needs_rehash(legacy_hash))

    def test_argon2_hash_does_not_need_rehash(self):
        h = core_auth.hash_password("fresh-pass")
        self.assertFalse(core_auth.password_needs_rehash(h))

    def test_empty_password_rejected(self):
        with self.assertRaises(core_auth.AuthError):
            core_auth.hash_password("")
        self.assertFalse(core_auth.verify_password("", "anyhash"))

    def test_wrong_hash_format_returns_false(self):
        self.assertFalse(core_auth.verify_password("anything", "not-a-real-hash"))


# ─── JWT ────────────────────────────────────────────────────────────────────


class JWTTests(TestSetupBase):

    def test_encode_decode_round_trip(self):
        token = core_auth.create_jwt(
            user_id=42, email="user@example.com",
            email_verified=True, is_admin=False,
        )
        claims = core_auth.decode_jwt(token)
        self.assertEqual(claims["sub"], "42")
        self.assertEqual(claims["email"], "user@example.com")
        self.assertTrue(claims["email_verified"])
        self.assertFalse(claims["is_admin"])
        self.assertIn("iat", claims)
        self.assertIn("exp", claims)

    def test_admin_claim(self):
        token = core_auth.create_jwt(1, "blisske@gmail.com", True, True)
        claims = core_auth.decode_jwt(token)
        self.assertTrue(claims["is_admin"])

    def test_expired_token_rejected(self):
        from datetime import timedelta
        token = core_auth.create_jwt(
            1, "u@x.com", True, False,
            expires_delta=timedelta(seconds=-1),  # already expired
        )
        with self.assertRaises(core_auth.AuthError):
            core_auth.decode_jwt(token)

    def test_tampered_token_rejected(self):
        token = core_auth.create_jwt(1, "u@x.com", True, False)
        # Flip the last char of the signature portion
        parts = token.rsplit(".", 1)
        tampered = parts[0] + "." + ("x" if parts[1][-1] != "x" else "y") + parts[1][1:]
        with self.assertRaises(core_auth.AuthError):
            core_auth.decode_jwt(tampered)

    def test_wrong_secret_rejected(self):
        token = core_auth.create_jwt(1, "u@x.com", True, False)
        original_secret = core_auth.JWT_SECRET_KEY
        try:
            core_auth.JWT_SECRET_KEY = "different-secret"
            with self.assertRaises(core_auth.AuthError):
                core_auth.decode_jwt(token)
        finally:
            core_auth.JWT_SECRET_KEY = original_secret

    def test_empty_token_rejected(self):
        with self.assertRaises(core_auth.AuthError):
            core_auth.decode_jwt("")


# ─── Token generation ─────────────────────────────────────────────────────


class TokenGenerationTests(unittest.TestCase):

    def test_token_is_url_safe_string(self):
        t = core_auth.generate_token()
        # URL-safe base64 chars: A-Z a-z 0-9 - _
        import re
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", t))
        self.assertGreaterEqual(len(t), 32)

    def test_tokens_are_unique(self):
        """Generate 1000 tokens; expect zero collisions."""
        tokens = {core_auth.generate_token() for _ in range(1000)}
        self.assertEqual(len(tokens), 1000)


# ─── User CRUD ─────────────────────────────────────────────────────────────


class UserCRUDTests(TestSetupBase):

    def test_create_and_lookup(self):
        uid = core_auth.create_user("alice@example.com", "verylongpassword123")
        self.assertEqual(uid, 1)
        u = core_auth.get_user_by_id(uid)
        self.assertEqual(u.email, "alice@example.com")
        self.assertFalse(u.email_verified)
        self.assertFalse(u.is_admin)

    def test_email_case_insensitive_lookup(self):
        core_auth.create_user("Bob@Example.com", "verylongpassword123")
        u = core_auth.get_user_by_email("bob@example.com")
        self.assertIsNotNone(u)
        self.assertEqual(u.email, "Bob@Example.com")  # original case preserved

    def test_email_uniqueness_enforced(self):
        core_auth.create_user("carol@example.com", "verylongpassword123")
        with self.assertRaises(sqlite3.IntegrityError):
            core_auth.create_user("carol@example.com", "anotherverylongpassword")

    def test_email_uniqueness_case_insensitive(self):
        core_auth.create_user("Dave@example.com", "verylongpassword123")
        with self.assertRaises(sqlite3.IntegrityError):
            core_auth.create_user("dave@example.com", "verylongpassword123")

    def test_short_password_rejected(self):
        with self.assertRaises(core_auth.AuthError):
            core_auth.create_user("e@example.com", "short")

    def test_invalid_email_rejected(self):
        with self.assertRaises(core_auth.AuthError):
            core_auth.create_user("not-an-email", "verylongpassword123")

    def test_soft_delete(self):
        uid = core_auth.create_user("frank@example.com", "verylongpassword123")
        core_auth.soft_delete_user(uid)
        # Default lookups exclude deleted users
        self.assertIsNone(core_auth.get_user_by_email("frank@example.com"))
        self.assertIsNone(core_auth.get_user_by_id(uid))
        # include_deleted=True returns the soft-deleted row
        u = core_auth.get_user_by_id(uid, include_deleted=True)
        self.assertIsNotNone(u)
        self.assertEqual(u.email, f"{uid}@deleted.foundationbots.com")
        self.assertIsNotNone(u.deleted_at)

    def test_email_reusable_after_soft_delete(self):
        """Per the partial unique index, a deleted user's email frees up."""
        uid = core_auth.create_user("grace@example.com", "verylongpassword123")
        core_auth.soft_delete_user(uid)
        # New signup with same email should succeed
        new_uid = core_auth.create_user("grace@example.com", "verylongpassword123")
        self.assertNotEqual(new_uid, uid)

    def test_mark_email_verified(self):
        uid = core_auth.create_user("hank@example.com", "verylongpassword123")
        self.assertFalse(core_auth.get_user_by_id(uid).email_verified)
        core_auth.mark_email_verified(uid)
        self.assertTrue(core_auth.get_user_by_id(uid).email_verified)

    def test_update_user_email_clears_verified(self):
        uid = core_auth.create_user("ian@example.com", "verylongpassword123")
        core_auth.mark_email_verified(uid)
        self.assertTrue(core_auth.get_user_by_id(uid).email_verified)
        core_auth.update_user_email(uid, "ian2@example.com")
        u = core_auth.get_user_by_id(uid)
        self.assertEqual(u.email, "ian2@example.com")
        self.assertFalse(u.email_verified)


# ─── Verification tokens ──────────────────────────────────────────────────


class VerificationTokenTests(TestSetupBase):

    def test_create_and_consume(self):
        uid = core_auth.create_user("jen@example.com", "verylongpassword123")
        token = core_auth.create_email_verification(uid)
        self.assertGreaterEqual(len(token), 32)
        consumed_uid = core_auth.consume_email_verification(token)
        self.assertEqual(consumed_uid, uid)

    def test_token_is_single_use(self):
        uid = core_auth.create_user("ken@example.com", "verylongpassword123")
        token = core_auth.create_email_verification(uid)
        self.assertEqual(core_auth.consume_email_verification(token), uid)
        # Second consume returns None
        self.assertIsNone(core_auth.consume_email_verification(token))

    def test_unknown_token_returns_none(self):
        self.assertIsNone(core_auth.consume_email_verification("never-issued-token"))
        self.assertIsNone(core_auth.consume_email_verification(""))

    def test_expired_token_returns_none(self):
        from datetime import datetime, timedelta, timezone
        uid = core_auth.create_user("liam@example.com", "verylongpassword123")
        token = core_auth.create_email_verification(uid)
        # Manually expire it
        with core_auth.db_connect() as conn:
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            conn.execute("UPDATE email_verifications SET expires_at = ? WHERE token = ?", (past, token))
            conn.commit()
        self.assertIsNone(core_auth.consume_email_verification(token))


class PasswordResetTokenTests(TestSetupBase):

    def test_create_and_consume(self):
        uid = core_auth.create_user("mia@example.com", "verylongpassword123")
        token = core_auth.create_password_reset(uid)
        self.assertEqual(core_auth.consume_password_reset(token), uid)

    def test_single_use(self):
        uid = core_auth.create_user("nick@example.com", "verylongpassword123")
        token = core_auth.create_password_reset(uid)
        self.assertEqual(core_auth.consume_password_reset(token), uid)
        self.assertIsNone(core_auth.consume_password_reset(token))


# ─── Login attempts logging ───────────────────────────────────────────────


class LoginAttemptLoggingTests(TestSetupBase):

    def test_record_login_attempt(self):
        core_auth.record_login_attempt("user@example.com", "127.0.0.1", "success")
        with core_auth.db_connect() as conn:
            row = conn.execute("SELECT username, ip, result FROM login_attempts").fetchone()
        self.assertEqual(row["username"], "user@example.com")
        self.assertEqual(row["ip"], "127.0.0.1")
        self.assertEqual(row["result"], "success")

    def test_failing_record_does_not_raise(self):
        """If DB is unreachable, record_login_attempt swallows the error."""
        # Force the function to use a bogus path
        core_auth.record_login_attempt(
            "x@y.com", "1.2.3.4", "test",
            db_path="/nonexistent/path/global.db",
        )
        # Reaching here = success (didn't raise)


# ─── Rate limiter ──────────────────────────────────────────────────────────


class RateLimiterTests(unittest.TestCase):

    def test_allows_within_limit(self):
        rl = core_auth.RateLimiter(max_attempts=3, window_sec=60)
        for _ in range(3):
            self.assertTrue(rl.allow("key1"))

    def test_rejects_above_limit(self):
        rl = core_auth.RateLimiter(max_attempts=2, window_sec=60)
        self.assertTrue(rl.allow("key2"))
        self.assertTrue(rl.allow("key2"))
        self.assertFalse(rl.allow("key2"))

    def test_per_key_isolated(self):
        rl = core_auth.RateLimiter(max_attempts=1, window_sec=60)
        self.assertTrue(rl.allow("alice"))
        self.assertFalse(rl.allow("alice"))
        # Different key has its own counter
        self.assertTrue(rl.allow("bob"))

    def test_reset_clears_counter(self):
        rl = core_auth.RateLimiter(max_attempts=1, window_sec=60)
        self.assertTrue(rl.allow("k"))
        self.assertFalse(rl.allow("k"))
        rl.reset("k")
        self.assertTrue(rl.allow("k"))

    def test_window_slides(self):
        rl = core_auth.RateLimiter(max_attempts=2, window_sec=1)
        self.assertTrue(rl.allow("k"))
        self.assertTrue(rl.allow("k"))
        self.assertFalse(rl.allow("k"))
        time.sleep(1.1)
        # Window has expired
        self.assertTrue(rl.allow("k"))

    def test_retry_after_within_window(self):
        rl = core_auth.RateLimiter(max_attempts=1, window_sec=60)
        rl.allow("k")
        retry = rl.retry_after("k")
        self.assertGreater(retry, 0)
        self.assertLessEqual(retry, 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
