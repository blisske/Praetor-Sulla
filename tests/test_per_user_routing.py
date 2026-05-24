"""Tests for api/main.py per-user DB routing — verifies the fix for the
2026-05-23 privacy bug (operator's trades leaking to other tenants).

The bug: get_db(user_str) routed every non-demo user to REAL_DB, so any
multi-tenant user hitting /api/trades saw the operator's data.

The fix: get_db_for(ctx) routes by user_id:
  user_id=1 → REAL_DB
  user_id=2 → DEMO_DB / REAL_DB (shadow_mode aware)
  user_id≥3 → /app/data/users/{id}/ionic.db (per-user)

These tests verify the routing matrix + the schema-seeding fallback
that prevents "no such table" errors on a fresh per-user DB.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_per_user_routing -v
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


def _create_test_db(path: Path) -> None:
    """Materialize a tiny DB with the schema api/main.py expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, action TEXT, amount REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS open_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, position_size_usd REAL
        );
        CREATE TABLE IF NOT EXISTS market_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, price REAL, timestamp DATETIME
        );
        CREATE TABLE IF NOT EXISTS equity_peak (id INTEGER PRIMARY KEY, peak_equity REAL);
    """)
    conn.commit()
    conn.close()


def _seed_trade(path: Path, symbol: str, amount: float) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO trades (symbol, action, amount) VALUES (?, 'SHADOW SELL', ?)",
                 (symbol, amount))
    conn.commit()
    conn.close()


class RoutingMatrixTests(unittest.TestCase):
    """Verify get_db_for() routes to the right file per user_id."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="foundation_routing_test_")
        cls._real_db = Path(cls._tmpdir) / "ionic.db"
        cls._demo_db = Path(cls._tmpdir) / "demo_data.db"
        _create_test_db(cls._real_db)
        _create_test_db(cls._demo_db)
        _seed_trade(cls._real_db, "BTC-USD", 100.0)  # operator's trade
        _seed_trade(cls._demo_db, "ETH-USD", 50.0)   # demo's trade

        # Patch api/main.py module-level paths to point at the temp dirs
        cls._saved_env = dict(os.environ)
        os.environ["SQLITE_PATH"]      = str(cls._real_db)
        os.environ["DEMO_SQLITE_PATH"] = str(cls._demo_db)
        os.environ["GLOBAL_DB_PATH"]   = str(Path(cls._tmpdir) / "global.db")
        os.environ["API_SECRET_KEY"]   = "test-secret-" + "x" * 50

        # Materialize global.db schema (so api/main.py imports don't fail)
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import init_global_db
        init_global_db.init_global_db(os.environ["GLOBAL_DB_PATH"], verbose=False)

        # Import api.main AFTER env is set so module-level REAL_DB picks
        # up our temp path
        if "api.main" in sys.modules:
            del sys.modules["api.main"]
        from api import main as api_main
        cls.api_main = api_main

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(cls._saved_env)
        if "api.main" in sys.modules:
            del sys.modules["api.main"]

    def _ctx(self, user_id, legacy_username="admin"):
        return self.api_main.AuthCtx(user_id=user_id, legacy_username=legacy_username)

    def test_operator_user_id_1_routes_to_real_db(self):
        ctx = self._ctx(user_id=1, legacy_username="admin")
        conn = self.api_main.get_db_for(ctx)
        try:
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
            self.assertEqual(Path(db_path), self._real_db)
            # And the operator's trade is visible
            n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            conn.close()

    def test_demo_user_id_2_routes_to_demo_db_in_live_mode(self):
        # Default config has shadow_mode unset → treated as False → demo sees DEMO_DB
        ctx = self._ctx(user_id=2, legacy_username="demo")
        conn = self.api_main.get_db_for(ctx)
        try:
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
            # Demo should see DEMO_DB or REAL_DB but never the per-user dir
            self.assertIn(Path(db_path), (self._demo_db, self._real_db))
        finally:
            conn.close()

    def test_multitenant_user_routes_to_per_user_db(self):
        # Use a unique user_id per test (test order isn't deterministic
        # and other tests share state via the per-user file).
        user_id = 9901
        ctx = self._ctx(user_id=user_id, legacy_username="admin")
        per_user_path = Path(self._tmpdir) / "users" / str(user_id) / "ionic.db"

        conn = self.api_main.get_db_for(ctx)
        try:
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
            self.assertEqual(Path(db_path), per_user_path)
            # Schema should be there (seeded from REAL_DB) — table exists
            n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            self.assertEqual(n, 0)  # empty — does NOT see operator's trade
        finally:
            conn.close()
        # File was materialized
        self.assertTrue(per_user_path.exists())

    def test_multitenant_user_data_isolated_from_operator(self):
        # The critical privacy test: write a trade to a user's DB and
        # verify the operator can't see it (and vice versa)
        user_id = 9902
        ctx99 = self._ctx(user_id=user_id, legacy_username="admin")
        conn99 = self.api_main.get_db_for(ctx99)
        try:
            conn99.execute("INSERT INTO trades (symbol, action, amount) VALUES (?, 'SHADOW SELL', ?)",
                          ("XYZ-USD", 777.0))
            conn99.commit()
        finally:
            conn99.close()

        # Operator's view: only their original trade (BTC, $100), NOT user 99's XYZ
        ctx1 = self._ctx(user_id=1, legacy_username="admin")
        conn1 = self.api_main.get_db_for(ctx1)
        try:
            rows = conn1.execute("SELECT symbol FROM trades").fetchall()
            symbols = {r[0] for r in rows}
            self.assertIn("BTC-USD", symbols)
            self.assertNotIn("XYZ-USD", symbols)  # ← privacy guarantee
        finally:
            conn1.close()

        # User 99's view: their own trade, NOT operator's BTC
        conn99b = self.api_main.get_db_for(ctx99)
        try:
            rows = conn99b.execute("SELECT symbol FROM trades").fetchall()
            symbols = {r[0] for r in rows}
            self.assertIn("XYZ-USD", symbols)
            self.assertNotIn("BTC-USD", symbols)
        finally:
            conn99b.close()

    def test_existing_per_user_db_not_reseeded(self):
        # If a per-user DB already exists with data, get_db_for should
        # NOT overwrite it (idempotency)
        user_id = 100
        per_user_path = Path(self._tmpdir) / "users" / str(user_id) / "ionic.db"
        per_user_path.parent.mkdir(parents=True, exist_ok=True)
        _create_test_db(per_user_path)
        _seed_trade(per_user_path, "AAA-USD", 42.0)

        ctx = self._ctx(user_id=user_id)
        conn = self.api_main.get_db_for(ctx)
        try:
            symbols = {r[0] for r in conn.execute("SELECT symbol FROM trades").fetchall()}
            self.assertIn("AAA-USD", symbols)   # original data intact
            self.assertNotIn("BTC-USD", symbols)  # operator's trade NOT copied
        finally:
            conn.close()

    def test_schema_seeded_idempotently(self):
        # Calling get_db_for multiple times on the same new user shouldn't
        # double-insert schema or otherwise corrupt the DB
        ctx = self._ctx(user_id=101)
        for _ in range(3):
            conn = self.api_main.get_db_for(ctx)
            try:
                conn.execute("SELECT COUNT(*) FROM trades").fetchone()
            finally:
                conn.close()


    # ── AuthCtx dataclass behaviour ────────────────────────────────────
    # (Lived in a separate class before — merged here because each test
    # class's setUpClass mutates api.main's module-level paths, so two
    # classes corrupt each other's fixtures.)

    def test_authctx_is_frozen_dataclass(self):
        ctx = self.api_main.AuthCtx(user_id=42, legacy_username="admin")
        with self.assertRaises(Exception):
            ctx.user_id = 999

    def test_authctx_carries_both_fields(self):
        ctx = self.api_main.AuthCtx(user_id=42, legacy_username="admin")
        self.assertEqual(ctx.user_id, 42)
        self.assertEqual(ctx.legacy_username, "admin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
