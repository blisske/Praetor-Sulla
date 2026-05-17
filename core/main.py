"""
Sulla — Phase 4 engine.

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

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sulla")

# ─── Paths + secrets ────────────────────────────────────────────────────────
HEARTBEAT_PATH    = Path(os.environ.get('HEARTBEAT_PATH',    '/app/data/.engine_heartbeat'))
RESTART_FLAG_PATH = Path(os.environ.get('RESTART_FLAG_PATH', '/app/data/.restart_engine'))
secrets = config_manager.load_secrets()

# ─── Module-level state ─────────────────────────────────────────────────────
_shutting_down = False
_bot = None             # Telegram Bot instance; None until main_async wires it
_kill_armed_at = 0.0    # Unix ts when /kill was sent; /confirm_kill must follow within 60s
_last_reveille_day = None  # date(year, month, day) of the last reveille; one per day
KILL_WINDOW_SECONDS = 60


# Rotating flavor lines for the daily reveille. FX trades 24/5 (Sun 17:00 ET
# → Fri 17:00 ET), so the equity "OPENING BELL" framing doesn't fit — these
# are tuned for the global / 24-hour nature of the FX market. One picked at
# random each morning so the message doesn't get stale.
REVEILLE_LINES = [
    # Roman / imperial (consistent with the Praetor swarm naming)
    "The forum trades in seven tongues. Sulla listens to them all.",
    "Dawn over the empire. The ledger turns.",
    "Ave, Caesar. Another orbit complete.",
    "The legions march. The pips fall in line.",
    "While Rome slept, the markets moved. So did I.",
    "Tempus fugit. The majors endure.",
    "No rest for Caesar's machine.",
    # FX-native
    "London bid. New York offered. Sulla scanning.",
    "Three sessions, seven pairs, one engine.",
    "The dollar leg never sleeps. Neither do I.",
    "Twenty-four hours of liquidity. Five days of opportunity.",
    "Tokyo wakes. London takes. New York closes.",
    "The cross-currents are flowing. I'm reading the tape.",
    "Pip-by-pip, the spread between intent and execution.",
    "Carry trades carry. Sulla follows.",
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


async def _maybe_send_reveille() -> None:
    """
    Daily "good morning" greeting. Fires once per calendar day after 07:30 in
    the user's local Mountain time IF the FX market is currently open. This
    matches Anton/Tiberius's once-per-day cadence so all three bots feel
    consistent — but Sulla's guard is FX-specific (closed Sat all-day,
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
        f"Sulla is ONLINE and scanning the seven majors."
    )
    _last_reveille_day = today


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


# ─── Shadow exit engine ─────────────────────────────────────────────────────
async def _run_shadow_exit_engine(config: dict, latest_indicators: dict) -> None:
    """
    Iterates every open shadow position and checks for stop hits, take-profits,
    or trailing-stop ratchets. Notifies Telegram on every close.
    """
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
        if cur_stop > 0 and price <= cur_stop:
            pnl_usd = (fx_math.position_notional_usd(sym, units, cur_stop)
                       - fx_math.position_notional_usd(sym, units, entry_price))
            pnl_pct = ((cur_stop - entry_price) / entry_price * 100) if entry_price else 0.0
            verdict = f'STOP HIT: {pnl_pct:.2f}%'
            database.log_trade(sym, 'SHADOW SELL', cur_stop, round(pnl_usd, 2),
                               entry_strat, verdict)
            database.adjust_shadow_cash(fx_math.position_notional_usd(sym, units, cur_stop))
            database.close_open_position(sym)
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
        try:
            exit_cmd = strategy.check_exit_signals(
                d, entry_strat, cur_stop, entry_price=entry_price, config=config,
            )
        except Exception as e:
            logger.error(f"[{sym}] check_exit_signals failed: {e}")
            exit_cmd = {'action': 'HOLD'}

        if exit_cmd.get('action') == 'TAKE_PROFIT':
            pnl_usd = (fx_math.position_notional_usd(sym, units, price)
                       - fx_math.position_notional_usd(sym, units, entry_price))
            pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price else 0.0
            verdict = f'TAKE PROFIT: {pnl_pct:.2f}%'
            database.log_trade(sym, 'SHADOW SELL', price, round(pnl_usd, 2),
                               entry_strat, verdict)
            database.adjust_shadow_cash(fx_math.position_notional_usd(sym, units, price))
            database.close_open_position(sym)
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
            continue

        # ── C. Trailing ratchet ────────────────────────────────────────────
        trail_mult = config.get('ratchet', {}).get('trailing_stop_mult', 2.5)
        new_stop = price - (atr * trail_mult)
        if cur_stop > 0 and new_stop > cur_stop:
            database.update_shadow_stop(sym, new_stop)
            logger.info(
                f"[{sym}] ratchet: stop "
                f"{fx_math.fp(cur_stop, sym)} → {fx_math.fp(new_stop, sym)}"
            )


