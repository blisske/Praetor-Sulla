"""
Ionic — Execution layer.

Two paths live here. Which one runs depends on `oanda.shadow_mode` in
Config.yaml:

  SHADOW MODE (default for new users):
    - Decisions run through the engine identically to live mode
    - Trade-log writes happen as `SHADOW BUY` / `SHADOW SELL` action strings
    - The synthetic cash ledger in `database.py` is debited on entry,
      credited on exit. P&L appears in the `amount` column of SHADOW SELL rows.
    - Stops live in `open_positions.current_stop` (no Oanda order placed)
    - The exit engine in main.py handles stop hits + take profits against
      in-DB stops, not against the exchange.

  LIVE MODE:
    - `execute_buy_with_stop()` submits a MARKET order to Oanda with the
      stop-loss ATTACHED (atomic — no separate place-then-attach race).
    - `execute_take_profit()` closes the position at market via the
      /positions/{instrument}/close endpoint.
    - `execute_ratchet_stop()` modifies the stop on the existing open
      trade via the /trades/{trade_id}/orders endpoint.
    - All three return enough information for main.py to write a tax-
      accurate `log_trade()` row (fill price, USD fee, realized P&L).

The shadow contract is non-negotiable: shadow paths NEVER call Oanda's
order endpoints. Same code, different destination — flipping
shadow_mode → false is the only flag that flips the behavior.

Reference: Doric's Alpaca-bound version preserved at
`_main_anton_reference.py` for porting comparison.
"""

import logging
import uuid

import fx_math
from oanda_client import (
    OandaError,
    OandaMissingCredentials,
    extract_fill_fee_usd,
    extract_fill_price,
    extract_close_fee_usd,
    extract_close_pl_usd,
)
import market_data  # for shared OandaClient cache via get_client()

logger = logging.getLogger("ionic")


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


# ── Live-Oanda execution ────────────────────────────────────────────────────
# All three functions are SYNCHRONOUS (called from main.py via
# `await asyncio.to_thread(...)`) to match the Anton/Doric pattern.
# All three return enough for the caller to write a tax-accurate log_trade
# row without needing a follow-up REST call.

def _client_or_log(action: str):
    """Get the shared OandaClient or log + return None.

    Returning None lets the caller log a clear "credentials missing"
    failure and continue the cycle instead of crashing the engine.
    """
    try:
        client = market_data.get_client()
    except OandaMissingCredentials as e:
        logger.error(f"[execution.{action}] OANDA credentials missing: {e}")
        return None
    if client is None:
        logger.error(f"[execution.{action}] OandaClient unavailable.")
        return None
    return client


def execute_buy_with_stop(
    symbol: str,
    units: int,
    stop_price: float,
) -> tuple[bool, float, float]:
    """LIVE buy: submit MARKET order to Oanda with attached stop-loss.

    Args:
        symbol:     "EUR/USD" or "EUR_USD"
        units:      signed int. Positive = long. Caller computes via
                    execution.calculate_position_units(...).
        stop_price: protective stop attached on fill, GTC.

    Returns:
        (success, fill_price, fee_usd)
        - success:    True if Oanda returned an orderFillTransaction
        - fill_price: actual fill price (zero on failure)
        - fee_usd:    sum of commission + financing + halfSpreadCost from the
                      fill txn. Folded into cost basis for the tax report.

    Failure tuple: (False, 0.0, 0.0). Defensive — any Oanda error returns
    cleanly so the cycle continues instead of crashing.
    """
    if units == 0:
        logger.warning(f"[{symbol}] execute_buy_with_stop called with 0 units; skipping.")
        return False, 0.0, 0.0

    client = _client_or_log("execute_buy_with_stop")
    if client is None:
        return False, 0.0, 0.0

    client_order_id = f"ionic_buy_{symbol.replace('/', '')}_{uuid.uuid4().hex[:8]}"
    logger.info(
        f"[{symbol}] Live BUY: {units} units, stop @ {stop_price:.5f}, id={client_order_id}"
    )
    try:
        resp = client.place_market_order(
            symbol=symbol,
            units=units,
            stop_loss_price=stop_price,
            client_order_id=client_order_id,
        )
    except OandaError as e:
        logger.error(f"[{symbol}] Oanda BUY rejected: {e}")
        return False, 0.0, 0.0

    fill = resp.get("orderFillTransaction") or {}
    if not fill:
        # Oanda accepted the order but it didn't fill (rare for MARKET FOK —
        # typically only happens on illiquid micro-pairs). cancel txn will
        # explain why.
        cancel = resp.get("orderCancelTransaction") or {}
        reason = cancel.get("reason", "UNKNOWN")
        logger.warning(f"[{symbol}] BUY did not fill: reason={reason}")
        return False, 0.0, 0.0

    fill_price = extract_fill_price(resp)
    fee_usd    = extract_fill_fee_usd(resp)
    logger.info(
        f"[{symbol}] ✅ BUY filled @ {fill_price:.5f}, fee ${fee_usd:.4f}"
    )
    return True, fill_price, fee_usd


