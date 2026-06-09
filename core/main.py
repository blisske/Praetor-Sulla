"""
Ionic — Phase 4 engine.

Phase 3 (consensus + shadow trading + FX math) + Phase 3b (Telegram bot)
+ Phase 4 (macro calendar blackout via ForexFactory feed).

Per cycle:
  1. Heartbeat + restart-flag check (os._exit pattern)
  2. Hot-reload Config.yaml
  3. Shadow exit engine — stop hits + take-profits + trailing ratchet
  4. Parallel indicator fetch for all 7 majors
  5. Persist market_states
  6. Per-symbol 4-layer consensus (paradigm → supporting → score → AI)
  7. Shadow buy if all clear; debit synthetic cash; set ATR stop
  8. Sleep with mid-sleep flag wakeups

Concurrently:
  - Telegram bot polling for /indicators /report /pnl /buy /kill etc.
  - Trade-event notifications fire from within the exit engine + entry
    consensus paths via the module-level _bot reference.

No live Oanda order submission. Shadow contract enforced.
"""

import os
import sys
import time
import math
import signal
import random
import datetime
import asyncio
import logging
from html import escape as html_escape
from pathlib import Path

import pytz

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

import config_manager
import database
import market_data
import strategy
import ai_brain
import execution
import fx_math
import macro_calendar
import tuner

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ionic")

# ─── Paths + secrets ────────────────────────────────────────────────────────
HEARTBEAT_PATH    = Path(os.environ.get('HEARTBEAT_PATH',    '/app/data/.engine_heartbeat'))
RESTART_FLAG_PATH = Path(os.environ.get('RESTART_FLAG_PATH', '/app/data/.restart_engine'))
secrets = config_manager.load_secrets()

# ─── Module-level state ─────────────────────────────────────────────────────
_shutting_down = False
_bot = None             # Telegram Bot instance; None until main_async wires it
_kill_armed_at = 0.0    # Unix ts when /kill was sent; /confirm_kill must follow within 60s
_last_reveille_day = None  # date(year, month, day) of the last reveille; one per day
_daily_trend_cache: dict[str, dict] = {}   # symbol → {'trend': 'BULL'|'BEAR', 'date': iso}
_veto_last_notified_at: dict[tuple[str, str], float] = {}  # (symbol, paradigm) → unix ts;
                                                            # used to debounce BEARISH VETO
                                                            # notifications when the same setup
                                                            # re-fires every cycle.
KILL_WINDOW_SECONDS = 60
VETO_COOLDOWN_SECONDS = 3600   # 60 min between veto notifications per (symbol, paradigm).
                                # The LLM call + logger.info still happen each cycle; only the
                                # Telegram notification is debounced.


# Rotating flavor lines for the daily reveille. FX trades 24/5 (Sun 17:00 ET
# → Fri 17:00 ET), so the equity "OPENING BELL" framing doesn't fit — these
# are tuned for the global / 24-hour nature of the FX market. One picked at
# random each morning so the message doesn't get stale.
REVEILLE_LINES = [
    # Roman / imperial (consistent with the Foundation swarm naming)
    "The forum trades in seven tongues. Ionic listens to them all.",
    "Dawn over the empire. The ledger turns.",
    "Ave, Caesar. Another orbit complete.",
    "The legions march. The pips fall in line.",
    "While Rome slept, the markets moved. So did I.",
    "Tempus fugit. The majors endure.",
    "No rest for Caesar's machine.",
    # FX-native
    "London bid. New York offered. Ionic scanning.",
    "Three sessions, seven pairs, one engine.",
    "The dollar leg never sleeps. Neither do I.",
    "Twenty-four hours of liquidity. Five days of opportunity.",
    "Tokyo wakes. London takes. New York closes.",
    "The cross-currents are flowing. I'm reading the tape.",
    "Pip-by-pip, the spread between intent and execution.",
    "Carry trades carry. Ionic follows.",
    # Wry / dry
    "Somewhere, a candle is forming. I'll know.",
    "Reveille, citizen. Coffee optional. Vigilance mandatory.",
    "You slept. I did not.",
    "Still here. Still scanning. Still unimpressed by most setups.",
    "Markets digest yesterday's headlines so I don't have to.",
]


# ─── Signals + flag-file plumbing ───────────────────────────────────────────
def _handle_signal(signum, frame):
    global _shutting_down
    logger.info(f"Received signal {signum}; flagging shutdown.")
    _shutting_down = True


def _install_signal_handlers():
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except (ValueError, OSError):
        pass


def _check_restart_flag() -> bool:
    try:
        return RESTART_FLAG_PATH.exists()
    except Exception:
        return False


def _consume_restart_flag() -> None:
    try:
        RESTART_FLAG_PATH.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Could not delete restart flag {RESTART_FLAG_PATH}: {e}")


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.touch()
    except Exception as e:
        logger.error(f"Heartbeat touch failed: {e}")


async def _notify(html: str) -> None:
    """Best-effort Telegram notification. No-op when bot isn't wired."""
    if _bot is None:
        return
    try:
        await _bot.send_message(
            chat_id=secrets['telegram_user_id'],
            text=html,
            parse_mode='HTML',
        )
    except Exception as e:
        logger.warning(f"Telegram notify failed: {e}")


def _should_notify_veto(symbol: str, paradigm: str) -> bool:
    """
    Returns True when enough time has passed since the last veto notification
    for this (symbol, paradigm) pair. Same setup re-vetoing in successive
    cycles is suppressed to keep Telegram clean — the LLM call still runs and
    the veto still logs to stdout, but the notification is debounced.
    """
    now = time.time()
    last = _veto_last_notified_at.get((symbol, paradigm), 0.0)
    return (now - last) >= VETO_COOLDOWN_SECONDS


def _mark_veto_notified(symbol: str, paradigm: str) -> None:
    _veto_last_notified_at[(symbol, paradigm)] = time.time()


async def _get_daily_trend(symbol: str, config: dict) -> str:
    """
    Returns 'BULL' or 'BEAR' for the symbol's daily EMA9/EMA21 cross.
    Cached once per ET date so daily fetches don't fire every cycle.
    Returns 'BULL' (fail-open) on any data error — connectivity blips never
    block trading. Honors the mtf_filter.enabled toggle: when disabled the
    function returns 'BULL' immediately and skips the fetch entirely.

    The MTF gate inside strategy.check_entry_signals reads
    `indicators.get('daily_trend', 'BULL')`, so populating this field
    before the entry check is what makes the toggle actually do anything.
    """
    if not config.get('mtf_filter', {}).get('enabled', True):
        return 'BULL'
    today_iso = datetime.datetime.now(pytz.timezone('America/New_York')).date().isoformat()
    cached = _daily_trend_cache.get(symbol)
    if cached and cached.get('date') == today_iso:
        return cached['trend']
    try:
        d_daily = await market_data.fetch_indicators(symbol, config=config, timeframe='1d', limit=60)
    except Exception as e:
        logger.warning(f"[{symbol}] MTF daily fetch failed: {e} — failing open (BULL)")
        return 'BULL'
    if not d_daily:
        logger.warning(f"[{symbol}] MTF daily fetch returned None — failing open (BULL)")
        return 'BULL'
    trend = d_daily.get('trend', 'BULL')
    _daily_trend_cache[symbol] = {'trend': trend, 'date': today_iso}
    logger.info(f"[{symbol}] MTF Daily: trend={trend} (cached through {today_iso})")
    return trend


async def _maybe_send_reveille() -> None:
    """
    Daily "good morning" greeting. Fires once per calendar day after 07:30 in
    the user's local Mountain time IF the FX market is currently open. This
    matches Anton/Tiberius's once-per-day cadence so all three bots feel
    consistent — but Ionic's guard is FX-specific (closed Sat all-day,
    closed Sun before 17:00 ET, closed Fri after 17:00 ET) per
    execution.is_market_open().
    """
    global _last_reveille_day
    if _bot is None:
        return
    if not execution.is_market_open():
        return  # FX market closed (weekend) — no greeting

    mt_tz = pytz.timezone("America/Denver")
    now_mt = datetime.datetime.now(mt_tz)
    today = now_mt.date()

    # 07:30 MT is roughly 09:30 ET — by then NY session has been open ~90 min
    # and London/NY overlap is at its peak. The user's typical wake-up window.
    after_7_30 = (now_mt.hour > 7) or (now_mt.hour == 7 and now_mt.minute >= 30)
    if not after_7_30 or _last_reveille_day == today:
        return

    line = random.choice(REVEILLE_LINES)
    await _notify(
        f"📈 <b>DAILY REVEILLE</b>\n"
        f"{line}\n"
        f"Ionic is ONLINE and scanning the seven majors."
    )
    _last_reveille_day = today


# ─── Tuner-promotion check (called after every position close) ─────────────
def _try_promote_tunings(sym: str) -> None:
    """Wrapper around tuner.check_promotions(sym). Called after a position
    closes to see if any pending self-tuning parameter changes accumulated
    enough validation closes to promote. Logs each promotion. Never raises.
    """
    try:
        for msg in tuner.check_promotions(sym):
            logger.info(f"[{sym}] tuner: {msg}")
    except Exception as e:
        logger.warning(f"[{sym}] tuner.check_promotions failed: {e}")


# ─── Symbol parsing helpers ─────────────────────────────────────────────────
def _normalize_symbol(raw: str) -> str | None:
    """
    Convert user input ("eurusd", "EUR_USD", "eur/usd", "EUR/USD") to the
    canonical internal form "EUR/USD". Returns None if it doesn't look like
    a 6-letter FX pair.
    """
    s = raw.strip().upper().replace("/", "").replace("_", "").replace("-", "")
    if len(s) != 6 or not s.isalpha():
        return None
    return f"{s[:3]}/{s[3:]}"


