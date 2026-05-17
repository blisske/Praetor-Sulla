import math
import uuid
import time
import logging
from alpaca.trading.requests import (
    MarketOrderRequest, StopOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
import config_manager

# ==============================================================================
# SULLA V1 - WALL STREET EXECUTION ENGINE
# Handles all Alpaca order routing: buys with stops, take-profits, ratchets,
# position sizing, and emergency liquidation.
# ==============================================================================

# Use the centralized Alpaca Trading Client from config_manager
trading_client = config_manager.get_trading_client()


# ==============================================================================
# ACCOUNT & MARKET STATUS
# ==============================================================================

def is_market_open() -> bool:
    """Queries Alpaca's clock to ensure the US Equity market is physically open."""
    try:
        clock = trading_client.get_clock()
        return clock.is_open
    except Exception as e:
        logging.error(f"⚠️ Failed to fetch Alpaca clock: {e}")
        return False


def get_account_balance() -> float:
    """Fetches the total paper equity available in the Alpaca account."""
    try:
        account = trading_client.get_account()
        return float(account.equity)
    except Exception as e:
        logging.error(f"⚠️ Failed to fetch Alpaca account balance: {e}")
        return 0.0


def get_open_positions():
    """Retrieves all currently held assets in the paper portfolio."""
    try:
        positions = trading_client.get_all_positions()
        return {pos.symbol: float(pos.qty) for pos in positions}
    except Exception as e:
        logging.error(f"⚠️ Failed to fetch Alpaca positions: {e}")
        return {}


# ==============================================================================
# POSITION SIZING
# ==============================================================================

def calculate_position_size(equity, atr, price, risk_config):
    """
    Calculates USD position size based on ATR volatility and risk limits.
    Enforces strictly WHOLE SHARES to align with Alpaca stop-loss rules.
    Returns 0.0 if the trade should be rejected.

    NOTE: `equity` must be Alpaca's `account.equity` (mark-to-market), which
    already includes unrealized P&L on open positions. Do not pre-deflate it
    with unrealized losses — that double-counts drawdown and starves new entries
    exactly when mean-reversion setups are most attractive.
    """
    risk_pct = risk_config.get('risk_per_trade_pct', 1.0) / 100.0
    risk_usd = equity * risk_pct

    cfg = config_manager.load_engine_config()
    stop_mult = cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)

    stop_dist = atr * stop_mult
    if stop_dist <= 0:
        stop_dist = price * 0.01

    # Calculate raw shares, then floor to whole shares immediately.
    # Alpaca rejects stop-loss orders on fractional share positions.
    raw_shares    = risk_usd / stop_dist
    actual_shares = math.floor(raw_shares)

    # If the risk allowance can't buy even 1 full share, reject early.
    if actual_shares < 1:
        return 0.0

    pos_size_usd = actual_shares * price

    # Guardrails
    min_size    = risk_config.get('min_trade_size_usd', 12.0)
    max_pct     = risk_config.get('position_size_max_pct', 5.0) / 100.0
    max_size    = equity * max_pct

    # If calculated size exceeds the configurable cap, recalculate at the cap.
    if pos_size_usd > max_size:
        capped_shares = math.floor(max_size / price)
        if capped_shares < 1:
            return 0.0
        pos_size_usd = capped_shares * price

    # Reject if final whole-share size doesn't meet the minimum.
    if pos_size_usd < min_size:
        return 0.0

    return pos_size_usd


# ==============================================================================
# ORDER EXECUTION
# ==============================================================================

def execute_buy_with_stop(symbol: str, size_usd: float, price: float, atr: float):
    """
    Buys a US equity via market order and places a defensive stop-loss beneath it.
    Includes a retry loop and idempotency keys to prevent ghost trades.

    Returns:
        tuple: (bool: success, float: stop_price)
    """
    # Gate 1: Exchange must be open
    if not is_market_open():
        logging.warning(f"🛑 Market is closed. Rejecting BUY for {symbol}.")
        return False, 0.0

    try:
        # Force whole shares. Alpaca rejects stop-losses on fractional shares.
        qty = math.floor(size_usd / price)

        if qty < 1:
            logging.warning(
                f"⚠️ ${size_usd:.2f} is not enough to buy 1 full share of "
                f"{symbol} at ${price:.2f}."
            )
            return False, 0.0

        # Generate unique IDs for this specific trade pair (buy + stop)
        trade_uuid = uuid.uuid4().hex[:8]
        buy_id  = f"sulla_buy_{symbol}_{trade_uuid}"
        stop_id = f"sulla_stop_{symbol}_{trade_uuid}"

        # --- Phase 1: Market Buy ---
        logging.info(f"Attempting to buy {qty} shares of {symbol} (ID: {buy_id})...")
        buy_order = trading_client.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=buy_id
            )
        )

        # --- Phase 1.5: Smart Wait Loop ---
        # Poll up to 15 times (30 seconds total) before giving up.
        # 10 seconds was too short for volatile opens where fills legitimately
        # take 15-25 seconds to confirm.
        order_filled = False
        max_retries  = 15
        status_check = None

        for attempt in range(max_retries):
            status_check = trading_client.get_order_by_id(buy_order.id)
            if status_check.status.value == 'filled':
                order_filled = True
                logging.info(f"✅ BUY confirmed filled: {qty} shares of {symbol}")
                break
            logging.warning(
                f"⏳ Order {status_check.status.value}. "
                f"Waiting 2s (Attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(2)

        if not order_filled:
            logging.error(
                f"❌ BUY order for {symbol} failed to fill after "
                f"{max_retries * 2}s. Status: {status_check.status.value if status_check else 'unknown'}"
            )
            return False, 0.0

        # --- Phase 2: Defensive Stop-Loss ---
        cfg       = config_manager.load_engine_config()
        stop_mult = cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)
        sl_price  = round(price - (atr * stop_mult), 2)

        if sl_price <= 0 or sl_price >= price:
            logging.error(
                f"❌ NAKED POSITION AVERTED: Invalid stop ${sl_price:.2f} for {symbol} "
                f"(price=${price:.2f}, atr={atr:.4f}, mult={stop_mult}). "
                f"Liquidating {qty} shares immediately."
            )
            try:
                trading_client.submit_order(
                    order_data=MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                        client_order_id=f"sulla_abort_{symbol}_{uuid.uuid4().hex[:8]}"
                    )
                )
            except Exception as abort_err:
                logging.error(f"❌ ABORT SELL FAILED for {symbol}: {abort_err}. Manual intervention required.")
            return False, 0.0

        trading_client.submit_order(
            order_data=StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=sl_price,
                client_order_id=stop_id
            )
        )
        logging.info(f"🛡️ STOP placed for {symbol}: {qty} shares at ${sl_price:.2f}")
        return True, sl_price

    except Exception as e:
        logging.error(f"⚠️ Execution Error on {symbol}: {e}")
        return False, 0.0