def execute_take_profit(symbol: str) -> tuple[bool, float, float]:
    """LIVE take-profit / mean-reversion exit: market-close the position.

    Args:
        symbol: "EUR/USD" or "EUR_USD"

    Returns:
        (success, pl_usd, fee_usd)
        - success:  True if Oanda returned a longOrderFillTransaction
        - pl_usd:   realized P&L from this close, in USD (positive = win)
        - fee_usd:  close-side fee (commission + financing + spread cost)

    Failure tuple: (False, 0.0, 0.0).
    """
    client = _client_or_log("execute_take_profit")
    if client is None:
        return False, 0.0, 0.0

    logger.info(f"[{symbol}] Live TP / close-at-market")
    try:
        resp = client.close_position(symbol, side="long")
    except OandaError as e:
        # MARKET_ORDER_INSUFFICIENT_LIQUIDITY happens occasionally on
        # weekend rollover; let main.py decide whether to retry next cycle.
        logger.error(f"[{symbol}] Oanda close rejected: {e}")
        return False, 0.0, 0.0

    fill = resp.get("longOrderFillTransaction") or {}
    if not fill:
        logger.warning(f"[{symbol}] close had no fill transaction in response.")
        return False, 0.0, 0.0

    pl_usd  = extract_close_pl_usd(resp, side="long")
    fee_usd = extract_close_fee_usd(resp, side="long")
    logger.info(
        f"[{symbol}] ✅ closed: P&L ${pl_usd:+.2f}, fee ${fee_usd:.4f}"
    )
    return True, pl_usd, fee_usd


def execute_ratchet_stop(symbol: str, new_stop_price: float) -> bool:
    """LIVE trend-following stop ratchet: tighten the stop on the open trade.

    Oanda lets us PUT a new stop directly onto an existing trade — no
    cancel-and-replace dance (one of v20's nicer features vs CCXT/Alpaca).

    Args:
        symbol:         "EUR/USD" or "EUR_USD"
        new_stop_price: new stop level (assumes ATR ratchet has already
                        verified this is tighter than the current stop —
                        execution layer does NOT re-check direction).

    Returns:
        True on success, False on any failure. No fee/PL — modifying a stop
        order doesn't cost anything.
    """
    client = _client_or_log("execute_ratchet_stop")
    if client is None:
        return False

    try:
        # Find the open trade for this instrument. We pick the first
        # matching trade — for the long-only Ionic strategy there should
        # be at most one open trade per symbol at any time.
        trades = client.get_open_trades()
    except OandaError as e:
        logger.error(f"[{symbol}] couldn't list open trades for ratchet: {e}")
        return False

    target = None
    instrument_oanda = symbol.replace("/", "_")
    for t in trades:
        if t.get("instrument") == instrument_oanda:
            target = t
            break

    if target is None:
        logger.warning(
            f"[{symbol}] no open trade found for ratchet "
            f"(maybe closed between cycles?)"
        )
        return False

    trade_id = target.get("id")
    if not trade_id:
        logger.error(f"[{symbol}] open trade has no id field: {target}")
        return False

    try:
        client.modify_trade_stop(trade_id, new_stop_price)
    except OandaError as e:
        logger.error(f"[{symbol}] ratchet stop modify rejected: {e}")
        return False

    logger.info(f"[{symbol}] ✅ stop ratcheted to {new_stop_price:.5f}")
    return True
