"""Unit tests for core.daily_risk — daily-drawdown circuit breaker.

Pure-logic tests: the DB module is mocked via a fake-DB object, the
"today" function is parameterized. No SQLite needed.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_daily_risk -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import daily_risk


class FakeDB:
    """Tiny in-memory replacement for core.database for evaluator tests."""

    def __init__(self):
        self.baseline_eq:   float | None = None
        self.baseline_date: str   | None = None
        self.set_calls = []  # for assertions about when set was called

    def get_daily_baseline(self):
        return (self.baseline_eq, self.baseline_date)

    def set_daily_baseline(self, equity, date_str):
        self.baseline_eq = equity
        self.baseline_date = date_str
        self.set_calls.append((equity, date_str))


def _today_fn(date_str):
    """Returns a fn that always reports `date_str` as today's date."""
    return lambda tz_name="America/Denver": date_str


def _cfg(alert=5.0, derisk=10.0, halt=20.0):
    return {"risk": {
        "daily_alert_pct":  alert,
        "daily_derisk_pct": derisk,
        "daily_halt_pct":   halt,
    }}


# ─── First-call / baseline-fresh behavior ─────────────────────────────────


class FirstCallTests(unittest.TestCase):

    def test_first_call_snapshots_baseline(self):
        db = FakeDB()
        s = daily_risk.evaluate(
            current_equity=10000.0,
            config=_cfg(),
            db=db,
            today_str_fn=_today_fn("2026-05-22"),
        )
        self.assertEqual(s.baseline_equity, 10000.0)
        self.assertEqual(s.drawdown_pct, 0.0)
        self.assertTrue(s.baseline_was_fresh)
        # action='none' on fresh baseline regardless of thresholds
        self.assertEqual(s.action, "none")
        # DB was written
        self.assertEqual(db.set_calls, [(10000.0, "2026-05-22")])

    def test_first_call_with_low_equity_still_fresh(self):
        # Even if current equity is very low, first call snapshots it
        db = FakeDB()
        s = daily_risk.evaluate(current_equity=100.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertTrue(s.baseline_was_fresh)
        self.assertEqual(s.action, "none")


# ─── Same-day evaluation (baseline persists) ───────────────────────────────


class SameDayTests(unittest.TestCase):

    def _setup_baseline(self, baseline=10000.0, date="2026-05-22"):
        db = FakeDB()
        db.baseline_eq = baseline
        db.baseline_date = date
        return db

    def test_no_drawdown_returns_none(self):
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=10000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "none")
        self.assertEqual(s.drawdown_pct, 0.0)
        self.assertFalse(s.baseline_was_fresh)
        self.assertEqual(db.set_calls, [])  # baseline NOT rewritten

    def test_equity_went_up_returns_none(self):
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=10500.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "none")
        self.assertEqual(s.drawdown_pct, 0.0)

    def test_alert_threshold(self):
        # 5% drawdown — at the alert threshold (5.0)
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=9500.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "alert")
        self.assertAlmostEqual(s.drawdown_pct, 5.0)

    def test_below_alert_returns_none(self):
        # 4.9% drawdown — just under alert
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=9510.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "none")

    def test_derisk_threshold(self):
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=9000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "derisk")
        self.assertAlmostEqual(s.drawdown_pct, 10.0)

    def test_halt_threshold(self):
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=8000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "halt")
        self.assertAlmostEqual(s.drawdown_pct, 20.0)

    def test_extreme_loss_still_halts(self):
        # 90% loss — way past halt
        db = self._setup_baseline()
        s = daily_risk.evaluate(current_equity=1000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "halt")
        self.assertAlmostEqual(s.drawdown_pct, 90.0)


# ─── Date rollover (midnight reset) ────────────────────────────────────────


class DateRolloverTests(unittest.TestCase):

    def test_new_day_resets_baseline(self):
        # Yesterday: baseline 10000, now 8000 (would have been halt)
        # Today: rolls over, new baseline 8000, drawdown 0%
        db = FakeDB()
        db.baseline_eq = 10000.0
        db.baseline_date = "2026-05-22"
        s = daily_risk.evaluate(current_equity=8000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-23"))
        self.assertTrue(s.baseline_was_fresh)
        self.assertEqual(s.baseline_equity, 8000.0)
        self.assertEqual(s.drawdown_pct, 0.0)
        self.assertEqual(s.action, "none")
        # DB was rewritten with the new baseline
        self.assertEqual(db.set_calls, [(8000.0, "2026-05-23")])

    def test_rollover_clears_yesterday_halt(self):
        """After midnight, yesterday's halt state goes away — operator
        can start fresh (engine wouldn't have to call /resume manually
        on date change since the daily-risk component returns 'none')."""
        db = FakeDB()
        db.baseline_eq = 10000.0
        db.baseline_date = "2026-05-22"
        s = daily_risk.evaluate(current_equity=7000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-23"))
        self.assertEqual(s.action, "none")


# ─── Config overrides ──────────────────────────────────────────────────────


class ConfigOverrideTests(unittest.TestCase):

    def test_custom_thresholds_respected(self):
        # User sets tighter caps: alert 2%, derisk 5%, halt 10%
        db = FakeDB()
        db.baseline_eq = 10000.0
        db.baseline_date = "2026-05-22"
        cfg = _cfg(alert=2.0, derisk=5.0, halt=10.0)
        # 3% drawdown — alert (would be 'none' with defaults)
        s = daily_risk.evaluate(current_equity=9700.0, config=cfg,
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.action, "alert")

    def test_missing_config_uses_defaults(self):
        # Empty config dict — evaluator uses 5/10/20 defaults
        db = FakeDB()
        db.baseline_eq = 10000.0
        db.baseline_date = "2026-05-22"
        s = daily_risk.evaluate(current_equity=8500.0, config={},
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        # 15% drawdown — between derisk (10) + halt (20) → derisk
        self.assertEqual(s.action, "derisk")
        self.assertEqual(s.derisk_pct, 10.0)
        self.assertEqual(s.halt_pct, 20.0)


# ─── Defensive paths ───────────────────────────────────────────────────────


class DefensivePathTests(unittest.TestCase):

    def test_zero_baseline_no_crash(self):
        db = FakeDB()
        db.baseline_eq = 0.0
        db.baseline_date = "2026-05-22"
        s = daily_risk.evaluate(current_equity=0.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        # No division by zero; treated as zero drawdown
        self.assertEqual(s.drawdown_pct, 0.0)
        self.assertEqual(s.action, "none")

    def test_negative_equity_clamps_drawdown(self):
        # current > baseline (gained money) → drawdown clamped to 0
        db = FakeDB()
        db.baseline_eq = 10000.0
        db.baseline_date = "2026-05-22"
        s = daily_risk.evaluate(current_equity=11000.0, config=_cfg(),
                                 db=db, today_str_fn=_today_fn("2026-05-22"))
        self.assertEqual(s.drawdown_pct, 0.0)
        self.assertEqual(s.action, "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
