"""
Sulla — Phase 3 engine.

Cycles every `update_interval_min` minutes:
  1. Touches heartbeat (docker healthcheck)
  2. Checks the restart flag (calls os._exit(0) if set so compose respawns)
  3. Loads Config.yaml (hot-reloaded — dashboard edits take effect next cycle)
  4. **Shadow exit engine** — checks every open shadow position for stop hits
     or take-profits against current price; closes them and updates the ledger.
  5. Fetches OHLCV + indicators for every symbol in active_symbols
  6. Writes the indicator snapshot to market_states
  7. **Consensus + shadow buy** — for each symbol with no open position:
       (a) Layer 1: check_entry_signals (does any paradigm fire?)
       (b) Layer 2: check_supporting_signals (2 of 3 confirm?)
       (c) Score gate: total >= min_consensus_score
       (d) Layer 3: ai_brain.get_ai_consensus (BULLISH / NEUTRAL / BEARISH)
       (e) If all pass: compute units via fx_math, log SHADOW BUY, debit
           synthetic cash, persist position with ATR stop.
  8. Sleeps with mid-sleep restart-flag wakeups every 30s.

No Telegram bot yet (Phase 3b). No live Oanda orders ever in Phase 3 — the
shadow contract keeps every decision in-DB even though we're pulling real
market data.
"""

import os
import sys
import time
import signal
import asyncio
import logging
from pathlib import Path

import config_manager
import database
import market_data
import strategy
import ai_brain
import execution
import fx_math

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sulla")

# ─── Paths (env-driven, matches Anton/Tiberius convention) ──────────────────
HEARTBEAT_PATH    = Path(os.environ.get('HEARTBEAT_PATH',    '/app/data/.engine_heartbeat'))
RESTART_FLAG_PATH = Path(os.environ.get('RESTART_FLAG_PATH', '/app/data/.restart_engine'))

# ─── Secrets (loaded once at module init) ───────────────────────────────────
secrets = config_manager.load_secrets()


# ─── Signal handling ────────────────────────────────────────────────────────
_shutting_down = False

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


# ─── Restart-flag plumbing ──────────────────────────────────────────────────
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


