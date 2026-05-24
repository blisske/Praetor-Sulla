"""Tests for Ionic core/tax.py — FX §988 ordinary-income flavor.

Builds a temp SQLite DB with the same shape as the per-user ionic.db,
seeds it with hand-picked trade sequences, then verifies compute_disposals
+ summarize emit the right numbers under each lot-matching method.

§988 differences from the Corinthian/Doric (capital-gains) test suite:
  - No HoldingPeriodBoundaryTests class — there is no boundary; every
    disposal is ordinary income regardless of holding duration.
  - Disposal.term is always 'ordinary' instead of 'short' | 'long'.
  - summarize() returns total_ordinary_gain + ordinary_count instead of
    short_term_gain / long_term_gain / short_count / long_count.

Edge cases covered:
  - FIFO/LIFO/HIFO basic ordering on a 3-lot ladder
  - All disposals get term='ordinary' regardless of holding period
  - Partial-lot consumption (sell less than one lot — leaves remainder)
  - Lot-spanning sell (one SELL consumes multiple BUY lots)
  - Year scoping (cross-year sells emit only for the requested year)
  - Empty year (returns [], summary aggregates to zero)
  - Shadow filter (default excludes SHADOW BUY/SELL, opt-in includes)
  - Multi-symbol independence (EUR lots can't be matched against GBP sells)
  - Fee allocation (buy + sell fees attributed proportionally per lot share)
  - has_live_trades + available_years helpers
  - Unknown method raises ValueError
  - RATCHET / zero-qty rows are skipped (non-positional)
  - Sell with no prior buy is silently dropped (no junk records)
  - Float dust at lot boundary (within 1e-12) doesn't leak
  - summarize() reports total_ordinary_gain == realized_gain

Run with:
    docker exec -w /app ionic-api python3 -m unittest tests.test_tax -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import tax as tax_module
from core.tax import Disposal, compute_disposals, summarize, has_live_trades, available_years


# ─── Test-DB construction helpers ─────────────────────────────────────────


def _make_db(path: Path) -> None:
    """Materialize a trades table matching core/database.py's schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            symbol    TEXT,
            action    TEXT,
            price     REAL,
            amount    REAL,
            strategy  TEXT,
            verdict   TEXT,
            position_size_usd REAL DEFAULT 0.0,
            fee_usd   REAL DEFAULT 0.0
        )
    """)
    conn.commit()
    conn.close()


def _add(path: Path, *, ts: str, symbol: str, action: str,
         price: float, amount: float, fee_usd: float = 0.0) -> None:
    """Insert a synthetic trade row at a controllable timestamp.

    ts is a 'YYYY-MM-DD HH:MM:SS' string (SQLite CURRENT_TIMESTAMP shape).
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, action, price, amount, fee_usd) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, symbol, action, price, amount, fee_usd),
    )
    conn.commit()
    conn.close()


def _ts(year: int, month: int = 1, day: int = 1, hour: int = 12) -> str:
    """Format a SQLite-style UTC timestamp."""
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ─── Tests ────────────────────────────────────────────────────────────────