def execute_take_profit(symbol: str):
    """
    Mean Reversion / Liquidity Sweep exit: cancel open stops and market-sell
    the entire position.

    Returns:
        bool: success
    """
    if not is_market_open():
        return False

    try:
        _cancel_stops_for_symbol(symbol)
        time.sleep(1)

        positions = trading_client.get_all_positions()
        target = next((p for p in positions if p.symbol == symbol), None)

        if not target or float(target.qty) <= 0:
            return False

        tp_id = f"sulla_tp_{symbol}_{uuid.uuid4().hex[:8]}"

        trading_client.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                qty=float(target.qty),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=tp_id
            )
        )
        logging.info(
            f"💰 TAKE PROFIT: Sold {target.qty} shares of {symbol} (ID: {tp_id})"
        )
        return True

    except Exception as e:
        logging.error(f"⚠️ Take profit error on {symbol}: {e}")
        return False


def execute_ratchet_stop(symbol: str, new_stop_price: float):
    """
    Trend Following / Breakout exit: cancel the old stop and place a tighter one.
    If the new stop is rejected, attempt to restore the old price (Rescue Block).

    Returns:
        bool: success
    """
    try:
        old_stop_price = _get_current_stop_price(symbol)
        _cancel_stops_for_symbol(symbol)
        time.sleep(1)

        positions = trading_client.get_all_positions()
        target = next((p for p in positions if p.symbol == symbol), None)

        if not target or float(target.qty) <= 0:
            return False

        qty        = float(target.qty)
        ratchet_id = f"sulla_ratchet_{symbol}_{uuid.uuid4().hex[:8]}"

        try:
            trading_client.submit_order(
                order_data=StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.GTC,
                    stop_price=round(new_stop_price, 2),
                    client_order_id=ratchet_id
                )
            )
            logging.info(
                f"🛡️ RATCHET: {symbol} stop moved to ${new_stop_price:.2f} (ID: {ratchet_id})"
            )
            return True

        except Exception as e:
            logging.error(f"⚠️ New stop rejected for {symbol}: {e}")
            # RESCUE BLOCK: restore the old stop to prevent a naked position
            if old_stop_price and old_stop_price > 0:
                rescue_id = f"sulla_rescue_{symbol}_{uuid.uuid4().hex[:8]}"
                try:
                    trading_client.submit_order(
                        order_data=StopOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.GTC,
                            stop_price=round(old_stop_price, 2),
                            client_order_id=rescue_id
                        )
                    )
                    logging.info(
                        f"🔄 RESCUE: Restored old stop at ${old_stop_price:.2f} (ID: {rescue_id})"
                    )
                except Exception:
                    raise RuntimeError(
                        f"NAKED POSITION: {symbol} — stop cancel succeeded but both new stop "
                        f"and rescue restore failed. Manual intervention required."
                    )
            return False

    except Exception as e:
        logging.error(f"⚠️ Ratchet error on {symbol}: {e}")
        return False


def liquidate_all_positions():
    """
    Emergency kill switch: closes all positions and cancels all open orders.

    Returns:
        bool: success
    """
    try:
        trading_client.cancel_orders()
        time.sleep(1)
        trading_client.close_all_positions(cancel_orders=True)
        logging.info("🔴 KILL SWITCH: All positions liquidated, all orders cancelled.")
        return True
    except Exception as e:
        logging.error(f"❌ Kill switch error: {e}")
        return False


# ==============================================================================
# INTERNAL HELPERS
# ==============================================================================

def _cancel_stops_for_symbol(symbol: str):
    """Cancels all open stop orders for a specific symbol."""
    try:
        request = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[symbol]
        )
        orders = trading_client.get_orders(filter=request)
        for order in orders:
            if order.order_type.value in ('stop', 'stop_limit'):
                trading_client.cancel_order_by_id(order.id)
    except Exception as e:
        logging.error(f"⚠️ Failed to cancel stops for {symbol}: {e}")


def _get_current_stop_price(symbol: str) -> float:
    """Returns the stop price of the current open stop order for a symbol, or 0.0."""
    try:
        request = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[symbol]
        )
        orders = trading_client.get_orders(filter=request)
        for order in orders:
            if order.order_type.value in ('stop', 'stop_limit') and order.stop_price:
                return float(order.stop_price)
    except Exception as e:
        logging.error(f"⚠️ Failed to read stop for {symbol}: {e}")
    return 0.0
