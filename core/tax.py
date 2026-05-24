"""Tax-lot accounting for Foundation Ionic — FX §988 ordinary-income flavor.

Given a user's trade history, computes per-disposal records suitable for
US IRC §988 ordinary-income reporting (Schedule 1 line 8z "Other income"
or Form 6781 line 1). Pure functions — no FastAPI, no HTTP, just SQLite
reads + math. Tested standalone via tests/test_tax.py.

⚠️ FX TAX TREATMENT IS DIFFERENT — read carefully:

  Default (IRC §988): retail spot FX gains/losses are ORDINARY INCOME.
  - No short/long-term distinction
  - No holding-period boundary
  - Taxed at the trader's marginal rate (not capital-gains rate)
  - Reported on Schedule 1 line 8z (or 6781 line 1) — NOT Schedule D

  Opt-out (§988(a)(1)(B)): a trader can elect to treat §988 transactions
  as CAPITAL gains/losses instead. The election must be made on a
  per-trade basis BEFORE the transaction (must be "clearly identified"
  in books and records). For most retail FX, this election is rare.

  Section 1256 (60/40 rule): does NOT apply to Oanda spot FX. Only
  regulated futures contracts + listed options qualify.

This module assumes default §988 treatment. Every Disposal record's
`term` field is 'ordinary'. Summary aggregates to a single
`total_ordinary_gain` figure with no short/long split. Lot-matching
machinery (FIFO/LIFO/HIFO) is preserved for record-keeping clarity even
though it doesn't affect §988 tax math (gains aggregate regardless of
which lot they came from). FIFO is the conservative default.

Ionic-specific notes:
  - FX pairs like 'EUR/USD'. Symbol convention from Oanda v20.
  - `amount` column on shadow SELLs stores `pnl_usd`, not units of base
    currency, per the shared shadow-ledger convention. DEMO numbers from
    include_shadow=True are shape-correct but quantity-skewed.
  - `fee_usd` is 0.0 in Phase 1 scaffold (no live broker yet). Phase 2
    (Oanda integration) will derive this from spread + financing in the
    v20 trade transaction stream. Oanda has no separate commission on
    standard accounts; the cost is baked into the spread.
  - No holding-period boundary — `holding_days` is computed but doesn't
    drive term assignment.

Public API:

    compute_disposals(
        db_path: str,
        *,
        year: int,
        method: str = 'FIFO',
        include_shadow: bool = False,
    ) -> list[Disposal]

    summarize(disposals: list[Disposal]) -> dict
        Returns {total_proceeds, total_cost_basis, realized_gain,
                 total_ordinary_gain, total_fees,
                 disposal_count, ordinary_count}.

Disposal record shape (frozen dataclass):
    symbol           — e.g. 'EUR/USD', 'USD/JPY'
    date_acquired    — UTC ISO timestamp of the buy lot this came from
    date_sold        — UTC ISO timestamp of the sell that disposed it
    qty              — units of base currency disposed
    proceeds_usd     — sell price × qty − attributed sell fee
    cost_basis_usd   — buy price × qty + attributed buy fee
    gain_loss_usd    — proceeds − cost_basis (ORDINARY under §988)
    holding_days     — calendar days (informational only under §988)
    term             — always 'ordinary' under §988 default treatment
    fees_usd         — total fees attributed (buy share + sell share)

Lot-matching methods (per-symbol, independent — informational for §988):
    FIFO — oldest lot disposed first
    LIFO — newest lot disposed first
    HIFO — highest-cost-basis lot disposed first

Shadow trades (action LIKE 'SHADOW%') are EXCLUDED by default since they
represent simulated paper trades with no tax consequence. Caller can set
include_shadow=True to surface them in the dashboard with a DEMO watermark.

⚠️ Not tax advice. FX tax law has §988/§1256 nuances that vary by
trader profile (retail vs trader-status), election history, and account
type. Verify with a CPA before filing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ─── Data classes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Disposal:
    """One disposal event for §988 ordinary-income reporting.

    A single 'SELL' trade row in the DB can generate MULTIPLE Disposal
    records if it spans multiple buy lots (e.g. sold 10,000 EUR/USD when
    four 2,500-unit lots were held — that's 4 Disposal records, one per
    lot). Lot-matching is informational under §988; tax math aggregates
    regardless of which lot was matched.
    """
    symbol:         str
    date_acquired:  str    # UTC ISO 8601
    date_sold:      str    # UTC ISO 8601
    qty:            float
    proceeds_usd:   float
    cost_basis_usd: float
    gain_loss_usd:  float
    holding_days:   int    # informational only under §988
    term:           str    # always 'ordinary' under §988 default
    fees_usd:       float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Lot:
    """Internal: a single buy lot, possibly partially-consumed by prior sells.

    Mutable: qty_remaining decrements as sells consume it. When it hits 0,
    the lot is exhausted + dropped from the per-symbol inventory.
    """
    date_acquired:    str       # UTC ISO
    qty_remaining:    float     # tokens still in this lot
    price_per_unit:   float     # buy price ($/token)
    fee_per_unit:     float     # buy fee allocated per-token ($/token)


# ─── Public API ────────────────────────────────────────────────────────────


# Under §988 default treatment for retail spot FX, there is NO long-term
# holding boundary — every disposal is ordinary income regardless of how
# long the position was held. We compute `holding_days` for informational
# display but the `term` field is always 'ordinary'.
TERM_ORDINARY = "ordinary"


def compute_disposals(
    db_path: str,
    *,
    year: int,
    method: str = "FIFO",
    include_shadow: bool = False,
) -> list[Disposal]:
    """Walk the user's trade history chronologically and emit a Disposal
    record for every (lot, sell) match in the given calendar year.

    Args:
        db_path:        Path to the per-user corinthian.db (or operator's)
        year:           Calendar year (UTC) to scope disposals to
        method:         'FIFO' | 'LIFO' | 'HIFO' (case-insensitive)
        include_shadow: If True, treat 'SHADOW BUY' / 'SHADOW SELL' as
                        live for the purposes of the report. Used by the
                        dashboard for DEMO-mode preview.

    Returns:
        List of Disposal records sorted by date_sold ascending.

    Raises:
        ValueError on unknown method
    """
    method = method.upper()
    if method not in ("FIFO", "LIFO", "HIFO"):
        raise ValueError(f"Unknown lot method: {method!r}. Use FIFO, LIFO, or HIFO.")

    rows = _load_trade_rows(db_path, include_shadow=include_shadow)
    disposals: list[Disposal] = []
    inventory: dict[str, list[_Lot]] = {}   # symbol → list[_Lot]

    for row in rows:
        symbol  = row["symbol"]
        action  = row["action"]
        price   = float(row["price"] or 0.0)
        qty     = float(row["amount"] or 0.0)
        fee     = float(row["fee_usd"] or 0.0)
        ts_str  = row["timestamp"]

        if qty <= 0 or price <= 0:
            # Skip RATCHET rows (qty=0), kill-switch rows, etc.
            continue

        # Identify direction. We accept BUY, BUY ADD, SHADOW BUY, SHADOW BUY ADD,
        # SELL, SHADOW SELL, SHADOW PARTIAL SELL — anything else (RATCHET, KILL)
        # is non-positional and filtered above by qty/price guard.
        action_upper = action.upper()
        is_buy  = "BUY"  in action_upper
        is_sell = "SELL" in action_upper
        if not (is_buy or is_sell):
            continue

        # Shadow-mode trades come back as SHADOW * — they're a USD amount on
        # sells (not a token qty), so the math is different. For the tax
        # report DEMO path, we re-derive token qty from position_size_usd if
        # the action stamped it. Simpler: skip SHADOW SELL pnl-as-amount and
        # only count live trades for tax. include_shadow surfaces SHADOW BUY
        # entries but their SELLs need conversion. For MVP demo: pull qty
        # straight from `amount` and trust shadow ledger conventions.
        # (Live BUY/SELL have token qty in amount per execution.py contract.)

        if is_buy:
            # Add lot to inventory
            inventory.setdefault(symbol, []).append(_Lot(
                date_acquired  = _normalize_ts(ts_str),
                qty_remaining  = qty,
                price_per_unit = price,
                # Allocate buy-side fee proportionally per token
                fee_per_unit   = (fee / qty) if qty > 0 else 0.0,
            ))
            continue

        # is_sell — consume lots per chosen method
        lots = inventory.get(symbol, [])
        if not lots:
            # Sell with no matching buy lot — probably the friend's first
            # SHADOW SELL where amount=pnl_usd (shadow convention) rather
            # than qty. Skip rather than producing junk records. Real live
            # selling can't happen without prior live buying so this is
            # safe for live path.
            continue

        qty_to_sell = qty
        sell_year   = _year_of(ts_str)

        while qty_to_sell > 1e-12 and lots:
            lot = _pick_lot(lots, method)
            qty_from_this_lot = min(qty_to_sell, lot.qty_remaining)

            # Allocate sell-side fee proportionally to the portion of this
            # sell that came from THIS lot
            fee_share_of_sell = fee * (qty_from_this_lot / qty) if qty > 0 else 0.0

            proceeds   = qty_from_this_lot * price - fee_share_of_sell
            buy_basis  = qty_from_this_lot * lot.price_per_unit
            buy_fee_sh = qty_from_this_lot * lot.fee_per_unit
            cost_basis = buy_basis + buy_fee_sh
            gain_loss  = proceeds - cost_basis

            date_acquired_dt = _parse_iso(lot.date_acquired)
            date_sold_dt     = _parse_iso(_normalize_ts(ts_str))
            holding_days     = (date_sold_dt.date() - date_acquired_dt.date()).days
            term             = TERM_ORDINARY  # §988 default — no short/long split

            # Only emit disposals whose SELL falls in the requested year.
            # The acquired-date can be any prior year (carryover lots).
            if sell_year == year:
                disposals.append(Disposal(
                    symbol         = symbol,
                    date_acquired  = lot.date_acquired,
                    date_sold      = _normalize_ts(ts_str),
                    qty            = qty_from_this_lot,
                    proceeds_usd   = round(proceeds, 4),
                    cost_basis_usd = round(cost_basis, 4),
                    gain_loss_usd  = round(gain_loss, 4),
                    holding_days   = holding_days,
                    term           = term,
                    fees_usd       = round(fee_share_of_sell + buy_fee_sh, 4),
                ))

            # Decrement lot + remove if exhausted
            lot.qty_remaining -= qty_from_this_lot
            qty_to_sell       -= qty_from_this_lot
            if lot.qty_remaining <= 1e-12:
                lots.remove(lot)

        # qty_to_sell residual (sold more than we had) is silently dropped —
        # implies bad data; logging in caller is enough.

    disposals.sort(key=lambda d: d.date_sold)
    return disposals


def summarize(disposals: list[Disposal]) -> dict:
    """Aggregate a list of disposals into dashboard summary card numbers.

    Under §988 default treatment, every disposal contributes to
    `total_ordinary_gain` — no short/long split. We preserve
    `realized_gain` as a synonym so the frontend can use it
    interchangeably (it'll equal total_ordinary_gain by construction).
    """
    total = round(sum(d.gain_loss_usd for d in disposals), 2)
    return {
        "disposal_count":      len(disposals),
        "ordinary_count":      len(disposals),
        "total_proceeds":      round(sum(d.proceeds_usd   for d in disposals), 2),
        "total_cost_basis":    round(sum(d.cost_basis_usd for d in disposals), 2),
        "realized_gain":       total,
        "total_ordinary_gain": total,
        "total_fees":          round(sum(d.fees_usd for d in disposals), 2),
    }


def available_years(db_path: str, *, include_shadow: bool = False) -> list[int]:
    """Return years (UTC, descending) that have at least one BUY or SELL
    trade — drives the year-tab UI."""
    rows = _load_trade_rows(db_path, include_shadow=include_shadow)
    years = set()
    for r in rows:
        action = (r["action"] or "").upper()
        if not ("BUY" in action or "SELL" in action):
            continue
        years.add(_year_of(r["timestamp"]))
    return sorted(years, reverse=True)


def has_live_trades(db_path: str) -> bool:
    """True iff the user has at least one non-SHADOW BUY/SELL row. Drives
    the dashboard DEMO watermark."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM trades "
                "WHERE action NOT LIKE 'SHADOW%' "
                "  AND (action LIKE '%BUY%' OR action LIKE '%SELL%') "
                "LIMIT 1"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


# ─── Internal helpers ──────────────────────────────────────────────────────


def _load_trade_rows(db_path: str, *, include_shadow: bool) -> list:
    """Return the per-user trades rows in chronological order, scoped to
    BUY/SELL actions (filters out RATCHET, KILL SWITCH, MANUAL OVERRIDE, etc).
    Live-only by default; pass include_shadow=True to also surface SHADOW BUY/SELL."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if include_shadow:
                # All BUYs + SELLs, live + shadow
                where = "(action LIKE '%BUY%' OR action LIKE '%SELL%')"
            else:
                # Live only — exclude SHADOW *
                where = ("(action LIKE '%BUY%' OR action LIKE '%SELL%') "
                         "AND action NOT LIKE 'SHADOW%'")
            rows = conn.execute(
                f"SELECT timestamp, symbol, action, price, amount, "
                f"       COALESCE(fee_usd, 0.0) AS fee_usd "
                f"FROM trades WHERE {where} "
                f"ORDER BY datetime(timestamp) ASC, id ASC"
            ).fetchall()
            return rows
        finally:
            conn.close()
    except sqlite3.Error as e:
        # Treat unreadable DB as empty — caller's dashboard will show
        # "no disposals" rather than a stack trace
        print(f"⚠️  tax._load_trade_rows({db_path}): {e}")
        return []


def _pick_lot(lots: list[_Lot], method: str) -> _Lot:
    """Pick the next lot to consume per the chosen method.

    FIFO: oldest first (lots are already chronological — head of list)
    LIFO: newest first (tail of list)
    HIFO: highest cost basis per unit first (max by price_per_unit + fee_per_unit)
    """
    if method == "FIFO":
        return lots[0]
    if method == "LIFO":
        return lots[-1]
    # HIFO
    return max(lots, key=lambda l: l.price_per_unit + l.fee_per_unit)


def _normalize_ts(ts: str) -> str:
    """SQLite CURRENT_TIMESTAMP returns 'YYYY-MM-DD HH:MM:SS' (no tz).
    Normalize to ISO 8601 with Z suffix so callers can round-trip cleanly."""
    if not ts:
        return ""
    if "T" in ts or "+" in ts or "Z" in ts:
        return ts
    return ts.replace(" ", "T") + "Z"


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string (with or without Z) to a UTC datetime."""
    s = ts.replace("Z", "+00:00")
    if "+" not in s and "T" in s:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def _year_of(ts: str) -> int:
    """Extract the UTC calendar year from a timestamp string."""
    return _parse_iso(_normalize_ts(ts)).astimezone(timezone.utc).year