# ─── Entry consensus + shadow buy ───────────────────────────────────────────
async def _evaluate_entry(sym: str, d: dict, config: dict,
                          open_symbols: set[str], shadow_cash: float,
                          shadow_equity: float) -> None:
    if sym in open_symbols:
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

    if bear_abort and verdict_str.startswith('BEARISH'):
        logger.info(f"[{sym}] BEARISH VETO ({paradigm}): aborting entry")
        await _notify(
            f"🚫 <b>BEARISH VETO</b> {sym} | {paradigm}\n"
            f"{html_escape(verdict_body[:500])}"
        )
        return
    if not is_bullish:
        logger.info(f"[{sym}] AI {verdict_str} on {paradigm} — not bullish, holding")
        return

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
    database.log_trade(sym, 'SHADOW BUY', d['price'], units, paradigm, enriched_verdict)
    database.record_open_position(
        symbol=sym, entry_price=d['price'], strategy=paradigm,
        entry_atr=d['atr'], shares=units,
    )
    database.update_shadow_stop(sym, stop_price)
    database.adjust_shadow_cash(-notional)
    logger.info(
        f"[{sym}] 🟢 SHADOW BUY | {paradigm} · {units:,} units @ "
        f"{fx_math.fp(d['price'], sym)} | stop {fx_math.fp(stop_price, sym)} | "
        f"notional ${notional:,.2f}"
    )

    icons = {
        "TREND FOLLOWING":     "📈",
        "MEAN REVERSION":      "🧲",
        "VOLATILITY BREAKOUT": "🚀",
        "LIQUIDITY SWEEP":     "🐋",
    }
    icon = icons.get(paradigm, "🎯")
    sentiment_tag = "🟢 BULLISH" if is_bullish else f"🔴 {verdict_str}"
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

    try:
        await _run_shadow_exit_engine(config, latest)
    except Exception as e:
        logger.error(f"Shadow exit engine failed: {e}", exc_info=True)

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
    max_open      = config.get('strategy', {}).get('max_open_trades', 5)
    if len(open_symbols) >= max_open:
        logger.info(f"At max open trades ({len(open_symbols)}/{max_open}); skipping entry scan.")
        return

    for sym, d in latest.items():
        if _shutting_down or _check_restart_flag():
            return
        if len(open_symbols) >= max_open:
            break
        try:
            await _evaluate_entry(sym, d, config, open_symbols, shadow_cash, shadow_equity)
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
        "📖 <b>Sulla — Command Reference</b>\n\n"
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
    snap = database.get_shadow_account_state()
    equity = snap.get('equity', 0.0)
    cash   = snap.get('cash',   0.0)
    held   = snap.get('held_assets', {})

    peak_eq = database.get_equity_peak() or 0.0
    dd_pct  = max(0.0, (peak_eq - equity) / peak_eq * 100) if peak_eq > 0 else 0.0
    risk_state = database.get_risk_state()
    risk_icons = {'NORMAL':'✅', 'ALERT':'⚠️', 'DERISK':'🟡', 'HALT':'🚨'}
    risk_icon = risk_icons.get(risk_state.get('risk_mode', 'NORMAL'), '✅')

    mode_tag = "👻 SHADOW" if shadow_mode else "🔴 LIVE"
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
    """/buy PAIR USD — manual buy, bypasses consensus. Shadow only."""
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
    cash       = database.get_shadow_cash()
    if notional > cash:
        await update.message.reply_text(
            f"⚠️ Insufficient shadow cash (${cash:,.2f}) for ${notional:,.2f} buy."
        )
        return

    database.log_trade(sym, 'SHADOW BUY', entry, units, 'MANUAL OVERRIDE',
                       'Manual user /buy (shadow mode)')
    database.record_open_position(sym, entry, 'MANUAL OVERRIDE',
                                  entry_atr=d['atr'], shares=units)
    database.update_shadow_stop(sym, stop_price)
    database.adjust_shadow_cash(-notional)
    await update.message.reply_html(
        f"👻 <b>SHADOW MANUAL BUY</b>\n"
        f"Pair: {sym}\n"
        f"Units: {units:,} @ ${fx_math.fp(entry, sym)}\n"
        f"Stop: ${fx_math.fp(stop_price, sym)}\n"
        f"Notional: ${notional:,.2f}"
    )


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
    """Two-step confirm — close all open shadow positions at current price."""
    global _kill_armed_at
    if not _auth(update): return
    if _kill_armed_at == 0 or (time.time() - _kill_armed_at) > KILL_WINDOW_SECONDS:
        await update.message.reply_text(
            f"❌ Kill switch not armed (or window expired). Send /kill first."
        )
        return
    _kill_armed_at = 0
    await update.message.reply_html("👻 <b>EXECUTING SHADOW KILL...</b>")

    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')
    closed = 0
    for pos in database.get_all_open_positions():
        sym = pos['symbol']
        try:
            d = await market_data.fetch_indicators(sym, config=cfg, timeframe=tf)
            if not d:
                continue
            price = d['price']
            entry = pos['entry_price']
            units = pos.get('shares', 0.0)
            pnl_usd = (fx_math.position_notional_usd(sym, units, price)
                       - fx_math.position_notional_usd(sym, units, entry))
            pnl_pct = ((price - entry) / entry * 100) if entry else 0.0
            database.log_trade(sym, 'SHADOW SELL', price, round(pnl_usd, 2),
                               pos.get('strategy', '?'),
                               f'KILL SWITCH: {pnl_pct:.1f}%')
            database.adjust_shadow_cash(fx_math.position_notional_usd(sym, units, price))
            database.close_open_position(sym)
            closed += 1
        except Exception as e:
            logger.error(f"[{sym}] kill close failed: {e}")
    await update.message.reply_html(
        f"✅ <b>SHADOW KILL COMPLETE.</b> {closed} position(s) closed."
    )


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

    if market_data.get_client() is None:
        logger.warning("Sulla running without Oanda credentials — trading idle.")

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
    logger.info("=== Sulla Phase 4 engine starting ===")

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
    # same "Sulla is alive" purpose, so when boot fires we suppress the
    # reveille for the rest of the day (otherwise a mid-morning restart
    # would deliver two back-to-back greetings).
    global _last_reveille_day
    try:
        await app.bot.send_message(
            chat_id=telegram_user,
            text=(
                "📈 <b>Sulla (FX) ONLINE</b>\n"
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
