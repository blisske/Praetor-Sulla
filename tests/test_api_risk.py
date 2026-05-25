"""Integration tests for api/risk.py (user-settable risk caps).

Verifies GET returns current/ceilings/floors, POST validates against
ceilings AND floors, partial updates work, restart flag is touched,
and audit log records the change.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_api_risk -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_test_env(db_path: str, user_data_dir: str, template_path: str) -> None:
    os.environ["GLOBAL_DB_PATH"] = db_path
    os.environ["API_SECRET_KEY"] = "test-api-secret-" + "x" * 50
    os.environ["JWT_EXPIRY_DAYS"] = "7"
    os.environ["USER_DATA_DIR"] = user_data_dir
    os.environ["POSTMARK_SERVER_TOKEN"] = "test-token"
    os.environ["USER_TEMPLATE_CONFIG"] = template_path

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import init_global_db
    init_global_db.init_global_db(db_path, verbose=False)

    from shared import auth as core_auth
    core_auth.GLOBAL_DB_PATH = db_path
    core_auth.JWT_SECRET_KEY = os.environ["API_SECRET_KEY"]
    core_auth.JWT_EXPIRY_DAYS = 7

    from api import mode as mode_mod
    mode_mod.USER_DATA_DIR = user_data_dir

    from api import risk as risk_mod
    risk_mod.TEMPLATE_CONFIG_PATH = Path(template_path)


def _truncate_all(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for t in ["users", "email_verifications", "password_resets",
              "broker_keys", "broker_key_events", "login_attempts", "market_states"]:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()


def _seed_user(email="alice@x.com"):
    from shared import auth as core_auth
    user_id = core_auth.create_user(email=email, password="longenoughpassword", is_admin=False)
    token = core_auth.create_jwt(user_id=user_id, email=email, email_verified=True, is_admin=False)
    return user_id, token


def _write_template(template_path: str, *,
                    risk_per_trade_pct=1.5,
                    position_size_max_pct=5.0,
                    max_open_trades=5,
                    drawdown_halt_pct=25.0,
                    daily_halt_pct=20.0):
    """Write a minimal operator template Config.yaml that supplies all
    five ceiling values risk.py reads from. Caller can override any one
    of them to simulate a different operator policy."""
    Path(template_path).parent.mkdir(parents=True, exist_ok=True)
    Path(template_path).write_text(textwrap.dedent(f"""\
        risk:
          risk_per_trade_pct: {risk_per_trade_pct}
          position_size_max_pct: {position_size_max_pct}
          drawdown_halt_pct: {drawdown_halt_pct}
          daily_halt_pct: {daily_halt_pct}
        strategy:
          max_open_trades: {max_open_trades}
        """))


def _seed_user_config(user_data_dir, user_id, *,
                      risk_per_trade_pct=None,
                      position_size_max_pct=None,
                      max_open_trades=None,
                      drawdown_halt_pct=None,
                      daily_halt_pct=None):
    """Materialize a user's Config.yaml. Any None values are omitted, so
    the user inherits the template ceiling for those fields."""
    user_dir = Path(user_data_dir) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    risk_lines = ["risk:", "  initial_capital: 10000"]
    if risk_per_trade_pct is not None:
        risk_lines.append(f"  risk_per_trade_pct: {risk_per_trade_pct}")
    if position_size_max_pct is not None:
        risk_lines.append(f"  position_size_max_pct: {position_size_max_pct}")
    if drawdown_halt_pct is not None:
        risk_lines.append(f"  drawdown_halt_pct: {drawdown_halt_pct}")
    if daily_halt_pct is not None:
        risk_lines.append(f"  daily_halt_pct: {daily_halt_pct}")
    strat_lines = ["strategy:"]
    if max_open_trades is not None:
        strat_lines.append(f"  max_open_trades: {max_open_trades}")
    else:
        strat_lines.append("  active_symbols: [BTC]")
    cfg = user_dir / "Config.yaml"
    cfg.write_text("\n".join(risk_lines + strat_lines) + "\n")
    return cfg


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class RiskCapsTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_risk_test_")
        cls._db_path = os.path.join(cls._tmpdir, "g.db")
        cls._user_data_dir = os.path.join(cls._tmpdir, "users")
        cls._template_path = os.path.join(cls._tmpdir, "template", "Config.yaml")
        os.makedirs(cls._user_data_dir, exist_ok=True)
        cls._saved_env = dict(os.environ)
        _write_template(cls._template_path)
        _setup_test_env(cls._db_path, cls._user_data_dir, cls._template_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.risk import router as risk_router
        app = FastAPI()
        app.include_router(risk_router)
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
        for entry in Path(self._user_data_dir).iterdir():
            if entry.is_dir():
                import shutil
                shutil.rmtree(entry)
        # Reset template to defaults each test so ceilings don't bleed
        _write_template(self._template_path)
        self.user_id, self.user_token = _seed_user("alice@x.com")


# ─── GET /api/user/risk-caps ───────────────────────────────────────────────


class GetRiskCapsTests(RiskCapsTestBase):

    def test_returns_template_ceilings(self):
        r = self.client.get("/api/user/risk-caps", headers=_bearer(self.user_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ceilings = body["ceilings"]
        self.assertEqual(ceilings["risk_per_trade_pct"], 1.5)
        self.assertEqual(ceilings["position_size_max_pct"], 5.0)
        self.assertEqual(ceilings["max_open_trades"], 5)
        self.assertEqual(ceilings["drawdown_halt_pct"], 25.0)
        self.assertEqual(ceilings["daily_halt_pct"], 20.0)

    def test_returns_hard_floors(self):
        r = self.client.get("/api/user/risk-caps", headers=_bearer(self.user_token))
        floors = r.json()["floors"]
        self.assertEqual(floors["risk_per_trade_pct"], 0.1)
        self.assertEqual(floors["position_size_max_pct"], 0.5)
        self.assertEqual(floors["max_open_trades"], 1)
        self.assertEqual(floors["drawdown_halt_pct"], 3.0)
        self.assertEqual(floors["daily_halt_pct"], 3.0)

    def test_no_user_config_present_false(self):
        r = self.client.get("/api/user/risk-caps", headers=_bearer(self.user_token))
        body = r.json()
        self.assertFalse(body["config_present"])
        # Without config, current echoes ceilings (preview before provisioning)
        self.assertEqual(body["current"], body["ceilings"])

    def test_user_config_present_returns_current_values(self):
        _seed_user_config(self._user_data_dir, self.user_id,
                          risk_per_trade_pct=0.8, max_open_trades=3)
        r = self.client.get("/api/user/risk-caps", headers=_bearer(self.user_token))
        body = r.json()
        self.assertTrue(body["config_present"])
        self.assertEqual(body["current"]["risk_per_trade_pct"], 0.8)
        self.assertEqual(body["current"]["max_open_trades"], 3)
        # Fields the user hasn't tightened inherit the ceiling
        self.assertEqual(body["current"]["position_size_max_pct"], 5.0)

    def test_unauth_returns_401(self):
        r = self.client.get("/api/user/risk-caps")
        self.assertEqual(r.status_code, 401)

    def test_missing_template_falls_back_to_module_defaults(self):
        # Operator template doesn't exist → conservative FALLBACK_CEILINGS
        Path(self._template_path).unlink()
        r = self.client.get("/api/user/risk-caps", headers=_bearer(self.user_token))
        body = r.json()
        # FALLBACK_CEILINGS matches the conservative defaults
        self.assertEqual(body["ceilings"]["risk_per_trade_pct"], 1.5)
        self.assertEqual(body["ceilings"]["max_open_trades"], 5)


# ─── POST /api/user/risk-caps ──────────────────────────────────────────────


class UpdateRiskCapsTests(RiskCapsTestBase):

    def test_tighten_all_fields(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        r = self.client.post(
            "/api/user/risk-caps",
            json={
                "risk_per_trade_pct": 0.75,
                "position_size_max_pct": 2.5,
                "max_open_trades": 3,
                "drawdown_halt_pct": 12.0,
                "daily_halt_pct": 8.0,
            },
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["updated"]), 5)

        # Verify written to YAML
        from ruamel.yaml import YAML
        cfg_path = Path(self._user_data_dir) / str(self.user_id) / "Config.yaml"
        with open(cfg_path) as f:
            data = YAML().load(f)
        self.assertEqual(data["risk"]["risk_per_trade_pct"], 0.75)
        self.assertEqual(data["risk"]["position_size_max_pct"], 2.5)
        self.assertEqual(data["strategy"]["max_open_trades"], 3)
        self.assertEqual(data["risk"]["drawdown_halt_pct"], 12.0)
        self.assertEqual(data["risk"]["daily_halt_pct"], 8.0)

    def test_partial_update_only_touches_named_fields(self):
        _seed_user_config(self._user_data_dir, self.user_id,
                          risk_per_trade_pct=1.2, position_size_max_pct=4.0)
        r = self.client.post(
            "/api/user/risk-caps",
            json={"max_open_trades": 2},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 200)

        from ruamel.yaml import YAML
        cfg_path = Path(self._user_data_dir) / str(self.user_id) / "Config.yaml"
        with open(cfg_path) as f:
            data = YAML().load(f)
        # Untouched
        self.assertEqual(data["risk"]["risk_per_trade_pct"], 1.2)
        self.assertEqual(data["risk"]["position_size_max_pct"], 4.0)
        # Updated
        self.assertEqual(data["strategy"]["max_open_trades"], 2)

    def test_rejects_value_above_ceiling(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        # Template ceiling for risk_per_trade_pct is 1.5
        r = self.client.post(
            "/api/user/risk-caps",
            json={"risk_per_trade_pct": 2.5},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("exceeds the maximum allowed", r.json()["detail"])

    def test_rejects_value_below_floor(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        # Floor for risk_per_trade_pct is 0.1
        r = self.client.post(
            "/api/user/risk-caps",
            json={"risk_per_trade_pct": 0.05},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("hard floor", r.json()["detail"])

    def test_rejects_at_ceiling_exact_is_allowed(self):
        # Setting equal to ceiling is OK — only strictly above is rejected
        _seed_user_config(self._user_data_dir, self.user_id)
        r = self.client.post(
            "/api/user/risk-caps",
            json={"risk_per_trade_pct": 1.5},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 200)

    def test_empty_body_rejected(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        r = self.client.post(
            "/api/user/risk-caps",
            json={},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("At least one", r.json()["detail"])

    def test_no_config_returns_503(self):
        # User not yet provisioned (no Config.yaml)
        r = self.client.post(
            "/api/user/risk-caps",
            json={"risk_per_trade_pct": 0.5},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 503)

    def test_touches_restart_flag(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        self.client.post(
            "/api/user/risk-caps",
            json={"max_open_trades": 2},
            headers=_bearer(self.user_token),
        )
        flag = Path(self._user_data_dir) / str(self.user_id) / ".restart_engine"
        self.assertTrue(flag.exists())

    def test_writes_backup_yaml(self):
        cfg = _seed_user_config(self._user_data_dir, self.user_id)
        self.client.post(
            "/api/user/risk-caps",
            json={"max_open_trades": 2},
            headers=_bearer(self.user_token),
        )
        bak = cfg.with_suffix(".yaml.bak")
        self.assertTrue(bak.exists())

    def test_audit_log_records_update(self):
        _seed_user_config(self._user_data_dir, self.user_id)
        self.client.post(
            "/api/user/risk-caps",
            json={"risk_per_trade_pct": 0.8, "max_open_trades": 2},
            headers=_bearer(self.user_token),
        )
        conn = sqlite3.connect(self._db_path)
        rows = [(r[0], r[1]) for r in conn.execute(
            "SELECT event_type, detail FROM broker_key_events WHERE user_id=?",
            (self.user_id,),
        )]
        conn.close()
        events = [t for t, _ in rows]
        self.assertIn("risk_caps_updated", events)
        detail = next(d for t, d in rows if t == "risk_caps_updated")
        self.assertIn("risk_per_trade_pct=0.8", detail)
        self.assertIn("max_open_trades=2", detail)

    def test_ceiling_below_fallback_uses_template_value(self):
        # Operator sets a STRICTER ceiling than fallback — user can't
        # exceed the stricter one
        _write_template(self._template_path, max_open_trades=3)
        # Reload the template path (already pointed at same file, just contents changed)
        _seed_user_config(self._user_data_dir, self.user_id)
        # Try to set to fallback ceiling (5) — should fail because operator's ceiling is 3
        r = self.client.post(
            "/api/user/risk-caps",
            json={"max_open_trades": 5},
            headers=_bearer(self.user_token),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("3", r.json()["detail"])

    def test_partial_update_preserves_existing_strategy_keys(self):
        # Make sure writing max_open_trades doesn't blow away other strategy
        # keys (active_symbols, paradigms, etc.)
        user_dir = Path(self._user_data_dir) / str(self.user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "Config.yaml").write_text(textwrap.dedent("""\
            risk:
              initial_capital: 10000
            strategy:
              active_symbols: [BTC, ETH, SOL]
              max_open_trades: 5
              min_consensus: 3
            """))
        self.client.post(
            "/api/user/risk-caps",
            json={"max_open_trades": 2},
            headers=_bearer(self.user_token),
        )
        from ruamel.yaml import YAML
        with open(user_dir / "Config.yaml") as f:
            data = YAML().load(f)
        self.assertEqual(data["strategy"]["max_open_trades"], 2)
        self.assertEqual(list(data["strategy"]["active_symbols"]), ["BTC", "ETH", "SOL"])
        self.assertEqual(data["strategy"]["min_consensus"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