# ─── Shadow exit engine ─────────────────────────────────────────────────────
async def _run_shadow_exit_engine(config: dict, latest_indicators: dict) -> None:
    """
    Iterates every open shadow position and checks for:
      (a) Stop hit — current price ≤ current_stop
      (b) Take profit — paradigm-specific exit signal from strategy.py
      (c) Trailing stop ratchet — new stop above current stop on positive move

    All exits write SHADOW SELL to the trades log with the realized P&L USD
    in the `amount` column. The synthetic cash ledger is credited for the
    return-of-capital + P&L.
    """
    open_positions = database.get_all_open_positions()
    if not open_positions:
        return

    for pos in open_positions:
        sym = pos['symbol']
        d = latest_indicators.get(sym)
        if not d:
            # No fresh data this cycle (Oanda outage or symbol dropped from
            # watchlist) — leave the position alone, the next cycle handles it.
            continue

        entry_price = pos['entry_price']
        entry_strat = pos['strategy']
        units       = pos.get('shares', 0.0)  # 'shares' column reused for FX units
        cur_stop    = pos.get('current_stop') or 0.0
        entry_atr   = pos.get('entry_atr', 0.0)
        price       = d['price']
        atr         = d['atr']

        # ── A. Stop hit ────────────────────────────────────────────────────
        if cur_stop > 0 and price <= cur_stop:
            pnl_usd = fx_math.position_notional_usd(sym, units, cur_stop) \
                    - fx_math.position_notional_usd(sym, units, entry_price)
            pnl_pct = ((cur_stop - entry_price) / entry_price * 100) if entry_price else 0.0
            verdict = f'STOP HIT: {pnl_pct:.2f}%'
            database.log_trade(sym, 'SHADOW SELL', cur_stop, round(pnl_usd, 2),
                               entry_strat, verdict)
            # Credit the synthetic ledger with exit notional (entry notional + pnl).
            exit_notional = fx_math.position_notional_usd(sym, units, cur_stop)
            database.adjust_shadow_cash(exit_notional)
            database.close_open_position(sym)
            dir_emoji = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < 0 else "⚪")
            logger.info(
                f"[{sym}] {dir_emoji} SHADOW STOP HIT | {entry_strat} · "
                f"{pnl_pct:+.2f}% (${pnl_usd:+.2f}) · "
                f"{fx_math.fp(entry_price, sym)}→{fx_math.fp(cur_stop, sym)}"
            )
            continue

        # ── B. Take profit / paradigm-driven exit ─────────────────────────
        try:
            exit_cmd = strategy.check_exit_signals(
                d, entry_strat, cur_stop, entry_price=entry_price, config=config,
            )
        except Exception as e:
            logger.error(f"[{sym}] check_exit_signals failed: {e}")
            exit_cmd = {'action': 'HOLD'}

        if exit_cmd.get('action') == 'TAKE_PROFIT':
            pnl_usd = fx_math.position_notional_usd(sym, units, price) \
                    - fx_math.position_notional_usd(sym, units, entry_price)
            pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price else 0.0
            verdict = f'TAKE PROFIT: {pnl_pct:.2f}%'
            database.log_trade(sym, 'SHADOW SELL', price, round(pnl_usd, 2),
                               entry_strat, verdict)
            exit_notional = fx_math.position_notional_usd(sym, units, price)
            database.adjust_shadow_cash(exit_notional)
            database.close_open_position(sym)
            logger.info(
                f"[{sym}] 🟢 SHADOW TAKE PROFIT | {entry_strat} · "
                f"+{pnl_pct:.2f}% (${pnl_usd:+.2f}) · "
                f"{fx_math.fp(entry_price, sym)}→{fx_math.fp(price, sym)}"
            )
            continue

        # ── C. Trailing stop ratchet ──────────────────────────────────────
        trail_mult = (config.get('ratchet', {}).get('trailing_stop_mult', 2.5))
        new_stop = price - (atr * trail_mult)
        # Ratchets up only — never down. Also clamp to entry as a floor so a
        # winning trade can lock in break-even before climbing.
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
    """
    Runs the 4-layer consensus check on a single symbol. If everything
    passes, logs a SHADOW BUY and records the open position.
    """
    if sym in open_symbols:
        return  # Already in a position — Phase 3 is one-leg-per-symbol

    # ── Layer 1: paradigm signal ─────────────────────────────────────────
    d['symbol'] = sym  # strategy.py reads it
    try:
        is_setup, paradigm = strategy.check_entry_signals(d, config=config)
    except Exception as e:
        logger.error(f"[{sym}] check_entry_signals failed: {e}")
        return
    if not is_setup:
        return

    # ── Layer 2: supporting signals ──────────────────────────────────────
    sup_score, sup_reasons = strategy.check_supporting_signals(d, paradigm, config=config)
    consensus_score = 1 + sup_score  # primary signal is +1, supporting adds up to 3
    cons_cfg        = config.get('consensus', {})
    min_consensus   = cons_cfg.get('min_consensus_score', 3)

    if consensus_score < min_consensus:
        logger.info(
            f"[{sym}] CONSENSUS FAIL: {consensus_score}/{min_consensus} "
            f"({paradigm}) | {' | '.join(sup_reasons)}"
        )
        return

    # ── Layer 3: AI verdict ──────────────────────────────────────────────
    llm_cfg     = config.get('ai_agent', {}).get('sentiment_analysis', {})
    llm_base    = llm_cfg.get('api_base')
    model_id    = llm_cfg.get('model_id')
    use_reason  = cons_cfg.get('llm_reasoning', True)
    brave_key   = secrets.get('brave_api_key')
    bear_abort  = cons_cfg.get('bearish_abort', True)

    if not (brave_key and llm_base and model_id):
        logger.warning(
            f"[{sym}] CONSENSUS clear but AI layer not configured "
            f"(brave_key={'set' if brave_key else 'missing'}, "
            f"llm_base={'set' if llm_base else 'missing'}). Skipping entry."
        )
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
        return
    if not is_bullish:
        logger.info(f"[{sym}] AI {verdict_str} on {paradigm} — not bullish, holding")
        return

    # ── Sizing ──────────────────────────────────────────────────────────
    risk_cfg = config.get('risk', {})
    initial_stop_mult = config.get('ratchet', {}).get('initial_stop_mult', 2.0)
    units, stop_price = execution.calculate_position_units(
        equity_usd=shadow_equity,
        atr=d['atr'],
        entry_price=d['price'],
        symbol=sym,
        risk_config=risk_cfg,
        stop_mult_override=initial_stop_mult,
    )
    if units <= 0:
        logger.info(f"[{sym}] sizing returned 0 units (atr/equity/cap issue) — skipping")
        return

    notional = fx_math.position_notional_usd(sym, units, d['price'])
    if notional > shadow_cash:
        logger.info(
            f"[{sym}] insufficient shadow cash: need ${notional:,.2f}, "
            f"have ${shadow_cash:,.2f} — skipping"
        )
        return

    # ── Shadow buy ──────────────────────────────────────────────────────
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
        f"notional ${notional:,.2f} | consensus {consensus_score}/{min_consensus} "
        f"| AI {verdict_str}"
    )


