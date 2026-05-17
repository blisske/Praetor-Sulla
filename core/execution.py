"""
Sulla — Execution layer.

Phase 3 is **shadow-only** by deliberate design: the engine logs trades to
the synthetic ledger in `database.py` but never calls Oanda's order
endpoints. This file used to be Anton's Alpaca-bound execution module; that
version is preserved at `_main_anton_reference.py` for the eventual port.

The shadow contract (inherited from Anton/Tiberius):
  - All decisions run through the engine identically to live mode
  - Trade-log writes happen as `SHADOW BUY` / `SHADOW SELL` action strings
  - The synthetic cash ledger in `database.py` is debited on entry, credited
    on exit. P&L appears in the `amount` column of SHADOW SELL rows.
  - Stops live in `open_positions.current_stop` (no Oanda order placed)
  - The exit engine in main.py handles stop hits + take profits against
    in-DB stops, not against the exchange

Phase 6+ wires Oanda v20 order submission against this same interface so
no other module changes when we flip shadow_mode → false.
"""

import logging

import fx_math

logger = logging.getLogger("sulla")


def is_market_open() -> bool:
    """
    FX market is open Sunday 17:00 ET → Friday 17:00 ET. Closed weekends
    only. No daily session boundaries like equities.

    Used by the cycle loop's guard logic before evaluating entry signals.
    """
    import datetime
    import pytz

    et = pytz.timezone("America/New_York")
    now = datetime.datetime.now(et)
    weekday = now.weekday()  # Mon=0, Sun=6

    # Saturday → closed all day
    if weekday == 5:
        return False
    # Sunday before 17:00 ET → still closed
    if weekday == 6 and now.hour < 17:
        return False
    # Friday after 17:00 ET → closed for the weekend
    if weekday == 4 and now.hour >= 17:
        return False
    return True


# ── Position sizing (the only "execution" math Phase 3 needs) ───────────────
def calculate_position_units(
    equity_usd: float,
    atr: float,
    entry_price: float,
    symbol: str,
    risk_config: dict,
    stop_mult_override: float | None = None,
) -> tuple[int, float]:
    """
    Returns (units, stop_price) for a shadow buy.

    Sizing follows Anton/Tiberius pattern (risk_pct / stop_distance) but uses
    fx_math for the pip-aware math.

    Args:
        equity_usd:         current shadow equity in USD
        atr:                ATR(14) from the indicator dict
        entry_price:        current mid price
        symbol:             "EUR/USD" etc.
        risk_config:        cfg.get('risk', {}) dict — reads risk_per_trade_pct
                            and position_size_max_pct
        stop_mult_override: caller can pass an explicit stop multiplier; falls
                            back to 2.0

    Returns:
        (units, stop_price). Both 0/0.0 if math returns nonsense; caller
        should `if not units: continue` to skip the trade cleanly.
    """
    if atr <= 0 or entry_price <= 0 or equity_usd <= 0:
        return 0, 0.0

    risk_pct = float(risk_config.get('risk_per_trade_pct', 5.0))
    cap_pct  = float(risk_config.get('position_size_max_pct', 12.0))
    stop_mult = stop_mult_override if stop_mult_override is not None else 2.0

    stop_price = entry_price - (atr * stop_mult)
    if stop_price <= 0:
        return 0, 0.0

    units = fx_math.calculate_units(
        equity_usd=equity_usd,
        risk_pct=risk_pct,
        entry_price=entry_price,
        stop_price=stop_price,
        symbol=symbol,
        position_cap_pct=cap_pct,
    )
    return units, stop_price


# ── Stubs for Phase 6+ live-Oanda wiring ────────────────────────────────────
# Keeping the function names + signatures stable now means Phase 6 just
# fills in the bodies — no callers need to change.

def execute_buy_with_stop(*args, **kwargs):
    """Placeholder — Phase 6 wires Oanda order placement."""
    raise NotImplementedError(
        "execute_buy_with_stop is Phase 6 (live Oanda). Phase 3 routes "
        "through the shadow buy path in main.py instead."
    )


def execute_take_profit(*args, **kwargs):
    """Placeholder — Phase 6 wires Oanda market close."""
    raise NotImplementedError(
        "execute_take_profit is Phase 6 (live Oanda). Phase 3 routes "
        "through the shadow exit engine in main.py instead."
    )
