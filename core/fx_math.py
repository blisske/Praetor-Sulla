"""
FX-specific math helpers for Ionic.

Three concerns the equity/crypto engines didn't have:

1. **Pip values vary by pair.** Most majors are quoted to 4 decimals so 1 pip
   = 0.0001. JPY pairs (USD/JPY, EUR/JPY, etc.) are quoted to 2-3 decimals
   so 1 pip = 0.01. Stop and target distances expressed in pips need to
   round-trip through these correctly.

2. **Position sizing is in units, not shares or USD notional.** Oanda's
   smallest deal is 1 unit (= 1 of the base currency). 10,000 units of
   EUR/USD ties up ~$10,800 of USD at current rates; pip value of that
   position is $1. For USD-base pairs (USD/JPY etc.), 10,000 units of
   USD/JPY ties up $10,000 and the pip value depends on the JPY rate.

3. **Display precision.** $1.45→$1.45 looks broken at 2 decimals when the
   real values are 1.4488→1.4561 (Tiberius hit this on crypto and we
   ported the magnitude-aware fp() helper). FX has the same issue
   inverted: $158.776 should NOT show as $158.77600.

Ionic is unleveraged by design — see CLAUDE.md. So position sizing budgets
units from available USD-equivalent equity directly, not from margin
allocation.
"""

import logging

logger = logging.getLogger("ionic")


# ── Pair classification ─────────────────────────────────────────────────────
def is_jpy_pair(symbol: str) -> bool:
    """JPY pairs use the 0.01 pip convention instead of the standard 0.0001."""
    return "JPY" in symbol


def base_currency(symbol: str) -> str:
    """EUR/USD → EUR. USD/JPY → USD."""
    return symbol.split("/")[0]


def quote_currency(symbol: str) -> str:
    """EUR/USD → USD. USD/JPY → JPY."""
    return symbol.split("/")[1]


# ── Pip math ────────────────────────────────────────────────────────────────
def pip_size(symbol: str) -> float:
    """
    Size of one pip in price space.

    Standard 4-decimal majors: 0.0001
    JPY pairs (2-decimal quote): 0.01
    """
    return 0.01 if is_jpy_pair(symbol) else 0.0001


def pip_value_usd(symbol: str, price: float, units: float = 1.0) -> float:
    """
    USD value of one pip of `symbol` for a position of `units` units.

    For USD-quote pairs (EUR/USD, GBP/USD, AUD/USD, NZD/USD):
        pip_value = pip_size × units                (already in USD)
    For USD-base pairs (USD/JPY, USD/CHF, USD/CAD):
        pip_value = (pip_size × units) ÷ price      (convert through current rate)
    For non-USD cross-pairs (EUR/JPY, GBP/JPY etc.):
        would need a USD conversion leg — not in the initial Ionic universe
        so this case raises NotImplementedError for now.

    Args:
        symbol:  "EUR/USD", "USD/JPY", etc.
        price:   current mid price (only used for USD-base pairs)
        units:   position size; defaults to 1 unit so callers can scale
                 (pip_value_usd(sym, p, 1) × units = same answer)

    Returns:
        USD value of one pip move. Always positive.
    """
    p = pip_size(symbol)
    base = base_currency(symbol)
    quote = quote_currency(symbol)

    if quote == "USD":
        # EUR/USD, GBP/USD, AUD/USD, NZD/USD — the pip itself is denominated in USD.
        return p * units
    if base == "USD":
        # USD/JPY, USD/CHF, USD/CAD — the pip is denominated in the quote
        # currency; divide by the rate to convert back to USD.
        if price <= 0:
            return 0.0
        return (p * units) / price
    # Non-USD crosses (EUR/JPY, GBP/CHF, etc.) — out of scope for the 7-major
    # Ionic universe. When we add them in a later phase, route through the
    # base-currency / USD rate from the pricing snapshot.
    raise NotImplementedError(
        f"Cross-pair pip value (no USD leg): {symbol}. Add USD-conversion logic "
        "when extending the universe beyond the 7 majors."
    )