# ─── Live position reconciliation ───────────────────────────────────────────
async def _reconcile_live_positions(open_db_positions: list[dict],
                                    latest_indicators: dict) -> None:
    """LIVE-MODE ONLY: detect DB positions that no longer exist on Oanda.

    Cases this catches:
      - Oanda's server-side attached stop fired (the most common case —
        engine sleeps between cycles, broker fires the stop, position is
        gone by the time we wake).
      - User manually closed via Oanda's web UI.
      - Margin call closed the position.

    For each missing position we log a `SELL (RECONCILED)` row using the
    current market price as an approximate close price. The realized P&L
    is approximate — for exact figures the user should pull from Oanda's
    own transaction history. The DB row is closed so the engine stops
    trying to manage a position that no longer exists.

    Returns nothing — side effects only.
    """
    try:
        client = market_data.get_client()
    except Exception:
        client = None
    if client is None:
        logger.warning("Live reconcile: OandaClient unavailable; skipping.")
        return

    try:
        live_positions = await asyncio.to_thread(client.get_open_positions)
    except Exception as e:
        logger.error(f"Live reconcile: could not fetch open positions: {e}")
        return

    live_instruments = {
        p.get("instrument", "").replace("_", "/")
        for p in live_positions
        # Only count positions that actually have units open (long or short)
        if (p.get("long", {}).get("units") not in (None, "0"))
        or (p.get("short", {}).get("units") not in (None, "0"))
    }

    for pos in open_db_positions:
        sym = pos['symbol']
        if sym in live_instruments:
            continue  # still open on Oanda — no action

        # DB says open, Oanda says closed — reconcile.
        entry_price = pos['entry_price']
        entry_strat = pos['strategy']
        units       = pos.get('shares', 0.0)
        d           = latest_indicators.get(sym, {})
        close_price = d.get('price', entry_price)  # fall back to entry if no data

        # Approximate P&L from current price (NOT exact — Oanda's records
        # are authoritative for the actual fill price).
        pnl_usd = fx_math.realized_pnl_usd(sym, units, entry_price, close_price)
        pnl_pct = ((close_price - entry_price) / entry_price * 100) if entry_price else 0.0
        verdict = (f'RECONCILED: position no longer on Oanda. '
                   f'Approximate P&L from current price: {pnl_pct:+.2f}%. '
                   f'See Oanda transaction history for exact.')
        database.log_trade(sym, 'SELL', close_price, round(pnl_usd, 2),
                           entry_strat, verdict)
        database.close_open_position(sym)
        _try_promote_tunings(sym)
        dir_emoji = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < 0 else "⚪")
        logger.info(
            f"[{sym}] {dir_emoji} RECONCILED CLOSE | {entry_strat} · "
            f"~{pnl_pct:+.2f}% (approx ${pnl_usd:+.2f}) — "
            f"check Oanda for exact fill price/P&L."
        )
        await _notify(
            f"🔄 <b>POSITION RECONCILED</b> {sym}\n"
            f"{dir_emoji} {entry_strat} · ~{pnl_pct:+.2f}% "
            f"({'+' if pnl_usd >= 0 else ''}${pnl_usd:,.2f} approx)\n"
            f"<i>Closed by Oanda (likely server-side stop). "
            f"Check Oanda transaction history for exact fill.</i>"
        )


# ─── Exit engine ────────────────────────────────────────────────────────────
async def _run_exit_engine(config: dict, latest_indicators: dict,
                           shadow_mode: bool = True) -> None:
    """
    Iterates every open position and checks for stop hits, take-profits,
    or trailing-stop ratchets. Notifies Telegram on every close.

    shadow_mode=True  (default): all side effects hit the synthetic ledger
                                 in database.py — no Oanda calls.
    shadow_mode=False (live):    take-profits and ratchets call Oanda via
                                 execution.execute_take_profit /
                                 execute_ratchet_stop. STOP HITS in live
                                 mode are handled by Oanda's server-side
                                 attached stop — this function skips the
                                 stop-check and relies on the periodic
                                 reconciliation pass (see
                                 _reconcile_live_positions).
    """
    open_positions = database.get_all_open_positions()
    if not open_positions:
        return

    # In live mode, reconcile first: detect positions that closed via
    # Oanda's server-side attached stop (or external close) so we don't
    # try to take-profit a phantom position next.
    if not shadow_mode:
        try:
            await _reconcile_live_positions(open_positions, latest_indicators)
        except Exception as e:
            logger.error(f"Live reconciliation failed: {e}", exc_info=True)
        # Refresh — positions may have been closed by reconciliation
        open_positions = database.get_all_open_positions()
        if not open_positions:
            return

    for pos in open_positions:
        sym = pos['symbol']
        d = latest_indicators.get(sym)
        if not d:
            continue

        entry_price = pos['entry_price']
        entry_strat = pos['strategy']
        units       = pos.get('shares', 0.0)
        cur_stop    = pos.get('current_stop') or 0.0
        price       = d['price']
        atr         = d['atr']

        # ── A. Stop hit ────────────────────────────────────────────────────
        # Shadow mode: synthetic check against DB stop.
        # Live mode: skip — Oanda's server-side attached stop has already
        # fired (or hasn't yet), and reconciliation above will have closed
        # the DB row if Oanda did fill. We never simulate stop hits in live.
        # Stop-hit realism (2026-06-09 audit): trigger on the bar LOW so an
        # intrabar wick through the stop registers (Oanda's server-side stop
        # would have filled live), and fill at min(stop, close) so a gap
        # through the stop fills at the worse market price instead of a
        # fantasy fill at the stop. Mirrors Pantheon's shadow engine.
        if shadow_mode and cur_stop > 0 and min(d.get('low', price), price) <= cur_stop:
            fill = min(cur_stop, price)
            pnl_usd = fx_math.realized_pnl_usd(sym, units, entry_price, fill)
            pnl_pct = ((fill - entry_price) / entry_price * 100) if entry_price else 0.0
            verdict = f'STOP HIT: {pnl_pct:.2f}%'
            database.log_trade(sym, 'SHADOW SELL', fill, round(pnl_usd, 2),
                               entry_strat, verdict)
            # Credit = entry notional (what was debited) + realized P&L. The
            # old notional-at-exit credit was identical for USD-quote pairs
            # but returned exactly the debit for USD-base, erasing P&L from
            # the cash ledger.
            database.adjust_shadow_cash(
                fx_math.position_notional_usd(sym, units, entry_price) + pnl_usd)
            database.close_open_position(sym)
            _try_promote_tunings(sym)
            dir_emoji = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < 0 else "⚪")
            logger.info(
                f"[{sym}] {dir_emoji} SHADOW STOP HIT | {entry_strat} · "
                f"{pnl_pct:+.2f}% (${pnl_usd:+.2f}) · "
                f"{fx_math.fp(entry_price, sym)}→{fx_math.fp(cur_stop, sym)}"
            )
            await _notify(
                f"🛑 <b>SHADOW STOP HIT</b> {sym}\n"
                f"{dir_emoji} {entry_strat} · {pnl_pct:+.2f}% "
                f"({'+' if pnl_usd >= 0 else ''}${pnl_usd:,.2f}) · "
                f"${fx_math.fp(entry_price, sym)}→${fx_math.fp(cur_stop, sym)}"
            )
            continue

        # ── B. Take profit / paradigm exit ─────────────────────────────────
        partial_taken = bool(pos.get('partial_exits_taken', 0))
        try:
            exit_cmd = strategy.check_exit_signals(
                d, entry_strat, cur_stop, entry_price=entry_price, config=config,
                partial_exit_taken=partial_taken,
            )
        except Exception as e:
            logger.error(f"[{sym}] check_exit_signals failed: {e}")
            exit_cmd = {'action': 'HOLD'}

        # Partial profit-take: sell `sell_pct` (default 50%), trail the rest.
        # If unit math floors to 0 or to the full position, the partial is skipped
        # and full TAKE_PROFIT fires on the next eligible cycle.
        if exit_cmd.get('action') == 'PARTIAL_TAKE_PROFIT':
            sell_pct        = exit_cmd.get('sell_pct', 50.0) / 100.0
            units_to_sell   = math.floor(units * sell_pct + 1e-9)
            if units_to_sell < 1 or units_to_sell >= units:
                logger.info(f"[{sym}] PARTIAL TP skipped: {units} × {sell_pct:.0%} → {units_to_sell} (degenerate)")
            else:
                remaining_units = units - units_to_sell
                pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price else 0.0
                remaining_size_usd = fx_math.position_notional_usd(sym, remaining_units, entry_price)
                ppt_cfg = config.get('strategy', {}).get('partial_profit_taking', {})
                new_stop = entry_price if ppt_cfg.get('move_stop_to_breakeven', True) else None

                if shadow_mode:
                    partial_pnl_usd = fx_math.realized_pnl_usd(sym, units_to_sell, entry_price, price)
                    database.log_trade(
                        sym, 'SHADOW PARTIAL SELL', price, round(partial_pnl_usd, 2),
                        entry_strat, f'PARTIAL TP: {pnl_pct:.2f}% on {sell_pct*100:.0f}%',
                        position_size_usd=fx_math.position_notional_usd(sym, units_to_sell, entry_price),
                    )
                    database.adjust_shadow_cash(
                        fx_math.position_notional_usd(sym, units_to_sell, entry_price) + partial_pnl_usd)
                    database.mark_partial_exit(sym, remaining_units, remaining_size_usd, new_stop=new_stop)
                    logger.info(
                        f"[{sym}] 💵 SHADOW PARTIAL TP | {entry_strat} · sold {units_to_sell:,} units "
                        f"@ +{pnl_pct:.2f}% (${partial_pnl_usd:+.2f}) | {remaining_units:,} remain"
                    )
                    await _notify(
                        f"💵 <b>SHADOW PARTIAL TP</b> {sym}\n"
                        f"🟢 {entry_strat} · sold {units_to_sell:,} units @ +{pnl_pct:.2f}% "
                        f"(${partial_pnl_usd:+.2f})\n"
                        + (f"Stop → ${fx_math.fp(new_stop, sym)} (BE), trailing {remaining_units:,} to upper BB"
                           if new_stop else "")
                    )
                else:
                    # LIVE: close PART of the position via Oanda. Stop on the
                    # remaining units stays attached server-side. If BE-move
                    # requested, push the new stop via ratchet right after.
                    ok, pl_usd, fee_usd = await asyncio.to_thread(
                        execution.execute_partial_take_profit, sym, units_to_sell,
                    )
                    if not ok:
                        logger.warning(
                            f"[{sym}] live PARTIAL TP failed — will retry next cycle."
                        )
                        continue
                    database.log_trade(
                        sym, 'PARTIAL SELL', price, round(pl_usd, 2),
                        entry_strat,
                        f'PARTIAL TP: {pnl_pct:.2f}% on {sell_pct*100:.0f}% (Oanda P&L ${pl_usd:+.2f})',
                        position_size_usd=fx_math.position_notional_usd(sym, units_to_sell, entry_price),
                        fee_usd=fee_usd,
                    )
                    database.mark_partial_exit(sym, remaining_units, remaining_size_usd, new_stop=new_stop)
                    # Push BE stop to Oanda for the surviving units
                    if new_stop is not None and new_stop > cur_stop:
                        live_ok = await asyncio.to_thread(
                            execution.execute_ratchet_stop, sym, new_stop
                        )
                        if not live_ok:
                            logger.warning(
                                f"[{sym}] partial TP: post-partial stop ratchet to "
                                f"${fx_math.fp(new_stop, sym)} REJECTED — server-side stop stale."
                            )
                    logger.info(
                        f"[{sym}] 💵 LIVE PARTIAL TP | {entry_strat} · sold {units_to_sell:,} units "
                        f"@ +{pnl_pct:.2f}% (${pl_usd:+.2f}, fee ${fee_usd:.2f}) | {remaining_units:,} remain"
                    )
                    await _notify(
                        f"💵 <b>LIVE PARTIAL TP</b> {sym}\n"
                        f"🟢 {entry_strat} · sold {units_to_sell:,} units @ +{pnl_pct:.2f}% "
                        f"(${pl_usd:+.2f}, fee ${fee_usd:.2f})\n"
                        + (f"Stop → ${fx_math.fp(new_stop, sym)} (BE), trailing {remaining_units:,}"
                           if new_stop else "")
                    )
            continue

        if exit_cmd.get('action') == 'TAKE_PROFIT':
            pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price else 0.0
            if shadow_mode:
                pnl_usd = fx_math.realized_pnl_usd(sym, units, entry_price, price)
                verdict = f'TAKE PROFIT: {pnl_pct:.2f}%'
                database.log_trade(sym, 'SHADOW SELL', price, round(pnl_usd, 2),
                                   entry_strat, verdict)
                database.adjust_shadow_cash(
                    fx_math.position_notional_usd(sym, units, entry_price) + pnl_usd)
                database.close_open_position(sym)
                _try_promote_tunings(sym)
                logger.info(
                    f"[{sym}] 🟢 SHADOW TAKE PROFIT | {entry_strat} · "
                    f"+{pnl_pct:.2f}% (${pnl_usd:+.2f}) · "
                    f"{fx_math.fp(entry_price, sym)}→{fx_math.fp(price, sym)}"
                )
                await _notify(
                    f"💰 <b>SHADOW TAKE PROFIT</b> {sym}\n"
                    f"🟢 {entry_strat} · +{pnl_pct:.2f}% "
                    f"(+${pnl_usd:,.2f}) · "
                    f"${fx_math.fp(entry_price, sym)}→${fx_math.fp(price, sym)}"
                )
            else:
                # LIVE: close the position at market via Oanda.
                ok, pl_usd, fee_usd = await asyncio.to_thread(
                    execution.execute_take_profit, sym
                )
                if not ok:
                    logger.warning(
                        f"[{sym}] live TP failed — will retry next cycle. "
                        f"(Oanda close rejected; position likely still open.)"
                    )
                    continue
                verdict = f'TAKE PROFIT: {pnl_pct:.2f}% (Oanda P&L ${pl_usd:+.2f})'
                database.log_trade(sym, 'SELL', price, round(pl_usd, 2),
                                   entry_strat, verdict, fee_usd=fee_usd)
                database.close_open_position(sym)
                _try_promote_tunings(sym)
                logger.info(
                    f"[{sym}] 🟢 LIVE TAKE PROFIT | {entry_strat} · "
                    f"+{pnl_pct:.2f}% (${pl_usd:+.2f}, fee ${fee_usd:.2f}) · "
                    f"{fx_math.fp(entry_price, sym)}→{fx_math.fp(price, sym)}"
                )
                await _notify(
                    f"💰 <b>LIVE TAKE PROFIT</b> {sym}\n"
                    f"🟢 {entry_strat} · +{pnl_pct:.2f}% "
                    f"(${pl_usd:+,.2f}, fee ${fee_usd:.2f}) · "
                    f"${fx_math.fp(entry_price, sym)}→${fx_math.fp(price, sym)}"
                )
            continue

        # ── B2. Regime-shift tightening ────────────────────────────────────
        # TF/VB position whose regime flipped to RANGING: lock in gains with a
        # 1× ATR stop, floored at entry so we never tighten into a locked loss.
        # Preempts the normal trailing ratchet, which uses a wider multiplier.
        # Parity with Tiberius core/main.py:1387.
        if exit_cmd.get('action') == 'HOLD_AND_TIGHTEN':
            tight_sl = max(entry_price, price - atr)
            if tight_sl > cur_stop and tight_sl < price:
                # Always update DB stop so the dashboard sees it
                database.update_shadow_stop(sym, tight_sl)
                # In live, also push the new stop to Oanda
                if not shadow_mode:
                    live_ok = await asyncio.to_thread(
                        execution.execute_ratchet_stop, sym, tight_sl
                    )
                    if not live_ok:
                        logger.warning(
                            f"[{sym}] HOLD_AND_TIGHTEN: Oanda stop modify "
                            f"failed; DB stop updated but server-side stop is stale."
                        )
                logger.info(
                    f"[{sym}] HOLD_AND_TIGHTEN: stop "
                    f"{fx_math.fp(cur_stop, sym)} → {fx_math.fp(tight_sl, sym)} "
                    f"(regime→RANGING, BE floor)"
                )
                await _notify(
                    f"⚠️ <b>REGIME SHIFT — STOP TIGHTENED</b> {sym}\n"
                    f"{entry_strat} · ${fx_math.fp(cur_stop, sym)} → "
                    f"${fx_math.fp(tight_sl, sym)} (1× ATR, BE floor)"
                )
            continue

        # ── C. Trailing ratchet ────────────────────────────────────────────
        # Use config_manager helper so the London/NY overlap volatility
        # window (configured via ratchet.power_hour_defense) widens stops
        # automatically when active.
        trail_mult = config_manager.get_ratchet_multiplier(config)
        new_stop = price - (atr * trail_mult)
        if cur_stop > 0 and new_stop > cur_stop:
            database.update_shadow_stop(sym, new_stop)
            if not shadow_mode:
                live_ok = await asyncio.to_thread(
                    execution.execute_ratchet_stop, sym, new_stop
                )
                if not live_ok:
                    logger.warning(
                        f"[{sym}] ratchet: Oanda stop modify failed; "
                        f"DB stop updated but server-side stop is stale."
                    )
            logger.info(
                f"[{sym}] ratchet: stop "
                f"{fx_math.fp(cur_stop, sym)} → {fx_math.fp(new_stop, sym)}"
            )


