"""Integration tests for api/freeze.py + admin unfreeze endpoint.

Verifies the user-initiated freeze + operator-only unfreeze flow,
including the Config.yaml side-effect (risk.frozen) + audit trail.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_freeze -v
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


def _setup_test_env(db_path: str, user_data_dir: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["USER_DATA_DIR"] = user_data_dir
    os.environ["POSTMARK_SERVER_TOKEN"] = "test-token"

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from core import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    from api import mode as mode_mod
    mode_mod.USER_DATA_DIR = user_data_dir


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states"]:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(email="alice@x.com", is_admin=False):
    from core import auth as core_auth
    user_id = core_auth.create_user(email=email, password="longenoughpassword", is_admin=is_admin)
    token = core_auth.create_jwt(user_id=user_id, email=email, email_verified=True, is_admin=is_admin)
    return user_id, token


def _seed_config(user_data_dir, user_id, frozen=False):
    user_dir = Path(user_data_dir) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    cfg = user_dir / "Config.yaml"
    cfg.write_text(textwrap.dedent(f"""\
        exchange:
          shadow_mode: true
        risk:
          frozen: {str(frozen).lower()}
          initial_capital: 10000
        """))
    return cfg


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class FreezeTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_freeze_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._user_data_dir = os.path.join(cls._tmpdir, "users")
        os.makedirs(cls._user_data_dir, exist_ok=True)
        cls._saved_env = dict(os.environ)
        _setup_test_env(cls._db_path, cls._user_data_dir)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.freeze import router as freeze_router
        from api.admin import router as admin_router

        app = FastAPI()
        app.include_router(freeze_router)
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
        # Wipe per-user dirs
        for entry in Path(self._user_data_dir).iterdir():
            if entry.is_dir():
                import shutil
                shutil.rmtree(entry)
        self.user_id, self.user_token = _seed_user("alice@x.com")
        self.admin_id, self.admin_token = _seed_user("admin@x.com", is_admin=True)


# ─── GET /api/user/freeze ──────────────────────────────────────────────────


class GetFreezeStateTests(FreezeTestBase):

    def test_no_config_yet_returns_not_frozen(self):
        r = self.client.get("/api/user/freeze", headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["frozen"])
        self.assertFalse(body["config_present"])

    def test_config_present_not_frozen(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=False)
        r = self.client.get("/api/user/freeze", headers=_bearer(self.user_token))
        body = r.json()
        self.assertFalse(body["frozen"])
        self.assertTrue(body["config_present"])

    def test_config_present_already_frozen(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=True)
        r = self.client.get("/api/user/freeze", headers=_bearer(self.user_token))
        body = r.json()
        self.assertTrue(body["frozen"])

    def test_can_unfreeze_always_false_for_user(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=True)
        r = self.client.get("/api/user/freeze", headers=_bearer(self.user_token))
        self.assertFalse(r.json()["can_unfreeze"])


# ─── POST /api/user/freeze ─────────────────────────────────────────────────


class FreezeAccountTests(FreezeTestBase):

    def test_freeze_sets_config_flag(self):
        cfg = _seed_config(self._user_data_dir, self.user_id, frozen=False)
        with patch("api.freeze.email_sender.send_email") as mock_send:
            mock_send.return_value = {"ok": True}
            r = self.client.post("/api/user/freeze",
                                  json={"reason": "test freeze"},
                                  headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        from ruamel.yaml import YAML
        with open(cfg) as f:
            data = YAML().load(f)
        self.assertTrue(data["risk"]["frozen"])
        self.assertEqual(data["risk"]["frozen_reason"], "test freeze")
        self.assertIn("frozen_at", data["risk"])

        # Confirmation email sent on FIRST freeze
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["tag"], "account-frozen")

    def test_freeze_touches_restart_flag(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=False)
        with patch("api.freeze.email_sender.send_email") as mock_send:
            mock_send.return_value = {"ok": True}
            self.client.post("/api/user/freeze", json={},
                              headers=_bearer(self.user_token))
        flag = Path(self._user_data_dir) / str(self.user_id) / ".restart_engine"
        self.assertTrue(flag.exists())

    def test_freeze_no_config_returns_503(self):
        # User without provisioned workspace
        r = self.client.post("/api/user/freeze", json={},
                              headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 503)

    def test_freeze_audit_logged(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=False)
        with patch("api.freeze.email_sender.send_email"):
            self.client.post("/api/user/freeze",
                              json={"reason": "market panic"},
                              headers=_bearer(self.user_token))
        conn = sqlite3.connect(self._db_path)
        rows = [(r[0], r[1]) for r in conn.execute(
            "SELECT event_type, detail FROM broker_key_events WHERE user_id=?",
            (self.user_id,),
        )]
        conn.close()
        types = [t for t, _ in rows]
        self.assertIn("account_frozen", types)
        # Reason recorded but truncated
        detail = next(d for t, d in rows if t == "account_frozen")
        self.assertIn("market panic", detail)

    def test_refreeze_idempotent_no_duplicate_email(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=True)
        with patch("api.freeze.email_sender.send_email") as mock_send:
            mock_send.return_value = {"ok": True}
            r = self.client.post("/api/user/freeze",
                                  json={"reason": "re-freeze"},
                                  headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 200)
        # Re-freezing already-frozen account does NOT email again
        mock_send.assert_not_called()

    def test_freeze_email_failure_does_not_break(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=False)
        with patch("api.freeze.email_sender.send_email") as mock_send:
            mock_send.side_effect = RuntimeError("postmark down")
            r = self.client.post("/api/user/freeze", json={},
                                  headers=_bearer(self.user_token))
        # Freeze still succeeds — config was written
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])


# ─── POST /api/admin/users/{id}/unfreeze ───────────────────────────────────


class AdminUnfreezeTests(FreezeTestBase):

    def test_admin_can_unfreeze_frozen_user(self):
        cfg = _seed_config(self._user_data_dir, self.user_id, frozen=True)
        r = self.client.post(f"/api/admin/users/{self.user_id}/unfreeze",
                              headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        from ruamel.yaml import YAML
        with open(cfg) as f:
            data = YAML().load(f)
        self.assertFalse(data["risk"]["frozen"])
        # Metadata cleared on unfreeze
        self.assertNotIn("frozen_at", data["risk"])
        self.assertNotIn("frozen_reason", data["risk"])

    def test_unfreeze_audit_logged_with_admin_id(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=True)
        self.client.post(f"/api/admin/users/{self.user_id}/unfreeze",
                          headers=_bearer(self.admin_token))
        conn = sqlite3.connect(self._db_path)
        rows = [(r[0], r[1]) for r in conn.execute(
            "SELECT event_type, detail FROM broker_key_events WHERE user_id=?",
            (self.user_id,),
        )]
        conn.close()
        unfreeze = [(t, d) for t, d in rows if t == "account_unfrozen_by_admin"]
        self.assertEqual(len(unfreeze), 1)
        self.assertIn(f"admin_id={self.admin_id}", unfreeze[0][1])

    def test_user_cannot_call_admin_unfreeze(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=True)
        r = self.client.post(f"/api/admin/users/{self.user_id}/unfreeze",
                              headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 403)

    def test_unfreeze_no_config_returns_ok_false(self):
        # User exists in DB but has no provisioned workspace
        r = self.client.post(f"/api/admin/users/{self.user_id}/unfreeze",
                              headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])

    def test_unfreeze_not_frozen_returns_ok_true_idempotent(self):
        _seed_config(self._user_data_dir, self.user_id, frozen=False)
        r = self.client.post(f"/api/admin/users/{self.user_id}/unfreeze",
                              headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_unfreeze_unknown_user_returns_404(self):
        r = self.client.post("/api/admin/users/9999/unfreeze",
                              headers=_bearer(self.admin_token))
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
