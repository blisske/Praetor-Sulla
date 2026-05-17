"""
Sulla — Phase 2 engine.

Cycles every `update_interval_min` minutes:
  1. Touches heartbeat (docker healthcheck)
  2. Checks the restart flag (calls os._exit(0) if set so compose respawns)
  3. Loads Config.yaml (hot-reloaded — dashboard edits take effect next cycle)
  4. Fetches OHLCV + indicators for every symbol in active_symbols
  5. Writes the indicator snapshot to market_states (so /api/market shows it)
  6. Logs a per-symbol regime/RSI/ADX line

No trading, no Telegram, no consensus layer yet. Phase 3 wires in the signal
engine + consensus + execution.

The Anton/Tiberius engine (full strategy + consensus + tuner + Telegram +
exit logic) is preserved verbatim at `_main_anton_reference.py` in this
same directory; that's the source we port from for Phase 3+.
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


# ─── Cycle ──────────────────────────────────────────────────────────────────
async def _run_cycle(config: dict, symbols: list[str], timeframe: str) -> None:
    """
    Fetches indicators for every symbol in parallel, logs the per-symbol
    state, and persists to market_states.
    """
    if not symbols:
        logger.warning("active_symbols is empty — nothing to scan this cycle.")
        return

    # Parallel fetch — one Oanda candle request per symbol, throttled by
    # the requests session's connection pool. For 7 majors this completes
    # in ~1-2 seconds.
    tasks = [
        market_data.fetch_indicators(sym, config=config, timeframe=timeframe)
        for sym in symbols
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for sym, d in zip(symbols, results):
        if isinstance(d, Exception):
            logger.error(f"[{sym}] fetch raised: {d}")
            continue
        if d is None:
            # Already logged at the fetch layer; don't double-log.
            continue

        logger.info(
            f"[{sym}] ${d['price']:.5f} | {d['regime']} | "
            f"ADX={d['adx']:.1f} | RSI={d['rsi']:.1f} | Trend={d['trend']}"
        )

        try:
            database.log_market_state(
                symbol=sym,
                price=d['price'],
                adx=d['adx'],
                regime=d['regime'],
                trend=d['trend'],
                rsi=d['rsi'],
                volume=d.get('volume'),
                avg_volume=d.get('avg_volume'),
            )
        except Exception as e:
            logger.error(f"[{sym}] log_market_state failed: {e}")


# ─── Main loop ──────────────────────────────────────────────────────────────
async def main_async() -> None:
    _install_signal_handlers()
    logger.info("=== Sulla Phase 2 engine starting ===")
    logger.info(f"HEARTBEAT_PATH    = {HEARTBEAT_PATH}")
    logger.info(f"RESTART_FLAG_PATH = {RESTART_FLAG_PATH}")

    try:
        database.init_db()
        logger.info(f"DB schema ready at {database.DB_PATH}")
    except Exception as e:
        logger.warning(f"init_db() failed (continuing): {e}")

    # Cred check at boot — non-fatal. If creds are missing we still cycle but
    # market_data.fetch_indicators() returns None each call. Once the user
    # populates ~/swarm/sulla/.env with OANDA_API_TOKEN and restarts, the
    # next fetch succeeds.
    client = market_data.get_client()
    if client is None:
        logger.warning(
            "Sulla is running without Oanda credentials. Cycles will idle "
            "until OANDA_API_TOKEN and OANDA_ACCOUNT_ID are set in "
            "~/swarm/sulla/.env, then restart sulla-engine."
        )

    last_config_load = 0.0
    config: dict = {}

    while not _shutting_down:
        _touch_heartbeat()

        if _check_restart_flag():
            logger.info("Restart flag detected; exiting for compose to restart.")
            _consume_restart_flag()
            os._exit(0)

        # Hot-reload config every cycle — dashboard edits take effect next pass.
        try:
            config = config_manager.load_engine_config()
        except Exception as e:
            logger.error(f"Config load failed: {e}. Falling back to empty dict.")
            config = config or {}

        strat = config.get('strategy', {})
        symbols   = strat.get('active_symbols', [])
        timeframe = strat.get('timeframe', '1h')
        interval  = strat.get('update_interval_min', 5)

        logger.info(f"--- CYCLE START | symbols: {len(symbols)} | tf: {timeframe} ---")

        try:
            await _run_cycle(config, symbols, timeframe)
        except Exception as e:
            logger.error(f"Cycle failed: {e}", exc_info=True)

        # Sleep — but wake up every 30s to check the restart flag so the web
        # Restart button feels responsive even when the cycle interval is 5 min.
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