# ─── Pyramiding add ─────────────────────────────────────────────────────────
async def _evaluate_pyramid_add(
    sym: str, d: dict, config: dict, shadow_cash: float,
    shadow_equity: float, py_cfg: dict,
    shadow_mode: bool = True,
) -> None:
    """
    Adds a leg to an existing position when pyramiding conditions pass.
    Only TREND FOLLOWING and VOLATILITY BREAKOUT positions pyramid —
    Mean Reversion and Liquidity Sweep are counter-trend by design, and
    adding to a winner there means doubling down against the entry thesis.

    Trigger: price must have advanced by `trigger_atr_mult × last_leg_atr`
    from the last leg's price AND the current setup must still match the
    original paradigm. Leg sizing decays geometrically:
        leg_units = base_units × (size_decay ^ existing_leg_count)
    so leg 2 is half-size, leg 3 quarter-size, etc.

    Skips silently when conditions aren't met. Logs at INFO when a leg
    is added; the existing position's stop is preserved (this is the
    operator's earned protection from the original entry).
    """
    pos = database.get_open_position(sym)
    if not pos:
        return

    paradigm = pos.get('strategy', '')
    if paradigm not in ('TREND FOLLOWING', 'VOLATILITY BREAKOUT'):
        return

    max_legs_cfg = py_cfg.get('max_legs', {})
    if isinstance(max_legs_cfg, dict):
        max_legs = max_legs_cfg.get(
            'trend_following' if paradigm == 'TREND FOLLOWING' else 'volatility_breakout',
            3 if paradigm == 'TREND FOLLOWING' else 2,
        )
    else:
        max_legs = int(max_legs_cfg or 3)
    leg_count = int(pos.get('leg_count') or 1)
    if leg_count >= max_legs:
        return

    trigger_atr_mult = float(py_cfg.get('trigger_atr_mult', 1.0))
    last_leg_price   = float(pos.get('last_leg_price') or pos.get('entry_price') or 0.0)
    last_leg_atr     = float(pos.get('last_leg_atr')   or pos.get('entry_atr')   or 0.0)
    if last_leg_price <= 0 or last_leg_atr <= 0:
        return
    if d['price'] < last_leg_price + (last_leg_atr * trigger_atr_mult):
        return

    d['symbol'] = sym
    try:
        is_setup, current_paradigm = strategy.check_entry_signals(d, config=config)
    except Exception as e:
        logger.warning(f"[{sym}] pyramid signal check raised: {e} — skipping leg add")
        return
    if not is_setup or current_paradigm != paradigm:
        return

    initial_stop_mult = config.get('ratchet', {}).get('initial_stop_mult', 2.0)
    risk_cfg = config.get('risk', {})
    base_units, _ = execution.calculate_position_units(
        equity_usd=shadow_equity, atr=d['atr'], entry_price=d['price'],
        symbol=sym, risk_config=risk_cfg, stop_mult_override=initial_stop_mult,
    )
    if base_units <= 0:
        return
    size_decay = float(py_cfg.get('leg_decay_factor', py_cfg.get('size_decay', 0.5)))
    leg_units = int(base_units * (size_decay ** leg_count))
    if leg_units <= 0:
        logger.info(
            f"[{sym}] pyramid leg #{leg_count+1} sized to 0 units after decay — skipping"
        )
        return

    leg_notional = fx_math.position_notional_usd(sym, leg_units, d['price'])
    if leg_notional > shadow_cash:
        logger.info(
            f"[{sym}] pyramid leg #{leg_count+1} needs ${leg_notional:,.2f} "
            f"but shadow cash is ${shadow_cash:,.2f} — skipping"
        )
        return

    # In LIVE mode, submit the leg to Oanda FIRST. Only if it fills do we
    # record the leg in the DB (we don't want a phantom leg if the Oanda
    # order is rejected). Reuse the existing position's stop_price for
    # the new leg — Oanda attaches it server-side to the new fill.
    fill_price = d['price']
    fee_usd    = 0.0
    if not shadow_mode:
        existing_stop = float(pos.get('current_stop') or 0.0)
        if existing_stop <= 0:
            logger.warning(
                f"[{sym}] pyramid leg #{leg_count+1}: existing position has "
                f"no current_stop set — refusing live add without a stop."
            )
            return
        ok, fill_price, fee_usd = await asyncio.to_thread(
            execution.execute_buy_with_stop, sym, leg_units, existing_stop,
        )
        if not ok:
            logger.warning(
                f"[{sym}] live pyramid leg #{leg_count+1} REJECTED by Oanda — "
                f"no DB leg created."
            )
            return

    added = database.add_pyramid_leg(
        symbol=sym, leg_price=fill_price, leg_atr=d['atr'], leg_shares=leg_units,
    )
    if not added:
        return

    if shadow_mode:
        database.log_trade(
            sym, 'SHADOW BUY', d['price'], leg_units, paradigm,
            f"PYRAMID LEG #{leg_count+1}/{max_legs} · base={base_units} units · "
            f"decay={size_decay}^{leg_count}",
        )
        database.adjust_shadow_cash(-leg_notional)
        action_tag = "SHADOW"
    else:
        database.log_trade(
            sym, 'BUY ADD', fill_price, leg_units, paradigm,
            f"PYRAMID LEG #{leg_count+1}/{max_legs} · base={base_units} units · "
            f"decay={size_decay}^{leg_count} · live",
            fee_usd=fee_usd,
        )
        action_tag = "LIVE"

    logger.info(
        f"[{sym}] 🟢 {action_tag} PYRAMID LEG #{leg_count+1}/{max_legs} | "
        f"{paradigm} · {leg_units:,} units @ {fx_math.fp(fill_price, sym)} | "
        f"notional ${leg_notional:,.2f}"
        + (f" | fee ${fee_usd:.2f}" if not shadow_mode else "")
    )
    icons = {"TREND FOLLOWING": "📈", "VOLATILITY BREAKOUT": "🚀"}
    icon = icons.get(paradigm, "🎯")
    badge = "👻" if shadow_mode else "🔴"
    await _notify(
        f"{badge} {icon} <b>{action_tag} PYRAMID LEG #{leg_count+1}/{max_legs}</b>\n"
        f"Pair: {sym}\n"
        f"Strategy: {paradigm}\n"
        f"Units: {leg_units:,} · Entry: ${fx_math.fp(fill_price, sym)} · "
        f"Notional: ${leg_notional:,.2f}"
        + (f" · Fee: ${fee_usd:.2f}" if not shadow_mode else "")
    )