# ── Position sizing ─────────────────────────────────────────────────────────
def calculate_units(
    equity_usd: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    symbol: str,
    position_cap_pct: float = 12.0,
) -> int:
    """
    Compute Ionic's position size in Oanda units for an unleveraged spot trade.

    Sizing logic:
        risk_usd      = equity_usd × risk_pct%
        stop_distance = abs(entry - stop)         in price space
        pip_distance  = stop_distance ÷ pip_size  (number of pips of risk)
        units         = risk_usd ÷ (pip_distance × pip_value_per_unit)

    Then capped at position_cap_pct of equity (in USD-equivalent notional).

    All math in USD. Returns whole units (Oanda accepts fractional units
    but rounding to whole keeps the position log easier to read).

    Args:
        equity_usd:        current shadow or live equity, USD
        risk_pct:          per-trade risk as %, e.g. 5.0 = 5%
        entry_price:       intended fill price
        stop_price:        ATR-derived stop price (always < entry for longs)
        symbol:            "EUR/USD" etc.
        position_cap_pct:  hard cap on position size as % of equity

    Returns:
        Whole units. 0 if the math returns nonsense (zero/negative stop dist,
        zero pip value, etc.) so callers can `if not units: continue`.
    """
    if entry_price <= 0 or stop_price <= 0 or equity_usd <= 0:
        return 0

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0

    pip_distance = stop_distance / pip_size(symbol)
    if pip_distance <= 0:
        return 0

    risk_usd = equity_usd * (risk_pct / 100.0)
    pip_value_per_unit = pip_value_usd(symbol, entry_price, units=1.0)
    if pip_value_per_unit <= 0:
        return 0

    raw_units = risk_usd / (pip_distance * pip_value_per_unit)

    # Apply the position cap: notional in USD must not exceed equity * cap.
    # Notional for USD-quote pairs = units × entry_price (price quotes USD/base).
    # Notional for USD-base pairs = units (units already in USD).
    if quote_currency(symbol) == "USD":
        max_units_by_cap = (equity_usd * position_cap_pct / 100.0) / entry_price
    elif base_currency(symbol) == "USD":
        max_units_by_cap = equity_usd * position_cap_pct / 100.0
    else:
        # Cross pair — same blocker as pip_value_usd above.
        max_units_by_cap = raw_units  # Effectively no cap until we wire crosses.

    units = min(raw_units, max_units_by_cap)
    return max(0, int(units))


def realized_pnl_usd(symbol: str, units: float, entry_price: float, exit_price: float) -> float:
    """
    Realized USD P&L for a LONG position of `units` base-currency units
    opened at entry_price and closed at exit_price.

    - USD-quote pairs (EUR/USD …): pnl = units × (exit − entry)  — quote IS USD.
    - USD-base pairs (USD/JPY …):  profit accrues in the QUOTE currency:
        pnl_quote = units × (exit − entry); convert at the exit rate:
        pnl_usd   = units × (exit − entry) ÷ exit

    This is the calculation that `position_notional_usd(exit) −
    position_notional_usd(entry)` silently got WRONG for USD-base pairs —
    notional is price-independent there (= units), so every USD/JPY, USD/CHF
    and USD/CAD close recorded $0.00 P&L (18 of Ionic's first 22 shadow
    trades; found in the 2026-06-09 trading-logic audit). Use THIS for P&L;
    use position_notional_usd only for cash debits/position value.
    """
    if not units or entry_price <= 0 or exit_price <= 0:
        return 0.0
    if quote_currency(symbol) == "USD":
        return units * (exit_price - entry_price)
    # USD-base (and, as a fallback, crosses): quote-ccy P&L converted at exit.
    return units * (exit_price - entry_price) / exit_price


def position_notional_usd(symbol: str, units: float, price: float) -> float:
    """
    USD-equivalent notional value of a position. For USD-quote pairs that's
    units × price; for USD-base pairs that's just units. Used by the shadow
    ledger to know how much synthetic cash to debit/credit on entry/exit.
    """
    if quote_currency(symbol) == "USD":
        return units * price
    if base_currency(symbol) == "USD":
        return float(units)
    return float(units)  # crosses — placeholder until we add USD conversion


# ── Magnitude-aware display ─────────────────────────────────────────────────
def fp(price, symbol: str | None = None) -> str:
    """
    Format a price string with FX-appropriate precision.

    If `symbol` is provided and is a JPY pair: 3 decimals (matches Oanda's
    quote precision). Otherwise: 5 decimals (standard 4-pip-precision pairs
    quote to 5 places to capture the fractional pip).

    Without a symbol hint, falls back to Tiberius's magnitude-based rule
    (>=10 → 2dp, 0.1-10 → 4dp, etc.) — useful for non-symbol-context callers
    like equity log lines.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return str(price)

    if symbol is not None:
        if is_jpy_pair(symbol):
            return f"{p:.3f}"
        return f"{p:.5f}"

    ap = abs(p)
    if ap >= 10:    return f"{p:,.2f}"
    if ap >= 0.1:   return f"{p:.4f}"
    if ap >= 0.001: return f"{p:.6f}"
    return f"{p:.8f}"


def pip_diff(entry_price: float, exit_price: float, symbol: str) -> float:
    """How many pips the price moved. Signed (positive = price up)."""
    return (exit_price - entry_price) / pip_size(symbol)
