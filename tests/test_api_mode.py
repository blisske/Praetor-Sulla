"""Integration tests for api/mode.py — shadow ↔ live toggle.

Mocks email_sender. Real per-user Config.yaml in a temp directory so the
ruamel.yaml round-trip is actually exercised (file I/O + comment preservation
+ backup file creation).

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_mode -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ─── Test setup helpers ────────────────────────────────────────────────────


def _setup_test_env(db_path: str, user_data_dir: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["USER_DATA_DIR"] = user_data_dir
    os.environ["POSTMARK_SERVER_TOKEN"] = "test-postmark-token-not-real"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from shared import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    # Force api.mode to reread USER_DATA_DIR (module-level cache)
    from api import mode as mode_mod
    mode_mod.USER_DATA_DIR = user_data_dir


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states",
              "live_mode_confirmations"]:
        try:
            conn.execute(f"DELETE FROM {t}")
        except sqlite3.OperationalError:
            pass  # table may not exist in older test DB schemas
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(db_path: str, email: str = "alice@example.com") -> tuple[int, str]:
    from shared import auth as core_auth
    user_id = core_auth.create_user(email=email, password="longenoughpassword")
    token = core_auth.create_jwt(user_id=user_id, email=email, email_verified=False, is_admin=False)
    return user_id, token


def _seed_broker_key(db_path: str, user_id: int, scope: str = "trade") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO broker_keys (user_id, broker, key_enc, secret_enc, scope, validated_at) "
        "VALUES (?, 'oanda', 'fake_enc_key', 'fake_enc_secret', ?, CURRENT_TIMESTAMP)",
        (user_id, scope),
    )
    conn.commit()
    conn.close()


def _seed_config_yaml(user_data_dir: str, user_id: int, shadow_mode: bool = True) -> Path:
    """Create a per-user Config.yaml with the expected exchange.shadow_mode key.
    Returns the path so tests can inspect it."""
    user_dir = Path(user_data_dir) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    cfg = user_dir / "Config.yaml"
    cfg.write_text(textwrap.dedent(f"""\
        # Test config for user {user_id}
        exchange:
          venue: oanda
          api_url: https://api.oanda.com  # informational
          shadow_mode: {str(shadow_mode).lower()}
        consensus:
          min_consensus: 3
        """))
    return cfg


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class ModeTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_mode_test_")
        cls._db_path = os.path.join(cls._tmpdir, "test_global.db")
        cls._user_data_dir = os.path.join(cls._tmpdir, "users")
        os.makedirs(cls._user_data_dir, exist_ok=True)
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path, cls._user_data_dir)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.mode import router as mode_router

        app = FastAPI()
        app.include_router(mode_router)
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
        # Wipe per-user dirs
        for entry in Path(self._user_data_dir).iterdir():
            if entry.is_dir():
                import shutil
                shutil.rmtree(entry)
        self.user_id, self.token = _seed_user(self._db_path)
        self.headers = _bearer(self.token)


# ─── GET /api/user/mode ────────────────────────────────────────────────────


class GetModeTests(ModeTestBase):

    def test_no_config_no_broker_returns_shadow_cant_live(self):
        resp = self.client.get("/api/user/mode", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["mode"], "shadow")
        self.assertFalse(body["can_live"])
        self.assertFalse(body["config_present"])
        # When config is missing, that's the gate reason (not broker)
        self.assertIn("being set up", body["gate_reason"])

    def test_config_present_no_broker(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        resp = self.client.get("/api/user/mode", headers=self.headers)
        body = resp.json()
        self.assertEqual(body["mode"], "shadow")
        self.assertTrue(body["config_present"])
        self.assertFalse(body["can_live"])
        self.assertIn("Connect a Oanda API key", body["gate_reason"])

    def test_config_present_read_scope_key(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="read")
        resp = self.client.get("/api/user/mode", headers=self.headers)
        body = resp.json()
        self.assertEqual(body["broker_scope"], "read")
        self.assertFalse(body["can_live"])
        self.assertIn("read permission", body["gate_reason"])

    def test_config_present_trade_scope_key_unlocks_live(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        resp = self.client.get("/api/user/mode", headers=self.headers)
        body = resp.json()
        self.assertEqual(body["mode"], "shadow")
        self.assertEqual(body["broker_scope"], "trade")
        self.assertTrue(body["can_live"])
        self.assertIsNone(body["gate_reason"])

    def test_reads_live_from_config(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=False)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        resp = self.client.get("/api/user/mode", headers=self.headers)
        body = resp.json()
        self.assertEqual(body["mode"], "live")


# ─── POST /api/user/mode — to live ─────────────────────────────────────────


class FlipToLiveTests(ModeTestBase):

    def test_no_oanda_key_returns_400(self):
        _seed_config_yaml(self._user_data_dir, self.user_id)
        resp = self.client.post(
            "/api/user/mode", json={"mode": "live"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Connect a Oanda", resp.json()["detail"])

    def test_read_scope_key_returns_400(self):
        _seed_config_yaml(self._user_data_dir, self.user_id)
        _seed_broker_key(self._db_path, self.user_id, scope="read")
        resp = self.client.post(
            "/api/user/mode", json={"mode": "live"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("read-only", resp.json()["detail"])

    def test_no_config_returns_503(self):
        # User has a trade key but their workspace was never provisioned —
        # we treat this as "still being set up" rather than letting them
        # flip a config that doesn't exist
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        resp = self.client.post(
            "/api/user/mode", json={"mode": "live"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("being set up", resp.json()["detail"])

    def _flip_to_live_with_otp(self, mock_email_paths=("api.mode.email_sender.send_live_mode_activated_email",
                                                          "api.mode.email_sender.send_live_mode_confirmation_code")):
        """Helper: do the full 2-step live flip (request code → POST with code).
        Returns (initial_response, final_response, the_code)."""
        # Step 1: no code → backend issues + emails one
        with patch(mock_email_paths[0]) as mock_live, \
             patch(mock_email_paths[1]) as mock_code:
            mock_live.return_value = {"ok": True}
            mock_code.return_value = {"ok": True}
            r1 = self.client.post("/api/user/mode", json={"mode": "live"}, headers=self.headers)
            # Capture the code by reading it from DB (since send_live_mode_confirmation_code was mocked)
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            code = conn.execute(
                "SELECT code FROM live_mode_confirmations WHERE user_id=? AND consumed_at IS NULL "
                "ORDER BY id DESC LIMIT 1", (self.user_id,)
            ).fetchone()[0]
            conn.close()
            # Step 2: POST with the code
            r2 = self.client.post("/api/user/mode",
                                    json={"mode": "live", "confirmation_code": code},
                                    headers=self.headers)
        return (r1, r2, code)

    def test_first_post_without_code_returns_confirmation_required(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        with patch("api.mode.email_sender.send_live_mode_confirmation_code") as mock_code:
            mock_code.return_value = {"ok": True}
            resp = self.client.post("/api/user/mode", json={"mode": "live"}, headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["confirmation_required"])
        self.assertEqual(body["mode"], "shadow")  # NOT flipped yet
        mock_code.assert_called_once()

    def test_happy_path_with_otp_flips_yaml_and_emails(self):
        cfg = _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        r1, r2, _ = self._flip_to_live_with_otp()
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["confirmation_required"])
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["mode"], "live")
        # YAML actually flipped on disk
        from ruamel.yaml import YAML
        with open(cfg) as f:
            data = YAML().load(f)
        self.assertFalse(data["exchange"]["shadow_mode"])
        # .bak created
        self.assertTrue(cfg.with_suffix(".yaml.bak").exists())
        # Audit row written
        conn = sqlite3.connect(self._db_path)
        events = [r[0] for r in conn.execute(
            "SELECT event_type FROM broker_key_events WHERE user_id=?", (self.user_id,)
        )]
        conn.close()
        self.assertIn("mode_flipped_to_live", events)
        self.assertIn("live_mode_confirmation_sent", events)

    def test_happy_path_touches_restart_flag(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        self._flip_to_live_with_otp()
        flag = Path(self._user_data_dir) / str(self.user_id) / ".restart_engine"
        self.assertTrue(flag.exists())

    def test_yaml_comments_preserved(self):
        cfg = _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        self._flip_to_live_with_otp()
        text = cfg.read_text()
        self.assertIn("# informational", text)
        self.assertIn("# Test config", text)

    def test_email_failure_does_not_break_flip(self):
        """Postmark down for the ACTIVATED email (post-flip) shouldn't
        unflip. The OTP-code email failure is a separate path tested
        in test_otp_code_email_failure_doesnt_block_request."""
        cfg = _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        # Step 1: get code (with code-email succeeding)
        with patch("api.mode.email_sender.send_live_mode_confirmation_code") as mock_code:
            mock_code.return_value = {"ok": True}
            self.client.post("/api/user/mode", json={"mode": "live"}, headers=self.headers)
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        code = conn.execute(
            "SELECT code FROM live_mode_confirmations WHERE user_id=? AND consumed_at IS NULL "
            "ORDER BY id DESC LIMIT 1", (self.user_id,)
        ).fetchone()[0]
        conn.close()
        # Step 2: activated-email path fails, but flip succeeds
        with patch("api.mode.email_sender.send_live_mode_activated_email") as mock_live:
            mock_live.side_effect = RuntimeError("postmark down")
            resp = self.client.post("/api/user/mode",
                                      json={"mode": "live", "confirmation_code": code},
                                      headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        from ruamel.yaml import YAML
        with open(cfg) as f:
            data = YAML().load(f)
        self.assertFalse(data["exchange"]["shadow_mode"])

    def test_wrong_code_returns_400(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=True)
        _seed_broker_key(self._db_path, self.user_id, scope="trade")
        # Trigger code generation
        with patch("api.mode.email_sender.send_live_mode_confirmation_code") as mock_code:
            mock_code.return_value = {"ok": True}
            self.client.post("/api/user/mode", json={"mode": "live"}, headers=self.headers)
        # Submit wrong code
        resp = self.client.post("/api/user/mode",
                                  json={"mode": "live", "confirmation_code": "000000"},
                                  headers=self.headers)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Wrong code", resp.json()["detail"])

    def test_blocked_attempt_audit_logged_with_reason(self):
        _seed_config_yaml(self._user_data_dir, self.user_id)
        _seed_broker_key(self._db_path, self.user_id, scope="read")
        self.client.post(
            "/api/user/mode", json={"mode": "live"}, headers=self.headers
        )
        conn = sqlite3.connect(self._db_path)
        rows = conn.execute(
            "SELECT event_type, detail FROM broker_key_events WHERE user_id=?",
            (self.user_id,),
        ).fetchall()
        conn.close()
        block_rows = [r for r in rows if r[0] == "mode_flip_to_live_blocked"]
        self.assertEqual(len(block_rows), 1)
        self.assertIn("scope=read", block_rows[0][1])


# ─── POST /api/user/mode — to shadow ────────────────────────────────────────


class FlipToShadowTests(ModeTestBase):

    def test_shadow_flip_unconditional(self):
        """Flipping back to shadow needs no broker key — always allowed."""
        cfg = _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=False)
        resp = self.client.post(
            "/api/user/mode", json={"mode": "shadow"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["mode"], "shadow")
        from ruamel.yaml import YAML
        with open(cfg) as f:
            data = YAML().load(f)
        self.assertTrue(data["exchange"]["shadow_mode"])

    def test_shadow_flip_no_config_returns_503(self):
        """Even shadow flip needs a workspace — the engine needs SOME config."""
        resp = self.client.post(
            "/api/user/mode", json={"mode": "shadow"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 503)

    def test_shadow_flip_audit_logged(self):
        _seed_config_yaml(self._user_data_dir, self.user_id, shadow_mode=False)
        self.client.post(
            "/api/user/mode", json={"mode": "shadow"}, headers=self.headers
        )
        conn = sqlite3.connect(self._db_path)
        events = [r[0] for r in conn.execute(
            "SELECT event_type FROM broker_key_events WHERE user_id=?",
            (self.user_id,),
        )]
        conn.close()
        self.assertIn("mode_flipped_to_shadow", events)


# ─── Validation + auth ─────────────────────────────────────────────────────


class ModeValidationTests(ModeTestBase):

    def test_invalid_mode_value_returns_422(self):
        _seed_config_yaml(self._user_data_dir, self.user_id)
        resp = self.client.post(
            "/api/user/mode", json={"mode": "paper"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 422)

    def test_missing_bearer_returns_401_get(self):
        resp = self.client.get("/api/user/mode")
        self.assertEqual(resp.status_code, 401)

    def test_missing_bearer_returns_401_post(self):
        resp = self.client.post("/api/user/mode", json={"mode": "shadow"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