# ─── Entry consensus + buy (shadow or live) ─────────────────────────────────
async def _evaluate_entry(sym: str, d: dict, config: dict,
                          open_symbols: set[str], shadow_cash: float,
                          shadow_equity: float,
                          shadow_mode: bool = True) -> None:
    # If a position already exists for this symbol, we either pyramid into it
    # (if pyramiding is enabled and the trigger conditions pass) or skip.
    if sym in open_symbols:
        py_cfg = config.get('pyramiding') or {}
        if py_cfg.get('enabled', False):
            await _evaluate_pyramid_add(sym, d, config, shadow_cash,
                                        shadow_equity, py_cfg,
                                        shadow_mode=shadow_mode)
        return

    # ── Macro-event blackout (Phase 4) ───────────────────────────────────
    # Block new entries if any high-impact macro event lands within the
    # configured window on EITHER currency in the pair. The check is cheap
    # (in-memory cache) so it sits before the paradigm evaluation.
    macro_cfg = config.get('macro_blackout', {})
    if macro_cfg.get('enabled', True):
        in_blackout, event = macro_calendar.get_blackout_status(sym, macro_cfg)
        if in_blackout and event:
            logger.info(
                f"[{sym}] MACRO BLACKOUT: {event.get('country')} "
                f"{event.get('title')} @ {event.get('date')} ({event.get('impact')}) "
                f"— skipping entry"
            )
            return

    d['symbol'] = sym
    try:
        is_setup, paradigm = strategy.check_entry_signals(d, config=config)
    except Exception as e:
        logger.error(f"[{sym}] check_entry_signals failed: {e}")
        return
    if not is_setup:
        return

    sup_score, sup_reasons = strategy.check_supporting_signals(d, paradigm, config=config)
    consensus_score = 1 + sup_score
    cons_cfg        = config.get('consensus', {})
    min_consensus   = cons_cfg.get('min_consensus_score', 3)

    if consensus_score < min_consensus:
        logger.info(
            f"[{sym}] CONSENSUS FAIL: {consensus_score}/{min_consensus} "
            f"({paradigm}) | {' | '.join(sup_reasons)}"
        )
        return

    llm_cfg     = config.get('ai_agent', {}).get('sentiment_analysis', {})
    llm_base    = llm_cfg.get('api_base')
    model_id    = llm_cfg.get('model_id')
    use_reason  = cons_cfg.get('llm_reasoning', True)
    brave_key   = secrets.get('brave_api_key')
    bear_abort  = cons_cfg.get('bearish_abort', True)

    if not (brave_key and llm_base and model_id):
        logger.warning(f"[{sym}] AI layer not configured — skipping entry")
        return

    # Per-bar AI verdict cache (2026-06-09 Tier 2): one consult per
    # (symbol, paradigm) per 1h bar. Re-consulting every 5-min cycle re-rolled
    # the dice at sampling temperature — a borderline setup got ~12 draws/bar
    # and entered on the first non-BEARISH, polluting the veto measurement
    # and burning Brave/GPU. Offline verdicts are never cached.
    _vc = globals().setdefault('_AI_VERDICT_CACHE', {})
    _vk = (sym, paradigm)
    _vhit = _vc.get(_vk)
    if _vhit and time.monotonic() < _vhit[0]:
        is_bullish, verdict_str, verdict_body = _vhit[1]
        logger.info(f"[{sym}] AI verdict (cached): {verdict_str}")
    else:
        try:
            is_bullish, verdict_str, verdict_body = await ai_brain.get_ai_consensus(
                symbol=sym, price=d['price'], strategy_type=paradigm,
                indicators=d, supporting_reasons=sup_reasons,
                brave_key=brave_key, llm_base_url=llm_base, model_id=model_id,
                use_reasoning=use_reason,
            )
        except Exception as e:
            logger.error(f"[{sym}] AI verdict raised: {e}")
            return
        if 'AI GATE OFFLINE' not in verdict_body:
            _vc[_vk] = (time.monotonic() + 3600, (is_bullish, verdict_str, verdict_body))

    if bear_abort and verdict_str.startswith('BEARISH'):
        logger.info(f"[{sym}] BEARISH VETO ({paradigm}): aborting entry")
        # Audit trail (2026-06-09): persist every AI veto as a BEARISH ABORT
        # row (amount=0 — excluded from all %SELL% accounting) so the veto's
        # hit-rate is measurable. Only Doric logged these; the trading-logic
        # audit had zero Ionic abort data to judge the AI layer with.
        database.log_trade(sym, 'BEARISH ABORT', d['price'], 0, paradigm,
                           f"[SCORE:{consensus_score}/{min_consensus}] {verdict_body}"[:300])
        # Tier 2 ghost ledger: track what the vetoed entry would have done
        # under the real exit rules (counterfactuals.py).
        try:
            from core import counterfactuals
        except ImportError:
            import counterfactuals
        counterfactuals.open_ghost(sym, paradigm, verdict_str, d, config)
        # Notify only if outside the cooldown window — same setup re-vetoing in
        # successive cycles would otherwise spam Telegram (USD/JPY 19:36 / 19:47
        # / 19:53 etc.). Full verdict body, no truncation; the get_ai_consensus
        # layer already caps at 3500 chars which fits inside Telegram's 4096
        # message limit with the header prefix.
        if _should_notify_veto(sym, paradigm):
            await _notify(
                f"🚫 <b>BEARISH VETO</b> {sym} | {paradigm}\n"
                f"{html_escape(verdict_body)}"
            )
            _mark_veto_notified(sym, paradigm)
        return
    # 2+1+1 (Design Pillar 2): the BEARISH veto above is the ONLY AI gate.
    # BULLISH / NEUTRAL / AI-offline(→NEUTRAL) all PASS — AI is a veto layer,
    # NOT a required bullish confirmation. Gating on is_bullish here silently
    # froze every entry whenever Gemma returned NEUTRAL or was unreachable
    # (the 0-trades-for-7-days class of bug). Log the non-bullish pass for
    # observability, but proceed.
    if not is_bullish:
        logger.info(f"[{sym}] AI {verdict_str} ({paradigm}) — not BEARISH, proceeding")

    risk_cfg = config.get('risk', {})
    initial_stop_mult = config.get('ratchet', {}).get('initial_stop_mult', 2.0)
    units, stop_price = execution.calculate_position_units(
        equity_usd=shadow_equity, atr=d['atr'], entry_price=d['price'],
        symbol=sym, risk_config=risk_cfg, stop_mult_override=initial_stop_mult,
    )
    if units <= 0:
        logger.info(f"[{sym}] sizing returned 0 units — skipping")
        return

    notional = fx_math.position_notional_usd(sym, units, d['price'])
    if notional > shadow_cash:
        logger.info(
            f"[{sym}] insufficient shadow cash: need ${notional:,.2f}, "
            f"have ${shadow_cash:,.2f}"
        )
        return

    enriched_verdict = (
        f"[SCORE:{consensus_score}/{min_consensus} | {' | '.join(sup_reasons)}] "
        f"{verdict_body}"
    )

    icons = {
        "TREND FOLLOWING":     "📈",
        "MEAN REVERSION":      "🧲",
        "VOLATILITY BREAKOUT": "🚀",
        "LIQUIDITY SWEEP":     "🐋",
    }
    icon = icons.get(paradigm, "🎯")
    sentiment_tag = "🟢 BULLISH" if is_bullish else f"🔴 {verdict_str}"

    if shadow_mode:
        # ── SHADOW: synthetic ledger only ──
        database.log_trade(sym, 'SHADOW BUY', d['price'], units, paradigm, enriched_verdict,
                           position_size_usd=notional)
        database.record_open_position(
            symbol=sym, entry_price=d['price'], strategy=paradigm,
            entry_atr=d['atr'], shares=units, position_size_usd=notional,
        )
        database.update_shadow_stop(sym, stop_price)
        database.adjust_shadow_cash(-notional)
        logger.info(
            f"[{sym}] 🟢 SHADOW BUY | {paradigm} · {units:,} units @ "
            f"{fx_math.fp(d['price'], sym)} | stop {fx_math.fp(stop_price, sym)} | "
            f"notional ${notional:,.2f}"
        )
        await _notify(
            f"👻 {icon} <b>SHADOW BUY</b>\n"
            f"Pair: {sym}\n"
            f"Strategy: {paradigm}\n"
            f"Units: {units:,} · Entry: ${fx_math.fp(d['price'], sym)} · "
            f"Stop: ${fx_math.fp(stop_price, sym)}\n"
            f"Notional: ${notional:,.2f}\n"
            f"Consensus: {consensus_score}/{min_consensus} | {' | '.join(sup_reasons)}\n"
            f"AI Sentiment: {sentiment_tag}\n\n"
            f"{html_escape(verdict_body)}"
        )
        return

    # ── LIVE: submit MARKET FOK to Oanda with attached stop ──
    ok, fill_price, fee_usd = await asyncio.to_thread(
        execution.execute_buy_with_stop, sym, units, stop_price,
    )
    if not ok:
        logger.warning(
            f"[{sym}] live BUY failed (Oanda rejected or did not fill); "
            f"no DB position created. Will retry next eligible cycle."
        )
        await _notify(
            f"⚠️ <b>LIVE BUY REJECTED</b> {sym}\n"
            f"{paradigm} · {units:,} units · "
            f"Oanda did not fill — see engine logs."
        )
        return

    # Record DB position at the ACTUAL fill price (may differ slightly from
    # the indicator-cycle price by 1-2 pips of slippage). Stop is whatever
    # Oanda accepted server-side (we sent stop_price; if it adjusted to
    # instrument precision it's effectively the same).
    database.log_trade(sym, 'BUY', fill_price, units, paradigm, enriched_verdict,
                       position_size_usd=notional, fee_usd=fee_usd)
    database.record_open_position(
        symbol=sym, entry_price=fill_price, strategy=paradigm,
        entry_atr=d['atr'], shares=units, position_size_usd=notional,
    )
    database.update_shadow_stop(sym, stop_price)
    # NOTE: live mode does NOT call adjust_shadow_cash — the real cash is
    # tracked on Oanda; the shadow_cash field becomes informational only
    # (the dashboard will diverge from Oanda balance — Phase 5 will reconcile).
    logger.info(
        f"[{sym}] 🟢 LIVE BUY | {paradigm} · {units:,} units @ "
        f"{fx_math.fp(fill_price, sym)} (req {fx_math.fp(d['price'], sym)}) | "
        f"stop {fx_math.fp(stop_price, sym)} | fee ${fee_usd:.2f} | "
        f"notional ${notional:,.2f}"
    )
    await _notify(
        f"🔴 {icon} <b>LIVE BUY</b>\n"
        f"Pair: {sym}\n"
        f"Strategy: {paradigm}\n"
        f"Units: {units:,} · Fill: ${fx_math.fp(fill_price, sym)} · "
        f"Stop: ${fx_math.fp(stop_price, sym)}\n"
        f"Notional: ${notional:,.2f} · Fee: ${fee_usd:.2f}\n"
        f"Consensus: {consensus_score}/{min_consensus} | {' | '.join(sup_reasons)}\n"
        f"AI Sentiment: {sentiment_tag}\n\n"
        f"{html_escape(verdict_body)}"
    )


