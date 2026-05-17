import os
import sys
import math
import signal
import logging
import asyncio
import time
import datetime
from pathlib import Path
import pytz
from html import escape as html_escape
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- LOGGING BOOTSTRAP ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("anton")

# --- IMPORT OUR CUSTOM TRADFI MODULES ---
import config_manager
import database
import market_data
import strategy
import ai_brain
import execution
import tuner
import earnings

# ==============================================================================
# ANTON V1 - TRADFI COMMAND CENTER
# Orchestrates the Telegram UI, the Market Clock loop, and all external modules.
# ==============================================================================

# 1. INITIALIZATION & GLOBAL STATE
secrets = config_manager.load_secrets()
database.init_db()

# Watchlist loaded from Config.yaml — add/remove symbols there, not here
_boot_cfg      = config_manager.load_engine_config()
ACTIVE_SYMBOLS = _boot_cfg.get('strategy', {}).get('active_symbols', ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMD', 'GOOGL', 'XOM', 'CVX', 'COP', 'JPM', 'GS', 'BAC', 'UNH', 'LLY', 'JNJ', 'AMZN', 'TSLA', 'HD', 'CAT'])

# Seed the shadow ledger from Config.yaml initial_capital. Idempotent — only
# inserts if the row is absent (fresh DB). Existing balances are preserved.
_init_capital = _boot_cfg.get('risk', {}).get('initial_capital', 10000.0)
database.init_shadow_account(_init_capital)
database.init_risk_state()

logger.info(f"Watchlist loaded: {ACTIVE_SYMBOLS}")
logger.info("=== Anton V1 Bootstrap Complete ===")

system_active    = True
kill_armed       = False
kill_armed_time  = 0.0
shadow_sell_counts = {}   # tracks SHADOW SELL count per symbol for tuner trigger

# Container coordination: the API writes RESTART_FLAG_PATH after a config save
# to ask the engine to restart with fresh config. We delete the flag and exit
# cleanly; compose `restart: unless-stopped` brings us back. HEARTBEAT_PATH is
# touched at the top of every loop iteration so a docker healthcheck can stat
# it to detect a frozen daemon. Both default to siblings of the SQLite DB so
# they ride the same bind mount the API can see.
_DEFAULT_RUNTIME_DIR = database.DB_PATH.parent
RESTART_FLAG_PATH = Path(os.environ.get('RESTART_FLAG_PATH', _DEFAULT_RUNTIME_DIR / '.restart_engine'))
HEARTBEAT_PATH    = Path(os.environ.get('HEARTBEAT_PATH',    _DEFAULT_RUNTIME_DIR / '.engine_heartbeat'))


def _check_restart_flag():
    """Returns True iff a restart was requested by the API (flag file present)."""
    try:
        return RESTART_FLAG_PATH.exists()
    except Exception:
        return False


def _consume_restart_flag():
    """Deletes the restart flag so the next launch starts clean."""
    try:
        RESTART_FLAG_PATH.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Could not delete restart flag {RESTART_FLAG_PATH}: {e}")


def _touch_heartbeat():
    """Updates the heartbeat file mtime so docker healthcheck can verify
    the engine is making progress through its main loop."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.touch()
    except Exception:
        pass  # Heartbeat is best-effort; a missed touch shouldn't kill the loop


def _install_signal_handlers():
    """SIGTERM/SIGINT trigger graceful shutdown. Without this, `docker stop`
    waits 10s then SIGKILLs us, which can leave the SQLite WAL in a recoverable
    but messy state on next boot."""
    def _handler(signum, frame):
        global system_active
        logger.info(f"Received signal {signum}; flagging shutdown.")
        system_active = False
    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        # Outside main thread or in a context that disallows signal handlers.
        # Telegram's own polling thread handles SIGINT; no-op there.
        pass

# Latest observed price per symbol — populated by the scanner and reused by
# the shadow-equity calculation at the top of the next cycle. One-cycle stale
# is acceptable for drawdown tracking.
latest_prices: dict = {}

# Daily trend cache for the multi-timeframe regime filter (Phase 4). Keyed by
# symbol → {'trend': 'BULL'|'BEAR', 'date': 'YYYY-MM-DD'}. Refreshes once per
# ET trading day per symbol — daily regime is by design a slow macro signal.
daily_trend_cache: dict = {}
ET_TZ_MODULE = pytz.timezone('America/New_York')


async def _run_consensus_layers_2_3(d: dict, symbol: str, strategy_type: str, config: dict) -> dict:
    """
    Runs Layer 2 (supporting signals) and Layer 3 (AI verdict) of the consensus
    model. Layer 1 (paradigm match) must already be true. Returns:
      {
        'passed': bool,           # all three layers OK
        'is_bullish': bool,       # AI returned BULLISH
        'tagged_verdict': str,    # full verdict prefixed with score for trade log
        'full_verdict': str,
        'reason': str,            # 'ok' | 'consensus_score' | 'bearish_abort'
        'consensus_score': int,
        'reasons_str': str,
      }
    """
    support_score, support_reasons = strategy.check_supporting_signals(d, strategy_type, config=config)
    consensus_score = 1 + support_score   # primary signal = 1
    min_score       = config.get('consensus', {}).get('min_consensus_score', 3)
    reasons_str     = " | ".join(support_reasons)
    logger.info(f"[{symbol}] CONSENSUS: {consensus_score}/{min_score} | {reasons_str}")

    if consensus_score < min_score:
        return {
            'passed': False, 'is_bullish': False,
            'tagged_verdict': None, 'full_verdict': None,
            'reason': 'consensus_score',
            'consensus_score': consensus_score, 'reasons_str': reasons_str,
        }

    llm_cfg       = config.get('ai_agent', {}).get('sentiment_analysis', {})
    llm_reasoning = config.get('consensus', {}).get('llm_reasoning', True)
    verdict, full_verdict = await ai_brain.get_ai_consensus(
        symbol, d['price'], secrets['brave_api_key'],
        llm_cfg.get('api_base'), llm_cfg.get('model_id'),
        paradigm=strategy_type,
        support_reasons=support_reasons,
        indicators=d,
        llm_reasoning=llm_reasoning
    )

    bearish_abort = config.get('consensus', {}).get('bearish_abort', True)
    if bearish_abort and verdict == "BEARISH":
        return {
            'passed': False, 'is_bullish': False,
            'tagged_verdict': None, 'full_verdict': full_verdict,
            'reason': 'bearish_abort',
            'consensus_score': consensus_score, 'reasons_str': reasons_str,
        }

    is_bullish     = (verdict == "BULLISH")
    full_score_tag = f"[SCORE:{consensus_score}/{min_score} | {reasons_str}]"
    tagged_verdict = f"{full_score_tag} {full_verdict}"
    return {
        'passed': True, 'is_bullish': is_bullish,
        'tagged_verdict': tagged_verdict, 'full_verdict': full_verdict,
        'reason': 'ok',
        'consensus_score': consensus_score, 'reasons_str': reasons_str,
    }


_PARADIGM_TO_PYRAMID_KEY = {
    'TREND FOLLOWING':     'trend_following',
    'VOLATILITY BREAKOUT': 'volatility_breakout',
}


def _check_pyramid_eligibility(symbol: str, strategy_type: str, d: dict, pos: dict, config: dict) -> tuple[bool, str]:
    """
    Decides whether a fresh same-paradigm setup on a held position should add a
    pyramid leg. Returns (eligible, reason). All TradFi-specific risk gates
    (daily_halt, earnings, session cutoff) are checked at the call site so this
    function stays focused on pyramid mechanics.
    """
    pyramid_cfg = config.get('pyramiding', {})
    if not pyramid_cfg.get('enabled', False):
        return False, 'pyramiding_disabled'

    paradigm_key = _PARADIGM_TO_PYRAMID_KEY.get(strategy_type)
    if paradigm_key is None:
        return False, f'paradigm_{strategy_type}_not_eligible'

    if pos['strategy'] != strategy_type:
        return False, f'paradigm_mismatch_{pos["strategy"]}_vs_{strategy_type}'

    max_legs = pyramid_cfg.get('max_legs', {}).get(paradigm_key, 1)
    if pos['leg_count'] >= max_legs:
        return False, f'max_legs_reached_{pos["leg_count"]}_of_{max_legs}'

    trigger_mult = pyramid_cfg.get('trigger_atr_mult', 1.0)
    last_atr     = pos.get('last_leg_atr') or pos.get('entry_atr') or 0.0
    last_price   = pos.get('last_leg_price') or pos['entry_price']
    if last_atr <= 0:
        return False, 'invalid_last_leg_atr'

    advance_required = last_atr * trigger_mult
    advance          = d['price'] - last_price
    if advance < advance_required:
        return False, f'advance_{advance:.2f}_below_required_{advance_required:.2f}'

    return True, 'eligible'


def _get_sector(symbol: str, sectors_map: dict) -> str:
    """Returns the sector bucket name for a symbol, or 'Uncategorized'."""
    for sector_name, syms in sectors_map.items():
        if symbol in syms:
            return sector_name
    return 'Uncategorized'


def _correlation_multiplier(symbol: str, held_assets: dict, config: dict) -> float:
    """
    Returns sizing multiplier in (0, 1] based on count of same-sector open
    positions. Curve is indexed by count; final element applies at N+.
    Uncategorized symbols are not penalized (returns 1.0).
    """
    corr_cfg = config.get('correlation_aware_sizing', {})
    if not corr_cfg.get('enabled', True):
        return 1.0
    sectors = corr_cfg.get('sectors', {})
    curve   = corr_cfg.get('curve', [1.0, 0.85, 0.70, 0.55, 0.40])
    target  = _get_sector(symbol, sectors)
    if target == 'Uncategorized' or not curve:
        return 1.0
    n = sum(
        1 for s in held_assets
        if s != symbol and _get_sector(s, sectors) == target
    )
    n = min(n, len(curve) - 1)
    return float(curve[n])


async def _get_daily_trend(symbol: str, config: dict) -> str:
    """
    Returns 'BULL' or 'BEAR' for the symbol's daily EMA9/EMA21 cross.
    Cached once per ET trading day. Returns 'BULL' (fail-open) on data error
    so a connectivity blip doesn't lock the entire book out of trades.
    """
    if not config.get('mtf_filter', {}).get('enabled', True):
        return 'BULL'
    today_iso = datetime.datetime.now(ET_TZ_MODULE).date().isoformat()
    cached = daily_trend_cache.get(symbol)
    if cached and cached.get('date') == today_iso:
        return cached['trend']
    d_daily = await market_data.fetch_indicators(symbol, config=config, timeframe='1d', limit=60)
    if not d_daily:
        logger.warning(f"[{symbol}] MTF: daily fetch failed — failing open (BULL)")
        return 'BULL'
    trend = d_daily.get('trend', 'BULL')
    daily_trend_cache[symbol] = {'trend': trend, 'date': today_iso}
    logger.info(f"[{symbol}] MTF Daily: trend={trend} (cached through {today_iso})")
    return trend


def _get_account_snapshot(shadow: bool) -> dict:
    """
    Returns {equity, cash, held_assets} from the shadow ledger when shadow
    mode is on, or from the live Alpaca account otherwise. This is the single
    dispatch point that enforces the shadow contract — no other code should
    call execution.get_account_balance / get_open_positions when shadow is on.
    """
    if shadow:
        return database.get_shadow_account_state(latest_prices)
    return {
        'equity':      execution.get_account_balance(),
        'cash':        None,
        'held_assets': execution.get_open_positions(),
    }


# ------------------------------------------------------------------------------
# 2. TELEGRAM COMMAND HANDLERS
# ------------------------------------------------------------------------------

async def cmd_indicators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches the current technical data for all active equities.
    Fetches all symbols in parallel, then sends one message per sector
    to stay well under Telegram's 4096-character limit.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    cfg = config_manager.load_engine_config()
    tf  = cfg.get('strategy', {}).get('timeframe', '1h')

    # Sector groupings — matches active_symbols in Config.yaml
    sectors = {
        "🖥 Technology":             ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "GOOGL"],
        "⚡ Energy":                 ["XOM", "CVX", "COP"],
        "🏦 Financials":             ["JPM", "GS", "BAC"],
        "🏥 Healthcare":             ["UNH", "LLY", "JNJ"],
        "🛒 Consumer / Industrial":  ["AMZN", "TSLA", "HD", "CAT"],
    }

    # Fetch all symbols in parallel — one gather across all 20
    all_symbols = [sym for syms in sectors.values() for sym in syms]
    tasks   = [market_data.fetch_indicators(sym, config=cfg, timeframe=tf) for sym in all_symbols]
    results = await asyncio.gather(*tasks)
    data    = {sym: d for sym, d in zip(all_symbols, results) if d}

    await update.message.reply_html("🚦 <b>US Equity Cycle Report</b>")

    for sector_name, symbols in sectors.items():
        blocks: list[str] = [f"<b>{sector_name}</b>"]

        for symbol in symbols:
            d = data.get(symbol)
            if not d:
                blocks.append(f"<b>{symbol}</b> — <i>⚠️ no data</i>")
                continue

            d['symbol'] = symbol  # Required by strategy.py for symbol_overrides lookup
            if d['regime'] == "TRENDING":
                r_emoji      = "🔥"
                t_emoji      = "🟢" if d['trend'] == "BULL" else "🔴"
                primary_text = f"{t_emoji} EMA Trend ({d['trend']})"
            else:
                r_emoji  = "🧊"
                mid_dist = (d['price'] - d['bb_middle']) / d['bb_middle'] * 100
                loc      = "below" if d['price'] < d['bb_middle'] else "above"
                primary_text = f"🟡 {abs(mid_dist):.1f}% {loc} mid BB"

            rsi_tag = ""
            if d['rsi'] < 35:
                rsi_tag = " 🔻 oversold"
            elif d['rsi'] > 65:
                rsi_tag = " 🔺 overbought"

            is_setup, strat_type = strategy.check_entry_signals(d, config=cfg)
            sym_lines = [
                f"<b>{symbol}</b> — ${d['price']:.2f}",
                f"• Regime — {r_emoji} {d['regime']} (ADX {d['adx']:.1f})",
                f"• Signal — {primary_text}",
                f"• RSI — {d['rsi']:.1f}{rsi_tag} · ATR {d['atr']:.2f}",
            ]
            if is_setup:
                sym_lines.append(f"• 🚀 <b>Brewing</b> — {strat_type}")
            blocks.append("\n".join(sym_lines))

        await update.message.reply_html("\n\n".join(blocks))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Portfolio audit. In shadow mode reads the synthetic ledger; in live mode
    reads the Alpaca paper account. Never crosses streams.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    try:
        cfg        = config_manager.load_engine_config()
        max_trades = cfg.get('strategy', {}).get('max_open_trades', 2)
        shadow     = cfg.get('alpaca', {}).get('shadow_mode', False)

        snap        = _get_account_snapshot(shadow)
        total_equity = snap['equity']
        held_assets  = snap['held_assets']
        risk_state   = database.get_risk_state()
        peak_equity  = database.get_equity_peak()
        drawdown_pct = max(0.0, (peak_equity - total_equity) / peak_equity * 100) if peak_equity > 0 else 0.0

        mode_tag = "👻 SHADOW" if shadow else "🔴 LIVE PAPER"
        sections: list[str] = []
        sections.append(f"📊 <b>Command Report</b>  ·  {mode_tag}")

        # --- Account ---
        risk_icons = {'NORMAL':'✅', 'ALERT':'⚠️', 'DERISK':'🟡', 'HALT':'🚨'}
        risk_icon  = risk_icons.get(risk_state['risk_mode'], '✅')
        acct_lines = [
            f"• <b>Equity</b> — ${total_equity:,.2f}",
            f"• <b>Mode</b> — {risk_icon} {risk_state['risk_mode']}",
        ]
        if shadow:
            acct_lines.append(f"• <b>Cash</b> — ${snap['cash']:,.2f}")
            initial = database.get_shadow_initial_capital()
            if initial > 0:
                pnl_pct = (total_equity - initial) / initial * 100
                acct_lines.append(f"• <b>Total P&amp;L</b> — {pnl_pct:+.2f}% (from ${initial:,.0f})")
        if peak_equity > 0 and drawdown_pct > 0:
            acct_lines.append(f"• <b>Drawdown</b> — {drawdown_pct:.1f}% from peak ${peak_equity:,.2f}")
        elif peak_equity > 0:
            acct_lines.append(f"• <b>Drawdown</b> — none (at peak)")
        acct_lines.append(f"• <b>Exposure</b> — {len(held_assets)} / {max_trades} open")
        acct_lines.append("• <b>Account Type</b> — Cash (no PDT)")
        if risk_state['daily_halt']:
            acct_lines.append("• 🛑 <b>Daily Halt</b> — entries blocked rest of session")
        sections.append("<b>Account</b>\n" + "\n".join(acct_lines))

        # --- Holdings ---
        if not held_assets:
            sections.append("<b>Holdings</b>\n<i>None</i>")
        else:
            lines = []
            for asset, qty in held_assets.items():
                pos       = database.get_open_position(asset)
                leg_count = pos.get('leg_count', 1) if pos else 1
                avg       = pos.get('avg_entry_price') if pos else None
                if leg_count > 1 and avg:
                    lines.append(f"• <b>{asset}</b> — {qty} sh × {leg_count} legs, avg ${avg:.2f}")
                else:
                    lines.append(f"• <b>{asset}</b> — {qty} shares")
            sections.append("<b>Holdings</b>\n" + "\n".join(lines))

        # --- Earnings Blackout ---
        blackout_days = cfg.get('strategy', {}).get('earnings_blackout_days', 2)
        blocked = earnings.get_blackout_summary(ACTIVE_SYMBOLS, blackout_days)
        if blocked:
            lines = [f"• ⚠️ <b>{sym}</b> — earnings in {days} day(s)" for sym, days in blocked.items()]
            sections.append("<b>Earnings Blackout</b>\n" + "\n".join(lines))

        await update.message.reply_html("\n\n".join(sections))
    except Exception as e:
        await update.message.reply_text(f"❌ Report Error: {e}")


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered by /buy [SYMBOL] [USD_AMOUNT]. Manual override.
    In shadow mode writes to the shadow ledger only. In live mode places
    a real Alpaca order with ATR-based stop-loss.
    Example: /buy AAPL 500
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    try:
        symbol     = context.args[0].upper()
        amount_usd = float(context.args[1])

        if not execution.is_market_open():
            await update.message.reply_text("🛑 Market is closed. Cannot execute buy.")
            return

        cfg    = config_manager.load_engine_config()
        tf     = cfg.get('strategy', {}).get('timeframe', '1h')
        shadow = cfg.get('alpaca', {}).get('shadow_mode', False)

        d = await market_data.fetch_indicators(symbol, config=cfg, timeframe=tf)
        if not d:
            await update.message.reply_text(f"❌ Failed to fetch data for {symbol}.")
            return

        if shadow:
            # Shadow path — synthetic fill, ledger debit, no Alpaca contact
            qty = math.floor(amount_usd / d['price'])
            if qty < 1:
                await update.message.reply_text(
                    f"⚠️ ${amount_usd:.2f} can't buy 1 full share of {symbol} at ${d['price']:.2f}."
                )
                return
            cost = qty * d['price']
            cash = database.get_shadow_cash()
            if cost > cash:
                await update.message.reply_text(
                    f"⚠️ Insufficient shadow cash (${cash:,.2f}) for ${cost:,.2f} buy."
                )
                return
            database.log_trade(
                symbol, "SHADOW BUY", d['price'], qty,
                "MANUAL OVERRIDE", "Manual User Command (shadow)"
            )
            database.record_open_position(
                symbol, d['price'], "MANUAL OVERRIDE", entry_atr=d['atr'], shares=qty
            )
            database.adjust_shadow_cash(-cost)
            await update.message.reply_html(
                f"👻 <b>SHADOW BUY</b>\n"
                f"{qty} shares of {symbol} at ${d['price']:.2f} = ${cost:,.2f}\n"
                f"Stop auto-bootstrapped on next cycle."
            )
            return

        # Live path — real Alpaca order with stop
        success, sl_price = await asyncio.to_thread(
            execution.execute_buy_with_stop,
            symbol, amount_usd, d['price'], d['atr']
        )

        if success:
            database.log_trade(
                symbol, "BUY", d['price'], amount_usd / d['price'],
                "MANUAL OVERRIDE", "Manual User Command"
            )
            database.record_open_position(symbol, d['price'], "MANUAL OVERRIDE", entry_atr=d['atr'])
            await update.message.reply_html(
                f"✅ <b>MANUAL BUY</b>\n"
                f"Bought ${amount_usd:.2f} of {symbol}.\n"
                f"Stop placed at ${sl_price:.2f}"
            )
        else:
            await update.message.reply_text("❌ Failed to execute manual buy.")

    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: /buy SYMBOL USD_AMOUNT\nExample: /buy AAPL 500"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pnl — Shadow P&L report. Per-symbol win rate, profit factor, avg return,
    and tuning readiness. Use letter L not number 1.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    all_trades = database.get_closed_trades()
    if not all_trades:
        await update.message.reply_text("No closed shadow trades yet.")
        return

    cfg = config_manager.load_engine_config()
    min_trades = cfg.get('tuning', {}).get('min_trades_to_tune', 10)

    stats = {}
    for t in all_trades:
        s = stats.setdefault(t['symbol'], {'wins': 0, 'gross_win': 0.0, 'gross_loss': 0.0,
                                            'pnls': [], 'pnl_usd_sum': 0.0})
        pnl = t['pnl_pct']
        s['pnls'].append(pnl)
        s['pnl_usd_sum'] += t.get('pnl_usd', 0.0)
        if pnl > 0:
            s['wins'] += 1
            s['gross_win'] += pnl
        else:
            s['gross_loss'] += abs(pnl)

    sections: list[str] = ["📊 <b>Shadow P&amp;L Report</b>"]

    by_sym_lines = []
    for sym, s in sorted(stats.items()):
        n   = len(s['pnls'])
        wr  = s['wins'] / n * 100
        pf  = (s['gross_win'] / s['gross_loss']) if s['gross_loss'] > 0 else float('inf')
        avg = sum(s['pnls']) / n
        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
        pnl_usd = s['pnl_usd_sum']
        pnl_str = f"{'+' if pnl_usd >= 0 else ''}${pnl_usd:,.2f}"
        by_sym_lines.append(f"• <b>{sym}</b> — {n} trades · WR {wr:.0f}% · PF {pf_str} · Avg {avg:+.1f}% · {pnl_str}")
    sections.append("<b>By Symbol</b>\n" + "\n".join(by_sym_lines))

    total_trades = sum(len(v['pnls']) for v in stats.values())
    total_wins   = sum(v['wins'] for v in stats.values())
    overall_wr   = total_wins / total_trades * 100 if total_trades else 0
    net_usd      = sum(v['pnl_usd_sum'] for v in stats.values())
    net_emoji    = "🟢" if net_usd > 0 else ("🔴" if net_usd < 0 else "⚪")

    best_sym_name, best_sym_data = max(stats.items(), key=lambda x: len(x[1]['pnls']))
    best_sym_count = len(best_sym_data['pnls'])
    summary_lines = [
        f"• <b>Total Trades</b> — {total_trades}",
        f"• <b>Overall WR</b> — {overall_wr:.0f}%",
        f"• <b>Net P&amp;L</b> — {net_emoji} {'+' if net_usd >= 0 else ''}${net_usd:,.2f}",
    ]
    if best_sym_count >= min_trades:
        summary_lines.append(f"• ✅ {best_sym_name} has {best_sym_count} closes — tuning cycle eligible")
    else:
        remaining = min_trades - best_sym_count
        summary_lines.append(f"• ⏳ {remaining} more closes needed on {best_sym_name} for first tuning cycle")
    sections.append("<b>Summary</b>\n" + "\n".join(summary_lines))

    tuning_log = database.get_tuning_summary()
    if tuning_log:
        tlines = [f"• <b>{e['symbol']}</b>/{e['parameter']} — {e['old_value']}→{e['new_value']} [{e['status']}]"
                  for e in tuning_log[:5]]
        sections.append("🔬 <b>Recent Tuning Activity</b>\n" + "\n".join(tlines))

    await update.message.reply_html("\n\n".join(sections))


async def cmd_protect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered by /protect. Scans all open Alpaca positions and places a 1.5x
    ATR stop-loss on any that don't already have a stop order.
    Live-only — in shadow mode the exit engine bootstraps stops from entry_atr
    on the next cycle automatically.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    cfg = config_manager.load_engine_config()
    if cfg.get('alpaca', {}).get('shadow_mode', False):
        await update.message.reply_html(
            "👻 <b>Shadow mode</b> — stops are managed in the DB, not at the exchange.\n"
            "The autonomous loop bootstraps stops from entry_atr each cycle. "
            "No live orders to protect."
        )
        return

    await update.message.reply_text("🛡️ Initiating Safety Scan...")

    try:
        tf        = cfg.get('strategy', {}).get('timeframe', '1h')
        positions = execution.get_open_positions()

        if not positions:
            await update.message.reply_text("No open positions to protect.")
            return

        for symbol, qty in positions.items():
            existing_stop = execution._get_current_stop_price(symbol)
            if existing_stop > 0:
                continue

            d = await market_data.fetch_indicators(symbol, config=cfg, timeframe=tf)
            if not d:
                continue

            sl_price = round(d['price'] - (d['atr'] * 1.5), 2)
            if sl_price <= 0 or sl_price >= d['price']:
                continue

            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            execution.trading_client.submit_order(
                order_data=StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.GTC,
                    stop_price=sl_price
                )
            )
            await update.message.reply_html(
                f"✅ Protected {symbol}: Stop at ${sl_price:.2f}"
            )

        await update.message.reply_text("🛡️ Safety Scan Complete.")

    except Exception as e:
        await update.message.reply_text(f"❌ Protection Error: {e}")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Arms the emergency kill switch for Alpaca positions."""
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    global kill_armed, kill_armed_time
    kill_armed      = True
    kill_armed_time = time.time()

    msg = (
        "🚨 <b>EMERGENCY KILL SWITCH ARMED</b> 🚨\n\n"
        "⚠️ WARNING: This will immediately MARKET SELL ALL US EQUITY holdings.\n"
        "You have exactly <b>60 seconds</b> to type <code>/confirm_kill</code>."
    )
    await update.message.reply_html(msg)


async def cmd_confirm_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Executes the kill switch. In shadow mode closes all DB positions and
    credits the shadow ledger at current market prices. In live mode market-
    sells everything via Alpaca.
    """
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    global kill_armed

    if not kill_armed or (time.time() - kill_armed_time > 60):
        kill_armed = False
        await update.message.reply_text(
            "Kill switch not armed or expired. Type /kill to arm."
        )
        return

    kill_armed = False
    cfg    = config_manager.load_engine_config()
    shadow = cfg.get('alpaca', {}).get('shadow_mode', False)

    if shadow:
        await update.message.reply_html("👻 <b>EXECUTING SHADOW KILL SWITCH...</b>")
        try:
            tf        = cfg.get('strategy', {}).get('timeframe', '1h')
            positions = database.get_all_open_positions()
            if not positions:
                await update.message.reply_html("No shadow positions to liquidate.")
                return
            closed = 0
            for pos in positions:
                sym = pos['symbol']
                d_kill = await market_data.fetch_indicators(sym, config=cfg, timeframe=tf)
                close_price = d_kill['price'] if d_kill else pos['entry_price']
                shares      = pos.get('shares', 0.0)
                proceeds    = shares * close_price
                pnl_pct     = (close_price - pos['entry_price']) / pos['entry_price'] * 100 if pos['entry_price'] else 0.0
                pnl_usd     = round((close_price - pos['entry_price']) * shares, 2)
                database.log_trade(
                    sym, 'SHADOW SELL', close_price, pnl_usd,
                    pos['strategy'], f'KILL SWITCH: {pnl_pct:.1f}%'
                )
                database.adjust_shadow_cash(proceeds)
                database.close_open_position(sym)
                closed += 1
            await update.message.reply_html(
                f"✅ <b>SHADOW KILL SWITCH COMPLETE.</b> {closed} position(s) closed."
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Shadow kill error: {e}")
        return

    # Live path
    await update.message.reply_html("🔴 <b>EXECUTING TRADFI KILL SWITCH...</b>")

    try:
        success = await asyncio.to_thread(execution.liquidate_all_positions)
        if success:
            for symbol in ACTIVE_SYMBOLS:
                database.close_open_position(symbol)
            await update.message.reply_html(
                "✅ <b>KILL SWITCH COMPLETE. All positions liquidated.</b>"
            )
        else:
            await update.message.reply_html(
                "⚠️ <b>KILL SWITCH FAILED. Check Alpaca Dashboard immediately.</b>"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Error during execution: {e}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /resume — Restarts the autonomous loop after a drawdown halt.
    Requires current drawdown to be below the alert threshold before allowing.
    Resets the equity peak watermark to current equity on success.
    Reads shadow ledger when shadow_mode is on; live Alpaca otherwise.
    """
    global system_active
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    cfg       = config_manager.load_engine_config()
    alert_pct = cfg.get('risk', {}).get('drawdown_alert_pct', 8.0)
    shadow    = cfg.get('alpaca', {}).get('shadow_mode', False)

    try:
        snap           = _get_account_snapshot(shadow)
        current_equity = snap['equity']
        peak           = database.get_equity_peak()
        drawdown_pct   = max(0.0, (peak - current_equity) / peak * 100) if peak > 0 else 0.0

        if drawdown_pct >= alert_pct:
            await update.message.reply_html(
                f"🚫 <b>RESUME BLOCKED</b>\n"
                f"Current drawdown is still <b>{drawdown_pct:.1f}%</b> "
                f"(alert threshold: {alert_pct}%).\n"
                f"Resolve market conditions before resuming."
            )
            return

        database.reset_equity_peak(current_equity)
        database.update_risk_state(risk_mode='NORMAL')
        system_active = True  # safety: covers KeyboardInterrupt path
        logger.info(f"RESUME: risk_mode=NORMAL. Peak reset to ${current_equity:,.2f}.")
        await update.message.reply_html(
            f"✅ <b>TRADING RESUMED</b>\n"
            f"Equity peak watermark reset to ${current_equity:,.2f}.\n"
            f"Risk mode → NORMAL. Autonomous loop active."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Resume error: {e}")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger a clean engine restart via the shared flag-file pattern.
    The next top-of-loop check picks up the flag and exits the process;
    compose's `restart: unless-stopped` brings it back with fresh Config.yaml."""
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return
    try:
        RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_FLAG_PATH.touch()
        await update.message.reply_html(
            "🔄 <b>Restart queued</b>\n"
            "The engine will exit at the next cycle and compose will respawn "
            "it with fresh config."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Could not write restart flag: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Static help message listing every available command."""
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return
    msg = (
        "📖 <b>Anton — Command Reference</b>\n\n"
        "<b>Status &amp; Reporting</b>\n"
        "• /indicators — technical readout for all watchlist symbols\n"
        "• /report — portfolio audit (equity, holdings, defense, blackouts)\n"
        "• /pnl — shadow P&amp;L report (per-symbol + summary)\n\n"
        "<b>Manual Trade Control</b>\n"
        "• /buy SYMBOL USD — manual buy with auto stop-loss\n"
        "• /protect — place protective stops on any naked live positions\n\n"
        "<b>Safety</b>\n"
        "• /kill → /confirm_kill — two-step emergency liquidation\n"
        "• /resume — clear drawdown halt (re-checks first)\n"
        "• /restart — queue a clean engine restart\n\n"
        "<b>AI Sentiment</b>\n"
        "• Type any ticker (e.g. <code>AAPL</code>) for an ad-hoc AI analysis"
    )
    await update.message.reply_html(msg)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches plain text tickers and runs the AI sentiment scan."""
    if str(update.effective_user.id) != secrets['telegram_user_id']:
        return

    symbol = update.message.text.upper().strip()

    if not (1 <= len(symbol) <= 5 and symbol.isalpha()):
        await update.message.reply_text(
            "Please enter a valid US Equity ticker (e.g., AAPL or TSLA)."
        )
        return

    await update.message.reply_html(f"🧠 <b>Querying AI Sentiment for {symbol}...</b>")

    try:
        cfg     = config_manager.load_engine_config()
        llm_cfg = cfg.get('ai_agent', {}).get('sentiment_analysis', {})
        tf      = cfg.get('strategy', {}).get('timeframe', '1h')

        d = await market_data.fetch_indicators(symbol, config=cfg, timeframe=tf)
        if not d:
            await update.message.reply_text(
                f"❌ Could not fetch Alpaca market data for {symbol}."
            )
            return

        verdict_str, verdict = await ai_brain.get_ai_consensus(
            symbol, d['price'], secrets['brave_api_key'],
            llm_cfg.get('api_base'), llm_cfg.get('model_id'),
            indicators=d
        )

        msg = (
            f"🧠 <b>AI VERDICT FOR {symbol}</b>\n"
            f"Price: ${d['price']:.2f} | Verdict: {verdict_str}\n\n{html_escape(verdict)}"
        )
        await update.message.reply_html(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ Sentiment Error: {e}")


# ------------------------------------------------------------------------------
# 3. THE AUTONOMOUS CORE ENGINE (TRADFI LOOP)
# ------------------------------------------------------------------------------

async def adaptive_manager_loop(app):
    global system_active, ACTIVE_SYMBOLS

    last_heartbeat_day  = None
    consecutive_errors  = 0
    config              = {}
    previous_holdings   = {}
    mt_tz               = pytz.timezone('America/Denver')
    et_tz               = pytz.timezone('America/New_York')

    while system_active:
        # Heartbeat for docker healthcheck — proves the loop is making progress.
        _touch_heartbeat()

        # Cross-container restart signal from the API after a config save.
        # Consume the flag and force-exit so compose `restart: unless-stopped`
        # spawns a fresh container with the freshly saved Config.yaml.
        # NOTE: just setting `system_active = False` and breaking is not enough —
        # the main coroutine is still parked in `while True: sleep(3600)` so the
        # process would stay alive on Telegram polling only. os._exit(0) makes
        # the container actually die so compose restarts it.
        if _check_restart_flag():
            logger.info("Restart flag detected; exiting for compose to restart.")
            _consume_restart_flag()
            os._exit(0)

        try:
            config     = config_manager.load_engine_config()
            autonomous = config.get('strategy', {}).get('autonomous_mode', False)
            tf         = config.get('strategy', {}).get('timeframe', '1h')
            shadow     = config.get('alpaca', {}).get('shadow_mode', False)
            ACTIVE_SYMBOLS = config.get('strategy', {}).get('active_symbols', ACTIVE_SYMBOLS)

            # --- 1. THE TRADFI BOUNCER ---
            # If the exchange is closed, sleep and do nothing.
            if not execution.is_market_open():
                await asyncio.sleep(300)
                continue

            # --- 2. THE REVEILLE ---
            now_mt = datetime.datetime.now(mt_tz)
            if (
                (now_mt.hour > 7 or (now_mt.hour == 7 and now_mt.minute >= 30))
                and last_heartbeat_day != now_mt.day
            ):
                heartbeat_msg = (
                    "🔔 <b>OPENING BELL STATUS</b>\n"
                    "US Markets are open. Anton is ONLINE and scanning."
                )
                await app.bot.send_message(
                    chat_id=secrets['telegram_user_id'],
                    text=heartbeat_msg,
                    parse_mode='HTML'
                )
                last_heartbeat_day = now_mt.day

            # --- EOD FORCE-EXIT ---
            # Close all open positions after 3:50 PM ET to avoid overnight exposure.
            # Configurable via eod_exit_time in Config.yaml (default: 15:50).
            ny_tz        = pytz.timezone('America/New_York')
            now_ny       = datetime.datetime.now(ny_tz)
            eod_hour     = config.get('strategy', {}).get('eod_exit_hour', 15)
            eod_minute   = config.get('strategy', {}).get('eod_exit_minute', 50)
            is_eod       = (now_ny.hour > eod_hour) or (now_ny.hour == eod_hour and now_ny.minute >= eod_minute)

            if is_eod and autonomous and not shadow:
                held_eod = execution.get_open_positions()
                if held_eod:
                    logger.info(f"EOD FORCE-EXIT: {list(held_eod.keys())} — closing before market close.")
                    await app.bot.send_message(
                        chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                        text=(f"🔔 <b>EOD Force-Exit</b>\n"
                              f"Closing {len(held_eod)} position(s) before market close.\n"
                              f"{', '.join(held_eod.keys())}")
                    )
                    closed: list[dict] = []
                    failed: list[str]  = []
                    pre_eod_equity = execution.get_account_balance()
                    for sym in list(held_eod.keys()):
                        pos          = database.get_open_position(sym)
                        entry_price  = pos['entry_price'] if pos else 0.0
                        entry_strat  = pos['strategy']    if pos else "EOD EXIT"
                        shares       = held_eod[sym]
                        d_eod        = await market_data.fetch_indicators(sym, config=config, timeframe=tf)
                        eod_price    = d_eod['price'] if d_eod else entry_price

                        if await asyncio.to_thread(execution.execute_take_profit, sym):
                            pnl_pct = ((eod_price - entry_price) / entry_price * 100) if entry_price else 0.0
                            pnl_usd = round((eod_price - entry_price) * shares, 2) if entry_price else 0.0
                            database.log_trade(sym, "SELL", eod_price, shares, entry_strat,
                                               f"EOD FORCE-EXIT: {pnl_pct:.1f}%")
                            database.close_open_position(sym)
                            logger.info(f"[{sym}] EOD EXIT complete at ${eod_price:.2f} | {pnl_pct:+.1f}%")
                            closed.append({
                                'symbol': sym, 'strategy': entry_strat,
                                'pnl_pct': pnl_pct, 'pnl_usd': pnl_usd,
                            })
                        else:
                            logger.error(f"[{sym}] EOD EXIT failed — order rejected by Alpaca")
                            failed.append(sym)

                    if closed:
                        wins      = sum(1 for c in closed if c['pnl_pct'] > 0)
                        losses    = len(closed) - wins
                        net_usd   = sum(c['pnl_usd'] for c in closed)
                        end_eq    = execution.get_account_balance()
                        session_pct = (net_usd / pre_eod_equity * 100) if pre_eod_equity > 0 else 0.0
                        net_emoji = "🟢" if net_usd > 0 else ("🔴" if net_usd < 0 else "⚪")

                        closed_lines = [
                            f"• <b>{c['symbol']}</b> — {c['strategy']} · {c['pnl_pct']:+.1f}% ({'+' if c['pnl_usd'] >= 0 else ''}${c['pnl_usd']:,.2f})"
                            for c in closed
                        ]
                        tally_lines = [
                            f"• <b>Results</b> — {wins}W / {losses}L",
                            f"• <b>Net P&amp;L</b> — {net_emoji} {'+' if net_usd >= 0 else ''}${net_usd:,.2f} ({session_pct:+.2f}% session)",
                            f"• <b>Equity at Bell</b> — ${end_eq:,.2f}",
                        ]
                        if failed:
                            tally_lines.append(f"• ⚠️ <b>Failed</b> — {', '.join(failed)}")

                        sections = [
                            "🔔 <b>EOD Force-Exit Complete</b>",
                            "<b>Closed Positions</b>\n" + "\n".join(closed_lines),
                            "<b>Day Tally</b>\n" + "\n".join(tally_lines),
                        ]
                        await app.bot.send_message(
                            chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                            text="\n\n".join(sections)
                        )
                await asyncio.sleep(600)  # Sleep 10 min — market will be closed on next cycle
                continue

            elif is_eod and shadow:
                # Shadow mode: close paper positions at EOD too
                shadow_eod = database.get_all_open_positions()
                if shadow_eod:
                    logger.info(f"EOD SHADOW EXIT: closing {len(shadow_eod)} paper position(s).")
                    closed: list[dict] = []
                    for pos in shadow_eod:
                        sym         = pos['symbol']
                        entry_price = pos['entry_price']
                        entry_strat = pos['strategy']
                        d_eod = await market_data.fetch_indicators(sym, config=config, timeframe=tf)
                        close_price = d_eod['price'] if d_eod else entry_price
                        shares      = pos.get('shares', 0.0)
                        pnl_pct = (close_price - entry_price) / entry_price * 100
                        pnl_usd = round((close_price - entry_price) * shares, 2)
                        database.log_trade(sym, 'SHADOW SELL', close_price, pnl_usd,
                                           entry_strat, f'EOD EXIT: {pnl_pct:.1f}%')
                        database.adjust_shadow_cash(shares * close_price)
                        database.close_open_position(sym)
                        logger.info(f"[{sym}] EOD SHADOW EXIT | {pnl_pct:+.1f}%")
                        closed.append({
                            'symbol': sym, 'strategy': entry_strat,
                            'pnl_pct': pnl_pct, 'pnl_usd': pnl_usd,
                        })

                    # Build the day-tally summary
                    wins      = sum(1 for c in closed if c['pnl_pct'] > 0)
                    losses    = len(closed) - wins
                    net_usd   = sum(c['pnl_usd'] for c in closed)
                    end_snap  = _get_account_snapshot(shadow=True)
                    initial   = database.get_shadow_initial_capital()
                    session_pct = (net_usd / (end_snap['equity'] - net_usd) * 100) \
                                  if (end_snap['equity'] - net_usd) > 0 else 0.0
                    net_emoji = "🟢" if net_usd > 0 else ("🔴" if net_usd < 0 else "⚪")

                    closed_lines = [
                        f"• <b>{c['symbol']}</b> — {c['strategy']} · {c['pnl_pct']:+.1f}% ({'+' if c['pnl_usd'] >= 0 else ''}${c['pnl_usd']:,.2f})"
                        for c in closed
                    ]
                    tally_lines = [
                        f"• <b>Results</b> — {wins}W / {losses}L",
                        f"• <b>Net P&amp;L</b> — {net_emoji} {'+' if net_usd >= 0 else ''}${net_usd:,.2f} ({session_pct:+.2f}% session)",
                        f"• <b>Equity at Bell</b> — ${end_snap['equity']:,.2f}",
                    ]
                    if initial > 0:
                        cum_pct = (end_snap['equity'] - initial) / initial * 100
                        tally_lines.append(f"• <b>Cumulative</b> — {cum_pct:+.2f}% (from ${initial:,.0f})")

                    sections = [
                        "🔔 <b>EOD Shadow Exit</b>",
                        "<b>Closed Positions</b>\n" + "\n".join(closed_lines),
                        "<b>Day Tally</b>\n" + "\n".join(tally_lines),
                    ]
                    await app.bot.send_message(
                        chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                        text="\n\n".join(sections)
                    )
                await asyncio.sleep(600)
                continue
            # Wallet snapshot — shadow ledger or live Alpaca, single dispatch.
            snap           = _get_account_snapshot(shadow)
            total_equity   = snap['equity']
            held_assets    = snap['held_assets']
            max_trades     = config.get('strategy', {}).get('max_open_trades', 2)
            is_at_capacity = len(held_assets) >= max_trades

            # --- SESSION-START EQUITY CAPTURE ---
            # Capture once per ET trading day. Used by daily session loss circuit.
            ny_today_str = datetime.datetime.now(et_tz).date().isoformat()
            risk_state   = database.get_risk_state()
            if risk_state['session_date'] != ny_today_str:
                database.update_risk_state(
                    session_date=ny_today_str,
                    session_start_equity=total_equity,
                    daily_halt=False,
                )
                risk_state = database.get_risk_state()
                logger.info(f"[SESSION] New session {ny_today_str}: start equity ${total_equity:,.2f}")

            # --- TIERED DRAWDOWN STATE MACHINE ---
            # Peak-based: NORMAL → ALERT → DERISK → HALT, with hysteresis on
            # DERISK exit. Replaces the old single-threshold halt.
            peak_equity = database.get_equity_peak()
            if peak_equity == 0.0:
                database.update_equity_peak(total_equity)
                peak_equity = total_equity
            elif total_equity > peak_equity:
                database.update_equity_peak(total_equity)
                peak_equity = total_equity

            drawdown_pct = max(0.0, (peak_equity - total_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
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

            # Hysteresis: stay in DERISK until drawdown shrinks below recovery_pct
            prev_mode = risk_state['risk_mode']
            if prev_mode == 'DERISK' and target_mode in ('NORMAL', 'ALERT') and drawdown_pct >= recovery_pct:
                target_mode = 'DERISK'
            # Don't auto-exit HALT — only /resume clears it
            if prev_mode == 'HALT' and target_mode != 'HALT':
                target_mode = 'HALT'

            if target_mode != prev_mode:
                database.update_risk_state(risk_mode=target_mode)
                risk_state['risk_mode'] = target_mode
                icon = {'NORMAL':'✅', 'ALERT':'⚠️', 'DERISK':'🟡', 'HALT':'🚨'}[target_mode]
                logger.warning(f"RISK MODE: {prev_mode} → {target_mode} (drawdown {drawdown_pct:.1f}%)")
                msg = (f"{icon} <b>RISK MODE → {target_mode}</b>\n"
                       f"Drawdown <b>{drawdown_pct:.1f}%</b> from peak "
                       f"(${peak_equity:,.2f} → ${total_equity:,.2f})")
                if target_mode == 'DERISK':
                    mult = risk_cfg.get('derisk_size_multiplier', 0.5)
                    msg += f"\nPosition sizing × {mult} until recovery to {recovery_pct}%"
                elif target_mode == 'HALT':
                    msg += f"\n⛔ Trading halted. Open stops active. Use /resume after review."
                elif target_mode == 'NORMAL' and prev_mode in ('DERISK', 'ALERT'):
                    msg += "\nFull sizing restored."
                await app.bot.send_message(
                    chat_id=secrets['telegram_user_id'], parse_mode='HTML', text=msg
                )

            # HALT mode short-circuits the rest of the cycle. Existing stops
            # at Alpaca remain active; shadow exit engine still runs on its
            # next scheduled cycle since `continue` only exits this iteration.
            if risk_state['risk_mode'] == 'HALT':
                await asyncio.sleep(60)
                continue

            # --- DAILY SESSION LOSS CIRCUIT ---
            # Captures hard intraday losses regardless of peak. Auto-clears at
            # next ET session. Existing positions continue normal exit logic.
            if not risk_state['daily_halt'] and risk_state['session_start_equity']:
                sse        = risk_state['session_start_equity']
                daily_dd   = (sse - total_equity) / sse * 100 if sse > 0 else 0.0
                daily_lim  = risk_cfg.get('daily_session_loss_pct', 3.0)
                if daily_dd >= daily_lim:
                    database.update_risk_state(daily_halt=True)
                    risk_state['daily_halt'] = True
                    logger.warning(f"DAILY SESSION LOSS: {daily_dd:.2f}% from session start. Entries blocked.")
                    await app.bot.send_message(
                        chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                        text=(f"🛑 <b>DAILY SESSION LOSS LIMIT</b>\n"
                              f"Session down <b>{daily_dd:.2f}%</b> from open "
                              f"(${sse:,.2f} → ${total_equity:,.2f}).\n"
                              f"Limit: {daily_lim}%. New entries blocked rest of session.\n"
                              f"Open positions follow normal exit logic. Auto-resume next session.")
                    )

            # --- GHOST SELL DETECTOR (live mode only) ---
            # In shadow mode the closure paths are explicit (stop-hit, take-profit,
            # EOD, earnings, kill switch) so there are no "ghost" sells to detect.
            if not shadow and previous_holdings:
                for coin, prev_qty in previous_holdings.items():
                    if coin not in held_assets or held_assets.get(coin, 0) < (prev_qty * 0.1):
                        alert = (
                            f"🚨 <b>POSITION CLOSED</b>\n"
                            f"Anton detects your {coin} position was sold "
                            f"(stop hit or manual close)."
                        )
                        await app.bot.send_message(
                            chat_id=secrets['telegram_user_id'],
                            text=alert,
                            parse_mode='HTML'
                        )
                        database.close_open_position(coin)

            previous_holdings = held_assets.copy() if not shadow else {}

            # --- EARNINGS FORCE-EXIT ---
            # If earnings are tomorrow or sooner, close any existing position now.
            exit_days = config.get('strategy', {}).get('earnings_blackout_days', 2)
            for sym in list(held_assets.keys()):
                if earnings.should_force_exit(sym, exit_days_before=exit_days):
                    logger.info(f"[{sym}] EARNINGS FORCE-EXIT triggered.")
                    if autonomous and not shadow:
                        if await asyncio.to_thread(execution.execute_take_profit, sym):
                            pos = database.get_open_position(sym)
                            database.log_trade(sym, "SELL", 0.0, held_assets[sym],
                                               pos['strategy'] if pos else "EARNINGS EXIT",
                                               "EARNINGS FORCE-EXIT")
                            database.close_open_position(sym)
                            await app.bot.send_message(
                                chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                                text=f"⚠️ <b>EARNINGS FORCE-EXIT</b>\n{sym} closed — earnings imminent."
                            )
                    elif shadow:
                        pos = database.get_open_position(sym)
                        if pos:
                            d_ef = await market_data.fetch_indicators(sym, config=config, timeframe=tf)
                            close_price = d_ef['price'] if d_ef else pos['entry_price']
                            shares = pos.get('shares', 0.0)
                            pnl_pct = (close_price - pos['entry_price']) / pos['entry_price'] * 100
                            pnl_usd = round((close_price - pos['entry_price']) * shares, 2)
                            database.log_trade(sym, 'SHADOW SELL', close_price, pnl_usd,
                                               pos['strategy'], f'EARNINGS EXIT: {pnl_pct:.1f}%')
                            database.adjust_shadow_cash(shares * close_price)
                            database.close_open_position(sym)
                            await app.bot.send_message(
                                chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                                text=f"⚠️ <b>EARNINGS SHADOW EXIT</b>\n{sym} | {pnl_pct:+.1f}%"
                            )

            # --- SHADOW EXIT ENGINE ---
            # Evaluates all open paper positions every cycle against the live
            # price feed. Mirrors Tiberius's shadow exit logic exactly.
            if shadow:
                shadow_positions = database.get_all_open_positions()
                for pos in shadow_positions:
                    sym         = pos['symbol']
                    entry_price = pos['entry_price']
                    entry_strat = pos['strategy']
                    entry_atr   = pos['entry_atr']
                    cur_stop    = pos['current_stop']

                    d_shd = await market_data.fetch_indicators(sym, config=config, timeframe=tf)
                    if not d_shd:
                        continue

                    latest_prices[sym] = d_shd['price']  # cache for shadow equity calc next cycle
                    d_shd['symbol'] = sym  # Required by strategy.py for symbol_overrides lookup
                    sym_cfg    = config_manager.get_symbol_config(config, sym)
                    trail_mult = config_manager.get_ratchet_multiplier(sym_cfg)

                    # Ratchet the trailing stop upward if price has moved in our favour
                    new_stop = d_shd['price'] - (d_shd['atr'] * trail_mult)
                    if new_stop > cur_stop > 0:
                        database.update_shadow_stop(sym, new_stop)
                        cur_stop = new_stop

                    # Bootstrap stop on first cycle if entry_atr was recorded
                    if cur_stop == 0.0 and entry_atr > 0:
                        i_mult   = sym_cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)
                        cur_stop = entry_price - (entry_atr * i_mult)
                        database.update_shadow_stop(sym, cur_stop)

                    pnl_pct = (d_shd['price'] - entry_price) / entry_price * 100

                    # Stop hit check
                    if cur_stop > 0 and d_shd['price'] <= cur_stop:
                        shares       = pos.get('shares', 0.0)
                        stop_pnl     = (cur_stop - entry_price) / entry_price * 100
                        stop_pnl_usd = round((cur_stop - entry_price) * shares, 2)
                        database.log_trade(sym, 'SHADOW SELL', cur_stop, stop_pnl_usd,
                                           entry_strat, f'STOP HIT: {stop_pnl:.1f}%')
                        database.adjust_shadow_cash(shares * cur_stop)
                        database.close_open_position(sym)
                        logger.info(f"[{sym}] SHADOW STOP HIT | {stop_pnl:.1f}% | ${entry_price:.2f}→${cur_stop:.2f}")

                        # --- TUNER HOOKS ---
                        shadow_sell_counts[sym] = shadow_sell_counts.get(sym, 0) + 1
                        promo_msgs = tuner.check_promotions(sym)
                        min_trades = config.get('tuning', {}).get('min_trades_to_tune', 10)
                        if shadow_sell_counts[sym] % min_trades == 0:
                            tuner.run_tuning_cycle(ACTIVE_SYMBOLS)

                        dir_emoji = "🟢" if stop_pnl > 0 else ("🔴" if stop_pnl < 0 else "⚪")
                        await app.bot.send_message(
                            chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                            text=(f"🛑 <b>SHADOW STOP HIT</b> {sym}\n"
                                  f"{dir_emoji} {entry_strat} · {stop_pnl:+.1f}% · "
                                  f"${entry_price:.2f}→${cur_stop:.2f}")
                        )
                        continue

                    # Take profit check
                    exit_cmd = strategy.check_exit_signals(
                        d_shd, entry_strat, cur_stop, entry_price, config)
                    if exit_cmd['action'] == 'TAKE_PROFIT':
                        shares     = pos.get('shares', 0.0)
                        tp_pnl_usd = round((d_shd['price'] - entry_price) * shares, 2)
                        database.log_trade(sym, 'SHADOW SELL', d_shd['price'], tp_pnl_usd,
                                           entry_strat, f'TAKE PROFIT: {pnl_pct:.1f}%')
                        database.adjust_shadow_cash(shares * d_shd['price'])
                        database.close_open_position(sym)
                        logger.info(f"[{sym}] SHADOW TAKE PROFIT | +{pnl_pct:.1f}% | ${entry_price:.2f}→${d_shd['price']:.2f}")

                        # --- TUNER HOOKS ---
                        shadow_sell_counts[sym] = shadow_sell_counts.get(sym, 0) + 1
                        promo_msgs = tuner.check_promotions(sym)
                        for msg in promo_msgs:
                            await app.bot.send_message(
                                chat_id=secrets['telegram_user_id'],
                                text=msg, parse_mode='HTML'
                            )
                        min_trades = config.get('tuning', {}).get('min_trades_to_tune', 10)
                        if shadow_sell_counts[sym] % min_trades == 0:
                            tuner.run_tuning_cycle(ACTIVE_SYMBOLS)

                        await app.bot.send_message(
                            chat_id=secrets['telegram_user_id'], parse_mode='HTML',
                            text=(f"💰 <b>SHADOW TAKE PROFIT</b> {sym}\n"
                                  f"🟢 {entry_strat} · +{pnl_pct:.1f}% · "
                                  f"${entry_price:.2f}→${d_shd['price']:.2f}")
                        )

            # --- 4. THE SCANNER ---
            mode_str = "SHADOW" if shadow else "LIVE"
            logger.info(f"--- CYCLE START | Mode: {mode_str} | Timeframe: {tf} ---")
            for symbol in ACTIVE_SYMBOLS:
                d = await market_data.fetch_indicators(
                    symbol, config=config, timeframe=tf
                )
                if not d:
                    logger.warning(f"[{symbol}] Data fetch failed, skipping.")
                    continue

                latest_prices[symbol] = d['price']  # cache for shadow equity calc
                d['symbol'] = symbol  # Required by strategy.py for symbol_overrides lookup
                d['daily_trend'] = await _get_daily_trend(symbol, config)
                logger.info(
                    f"[{symbol}] ${d['price']:.2f} | {d['regime']} | "
                    f"ADX={d['adx']:.1f} | RSI={d['rsi']:.1f} | Trend={d['trend']} | Daily={d['daily_trend']}"
                )
                database.log_market_state(
                    symbol, d['price'], d['adx'], d['regime'], d['trend'], d['rsi'],
                    volume=d.get('volume'), avg_volume=d.get('avg_volume')
                )
                is_setup, strategy_type = strategy.check_entry_signals(
                    d, config=config
                )
                logger.info(f"[{symbol}] Entry: setup={is_setup} | paradigm={strategy_type}")

                # --- PYRAMID LEG ENGINE (Phase 7) ---
                # When the bar produces a fresh same-paradigm setup on a held
                # position, evaluate adding a leg. Runs BEFORE the fresh-entry
                # block — the existing block's `if symbol in held_assets: continue`
                # prevents duplicate buys whether or not the leg fires.
                pyramid_cfg = config.get('pyramiding', {})
                if (is_setup and symbol in held_assets and pyramid_cfg.get('enabled', False)
                        and not risk_state['daily_halt']
                        and risk_state['risk_mode'] != 'HALT'):
                    pos = database.get_open_position(symbol)
                    if pos:
                        eligible, reason = _check_pyramid_eligibility(
                            symbol, strategy_type, d, pos, config
                        )
                        if not eligible:
                            logger.debug(f"[{symbol}] PYRAMID skipped: {reason}")
                        else:
                            blackout_days = config.get('strategy', {}).get('earnings_blackout_days', 2)
                            if earnings.is_earnings_blackout(symbol, blackout_days):
                                logger.info(f"[{symbol}] PYRAMID skipped: earnings blackout")
                            else:
                                # Sizing: fresh size × DERISK × CORR × decay^(leg_count)
                                base_size = execution.calculate_position_size(
                                    total_equity, d['atr'], d['price'], config.get('risk', {})
                                )
                                if base_size > 0 and risk_state['risk_mode'] == 'DERISK':
                                    base_size *= config.get('risk', {}).get('derisk_size_multiplier', 0.5)
                                if base_size > 0:
                                    base_size *= _correlation_multiplier(symbol, held_assets, config)
                                decay = pyramid_cfg.get('size_decay', 0.5)
                                # leg_count is the number of existing legs; this leg will be #(leg_count+1).
                                # Decay exponent = leg_count so leg 2 → ×0.5, leg 3 → ×0.25.
                                leg_size_usd = base_size * (decay ** pos['leg_count'])
                                min_size = config.get('risk', {}).get('min_trade_size_usd', 12.0)

                                if leg_size_usd < min_size:
                                    logger.info(
                                        f"[{symbol}] PYRAMID leg #{pos['leg_count']+1} sized "
                                        f"${leg_size_usd:.2f} below min ${min_size} — skip"
                                    )
                                elif leg_size_usd > total_equity:
                                    logger.info(f"[{symbol}] PYRAMID skipped: insufficient equity")
                                else:
                                    leg_shares = math.floor(leg_size_usd / d['price'])
                                    leg_cost   = leg_shares * d['price']
                                    if leg_shares < 1:
                                        logger.info(f"[{symbol}] PYRAMID skipped: <1 share at ${d['price']:.2f}")
                                    elif shadow:
                                        # Shadow path: ledger-based, T+1 not relevant
                                        shadow_cash = database.get_shadow_cash()
                                        if leg_cost > shadow_cash:
                                            logger.info(
                                                f"[{symbol}] PYRAMID skipped: leg ${leg_cost:.2f} > "
                                                f"shadow cash ${shadow_cash:.2f}"
                                            )
                                        else:
                                            consensus = await _run_consensus_layers_2_3(d, symbol, strategy_type, config)
                                            if not consensus['passed']:
                                                if consensus['reason'] == 'bearish_abort':
                                                    logger.info(f"[{symbol}] PYRAMID BEARISH ABORT")
                                                    database.log_trade(
                                                        symbol, "BEARISH ABORT", d['price'], 0,
                                                        strategy_type, (consensus['full_verdict'] or '')[:300]
                                                    )
                                            else:
                                                if consensus['is_bullish']:
                                                    leg_num = pos['leg_count'] + 1
                                                    database.log_trade(
                                                        symbol, "SHADOW BUY ADD", d['price'],
                                                        leg_shares, strategy_type,
                                                        f"[LEG {leg_num}] {consensus['tagged_verdict']}"
                                                    )
                                                    database.add_pyramid_leg(symbol, d['price'], d['atr'], leg_shares)
                                                    database.adjust_shadow_cash(-leg_cost)
                                                    msg = (
                                                        f"👻 <b>SHADOW BUY ADD</b> — Leg {leg_num}/"
                                                        f"{pyramid_cfg['max_legs'][_PARADIGM_TO_PYRAMID_KEY[strategy_type]]}\n"
                                                        f"{symbol} {strategy_type}\n"
                                                        f"{leg_shares} sh × ${d['price']:.2f} = ${leg_cost:,.2f}"
                                                    )
                                                    await app.bot.send_message(
                                                        chat_id=secrets['telegram_user_id'],
                                                        text=msg, parse_mode='HTML'
                                                    )
                                    elif autonomous:
                                        # Live path: T+1 settled-cash guard
                                        try:
                                            acct = await asyncio.to_thread(execution.trading_client.get_account)
                                            settled = float(getattr(acct, 'non_marginable_buying_power', 0.0) or 0.0)
                                        except Exception as e:
                                            logger.warning(f"[{symbol}] PYRAMID buying-power check failed: {e}")
                                            settled = 0.0
                                        if leg_cost > settled:
                                            logger.info(
                                                f"[{symbol}] PYRAMID skipped: T+1 settled cash "
                                                f"${settled:.2f} < leg ${leg_cost:.2f}"
                                            )
                                        else:
                                            consensus = await _run_consensus_layers_2_3(d, symbol, strategy_type, config)
                                            if not consensus['passed']:
                                                if consensus['reason'] == 'bearish_abort':
                                                    logger.info(f"[{symbol}] PYRAMID BEARISH ABORT")
                                                    database.log_trade(
                                                        symbol, "BEARISH ABORT", d['price'], 0,
                                                        strategy_type, (consensus['full_verdict'] or '')[:300]
                                                    )
                                            else:
                                                if consensus['is_bullish']:
                                                    success, sl_price = await asyncio.to_thread(
                                                        execution.execute_buy_with_stop,
                                                        symbol, leg_cost, d['price'], d['atr']
                                                    )
                                                    if success:
                                                        leg_num = pos['leg_count'] + 1
                                                        database.log_trade(
                                                            symbol, "BUY ADD", d['price'],
                                                            leg_shares, strategy_type,
                                                            f"[LEG {leg_num}] {consensus['tagged_verdict']}"
                                                        )
                                                        database.add_pyramid_leg(symbol, d['price'], d['atr'], leg_shares)
                                                        msg = (
                                                            f"🎯 <b>BUY ADD</b> — Leg {leg_num}/"
                                                            f"{pyramid_cfg['max_legs'][_PARADIGM_TO_PYRAMID_KEY[strategy_type]]}\n"
                                                            f"{symbol} {strategy_type}\n"
                                                            f"{leg_shares} sh × ${d['price']:.2f} = ${leg_cost:,.2f}\n"
                                                            f"Stop: ${sl_price:.2f}"
                                                        )
                                                        await app.bot.send_message(
                                                            chat_id=secrets['telegram_user_id'],
                                                            text=msg, parse_mode='HTML'
                                                        )

                # --- ENTRY STRATEGY ENGINE ---
                if is_setup:
                    # Daily session loss circuit blocks all new entries until next session
                    if risk_state['daily_halt']:
                        logger.info(f"[{symbol}] DAILY HALT — entry blocked until next session")
                        continue

                    size_usd = execution.calculate_position_size(
                        total_equity, d['atr'], d['price'], config.get('risk', {})
                    )

                    # DERISK mode scales sizing down. Re-check minimum after scaling
                    # so we don't fire sub-minimum trades.
                    if size_usd > 0 and risk_state['risk_mode'] == 'DERISK':
                        derisk_mult = config.get('risk', {}).get('derisk_size_multiplier', 0.5)
                        size_usd *= derisk_mult

                    # Correlation-aware sizing: scale down by count of same-sector
                    # open positions. Composes with DERISK (multipliers stack).
                    if size_usd > 0:
                        corr_mult = _correlation_multiplier(symbol, held_assets, config)
                        if corr_mult < 1.0:
                            pre_corr = size_usd
                            size_usd *= corr_mult
                            logger.info(
                                f"[{symbol}] CORR: same-sector open → "
                                f"size ${pre_corr:.2f} × {corr_mult:.2f} = ${size_usd:.2f}"
                            )

                    # Re-check minimum after all sizing multipliers
                    min_size = config.get('risk', {}).get('min_trade_size_usd', 12.0)
                    if size_usd > 0 and size_usd < min_size:
                        logger.info(f"[{symbol}] Scaled size ${size_usd:.2f} below min ${min_size} — skipping")
                        continue

                    if size_usd <= 0:          continue
                    if symbol in held_assets:  continue
                    if is_at_capacity:         continue
                    if total_equity < size_usd: continue

                    # --- EARNINGS BLACKOUT GATE ---
                    blackout_days = config.get('strategy', {}).get('earnings_blackout_days', 2)
                    if earnings.is_earnings_blackout(symbol, blackout_days):
                        logger.info(f"[{symbol}] EARNINGS BLACKOUT — skipping entry")
                        continue

                    # --- LAYER 2: SUPPORTING SIGNALS ---
                    support_score, support_reasons = strategy.check_supporting_signals(
                        d, strategy_type, config=config
                    )
                    consensus_score = 1 + support_score   # primary signal = 1
                    min_score       = config.get('consensus', {}).get('min_consensus_score', 3)
                    reasons_str     = " | ".join(support_reasons)
                    logger.info(f"[{symbol}] CONSENSUS: {consensus_score}/{min_score} | {reasons_str}")

                    if consensus_score < min_score:
                        logger.info(f"[{symbol}] CONSENSUS FAIL — skipping AI gate")
                        continue

                    # --- LAYER 3: AI GATE (three-tier verdict) ---
                    llm_cfg        = config.get('ai_agent', {}).get('sentiment_analysis', {})
                    llm_reasoning  = config.get('consensus', {}).get('llm_reasoning', True)
                    verdict, full_verdict = await ai_brain.get_ai_consensus(
                        symbol, d['price'], secrets['brave_api_key'],
                        llm_cfg.get('api_base'), llm_cfg.get('model_id'),
                        paradigm=strategy_type,
                        support_reasons=support_reasons,
                        indicators=d,
                        llm_reasoning=llm_reasoning
                    )

                    bearish_abort = config.get('consensus', {}).get('bearish_abort', True)
                    if bearish_abort and verdict == "BEARISH":
                        logger.info(f"[{symbol}] BEARISH ABORT — trade blocked by AI gate")
                        database.log_trade(symbol, "BEARISH ABORT", d['price'], 0, strategy_type, full_verdict[:300])
                        continue

                    is_bullish = (verdict == "BULLISH")
                    full_score_tag = f"[SCORE:{consensus_score}/{min_score} | {reasons_str}]"
                    tagged_verdict = f"{full_score_tag} {full_verdict}"

                    if autonomous and not shadow:
                        if is_bullish:
                            # Off-thread the blocking Alpaca submit + fill poll.
                            success, sl_price = await asyncio.to_thread(
                                execution.execute_buy_with_stop,
                                symbol, size_usd, d['price'], d['atr']
                            )
                            if success:
                                database.log_trade(
                                    symbol, "PAPER BUY", d['price'],
                                    size_usd / d['price'], strategy_type, tagged_verdict
                                )
                                database.record_open_position(symbol, d['price'], strategy_type, entry_atr=d['atr'])
                                msg = (
                                    f"🎯 <b>PAPER BUY EXECUTED</b>\n"
                                    f"Strategy: {strategy_type}\n"
                                    f"Bought ${size_usd:.2f} of {symbol}\n"
                                    f"Stop: ${sl_price:.2f}\n"
                                    f"Reason: {full_verdict}"
                                )
                                await app.bot.send_message(
                                    chat_id=secrets['telegram_user_id'],
                                    text=msg, parse_mode='HTML'
                                )

                    elif autonomous and shadow:
                        if is_bullish:
                            shadow_shares = math.floor(size_usd / d['price'])
                            if shadow_shares < 1:
                                continue
                            shadow_cost = shadow_shares * d['price']
                            shadow_cash = database.get_shadow_cash()
                            if shadow_cost > shadow_cash:
                                logger.info(
                                    f"[{symbol}] SHADOW BUY skipped — "
                                    f"need ${shadow_cost:,.2f}, have ${shadow_cash:,.2f}"
                                )
                                continue
                            database.log_trade(
                                symbol, "SHADOW BUY", d['price'],
                                shadow_shares, strategy_type, tagged_verdict
                            )
                            database.record_open_position(symbol, d['price'], strategy_type,
                                                          entry_atr=d['atr'], shares=shadow_shares)
                            database.adjust_shadow_cash(-shadow_cost)
                            msg = (
                                f"👻 <b>SHADOW BUY</b>\n"
                                f"Strategy: {strategy_type}\n"
                                f"Asset: {symbol}\n"
                                f"Reason: {full_verdict}"
                            )
                            await app.bot.send_message(
                                chat_id=secrets['telegram_user_id'],
                                text=msg, parse_mode='HTML'
                            )

                    else:
                        # MANUAL MODE: always surface the signal regardless of AI verdict
                        sentiment_tag = "🟢 BULLISH" if is_bullish else "🔴 BEARISH/NEUTRAL"
                        msg = (
                            f"📈 <b>BUY SCANNED ({strategy_type})</b>\n"
                            f"Symbol: {symbol}\n"
                            f"Consensus: {consensus_score}/{min_score} | {reasons_str}\n"
                            f"AI Sentiment: {sentiment_tag}\n\n"
                            f"Verdict: {html_escape(full_verdict)}"
                        )
                        await app.bot.send_message(
                            chat_id=secrets['telegram_user_id'],
                            text=msg, parse_mode='HTML'
                        )

                # --- EXIT STRATEGY ENGINE (live-only) ---
                # Shadow positions are exited by the SHADOW EXIT ENGINE block above
                # which uses DB-stored stops, not Alpaca's. Skipping in shadow mode
                # avoids redundant Alpaca queries and the leak of live stop prices.
                if shadow:
                    continue

                if symbol in held_assets:
                    position_info = database.get_open_position(symbol)
                    if position_info:
                        entry_strategy = position_info['strategy']
                        entry_price    = position_info['entry_price']
                    else:
                        entry_strategy = (
                            "MEAN REVERSION" if d['regime'] == "RANGING"
                            else "TREND FOLLOWING"
                        )
                        entry_price = None

                    current_stop = execution._get_current_stop_price(symbol)
                    exit_cmd = strategy.check_exit_signals(
                        d, entry_strategy, current_stop, entry_price=entry_price
                    )

                    # MEAN REVERSION & LIQUIDITY SWEEP EXIT
                    if exit_cmd['action'] == 'TAKE_PROFIT':
                        if autonomous and not shadow:
                            if await asyncio.to_thread(execution.execute_take_profit, symbol):
                                qty_sold = held_assets.get(symbol, 0)

                                database.log_trade(
                                    symbol, "SELL", d['price'], qty_sold,
                                    f"{entry_strategy} (TAKE PROFIT)", exit_cmd['reason']
                                )
                                database.close_open_position(symbol)
                                await app.bot.send_message(
                                    chat_id=secrets['telegram_user_id'],
                                    text=(
                                        f"💰 <b>TAKE PROFIT</b>\n"
                                        f"Sold {qty_sold} shares of {symbol} "
                                        f"at ${d['price']:.2f}.\n{exit_cmd['reason']}"
                                    ),
                                    parse_mode='HTML'
                                )
                        elif not autonomous:
                            await app.bot.send_message(
                                chat_id=secrets['telegram_user_id'],
                                text=(
                                    f"🎯 <b>TAKE PROFIT TARGET</b>\n"
                                    f"{symbol} at ${d['price']:.2f}. "
                                    f"Consider manual exit."
                                ),
                                parse_mode='HTML'
                            )

                    # TREND FOLLOWING & BREAKOUT EXIT: The Ratchet
                    # MANUAL OVERRIDE included so manual buys can also ratchet.
                    elif entry_strategy in ["TREND FOLLOWING", "VOLATILITY BREAKOUT", "MANUAL OVERRIDE"]:
                        mult         = config_manager.get_ratchet_multiplier(config)
                        suggested_sl = round(d.get('high_closed', d['high']) - (d['atr'] * mult), 2)

                        profit_buffer_met = (
                            d['price'] > (current_stop * 1.02) if current_stop > 0 else False
                        )

                        # 1.5% threshold prevents Alpaca/Telegram spam over pennies
                        if (
                            profit_buffer_met
                            and suggested_sl > current_stop * 1.015
                            and suggested_sl < d['price']
                        ):
                            if autonomous and not shadow:
                                if await asyncio.to_thread(execution.execute_ratchet_stop, symbol, suggested_sl):
                                    database.log_trade(
                                        symbol, "RATCHET", d['price'], 0,
                                        "TRAILING STOP",
                                        f"Stop moved to ${suggested_sl:.2f}"
                                    )
                                    await app.bot.send_message(
                                        chat_id=secrets['telegram_user_id'],
                                        text=(
                                            f"🛡️ <b>RATCHET</b>\n"
                                            f"{symbol} Stop: "
                                            f"${current_stop:.2f} → ${suggested_sl:.2f}"
                                        ),
                                        parse_mode='HTML'
                                    )

        except Exception as e:
            consecutive_errors += 1
            backoff_minutes     = min(consecutive_errors * 2, 30)
            logger.error(f"Loop Error (#{consecutive_errors}, retry in {backoff_minutes}m): {e}", exc_info=True)

            if consecutive_errors == 3:
                try:
                    await app.bot.send_message(
                        chat_id=secrets['telegram_user_id'],
                        text=(
                            f"⚠️ <b>REPEATED ERRORS ({consecutive_errors}x)</b>\n"
                            f"Last: {str(e)[:200]}\n"
                            f"Backoff: {backoff_minutes}m"
                        ),
                        parse_mode='HTML'
                    )
                except Exception:
                    pass

            await asyncio.sleep(backoff_minutes * 60)
            continue

        consecutive_errors = 0
        interval = config.get('strategy', {}).get('update_interval_min', 5) if config else 5
        await asyncio.sleep(interval * 60)


# ------------------------------------------------------------------------------
# 4. SYSTEM BOOTSTRAP
# ------------------------------------------------------------------------------

async def main():
    # Install SIGTERM/SIGINT handlers BEFORE we register Telegram polling so a
    # `docker stop` flips system_active to False and we shut down gracefully.
    _install_signal_handlers()

    app = (
        Application.builder()
        .token(secrets['telegram_bot_token'])
        .read_timeout(15)
        .write_timeout(15)
        .build()
    )

    app.add_handler(CommandHandler("indicators",   cmd_indicators))
    app.add_handler(CommandHandler("report",       cmd_report))
    app.add_handler(CommandHandler("pnl",          cmd_pnl))
    app.add_handler(CommandHandler("buy",          cmd_buy))
    app.add_handler(CommandHandler("protect",      cmd_protect))
    app.add_handler(CommandHandler("kill",         cmd_kill))
    app.add_handler(CommandHandler("confirm_kill", cmd_confirm_kill))
    app.add_handler(CommandHandler("resume",       cmd_resume))
    app.add_handler(CommandHandler("restart",      cmd_restart))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("start",        cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Register the command menu so users see tappable autocomplete when they
    # type `/`. Best-effort — failure here is non-fatal.
    try:
        await app.bot.set_my_commands([
            BotCommand("indicators",   "Technical readout for all symbols"),
            BotCommand("report",       "Portfolio audit"),
            BotCommand("pnl",          "Shadow P&L report"),
            BotCommand("buy",          "Manual buy: /buy SYMBOL USD"),
            BotCommand("protect",      "Place stops on naked positions"),
            BotCommand("kill",         "Begin emergency liquidation"),
            BotCommand("confirm_kill", "Confirm liquidation"),
            BotCommand("resume",       "Clear drawdown halt"),
            BotCommand("restart",      "Queue clean engine restart"),
            BotCommand("help",         "Show this command list"),
        ])
    except Exception as e:
        logger.warning(f"set_my_commands failed (non-fatal): {e}")

    await app.bot.send_message(
        chat_id=secrets['telegram_user_id'],
        text=(
            "👔 <b>Anton V1 (TradFi) ONLINE</b>\n"
            "Systems Nominal. Connected to Alpaca Paper Trading."
        ),
        parse_mode='HTML'
    )

    loop_task = asyncio.create_task(adaptive_manager_loop(app))

    # Idle until the engine loop signals shutdown (SIGTERM, restart flag, or
    # an unhandled error in the loop). Polling system_active beats sleeping
    # for an hour because docker stop's grace window is 10s by default.
    while system_active:
        await asyncio.sleep(2)

    logger.info("Shutdown requested; tearing down Telegram + scanner loop.")
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception as e:
        logger.warning(f"Telegram shutdown error: {e}")

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        system_active = False
        logger.info("Anton offline.")
