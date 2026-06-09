"""AI-veto counterfactual ledger ("ghost positions").

Tier 2 of the 2026-06-09 trading-logic audit. On Doric (the only bot with
historical veto data) the AI gate vetoed ~68% of consensus-passing
candidates and a crude 24h counterfactual measured it WRONG 58-63% of the
time. Ionic had zero veto data. This module turns every veto into a
measurable counterfactual: the entry the bot WOULD have taken is recorded
as a "ghost" and managed by a compact replica of Ionic's exit rules
(initial stop, trailing ratchet, HOLD_AND_TIGHTEN, mid/upper-BB take-profit
via the real strategy.check_exit_signals, low-triggered stop with
gap-through fills). No cash moves, no position slot is consumed — rows
live only in `ai_veto_counterfactuals`.

Readout: scripts/analytics/veto_readout.py (Foundation-Scripts). Primary
metric is the binomial HIT-RATE (ghost closed <= 0 = veto was right); pnl
is recorded in percent of price (currency-neutral — sidesteps the USD-base
conversion entirely). The replica is compact, not byte-identical (no
pyramiding, partial-TP treated as a full close, fees ignored).
"""
import logging
import sqlite3
from datetime import datetime, timezone

import strategy
import config_manager
from database import DB_PATH

logger = logging.getLogger("ionic")

MAX_AGE_DAYS = 7          # safety: no immortal ghosts

_DDL = """
CREATE TABLE IF NOT EXISTS ai_veto_counterfactuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_ts   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    paradigm    TEXT NOT NULL,
    verdict     TEXT,
    entry_price REAL NOT NULL,
    entry_atr   REAL,
    stop_price  REAL,
    status      TEXT NOT NULL DEFAULT 'OPEN',
    exit_ts     TEXT,
    exit_price  REAL,
    exit_reason TEXT,
    pnl_pct     REAL
)
"""


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.execute(_DDL)
    return con


def open_ghost(symbol, paradigm, verdict, d, config):
    """Record the entry a vetoed candidate would have taken.

    Dedup: at most one OPEN ghost per (symbol, paradigm) — a persisting
    setup re-vetoed every cycle must not spawn a ghost per cycle.
    Never raises (measurement must not break trading).
    """
    try:
        entry = float(d['price'])
        atr = float(d.get('atr') or 0.0)
        sym_cfg = config_manager.get_symbol_config(config, symbol) if config else {}
        i_mult = float((sym_cfg.get('ratchet', {}) or {}).get('initial_stop_mult', 2.0))
        stop = entry - atr * i_mult if atr > 0 else 0.0

        con = _conn()
        try:
            dup = con.execute(
                "SELECT 1 FROM ai_veto_counterfactuals "
                "WHERE symbol=? AND paradigm=? AND status='OPEN' LIMIT 1",
                (symbol, paradigm)).fetchone()
            if dup:
                return
            con.execute(
                "INSERT INTO ai_veto_counterfactuals "
                "(opened_ts, symbol, paradigm, verdict, entry_price, entry_atr, stop_price) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)",
                (symbol, paradigm, (verdict or '')[:300], entry, atr, stop))
            con.commit()
            logger.info(f"[{symbol}] 👻 ghost opened ({paradigm}) @ {entry:.5f} stop {stop:.5f}")
        finally:
            con.close()
    except Exception:
        logger.warning("ghost open failed", exc_info=True)


def update_ghosts_for_symbol(symbol, d, config):
    """Apply Ionic's exit rules to every OPEN ghost on `symbol`.

    Mirrors the exit engine's order: exit-signal first (so HOLD_AND_TIGHTEN
    preempts the normal trail), then trail, then the low-triggered stop with
    a gap-through fill, then TP, then age-out. Never raises.
    """
    try:
        con = _conn()
        try:
            rows = con.execute(
                "SELECT id, opened_ts, paradigm, entry_price, entry_atr, stop_price "
                "FROM ai_veto_counterfactuals WHERE symbol=? AND status='OPEN'",
                (symbol,)).fetchall()
            if not rows:
                return
            price = float(d['price'])
            low = float(d.get('low', price))
            atr_now = float(d.get('atr') or 0.0)
            sym_cfg = config_manager.get_symbol_config(config, symbol) if config else {}
            trail_mult = config_manager.get_ratchet_multiplier(sym_cfg)

            for gid, opened_ts, paradigm, entry, entry_atr, stop in rows:
                entry = float(entry)
                stop = float(stop or 0.0)
                atr = float(entry_atr or atr_now or 0.0)

                def close(reason, exit_price):
                    pnl = ((exit_price - entry) / entry * 100.0) if entry else 0.0
                    con.execute(
                        "UPDATE ai_veto_counterfactuals SET status='CLOSED', "
                        "exit_ts=datetime('now'), exit_price=?, exit_reason=?, pnl_pct=? "
                        "WHERE id=?", (exit_price, reason, round(pnl, 4), gid))
                    logger.info(f"[{symbol}] 👻 ghost closed ({paradigm}) {reason} {pnl:+.2f}%")

                exit_cmd = strategy.check_exit_signals(d, paradigm, stop, entry, config)
                action = exit_cmd.get('action')

                if action == 'HOLD_AND_TIGHTEN' and atr > 0:
                    tight = max(entry, price - atr)
                    if tight > stop and tight < price:
                        stop = tight
                elif atr > 0:
                    cand = price - atr * trail_mult
                    if cand > stop:
                        stop = cand

                if stop > 0 and min(low, price) <= stop:
                    close('STOP HIT', min(stop, price))
                    continue
                if action in ('TAKE_PROFIT', 'PARTIAL_TAKE_PROFIT'):
                    close('TAKE PROFIT', price)
                    continue
                try:
                    age = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(opened_ts).replace(tzinfo=timezone.utc))
                    if age.days >= MAX_AGE_DAYS:
                        close('AGE LIMIT', price)
                        continue
                except Exception:
                    pass

                con.execute(
                    "UPDATE ai_veto_counterfactuals SET stop_price=? WHERE id=?",
                    (stop, gid))
            con.commit()
        finally:
            con.close()
    except Exception:
        logger.warning("ghost update failed", exc_info=True)