# ─── Trading cycle ──────────────────────────────────────────────────────────
async def _run_cycle(config: dict, symbols: list[str], timeframe: str) -> None:
    if not symbols:
        logger.warning("active_symbols is empty — nothing to scan this cycle.")
        return

    tasks = [
        market_data.fetch_indicators(sym, config=config, timeframe=timeframe)
        for sym in symbols
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    latest: dict[str, dict] = {}
    for sym, d in zip(symbols, results):
        if isinstance(d, Exception):
            logger.error(f"[{sym}] fetch raised: {d}")
            continue
        if d is None:
            continue
        latest[sym] = d
        logger.info(
            f"[{sym}] {fx_math.fp(d['price'], sym)} | {d['regime']} | "
            f"ADX={d['adx']:.1f} | RSI={d['rsi']:.1f} | Trend={d['trend']}"
        )
        try:
            database.log_market_state(
                symbol=sym, price=d['price'], adx=d['adx'],
                regime=d['regime'], trend=d['trend'], rsi=d['rsi'],
                volume=d.get('volume'), avg_volume=d.get('avg_volume'),
            )
        except Exception as e:
            logger.error(f"[{sym}] log_market_state failed: {e}")

    shadow_mode = config.get('oanda', {}).get('shadow_mode', True)

    # In live mode, refresh the Oanda account cache once per cycle so the
    # dashboard + /api/equity show real broker-side NAV instead of the
    # synthetic shadow_cash (which diverges from Oanda once live trading
    # begins). Best-effort — if Oanda call fails, the dashboard falls
    # back to shadow math (UI flags it as 'live-fallback-shadow').
    if not shadow_mode:
        try:
            client = market_data.get_client()
            if client is not None:
                acct = await asyncio.to_thread(client.get_account)
                a = acct.get('account', {}) if isinstance(acct, dict) else {}
                database.upsert_live_account_cache(
                    nav            = float(a.get('NAV', 0)             or 0),
                    balance        = float(a.get('balance', 0)         or 0),
                    unrealized_pl  = float(a.get('unrealizedPL', 0)    or 0),
                    margin_used    = float(a.get('marginUsed', 0)      or 0),
                    margin_avail   = float(a.get('marginAvailable', 0) or 0),
                    open_trades    = int(a.get('openTradeCount', 0)    or 0),
                    currency       = str(a.get('currency', 'USD')),
                )
        except Exception as e:
            logger.warning(f"Oanda account-cache refresh failed: {e}")

    try:
        await _run_exit_engine(config, latest, shadow_mode=shadow_mode)
    except Exception as e:
        logger.error(f"Exit engine failed: {e}", exc_info=True)

    # Tier 2: manage open AI-veto ghosts with this cycle's data.
    try:
        from core import counterfactuals
    except ImportError:
        import counterfactuals
    for _gsym, _gd in latest.items():
        counterfactuals.update_ghosts_for_symbol(_gsym, _gd, config)

    if not config.get('strategy', {}).get('autonomous_mode', True):
        return
    if not execution.is_market_open():
        return

    open_symbols = {p['symbol'] for p in database.get_all_open_positions()}
    snap = database.get_shadow_account_state(latest_prices={
        s: d['price'] for s, d in latest.items()
    })
    shadow_cash   = snap.get('cash', 0.0)
    shadow_equity = snap.get('equity', 0.0)

    # --- SESSION-START EQUITY CAPTURE ---
    # Baseline for the daily session-loss circuit below. Captured once per ET
    # calendar day; resets daily_halt at the rollover. (FX rolls at 17:00 ET;
    # ET-midnight is a close-enough daily-loss boundary and matches Doric's
    # mechanism — refine to a 17:00 cut later if the soak shows it matters.)
    _et_today = datetime.datetime.now(pytz.timezone('America/New_York')).date().isoformat()
    _rs0 = database.get_risk_state()
    if _rs0.get('session_date') != _et_today:
        database.update_risk_state(
            session_date=_et_today,
            session_start_equity=shadow_equity,
            daily_halt=False,
        )
        logger.info(f"[SESSION] New session {_et_today}: start equity ${shadow_equity:,.2f}")

    # --- TIERED DRAWDOWN STATE MACHINE ---
    # Peak-based: NORMAL → ALERT → DERISK → HALT, with hysteresis on DERISK exit.
    # Parity with Anton/Tiberius. HALT cleared only by /resume.
    peak_equity = database.get_equity_peak()
    if peak_equity == 0.0 or shadow_equity > peak_equity:
        database.update_equity_peak(shadow_equity)
        peak_equity = shadow_equity

    drawdown_pct = max(0.0, (peak_equity - shadow_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
    risk_cfg     = config.get('risk', {})
    alert_pct    = risk_cfg.get('drawdown_alert_pct',    8.0)
    derisk_pct   = risk_cfg.get('drawdown_derisk_pct',   15.0)
    halt_pct     = risk_cfg.get('drawdown_halt_pct',     25.0)
    recovery_pct = risk_cfg.get('drawdown_recovery_pct', 10.0)

    if drawdown_pct >= halt_pct:
        target_mode = 'HALT'
    elif drawdown_pct >= derisk_pct:
        target_mode = 'DERISK'
    elif drawdown_pct >= alert_pct:
        target_mode = 'ALERT'
    else:
        target_mode = 'NORMAL'

    risk_state = database.get_risk_state()
    prev_mode  = risk_state.get('risk_mode', 'NORMAL')
    # Hysteresis: stay in DERISK until drawdown shrinks below recovery_pct
    if prev_mode == 'DERISK' and target_mode in ('NORMAL', 'ALERT') and drawdown_pct >= recovery_pct:
        target_mode = 'DERISK'
    # HALT is sticky — only /resume can clear it
    if prev_mode == 'HALT' and target_mode != 'HALT':
        target_mode = 'HALT'

    if target_mode != prev_mode:
        database.update_risk_state(risk_mode=target_mode)
        icon = {'NORMAL':'✅', 'ALERT':'⚠️', 'DERISK':'🟡', 'HALT':'🚨'}[target_mode]
        logger.warning(f"RISK MODE: {prev_mode} → {target_mode} (drawdown {drawdown_pct:.1f}%)")
        msg = (f"{icon} <b>RISK MODE → {target_mode}</b>\n"
               f"Drawdown <b>{drawdown_pct:.1f}%</b> from peak "
               f"(${peak_equity:,.2f} → ${shadow_equity:,.2f})")
        if target_mode == 'DERISK':
            mult = risk_cfg.get('derisk_size_multiplier', 0.5)
            msg += f"\nPosition sizing × {mult} until recovery to {recovery_pct}%"
        elif target_mode == 'HALT':
            msg += "\n⛔ Trading halted. Open stops active. Use /resume after review."
        elif target_mode == 'NORMAL' and prev_mode in ('DERISK', 'ALERT'):
            msg += "\nFull sizing restored."
        await _notify(msg)

    # HALT short-circuits new entries. Open positions keep ratcheting through
    # the shadow exit engine that already ran above.
    if target_mode == 'HALT':
        return

    # ── DAILY SESSION LOSS CIRCUIT ─────────────────────────────────────────
    # Independent of the all-time-peak ladder above: blocks NEW entries after a
    # hard intraday loss whose drawdown doesn't breach the all-time-peak halt
    # (e.g. -X% since session open but still only -5% from the all-time peak).
    # Auto-clears at the next ET session (capture above); /resume also clears.
    # Open positions keep their normal exit logic. This was a NO-OP in Ionic
    # until now — daily_halt was read in /report + cleared by /resume but
    # NOTHING ever set it. (QC 2026-06-08; ports Doric's inline circuit.)
    if not risk_state.get('daily_halt') and risk_state.get('session_start_equity'):
        sse       = risk_state['session_start_equity']
        daily_dd  = (sse - shadow_equity) / sse * 100 if sse > 0 else 0.0
        daily_lim = risk_cfg.get('daily_session_loss_pct', 3.0)
        if daily_dd >= daily_lim:
            database.update_risk_state(daily_halt=True)
            risk_state['daily_halt'] = True
            logger.warning(f"DAILY SESSION LOSS: {daily_dd:.2f}% from session start — entries blocked")
            await _notify(
                f"🛑 <b>DAILY SESSION LOSS LIMIT</b>\n"
                f"Session down <b>{daily_dd:.2f}%</b> from open "
                f"(${sse:,.2f} → ${shadow_equity:,.2f}).\n"
                f"Limit: {daily_lim}%. New entries blocked for the rest of the session.\n"
                f"Open positions follow normal exit logic. Auto-resumes next session."
            )
    if risk_state.get('daily_halt'):
        return

    # DERISK halves the effective equity passed to sizing — math is equivalent
    # to halving both risk_per_trade_pct and position_size_max_pct (Tiberius's
    # explicit knobs in execution.py). Both surfaces scale linearly with equity.
    if target_mode == 'DERISK':
        derisk_mult   = risk_cfg.get('derisk_size_multiplier', 0.5)
        shadow_equity = shadow_equity * derisk_mult
        logger.info(f"DERISK active — sizing equity scaled to ${shadow_equity:,.2f} (× {derisk_mult})")

    max_open = config.get('strategy', {}).get('max_open_trades', 5)
    if len(open_symbols) >= max_open:
        logger.info(f"At max open trades ({len(open_symbols)}/{max_open}); skipping entry scan.")
        return

    for sym, d in latest.items():
        if _shutting_down or _check_restart_flag():
            return
        if len(open_symbols) >= max_open:
            break
        # Populate daily_trend so the MTF gate in check_entry_signals can
        # actually fire. Without this, the gate reads the 'BULL' default
        # and effectively never blocks. Cached per-day to avoid hammering
        # Oanda's daily endpoint every cycle.
        try:
            d['daily_trend'] = await _get_daily_trend(sym, config)
        except Exception as e:
            logger.warning(f"[{sym}] daily_trend lookup failed: {e} — defaulting to BULL")
            d['daily_trend'] = 'BULL'
        try:
            await _evaluate_entry(sym, d, config, open_symbols, shadow_cash,
                                  shadow_equity, shadow_mode=shadow_mode)
        except Exception as e:
            logger.error(f"[{sym}] entry evaluation failed: {e}", exc_info=True)
        open_symbols = {p['symbol'] for p in database.get_all_open_positions()}
        shadow_cash  = database.get_shadow_cash()


# ════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ════════════════════════════════════════════════════════════════════════════
def _auth(update: Update) -> bool:
    return str(update.effective_user.id) == secrets['telegram_user_id']


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    msg = (
        "📖 <b>Ionic — Command Reference</b>\n\n"
        "<b>Status &amp; Reporting</b>\n"
        "• /indicators — regime / RSI / ADX / trend for all 7 majors\n"
        "• /report — portfolio audit (equity, holdings, defense)\n"
        "• /pnl — shadow P&amp;L report (per-pair + summary)\n"
        "• /calendar — upcoming high-impact macro events (next 48h; "
        "<code>/calendar 168</code> for a full week)\n\n"
        "<b>Manual Trade Control</b>\n"
        "• /buy PAIR USD — manual buy with auto stop-loss "
        "(e.g. <code>/buy EUR/USD 1000</code>)\n\n"
        "<b>Safety</b>\n"
        "• /kill → /confirm_kill — two-step emergency liquidation\n"
        "• /resume — clear drawdown halt\n"
        "• /restart — queue a clean engine restart\n\n"
        "<b>AI Sentiment</b>\n"
        "• Type a pair (e.g. <code>EUR/USD</code>) for an ad-hoc AI analysis"
    )
    await update.message.reply_html(msg)


async def cmd_indicators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')
    symbols = cfg.get('strategy', {}).get('active_symbols', [])
    if not symbols:
        await update.message.reply_text("No active_symbols configured.")
        return

    await update.message.reply_html("🚦 <b>FX Cycle Report</b>")
    tasks   = [market_data.fetch_indicators(s, config=cfg, timeframe=tf) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    data    = {s: d for s, d in zip(symbols, results)
               if not isinstance(d, Exception) and d is not None}

    blocks = []
    for sym in symbols:
        d = data.get(sym)
        if not d:
            blocks.append(f"<b>{sym}</b> — <i>⚠️ no data</i>")
            continue
        if d['regime'] == "TRENDING":
            r_emoji = "🔥"
            t_emoji = "🟢" if d['trend'] == "BULL" else "🔴"
            primary = f"{t_emoji} EMA Trend ({d['trend']})"
        else:
            r_emoji = "🧊"
            mid_d = (d['price'] - d['bb_middle']) / d['bb_middle'] * 100
            loc = "below" if d['price'] < d['bb_middle'] else "above"
            primary = f"🟡 {abs(mid_d):.1f}% {loc} mid BB"

        rsi_tag = ""
        if d['rsi'] < 35:   rsi_tag = " 🔻 oversold"
        elif d['rsi'] > 65: rsi_tag = " 🔺 overbought"

        d['symbol'] = sym
        is_setup, strat_type = strategy.check_entry_signals(d, config=cfg)
        lines = [
            f"<b>{sym}</b> — ${fx_math.fp(d['price'], sym)}",
            f"• Regime — {r_emoji} {d['regime']} (ADX {d['adx']:.1f})",
            f"• Signal — {primary}",
            f"• RSI — {d['rsi']:.1f}{rsi_tag} · ATR {fx_math.fp(d['atr'], sym)}",
        ]
        if is_setup:
            lines.append(f"• 🚀 <b>Brewing</b> — {strat_type}")
        blocks.append("\n".join(lines))

    await update.message.reply_html("\n\n".join(blocks))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    cfg = config_manager.load_engine_config()
    max_trades = cfg.get('strategy', {}).get('max_open_trades', 5)
    shadow_mode = cfg.get('oanda', {}).get('shadow_mode', True)

    # Use the cached prices we have in the open positions; for a freshly-loaded
    # dashboard request we'd ideally re-fetch, but for the report use what's in
    # market_states most recently.
    # In live mode, get_account_state pulls real Oanda NAV from the cache
    # (refreshed each cycle in main loop); falls back to shadow math if cache
    # is stale.
    snap = database.get_account_state(shadow_mode=shadow_mode)
    equity = snap.get('equity', 0.0)
    cash   = snap.get('cash',   0.0)
    held   = snap.get('held_assets', {})
    source = snap.get('source', 'shadow')

    peak_eq = database.get_equity_peak() or 0.0
    dd_pct  = max(0.0, (peak_eq - equity) / peak_eq * 100) if peak_eq > 0 else 0.0
    risk_state = database.get_risk_state()
    risk_icons = {'NORMAL':'✅', 'ALERT':'⚠️', 'DERISK':'🟡', 'HALT':'🚨'}
    risk_icon = risk_icons.get(risk_state.get('risk_mode', 'NORMAL'), '✅')

    mode_tag = "👻 SHADOW" if shadow_mode else "🔴 LIVE"
    if source == 'live-fallback-shadow':
        mode_tag += " <i>(Oanda cache stale — showing shadow math)</i>"
    sections = [f"📊 <b>Command Report</b>  ·  {mode_tag}"]

    acct_lines = [
        f"• <b>Equity</b> — ${equity:,.2f}",
        f"• <b>Mode</b> — {risk_icon} {risk_state.get('risk_mode', 'NORMAL')}",
        f"• <b>Cash</b> — ${cash:,.2f}",
    ]
    initial = database.get_shadow_initial_capital()
    if initial > 0:
        pnl_pct = (equity - initial) / initial * 100
        acct_lines.append(f"• <b>Total P&amp;L</b> — {pnl_pct:+.2f}% (from ${initial:,.0f})")
    if peak_eq > 0 and dd_pct > 0:
        acct_lines.append(f"• <b>Drawdown</b> — {dd_pct:.1f}% from peak ${peak_eq:,.2f}")
    elif peak_eq > 0:
        acct_lines.append("• <b>Drawdown</b> — none (at peak)")
    acct_lines.append(f"• <b>Exposure</b> — {len(held)} / {max_trades} open")
    if risk_state.get('daily_halt'):
        acct_lines.append("• 🛑 <b>Daily Halt</b> — entries blocked for the session")
    sections.append("<b>Account</b>\n" + "\n".join(acct_lines))

    all_pos = database.get_all_open_positions()
    if not all_pos:
        sections.append("<b>Open Positions</b>\n<i>None</i>")
    else:
        lines = []
        for p in all_pos:
            sym       = p['symbol']
            units     = p.get('shares', 0.0)
            entry     = p.get('entry_price', 0.0)
            strat_lbl = p.get('strategy', '?')
            lines.append(
                f"• <b>{sym}</b> — {units:,.0f} units @ ${fx_math.fp(entry, sym)} "
                f"({strat_lbl})"
            )
        sections.append("<b>Open Positions</b>\n" + "\n".join(lines))

    defense_lines = []
    for p in all_pos:
        sym  = p['symbol']
        stop = p.get('current_stop') or 0.0
        if stop > 0:
            defense_lines.append(f"• <b>{sym}</b> — Stop ${fx_math.fp(stop, sym)}")
        else:
            defense_lines.append(f"• ⚠️ <b>{sym}</b> — <i>NAKED</i>")
    if defense_lines:
        sections.append("<b>Active Defense</b>\n" + "\n".join(defense_lines))

    # Macro blackout — surface any currently-firing event windows so the
    # operator knows why /pnl might be quiet despite a juicy setup on screen.
    macro_cfg = cfg.get('macro_blackout', {})
    if macro_cfg.get('enabled', True):
        active_symbols = cfg.get('strategy', {}).get('active_symbols', [])
        blackout_lines = []
        seen_events = set()
        for sym in active_symbols:
            in_b, ev = macro_calendar.get_blackout_status(sym, macro_cfg)
            if in_b and ev:
                event_key = (ev.get('country'), ev.get('title'), ev.get('date'))
                if event_key in seen_events:
                    continue
                seen_events.add(event_key)
                blackout_lines.append(
                    f"• ⏸ {ev.get('country')} · {ev.get('title')} "
                    f"({ev.get('impact')}) @ {ev.get('date')}"
                )
        if blackout_lines:
            sections.append("<b>Active Macro Blackout</b>\n" + "\n".join(blackout_lines))

    await update.message.reply_html("\n\n".join(sections))


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    all_trades = database.get_closed_trades()
    if not all_trades:
        await update.message.reply_text("No closed shadow trades yet.")
        return

    cfg = config_manager.load_engine_config()
    min_trades = cfg.get('tuning', {}).get('min_trades_to_tune', 10)

    stats: dict = {}
    for t in all_trades:
        s = stats.setdefault(t['symbol'], {'wins': 0, 'gross_win': 0.0,
                                            'gross_loss': 0.0, 'pnls': [], 'usd': 0.0})
        pnl = t['pnl_pct']
        s['pnls'].append(pnl)
        s['usd'] += t.get('pnl_usd', 0.0)
        if pnl > 0:
            s['wins'] += 1
            s['gross_win'] += pnl
        else:
            s['gross_loss'] += abs(pnl)

    sections = ["📊 <b>Shadow P&amp;L Report</b>"]
    by_sym = []
    for sym, s in sorted(stats.items()):
        n   = len(s['pnls'])
        wr  = s['wins'] / n * 100
        pf  = (s['gross_win'] / s['gross_loss']) if s['gross_loss'] > 0 else float('inf')
        avg = sum(s['pnls']) / n
        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
        usd_str = f"{'+' if s['usd'] >= 0 else ''}${s['usd']:,.2f}"
        by_sym.append(f"• <b>{sym}</b> — {n} trades · WR {wr:.0f}% · "
                       f"PF {pf_str} · Avg {avg:+.1f}% · {usd_str}")
    sections.append("<b>By Pair</b>\n" + "\n".join(by_sym))

    total_trades = sum(len(v['pnls']) for v in stats.values())
    total_wins   = sum(v['wins'] for v in stats.values())
    overall_wr   = total_wins / total_trades * 100 if total_trades else 0.0
    net_usd      = sum(v['usd'] for v in stats.values())
    net_emoji    = "🟢" if net_usd > 0 else ("🔴" if net_usd < 0 else "⚪")
    best_sym, best_data = max(stats.items(), key=lambda x: len(x[1]['pnls']))
    best_count = len(best_data['pnls'])
    summary = [
        f"• <b>Total Trades</b> — {total_trades}",
        f"• <b>Overall WR</b> — {overall_wr:.0f}%",
        f"• <b>Net P&amp;L</b> — {net_emoji} {'+' if net_usd >= 0 else ''}${net_usd:,.2f}",
    ]
    if best_count >= min_trades:
        summary.append(f"• ✅ {best_sym} has {best_count} closes — tuning cycle eligible")
    else:
        summary.append(f"• ⏳ {min_trades - best_count} more closes needed on {best_sym} "
                        f"for first tuning cycle")
    sections.append("<b>Summary</b>\n" + "\n".join(summary))

    tuning_log = database.get_tuning_summary()
    if tuning_log:
        tlines = [f"• <b>{e['symbol']}</b>/{e['parameter']} — "
                  f"{e['old_value']}→{e['new_value']} [{e['status']}]"
                  for e in tuning_log[:5]]
        sections.append("🔬 <b>Recent Tuning Activity</b>\n" + "\n".join(tlines))

    await update.message.reply_html("\n\n".join(sections))


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/buy PAIR USD — manual buy, bypasses consensus.

    Routes through the same shadow vs live branch as the autonomous loop:
    - shadow_mode=True  → synthetic ledger entry only
    - shadow_mode=False → execute_buy_with_stop submits to Oanda with
                          attached stop. DB row uses actual fill price.
    """
    if not _auth(update): return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /buy EUR/USD 1000")
        return
    sym_raw, usd_raw = context.args[0], context.args[1]
    sym = _normalize_symbol(sym_raw)
    if sym is None:
        await update.message.reply_text(f"❌ Unrecognized FX pair: {sym_raw}")
        return
    try:
        amount_usd = float(usd_raw)
    except ValueError:
        await update.message.reply_text(f"❌ Invalid USD amount: {usd_raw}")
        return
    if not execution.is_market_open():
        await update.message.reply_text("🛑 FX market is closed (weekend). No new entries.")
        return

    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')
    d   = await market_data.fetch_indicators(sym, config=cfg, timeframe=tf)
    if not d:
        await update.message.reply_text(f"❌ Couldn't fetch data for {sym}")
        return

    # Manual sizing: caller specifies USD; convert to units via fx_math
    entry = d['price']
    if fx_math.quote_currency(sym) == "USD":
        units = int(amount_usd / entry)
    else:
        units = int(amount_usd)  # USD-base: 1 unit = $1
    if units <= 0:
        await update.message.reply_text(
            f"❌ ${amount_usd:.2f} can't buy 1 full unit of {sym} at "
            f"${fx_math.fp(entry, sym)}"
        )
        return

    initial_stop_mult = cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)
    stop_price = entry - (d['atr'] * initial_stop_mult)
    notional   = fx_math.position_notional_usd(sym, units, entry)
    shadow_mode = cfg.get('oanda', {}).get('shadow_mode', True)

    if shadow_mode:
        cash = database.get_shadow_cash()
        if notional > cash:
            await update.message.reply_text(
                f"⚠️ Insufficient shadow cash (${cash:,.2f}) for ${notional:,.2f} buy."
            )
            return

        database.log_trade(sym, 'SHADOW BUY', entry, units, 'MANUAL OVERRIDE',
                           'Manual user /buy (shadow mode)',
                           position_size_usd=notional)
        database.record_open_position(sym, entry, 'MANUAL OVERRIDE',
                                      entry_atr=d['atr'], shares=units,
                                      position_size_usd=notional)
        database.update_shadow_stop(sym, stop_price)
        database.adjust_shadow_cash(-notional)
        await update.message.reply_html(
            f"👻 <b>SHADOW MANUAL BUY</b>\n"
            f"Pair: {sym}\n"
            f"Units: {units:,} @ ${fx_math.fp(entry, sym)}\n"
            f"Stop: ${fx_math.fp(stop_price, sym)}\n"
            f"Notional: ${notional:,.2f}"
        )
        return

    # ── LIVE manual buy ──
    ok, fill_price, fee_usd = await asyncio.to_thread(
        execution.execute_buy_with_stop, sym, units, stop_price,
    )
    if not ok:
        await update.message.reply_text(
            f"❌ LIVE BUY {sym} rejected by Oanda. See engine logs for details."
        )
        return
    database.log_trade(sym, 'BUY', fill_price, units, 'MANUAL OVERRIDE',
                       'Manual user /buy (live mode)',
                       position_size_usd=notional, fee_usd=fee_usd)
    database.record_open_position(sym, fill_price, 'MANUAL OVERRIDE',
                                  entry_atr=d['atr'], shares=units,
                                  position_size_usd=notional)
    database.update_shadow_stop(sym, stop_price)
    await update.message.reply_html(
        f"🔴 <b>LIVE MANUAL BUY</b>\n"
        f"Pair: {sym}\n"
        f"Units: {units:,} @ ${fx_math.fp(fill_price, sym)} "
        f"(req ${fx_math.fp(entry, sym)})\n"
        f"Stop: ${fx_math.fp(stop_price, sym)} · Fee: ${fee_usd:.2f}\n"
        f"Notional: ${notional:,.2f}"
    )


async def cmd_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /apply [PAIR] — Authorize a pending stop-loss ratchet. Parity stub with
    Tiberius. Ionic's shadow exit engine auto-ratchets on each cycle today,
    so there's nothing queued for manual approval. Stays in the command set
    for live-mode parity once Oanda execution wires in operator-confirmation
    flow for real stop-order moves.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return
    await update.message.reply_html(
        "🔄 <b>No pending ratchets</b>\n"
        "Ionic auto-applies trailing-stop moves in shadow mode. This command "
        "becomes meaningful once live Oanda execution is wired and stop "
        "ratchets require operator confirmation."
    )


async def cmd_protect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /protect — Scan open positions and bootstrap protective stops on any row
    that has current_stop = 0 (naked). In shadow mode the stop is written to
    open_positions.current_stop in the DB; the exit engine reads it from
    there on the next cycle. Live mode (post-Oanda integration) will also
    place a real stop order via the Oanda v20 REST API.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    cfg = config_manager.load_engine_config()
    shadow_mode = cfg.get('oanda', {}).get('shadow_mode', True)
    stop_mult   = cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)
    tf          = cfg.get('strategy', {}).get('timeframe', '1h')

    await update.message.reply_text("🛡️ Scanning open positions for naked stops...")

    positions = database.get_all_open_positions()
    if not positions:
        await update.message.reply_text("No open positions to protect.")
        return

    protected = []
    skipped   = []
    for pos in positions:
        sym  = pos['symbol']
        cur_stop = pos.get('current_stop') or 0.0
        if cur_stop > 0:
            skipped.append(f"{sym} (already at {cur_stop:.5f})")
            continue
        try:
            d = await market_data.fetch_indicators_async(sym, timeframe=tf)
        except Exception as e:
            skipped.append(f"{sym} (fetch failed: {e})")
            continue
        if not d or 'atr' not in d:
            skipped.append(f"{sym} (no ATR)")
            continue
        sl_price = round(d['price'] - (d['atr'] * stop_mult), 5)
        if sl_price <= 0 or sl_price >= d['price']:
            skipped.append(f"{sym} (invalid stop)")
            continue
        database.update_shadow_stop(sym, sl_price)
        # LIVE: also push the stop to Oanda so the protection is real, not
        # just a DB-side hint. Falls back to DB-only if Oanda rejects.
        live_label = ""
        if not shadow_mode:
            live_ok = await asyncio.to_thread(
                execution.execute_ratchet_stop, sym, sl_price
            )
            if live_ok:
                live_label = " (Oanda stop set)"
            else:
                live_label = " (DB only — Oanda modify failed; see logs)"
        protected.append(f"{sym} @ {sl_price:.5f}{live_label}")

    msg = ["🛡️ <b>Protection Scan Complete</b>"]
    if protected:
        msg.append(f"<b>Stops set ({len(protected)}):</b>")
        msg.extend(f"  • {p}" for p in protected)
    if skipped:
        msg.append(f"<b>Skipped ({len(skipped)}):</b>")
        msg.extend(f"  • {s}" for s in skipped)
    if not protected and not skipped:
        msg.append("All positions already protected.")
    if not shadow_mode:
        msg.append("<i>Live mode: stops pushed to Oanda via modify_trade_stop. "
                   "Server-side protection.</i>")

    await update.message.reply_html("\n".join(msg))


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Arm two-step emergency liquidation. /confirm_kill within 60s to execute."""
    global _kill_armed_at
    if not _auth(update): return
    _kill_armed_at = time.time()
    open_count = len(database.get_all_open_positions())
    await update.message.reply_html(
        "🚨 <b>EMERGENCY KILL SWITCH ARMED</b> 🚨\n\n"
        f"Will close all <b>{open_count}</b> open shadow positions at market.\n"
        f"Send <code>/confirm_kill</code> within {KILL_WINDOW_SECONDS}s to proceed."
    )


async def cmd_confirm_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Two-step confirm — close all open positions at market.

    Shadow mode: synthetic close at current market price, log SHADOW SELL.
    Live mode: real Oanda close_position for each instrument, log SELL with
    actual P&L + fee from Oanda's close transaction.
    """
    global _kill_armed_at
    if not _auth(update): return
    if _kill_armed_at == 0 or (time.time() - _kill_armed_at) > KILL_WINDOW_SECONDS:
        await update.message.reply_text(
            f"❌ Kill switch not armed (or window expired). Send /kill first."
        )
        return
    _kill_armed_at = 0

    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')
    shadow_mode = cfg.get('oanda', {}).get('shadow_mode', True)

    if shadow_mode:
        await update.message.reply_html("👻 <b>EXECUTING SHADOW KILL...</b>")
    else:
        await update.message.reply_html(
            "🔴 <b>EXECUTING LIVE KILL — closing all positions on Oanda...</b>"
        )

    closed = 0
    failures: list[str] = []
    for pos in database.get_all_open_positions():
        sym = pos['symbol']
        try:
            if shadow_mode:
                d = await market_data.fetch_indicators(sym, config=cfg, timeframe=tf)
                if not d:
                    continue
                price = d['price']
                entry = pos['entry_price']
                units = pos.get('shares', 0.0)
                pnl_usd = fx_math.realized_pnl_usd(sym, units, entry, price)
                pnl_pct = ((price - entry) / entry * 100) if entry else 0.0
                database.log_trade(sym, 'SHADOW SELL', price, round(pnl_usd, 2),
                                   pos.get('strategy', '?'),
                                   f'KILL SWITCH: {pnl_pct:.1f}%')
                database.adjust_shadow_cash(
                    fx_math.position_notional_usd(sym, units, entry) + pnl_usd)
                database.close_open_position(sym)
                _try_promote_tunings(sym)
                closed += 1
            else:
                # LIVE: close the position at market via Oanda. Use the same
                # execute_take_profit helper since it does exactly this and
                # returns (success, pl_usd, fee_usd).
                ok, pl_usd, fee_usd = await asyncio.to_thread(
                    execution.execute_take_profit, sym
                )
                if not ok:
                    failures.append(sym)
                    logger.error(f"[{sym}] kill switch live close failed.")
                    continue
                # Use current Oanda fill price for the log row's price field —
                # but we don't have it directly; fall back to entry+pl estimate.
                # Most common: the actual fill price will be close to current
                # market and pl_usd is authoritative regardless.
                entry = pos['entry_price']
                units = pos.get('shares', 0.0)
                # Approximate fill from pl: if pl=units*(close-entry)*pip_value,
                # we can back-solve. But for simplicity use entry as the log price
                # and rely on pl_usd as the source of truth (Oanda-authoritative).
                database.log_trade(sym, 'SELL', entry, round(pl_usd, 2),
                                   pos.get('strategy', '?'),
                                   f'LIVE KILL SWITCH (Oanda P&L ${pl_usd:+.2f})',
                                   fee_usd=fee_usd)
                database.close_open_position(sym)
                _try_promote_tunings(sym)
                closed += 1
        except Exception as e:
            failures.append(sym)
            logger.error(f"[{sym}] kill close failed: {e}", exc_info=True)

    mode_tag = "SHADOW" if shadow_mode else "LIVE"
    msg = f"✅ <b>{mode_tag} KILL COMPLETE.</b> {closed} position(s) closed."
    if failures:
        msg += f"\n⚠️ Failed to close: {', '.join(failures)} — check Oanda manually."
    await update.message.reply_html(msg)


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear drawdown halt — re-checks DD first; refuses if still over threshold."""
    if not _auth(update): return
    cfg = config_manager.load_engine_config()
    risk_cfg = cfg.get('risk', {})
    halt_pct = risk_cfg.get('drawdown_halt_pct', 25.0)

    snap = database.get_shadow_account_state()
    equity = snap.get('equity', 0.0)
    peak = database.get_equity_peak() or 0.0
    dd_pct = max(0.0, (peak - equity) / peak * 100) if peak > 0 else 0.0

    if dd_pct >= halt_pct:
        await update.message.reply_html(
            f"❌ <b>Resume refused.</b>\n"
            f"Drawdown is {dd_pct:.1f}% (limit {halt_pct:.0f}%). Wait for recovery."
        )
        return
    database.update_risk_state(risk_mode='NORMAL', daily_halt=0)
    await update.message.reply_html(
        f"✅ <b>Resumed.</b>\n"
        f"Drawdown {dd_pct:.1f}% under {halt_pct:.0f}% threshold. risk_mode → NORMAL."
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _auth(update): return
    try:
        RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_FLAG_PATH.touch()
        await update.message.reply_html(
            "🔄 <b>Restart queued</b>\n"
            "The engine will exit at the next cycle and compose will respawn it."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Could not write restart flag: {e}")


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upcoming macro events (high-impact only by default), next 48 hours."""
    if not _auth(update): return
    cfg = config_manager.load_engine_config()
    macro_cfg = cfg.get('macro_blackout', {})
    symbols = cfg.get('strategy', {}).get('active_symbols', [])
    if not symbols:
        await update.message.reply_text("No active_symbols configured.")
        return

    # Allow optional `/calendar <hours>` override
    hours = 48
    if context.args:
        try:
            hours = max(1, min(168, int(context.args[0])))
        except ValueError:
            pass

    events = macro_calendar.upcoming_events(symbols, macro_cfg, look_ahead_hours=hours)
    sections = [f"📅 <b>Macro Calendar</b>  ·  next {hours}h "
                f"(impact ≥ {macro_cfg.get('importance_min', 'High')})"]

    if not events:
        sections.append(
            "<i>No events at the configured impact threshold for the watchlist "
            f"currencies in the next {hours}h.</i>"
        )
        status = macro_calendar.cache_status()
        if status['fetched_at']:
            from datetime import datetime as _dt
            age_min = (status['age_seconds'] or 0) / 60
            sections.append(
                f"<i>Calendar refreshed {age_min:.0f} min ago — "
                f"{status['events']} events in cache.</i>"
            )
        if status['last_error']:
            sections.append(f"<i>⚠️ Last fetch error: {status['last_error']}</i>")
        await update.message.reply_html("\n\n".join(sections))
        return

    # Group by day so a busy week reads cleanly
    from collections import defaultdict
    by_day = defaultdict(list)
    for e in events:
        local_day = e['time_utc'].astimezone().strftime("%a %b %d")
        by_day[local_day].append(e)

    for day, day_events in by_day.items():
        lines = [f"<b>{day}</b>"]
        for e in day_events:
            local_time = e['time_utc'].astimezone().strftime("%H:%M")
            impact_emoji = "🔴" if e['impact'] == "High" else "🟡" if e['impact'] == "Medium" else "⚪"
            lines.append(
                f"  {impact_emoji} <b>{local_time}</b> · {e['currency']} · "
                f"{e['title']}"
                + (f"  (fcst {e['forecast']}, prev {e['previous']})"
                   if e['forecast'] != '—' or e['previous'] != '—' else "")
            )
        sections.append("\n".join(lines))

    sections.append(
        f"<i>Entry blackout fires {macro_cfg.get('minutes_before', 60)} min before "
        f"and {macro_cfg.get('minutes_after', 120)} min after each event.</i>"
    )
    await update.message.reply_html("\n\n".join(sections))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Plain text = pair name → ad-hoc AI sentiment."""
    if not _auth(update): return
    sym = _normalize_symbol(update.message.text)
    if sym is None:
        await update.message.reply_text(
            "Send a 6-letter FX pair like <code>EURUSD</code> or "
            "<code>EUR/USD</code> for an AI sentiment query.",
            parse_mode='HTML',
        )
        return
    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')
    d   = await market_data.fetch_indicators(sym, config=cfg, timeframe=tf)
    if not d:
        await update.message.reply_text(f"❌ Couldn't fetch data for {sym}")
        return
    llm_cfg = cfg.get('ai_agent', {}).get('sentiment_analysis', {})
    is_bull, verdict_str, body = await ai_brain.get_ai_consensus(
        symbol=sym, price=d['price'], strategy_type='MANUAL',
        indicators=d, supporting_reasons=['Manual sentiment query'],
        brave_key=secrets.get('brave_api_key'),
        llm_base_url=llm_cfg.get('api_base'),
        model_id=llm_cfg.get('model_id'),
    )
    sentiment = "🟢 BULLISH" if is_bull else ("🔴 BEARISH" if verdict_str.startswith('BEARISH') else "⚪ NEUTRAL")
    await update.message.reply_html(
        f"🧠 <b>AI Analysis · {sym}</b>\n"
        f"Price: ${fx_math.fp(d['price'], sym)} | RSI: {d['rsi']:.1f} | "
        f"ADX: {d['adx']:.1f} | {d['regime']}\n"
        f"Verdict: {sentiment}\n\n"
        f"{html_escape(body)}"
    )


# ════════════════════════════════════════════════════════════════════════════
# TRADING LOOP (background task)
# ════════════════════════════════════════════════════════════════════════════
async def trading_loop_async() -> None:
    """The autonomous trading loop. Runs concurrently with Telegram polling."""
    try:
        database.init_db()
        logger.info(f"DB schema ready at {database.DB_PATH}")
    except Exception as e:
        logger.warning(f"init_db() failed (continuing): {e}")

    try:
        initial = config_manager.load_engine_config().get('risk', {}).get('initial_capital', 10000.0)
        database.init_shadow_account(initial_capital=initial)
    except Exception as e:
        logger.warning(f"init_shadow_account() failed (continuing): {e}")

    # Seed the risk_state row so update_risk_state(...) (UPDATE-only) can persist
    # the tiered-drawdown machine. Without this, the row never exists and every
    # cycle reads `risk_mode='NORMAL'` (default fallback) → transitions fire
    # every cycle → Telegram alert spam. Idempotent (INSERT OR IGNORE).
    # Parity with Anton's startup at anton/core/main.py:51.
    try:
        database.init_risk_state()
    except Exception as e:
        logger.warning(f"init_risk_state() failed (continuing): {e}")

    if market_data.get_client() is None:
        logger.warning("Ionic running without Oanda credentials — trading idle.")

    config: dict = {}
    while not _shutting_down:
        _touch_heartbeat()
        if _check_restart_flag():
            logger.info("Restart flag detected; exiting for compose to restart.")
            _consume_restart_flag()
            os._exit(0)

        try:
            config = config_manager.load_engine_config()
        except Exception as e:
            logger.error(f"Config load failed: {e}")

        strat = config.get('strategy', {})
        symbols   = strat.get('active_symbols', [])
        timeframe = strat.get('timeframe', '1h')
        interval  = strat.get('update_interval_min', 5)

        logger.info(f"--- CYCLE START | symbols: {len(symbols)} | tf: {timeframe} ---")
        try:
            await _maybe_send_reveille()
        except Exception as e:
            logger.warning(f"Reveille check failed: {e}")
        try:
            await _run_cycle(config, symbols, timeframe)
        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)

        # --- TUNER: trade-count trigger (DB-backed, restart-safe) ---
        # Mirrors the Tiberius pattern landed 2026-05-18: count closed shadow
        # trades from the persistent DB, not a session-only counter, so the
        # gate survives engine restarts.
        try:
            _min_t = config.get('tuning', {}).get('min_trades_to_tune', 10)
            _db_counts: dict[str, int] = {}
            for _t in database.get_closed_trades():
                _db_counts[_t['symbol']] = _db_counts.get(_t['symbol'], 0) + 1
            _ready = [s for s, cnt in _db_counts.items() if cnt >= _min_t]
            if _ready:
                logger.info(f"[TUNER] Trigger fired for: {_ready}")
                tuner.run_tuning_cycle(_ready)
        except Exception as e:
            logger.warning(f"Tuner trigger failed: {e}", exc_info=True)

        sleep_total = max(60, interval * 60)
        slept = 0
        while slept < sleep_total and not _shutting_down:
            await asyncio.sleep(30)
            slept += 30
            if _check_restart_flag():
                logger.info("Restart flag detected mid-sleep; exiting for compose to restart.")
                _consume_restart_flag()
                os._exit(0)
            _touch_heartbeat()


# ════════════════════════════════════════════════════════════════════════════
# BOOTSTRAP
# ════════════════════════════════════════════════════════════════════════════
async def main_async() -> None:
    global _bot
    _install_signal_handlers()
    logger.info("=== Ionic Phase 4 engine starting ===")

    telegram_token = secrets.get('telegram_bot_token')
    telegram_user  = secrets.get('telegram_user_id')

    # If Telegram isn't configured, skip the bot entirely and just run the
    # trading loop. Keeps the engine functional pre-BotFather setup.
    if not (telegram_token and telegram_user):
        logger.warning(
            "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID "
            "missing); running headless. Trading loop will still execute."
        )
        await trading_loop_async()
        return

    # Build the Telegram app
    app = (
        Application.builder()
        .token(telegram_token)
        .read_timeout(15)
        .write_timeout(15)
        .build()
    )
    app.add_handler(CommandHandler("indicators",   cmd_indicators))
    app.add_handler(CommandHandler("report",       cmd_report))
    app.add_handler(CommandHandler("pnl",          cmd_pnl))
    app.add_handler(CommandHandler("buy",          cmd_buy))
    app.add_handler(CommandHandler("protect",      cmd_protect))
    app.add_handler(CommandHandler("apply",        cmd_apply))
    app.add_handler(CommandHandler("kill",         cmd_kill))
    app.add_handler(CommandHandler("confirm_kill", cmd_confirm_kill))
    app.add_handler(CommandHandler("resume",       cmd_resume))
    app.add_handler(CommandHandler("restart",      cmd_restart))
    app.add_handler(CommandHandler("calendar",     cmd_calendar))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("start",        cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    _bot = app.bot

    # Register the command menu for slash-autocomplete
    try:
        await app.bot.set_my_commands([
            BotCommand("indicators",   "Technical readout for all pairs"),
            BotCommand("report",       "Portfolio audit"),
            BotCommand("pnl",          "Shadow P&L report"),
            BotCommand("buy",          "Manual buy: /buy PAIR USD"),
            BotCommand("protect",      "Set stops on naked positions"),
            BotCommand("apply",        "Authorize pending stop ratchet (live mode)"),
            BotCommand("kill",         "Begin emergency liquidation"),
            BotCommand("confirm_kill", "Confirm liquidation"),
            BotCommand("resume",       "Clear drawdown halt"),
            BotCommand("restart",      "Queue clean engine restart"),
            BotCommand("calendar",     "Upcoming macro events"),
            BotCommand("help",         "Show this command list"),
        ])
    except Exception as e:
        logger.warning(f"set_my_commands failed (non-fatal): {e}")

    # Boot announcement. The boot greeting and the daily reveille serve the
    # same "Ionic is alive" purpose, so when boot fires we suppress the
    # reveille for the rest of the day (otherwise a mid-morning restart
    # would deliver two back-to-back greetings).
    global _last_reveille_day
    try:
        await app.bot.send_message(
            chat_id=telegram_user,
            text=(
                "📈 <b>Ionic (FX) ONLINE</b>\n"
                "Connected to Oanda Practice. Shadow mode.\n"
                "Send /help for the command list."
            ),
            parse_mode='HTML',
        )
        # Boot delivered → swallow today's reveille
        _last_reveille_day = datetime.datetime.now(pytz.timezone("America/Denver")).date()
    except Exception as e:
        logger.warning(f"Boot announcement failed: {e}")

    # Start the trading loop as a background task. Both run concurrently.
    trade_task = asyncio.create_task(trading_loop_async())

    # Idle until shutdown. The 2s poll beats sleeping for an hour because
    # docker stop's grace window is 10s by default.
    while not _shutting_down:
        await asyncio.sleep(2)

    logger.info("Shutdown requested; tearing down Telegram + trading loop.")
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception as e:
        logger.warning(f"Telegram shutdown error: {e}")
    trade_task.cancel()
    try:
        await trade_task
    except asyncio.CancelledError:
        pass


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting.")


if __name__ == "__main__":
    main()