# ─── Cycle ──────────────────────────────────────────────────────────────────
async def _run_cycle(config: dict, symbols: list[str], timeframe: str) -> None:
    """
    1. Fetches indicators for all watchlist symbols (parallel).
    2. Persists market_states.
    3. Runs the shadow exit engine on existing positions.
    4. Runs entry consensus on each symbol with no open position.
    """
    if not symbols:
        logger.warning("active_symbols is empty — nothing to scan this cycle.")
        return

    # ── 1. Parallel fetch ──
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

    # ── 2. Shadow exits before new entries ──
    try:
        await _run_shadow_exit_engine(config, latest)
    except Exception as e:
        logger.error(f"Shadow exit engine failed: {e}", exc_info=True)

    # ── 3. Entry evaluation ──
    if not config.get('strategy', {}).get('autonomous_mode', True):
        return  # Manual-only mode; skip the auto-entry path
    if not execution.is_market_open():
        return  # Weekend / closed FX market

    open_symbols = {p['symbol'] for p in database.get_all_open_positions()}
    snap         = database.get_shadow_account_state(latest_prices={
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
        # Re-read open_symbols + cash in case the eval above placed a buy
        open_symbols = {p['symbol'] for p in database.get_all_open_positions()}
        shadow_cash  = database.get_shadow_cash()


# ─── Main loop ──────────────────────────────────────────────────────────────
async def main_async() -> None:
    _install_signal_handlers()
    logger.info("=== Sulla Phase 3 engine starting ===")
    logger.info(f"HEARTBEAT_PATH    = {HEARTBEAT_PATH}")
    logger.info(f"RESTART_FLAG_PATH = {RESTART_FLAG_PATH}")

    try:
        database.init_db()
        logger.info(f"DB schema ready at {database.DB_PATH}")
    except Exception as e:
        logger.warning(f"init_db() failed (continuing): {e}")

    # Initialize the shadow account if it doesn't exist
    try:
        initial = config_manager.load_engine_config().get('risk', {}).get('initial_capital', 10000.0)
        database.init_shadow_account(initial_capital=initial)
    except Exception as e:
        logger.warning(f"init_shadow_account() failed (continuing): {e}")

    # Cred check at boot — non-fatal. If Oanda creds are missing the engine
    # still cycles but indicator fetches return None and nothing trades.
    client = market_data.get_client()
    if client is None:
        logger.warning(
            "Sulla is running without Oanda credentials. Cycles will idle "
            "until OANDA_API_TOKEN and OANDA_ACCOUNT_ID are set in "
            "~/swarm/sulla/.env, then `docker compose up -d --force-recreate "
            "sulla-engine` so the new env vars load."
        )

    config: dict = {}
    while not _shutting_down:
        _touch_heartbeat()
        if _check_restart_flag():
            logger.info("Restart flag detected; exiting for compose to restart.")
            _consume_restart_flag()
            os._exit(0)

        # Hot-reload config every cycle
        try:
            config = config_manager.load_engine_config()
        except Exception as e:
            logger.error(f"Config load failed: {e}. Falling back to previous.")

        strat = config.get('strategy', {})
        symbols   = strat.get('active_symbols', [])
        timeframe = strat.get('timeframe', '1h')
        interval  = strat.get('update_interval_min', 5)

        logger.info(f"--- CYCLE START | symbols: {len(symbols)} | tf: {timeframe} ---")
        try:
            await _run_cycle(config, symbols, timeframe)
        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)

        sleep_total = max(60, interval * 60)
        slept = 0
        while slept < sleep_total and not _shutting_down:
            time.sleep(30)
            slept += 30
            if _check_restart_flag():
                logger.info("Restart flag detected mid-sleep; exiting for compose to restart.")
                _consume_restart_flag()
                os._exit(0)
            _touch_heartbeat()

    logger.info("Sulla shutting down cleanly.")


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting.")


if __name__ == "__main__":
    main()