class LotMethodTests(unittest.TestCase):
    """FIFO vs LIFO vs HIFO on a hand-picked 3-lot ladder."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)
        # 3 BTC lots @ $10K, $20K, $15K (in that chronological order)
        _add(self.path, ts=_ts(2026, 1, 1),  symbol="EUR/USD", action="BUY", price=10000, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1),  symbol="EUR/USD", action="BUY", price=20000, amount=1.0)
        _add(self.path, ts=_ts(2026, 3, 1),  symbol="EUR/USD", action="BUY", price=15000, amount=1.0)
        # Sell 1 BTC @ $25K on 2026-04-01
        _add(self.path, ts=_ts(2026, 4, 1),  symbol="EUR/USD", action="SELL", price=25000, amount=1.0)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_fifo_uses_oldest_lot(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        # FIFO → matches the $10K lot → $15K gain
        self.assertEqual(d[0].cost_basis_usd, 10000.0)
        self.assertEqual(d[0].gain_loss_usd,  15000.0)
        self.assertEqual(d[0].term, "ordinary")   # §988 — no short/long split

    def test_lifo_uses_newest_lot(self):
        d = compute_disposals(str(self.path), year=2026, method="LIFO")
        self.assertEqual(len(d), 1)
        # LIFO → matches the $15K (most recent) lot → $10K gain
        self.assertEqual(d[0].cost_basis_usd, 15000.0)
        self.assertEqual(d[0].gain_loss_usd,  10000.0)

    def test_hifo_uses_highest_basis_lot(self):
        d = compute_disposals(str(self.path), year=2026, method="HIFO")
        self.assertEqual(len(d), 1)
        # HIFO → matches the $20K (highest cost) lot → $5K gain (smallest!)
        self.assertEqual(d[0].cost_basis_usd, 20000.0)
        self.assertEqual(d[0].gain_loss_usd,   5000.0)

    def test_method_is_case_insensitive(self):
        d_lower = compute_disposals(str(self.path), year=2026, method="fifo")
        d_upper = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(d_lower, d_upper)

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            compute_disposals(str(self.path), year=2026, method="WIFO")


class Section988OrdinaryTermTests(unittest.TestCase):
    """Under §988, every disposal is 'ordinary' regardless of holding period.

    This is the §988-flavor replacement for the capital-gains
    HoldingPeriodBoundaryTests class in Corinthian + Doric. The
    `holding_days` field is still computed (informational), but the
    `term` field is always 'ordinary'.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_short_hold_is_ordinary(self):
        # 1-day hold
        _add(self.path, ts=_ts(2025, 1, 1), symbol="EUR/USD", action="BUY",  price=1.10, amount=10000)
        _add(self.path, ts=_ts(2025, 1, 2), symbol="EUR/USD", action="SELL", price=1.11, amount=10000)
        d = compute_disposals(str(self.path), year=2025, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0].holding_days, 1)
        self.assertEqual(d[0].term, "ordinary")

    def test_long_hold_is_still_ordinary(self):
        # 2+ year hold — would be "long" under capital-gains; ordinary under §988
        _add(self.path, ts=_ts(2024, 1, 1), symbol="EUR/USD", action="BUY",  price=1.10, amount=10000)
        _add(self.path, ts=_ts(2026, 6, 1), symbol="EUR/USD", action="SELL", price=1.15, amount=10000)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertGreater(d[0].holding_days, 500)  # ~880 days, informational only
        self.assertEqual(d[0].term, "ordinary")

    def test_zero_day_hold_is_ordinary(self):
        # Same-day round-trip (scalp)
        _add(self.path, ts=_ts(2025, 1, 1, hour=9),  symbol="GBP/USD", action="BUY",  price=1.25, amount=10000)
        _add(self.path, ts=_ts(2025, 1, 1, hour=15), symbol="GBP/USD", action="SELL", price=1.26, amount=10000)
        d = compute_disposals(str(self.path), year=2025, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0].holding_days, 0)
        self.assertEqual(d[0].term, "ordinary")


class PartialAndSpanningLotTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_partial_lot_consumes_only_portion(self):
        """Sell 0.4 of a 1.0 lot → one disposal with qty=0.4, lot has 0.6 remaining."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="BUY",  price=100, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="SELL", price=200, amount=0.4)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertAlmostEqual(d[0].qty,            0.4,   places=10)
        self.assertAlmostEqual(d[0].proceeds_usd,   80.0,  places=4)  # 0.4 * 200
        self.assertAlmostEqual(d[0].cost_basis_usd, 40.0,  places=4)  # 0.4 * 100
        self.assertAlmostEqual(d[0].gain_loss_usd,  40.0,  places=4)

    def test_sell_spans_multiple_lots(self):
        """Sell 1.5 across two 1.0 lots → 2 disposals (1.0 + 0.5)."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="BUY",  price=100, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="BUY",  price=120, amount=1.0)
        _add(self.path, ts=_ts(2026, 3, 1), symbol="X", action="SELL", price=200, amount=1.5)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 2)
        # FIFO: first disposal consumes 1.0 of the $100 lot
        self.assertAlmostEqual(d[0].qty,            1.0)
        self.assertAlmostEqual(d[0].cost_basis_usd, 100.0)
        self.assertAlmostEqual(d[0].gain_loss_usd,  100.0)
        # Second disposal consumes 0.5 of the $120 lot
        self.assertAlmostEqual(d[1].qty,            0.5)
        self.assertAlmostEqual(d[1].cost_basis_usd, 60.0)
        self.assertAlmostEqual(d[1].gain_loss_usd,  40.0)

    def test_oversell_drops_residual_silently(self):
        """Selling more than inventory has → only what we can match emits."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="BUY",  price=100, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="SELL", price=200, amount=3.0)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertAlmostEqual(d[0].qty, 1.0)


class FeeAllocationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_buy_and_sell_fees_attributed_per_share(self):
        """Buy 2 @ $100 with $4 fee → $52/unit basis.
           Sell 1 @ $150 with $3 fee → $147 proceeds, $52 basis, $95 G/L,
           fees_usd = $2 (half of buy fee) + $3 (full sell fee) = $5."""
        _add(self.path, ts=_ts(2026, 1, 1),
             symbol="X", action="BUY", price=100, amount=2.0, fee_usd=4.0)
        _add(self.path, ts=_ts(2026, 2, 1),
             symbol="X", action="SELL", price=150, amount=1.0, fee_usd=3.0)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertAlmostEqual(d[0].proceeds_usd,   147.0,  places=4)  # 150 - 3
        self.assertAlmostEqual(d[0].cost_basis_usd, 102.0,  places=4)  # 100 + 2 fee
        self.assertAlmostEqual(d[0].gain_loss_usd,   45.0,  places=4)  # 147 - 102
        self.assertAlmostEqual(d[0].fees_usd,         5.0,  places=4)


class YearScopingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)
        # Lot acquired 2024, half sold in 2025, half in 2026
        _add(self.path, ts=_ts(2024, 6, 1),  symbol="X", action="BUY",  price=100, amount=2.0)
        _add(self.path, ts=_ts(2025, 6, 1),  symbol="X", action="SELL", price=150, amount=1.0)
        _add(self.path, ts=_ts(2026, 6, 1),  symbol="X", action="SELL", price=200, amount=1.0)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_only_sells_in_requested_year_emit(self):
        d2025 = compute_disposals(str(self.path), year=2025, method="FIFO")
        d2026 = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d2025), 1)
        self.assertEqual(len(d2026), 1)
        self.assertAlmostEqual(d2025[0].gain_loss_usd, 50.0)
        self.assertAlmostEqual(d2026[0].gain_loss_usd, 100.0)
        # Under §988, holding period is irrelevant — both are ordinary.
        self.assertEqual(d2025[0].term, "ordinary")
        self.assertEqual(d2026[0].term, "ordinary")

    def test_empty_year_returns_empty_list(self):
        self.assertEqual(compute_disposals(str(self.path), year=2027, method="FIFO"), [])

    def test_summary_handles_empty_list(self):
        s = summarize([])
        self.assertEqual(s["disposal_count"],      0)
        self.assertEqual(s["ordinary_count"],      0)
        self.assertEqual(s["realized_gain"],       0)
        self.assertEqual(s["total_ordinary_gain"], 0)
        self.assertEqual(s["total_fees"],          0)


class Section988SummaryTests(unittest.TestCase):
    """summarize() returns §988-shaped fields, not capital-gains split."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)
        # Two disposals in 2026 with different holding periods
        _add(self.path, ts=_ts(2024, 1, 1), symbol="EUR/USD", action="BUY",  price=1.10, amount=10000)
        _add(self.path, ts=_ts(2026, 6, 1), symbol="EUR/USD", action="SELL", price=1.15, amount=10000)  # ~880 days
        _add(self.path, ts=_ts(2026, 1, 1), symbol="GBP/USD", action="BUY",  price=1.25, amount=10000)
        _add(self.path, ts=_ts(2026, 6, 1), symbol="GBP/USD", action="SELL", price=1.27, amount=10000)  # ~150 days

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_summary_has_no_short_long_split(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        s = summarize(d)
        # §988-shaped keys exist
        self.assertIn("total_ordinary_gain", s)
        self.assertIn("ordinary_count", s)
        # capital-gains keys are absent
        self.assertNotIn("short_term_gain", s)
        self.assertNotIn("long_term_gain", s)
        self.assertNotIn("short_count", s)
        self.assertNotIn("long_count", s)

    def test_total_ordinary_gain_equals_realized_gain(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        s = summarize(d)
        # By construction, every disposal is ordinary, so the two totals match.
        self.assertEqual(s["total_ordinary_gain"], s["realized_gain"])

    def test_ordinary_count_equals_disposal_count(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        s = summarize(d)
        self.assertEqual(s["ordinary_count"], s["disposal_count"])
        self.assertEqual(s["disposal_count"], 2)

    def test_all_disposals_have_term_ordinary(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertTrue(all(x.term == "ordinary" for x in d))


class ShadowFilterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)
        # Shadow BUY + SHADOW SELL — should be skipped by default
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="SHADOW BUY",  price=100, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="SHADOW SELL", price=150, amount=1.0)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_shadow_excluded_by_default(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(d, [])

    def test_shadow_included_when_opted_in(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO", include_shadow=True)
        self.assertEqual(len(d), 1)
        self.assertAlmostEqual(d[0].gain_loss_usd, 50.0)

    def test_has_live_trades_false_for_shadow_only(self):
        self.assertFalse(has_live_trades(str(self.path)))

    def test_has_live_trades_true_after_live_trade(self):
        _add(self.path, ts=_ts(2026, 3, 1), symbol="Y", action="BUY", price=10, amount=1.0)
        self.assertTrue(has_live_trades(str(self.path)))


class MultiSymbolIndependenceTests(unittest.TestCase):
    """Lots are tracked PER symbol — BTC buys can't satisfy ETH sells."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)
        _add(self.path, ts=_ts(2026, 1, 1), symbol="EUR/USD", action="BUY",  price=10000, amount=1)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="GBP/USD", action="BUY",  price=2000,  amount=1)
        _add(self.path, ts=_ts(2026, 3, 1), symbol="EUR/USD", action="SELL", price=12000, amount=1)
        _add(self.path, ts=_ts(2026, 4, 1), symbol="GBP/USD", action="SELL", price=2500,  amount=1)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_each_symbol_matches_independently(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 2)
        by_sym = {x.symbol: x for x in d}
        self.assertAlmostEqual(by_sym["EUR/USD"].gain_loss_usd, 2000.0)
        self.assertAlmostEqual(by_sym["GBP/USD"].gain_loss_usd,  500.0)

    def test_summary_aggregates_across_symbols(self):
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        s = summarize(d)
        self.assertEqual(s["disposal_count"], 2)
        self.assertAlmostEqual(s["realized_gain"], 2500.0)


class NoiseRowTests(unittest.TestCase):
    """Non-positional rows (RATCHET, kill switch, zero-qty) must not affect math."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_zero_qty_rows_are_skipped(self):
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="BUY",       price=100, amount=1)
        _add(self.path, ts=_ts(2026, 1, 5), symbol="X", action="RATCHET",   price=110, amount=0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="SELL",      price=150, amount=1)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(len(d), 1)
        self.assertAlmostEqual(d[0].gain_loss_usd, 50.0)

    def test_sell_with_no_prior_buy_is_skipped(self):
        """No matching BUY lot → silently drop the SELL (no junk record)."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="SELL", price=150, amount=1)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(d, [])

    def test_unknown_action_words_are_filtered(self):
        """Anything that isn't BUY or SELL (and has price + qty) is ignored."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="MANUAL OVERRIDE", price=100, amount=1)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        self.assertEqual(d, [])


class HelperTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_available_years_returns_years_desc(self):
        _add(self.path, ts=_ts(2024, 6, 1), symbol="X", action="BUY",  price=10, amount=1)
        _add(self.path, ts=_ts(2025, 6, 1), symbol="X", action="SELL", price=15, amount=1)
        _add(self.path, ts=_ts(2026, 6, 1), symbol="X", action="BUY",  price=20, amount=1)
        years = available_years(str(self.path), include_shadow=False)
        self.assertEqual(years, [2026, 2025, 2024])

    def test_available_years_filters_shadow_by_default(self):
        _add(self.path, ts=_ts(2024, 1, 1), symbol="X", action="SHADOW BUY",  price=10, amount=1)
        _add(self.path, ts=_ts(2025, 1, 1), symbol="X", action="BUY",         price=10, amount=1)
        self.assertEqual(available_years(str(self.path), include_shadow=False), [2025])
        self.assertEqual(available_years(str(self.path), include_shadow=True),  [2025, 2024])

    def test_has_live_trades_handles_missing_db(self):
        # Non-existent path → should return False, not raise
        self.assertFalse(has_live_trades("/tmp/nonexistent-tax-test.db"))


class DustTests(unittest.TestCase):
    """Float dust at lot boundaries (within 1e-12) is treated as exhaustion."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        _make_db(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_dust_within_epsilon_does_not_create_extra_disposals(self):
        """Sell qty that consumes lot 1 fully + 1e-15 of lot 2 → 1 disposal,
        not 2.  The 1e-12 epsilon in compute_disposals guards against this."""
        _add(self.path, ts=_ts(2026, 1, 1), symbol="X", action="BUY",  price=100, amount=1.0)
        _add(self.path, ts=_ts(2026, 2, 1), symbol="X", action="BUY",  price=200, amount=1.0)
        _add(self.path, ts=_ts(2026, 3, 1), symbol="X", action="SELL", price=300, amount=1.0 + 1e-15)
        d = compute_disposals(str(self.path), year=2026, method="FIFO")
        # Only the first lot is consumed; dust below epsilon does not trigger
        # a second disposal off lot 2
        self.assertEqual(len(d), 1)


if __name__ == "__main__":
    unittest.main()
