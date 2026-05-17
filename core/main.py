"""
Sulla — Phase 1 placeholder engine.

This is the scaffolding boot path. It does the minimum the container
healthcheck needs (touches heartbeat each cycle, honors the restart flag)
and nothing else. No Oanda calls, no Telegram, no DB writes — Phase 2
swaps in the real engine on top of this skeleton.

The Anton/Tiberius engine architecture lives in `main_anton_reference.py.bak`
in this same directory; it's the source we'll port from when we wire in the
Oanda broker adapter.
"""

import os
import sys
import time
import signal
import logging
from pathlib import Path

# Pulled from sibling module so the api container's dashboard endpoints don't
# 500 on "table not found" before Phase 2 wires in real trading logic. The
# init_db() call below creates every table the dashboard queries.
import database

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sulla")

# ─── Paths (env-driven, matches Anton/Tiberius convention) ──────────────────
HEARTBEAT_PATH = Path(os.environ.get('HEARTBEAT_PATH', '/app/data/.engine_heartbeat'))
RESTART_FLAG_PATH = Path(os.environ.get('RESTART_FLAG_PATH', '/app/data/.restart_engine'))

# How often we touch the heartbeat and re-check the restart flag.
# Phase 2 will increase this to a real cycle interval once the trading loop
# lands; for now we just need to stay healthy and responsive to restarts.
CYCLE_SECONDS = 30


# ─── Signal handling ────────────────────────────────────────────────────────
_shutting_down = False

def _handle_signal(signum, frame):
    """SIGTERM/SIGINT flip a flag so the next loop iteration exits cleanly."""
    global _shutting_down
    logger.info(f"Received signal {signum}; flagging shutdown.")
    _shutting_down = True


def _install_signal_handlers():
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except (ValueError, OSError):
        # Outside main thread; harmless.
        pass


# ─── Restart-flag plumbing (same pattern as Anton/Tiberius) ─────────────────
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


# ─── Main loop ──────────────────────────────────────────────────────────────
def main() -> None:
    _install_signal_handlers()
    logger.info("=== Sulla Phase 1 placeholder engine starting ===")
    logger.info(f"HEARTBEAT_PATH    = {HEARTBEAT_PATH}")
    logger.info(f"RESTART_FLAG_PATH = {RESTART_FLAG_PATH}")
    logger.info(f"CYCLE_SECONDS     = {CYCLE_SECONDS}")

    # Create the empty schema so the dashboard endpoints have tables to query.
    try:
        database.init_db()
        logger.info(f"Initialized empty schema at {database.DB_PATH}")
    except Exception as e:
        logger.warning(f"init_db() failed (non-fatal in scaffold mode): {e}")

    logger.info("No trading logic active in Phase 1. Engine is in scaffold mode.")

    cycle = 0
    while not _shutting_down:
        _touch_heartbeat()

        # Restart-flag pattern — calling os._exit(0) (not break) is mandatory.
        # Setting a flag and breaking would leave the process alive (see the
        # 2026-05-12 Anton/Tiberius fix); compose's restart: unless-stopped
        # only kicks in when the process actually exits.
        if _check_restart_flag():
            logger.info("Restart flag detected; exiting for compose to restart.")
            _consume_restart_flag()
            os._exit(0)

        cycle += 1
        if cycle % 10 == 0:  # Every 5 minutes at 30s cycle
            logger.info(f"Sulla scaffold alive (cycle {cycle})")

        time.sleep(CYCLE_SECONDS)

    logger.info("Sulla scaffold shutting down cleanly.")


if __name__ == "__main__":
    main()
