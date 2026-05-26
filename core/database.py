import os
import re
import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

# ==============================================================================
# IONIC V1 - DATABASE & LOGGING ENGINE (The Ledger)
# Handles all local SQLite operations for Wall Street paper trading.
# ==============================================================================

logger  = logging.getLogger("ionic")
BASE_DIR = Path(__file__).parent
# Container-friendly: SQLITE_PATH env var overrides the source-tree default so
# the same code path works under systemd (DB next to source) or in Docker
# (DB at a bind-mounted /app/data/ionic.db).
DB_PATH  = Path(os.environ.get('SQLITE_PATH', BASE_DIR / 'ionic_data.db'))


def init_db():
    """
    Initializes the SQLite database. Creates tables if absent.
    Also runs safe ALTER TABLE migrations for columns added after initial deploy.
    """
    try:
        # Ensure the DB's parent directory exists (matters for /app/data when
        # the bind mount is empty on first boot in a container).
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # WAL mode lets the engine (writer) and API (reader) hit the same DB
        # without lock contention. Persistent across connections once set.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")

        # Table 1: Trade History
        c.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         DATETIME DEFAULT CURRENT_TIMESTAMP,
                symbol            TEXT,
                action            TEXT,
                price             REAL,
                amount            REAL,
                strategy          TEXT,
                verdict           TEXT,
                position_size_usd REAL DEFAULT 0.0,
                fee_usd           REAL DEFAULT 0.0
            )
        ''')
        # Idempotent add for existing DBs predating the position_size_usd column.
        try:
            c.execute("ALTER TABLE trades ADD COLUMN position_size_usd REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent add for existing DBs predating the fee_usd column (2026-05-24).
        # Used by core/tax.py for §988 ordinary-income attribution. For Phase 1
        # scaffold this is always 0.0; Phase 2 (Oanda broker integration) will
        # capture spread + commission from the v20 trade transaction stream.
        # Oanda's effective cost is mostly spread (no separate commission on
        # standard accounts) — extracting it requires comparing fill price to
        # mid-quote at submit time. See Phase 2 plan.
        try:
            c.execute("ALTER TABLE trades ADD COLUMN fee_usd REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass  # column already exists

        # Table 2: Market State History
        c.execute('''
            CREATE TABLE IF NOT EXISTS market_states (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                symbol    TEXT,
                price     REAL,
                adx       REAL,
                regime    TEXT,
                trend     TEXT,
                rsi       REAL
            )
        ''')

        # Table 3: Open Positions
        c.execute('''
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol                       TEXT PRIMARY KEY,
                entry_price                  REAL,
                strategy                     TEXT,
                entry_atr                    REAL DEFAULT 0.0,
                current_stop                 REAL DEFAULT 0.0,
                entry_timestamp              DATETIME DEFAULT CURRENT_TIMESTAMP,
                shares                       REAL DEFAULT 0.0,
                leg_count                    INTEGER DEFAULT 1,
                avg_entry_price              REAL DEFAULT 0.0,
                last_leg_price               REAL DEFAULT 0.0,
                last_leg_atr                 REAL DEFAULT 0.0,
                position_size_usd            REAL DEFAULT 0.0,
                original_position_size_usd   REAL DEFAULT 0.0,
                partial_exits_taken          INTEGER DEFAULT 0
            )
        ''')
        # Idempotent column adds for DBs predating later schema additions.
        for col, decl in [
            ('shares',                     'REAL DEFAULT 0.0'),
            ('leg_count',                  'INTEGER DEFAULT 1'),
            ('avg_entry_price',            'REAL DEFAULT 0.0'),
            ('last_leg_price',             'REAL DEFAULT 0.0'),
            ('last_leg_atr',               'REAL DEFAULT 0.0'),
            ('position_size_usd',          'REAL DEFAULT 0.0'),
            ('original_position_size_usd', 'REAL DEFAULT 0.0'),
            ('partial_exits_taken',        'INTEGER DEFAULT 0'),
        ]:
            try:
                c.execute(f"ALTER TABLE open_positions ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # column already exists

        # Table 5: Tuning Log — records every parameter change proposal
        c.execute('''
            CREATE TABLE IF NOT EXISTS tuning_log (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp              DATETIME DEFAULT CURRENT_TIMESTAMP,
                symbol                 TEXT,
                parameter              TEXT,
                old_value              REAL,
                new_value              REAL,
                metric_name            TEXT,
                metric_value           REAL,
                trade_count            INTEGER,
                status                 TEXT DEFAULT 'PENDING',
                shadow_trades_required INTEGER DEFAULT 10,
                rejection_reason       TEXT
            )
        ''')
        # Idempotent column add for DBs predating the operator-reject feature.
        try:
            c.execute("ALTER TABLE tuning_log ADD COLUMN rejection_reason TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists

        # Table 6: Param Snapshots — shadow validation queue
        c.execute('''
            CREATE TABLE IF NOT EXISTS param_snapshots (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                tuning_log_id            INTEGER,
                symbol                   TEXT,
                parameter                TEXT,
                baseline_value           REAL,
                candidate_value          REAL,
                shadow_trades_at_propose INTEGER DEFAULT 0,
                shadow_trades_completed  INTEGER DEFAULT 0,
                shadow_trades_required   INTEGER DEFAULT 10,
                status                   TEXT DEFAULT 'VALIDATING'
            )
        ''')

        # Table 7: Equity Peak Watermark — drawdown kill switch
        c.execute('''
            CREATE TABLE IF NOT EXISTS equity_peak (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                peak      REAL    NOT NULL DEFAULT 0.0,
                updated   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Table 8: Shadow Account — synthetic ledger for shadow_mode equity.
        # Source of truth for shadow equity, cash, drawdown peak. Decoupled
        # from live Alpaca account so shadow simulation never reads live state.
        c.execute('''
            CREATE TABLE IF NOT EXISTS shadow_account (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                cash            REAL    NOT NULL DEFAULT 10000.0,
                initial_capital REAL    NOT NULL DEFAULT 10000.0,
                updated         DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Table 9: Risk State — tiered drawdown + daily session loss tracking.
        # risk_mode: NORMAL / ALERT / DERISK / HALT (peak-based, hysteresis)
        # session_date: ET YYYY-MM-DD; resets daily_halt and captures session_start_equity
        # daily_halt: 1 if intraday loss limit hit; auto-clears at next session
        c.execute('''
            CREATE TABLE IF NOT EXISTS risk_state (
                id                    INTEGER PRIMARY KEY CHECK (id = 1),
                risk_mode             TEXT    NOT NULL DEFAULT 'NORMAL',
                session_date          TEXT,
                session_start_equity  REAL,
                daily_halt            INTEGER NOT NULL DEFAULT 0,
                updated               DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Table 10: Pending Events — engine writes, API drains and broadcasts
        # over /ws. DB-as-IPC, same pattern as the .restart_engine flag file.
        # broadcast_at is NULL until the API has shipped it; the drain task
        # uses that as the work queue.
        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                event_type    TEXT    NOT NULL,
                payload       TEXT    NOT NULL,
                broadcast_at  TEXT
            )
        ''')

        # Table 11: Live account cache — engine writes Oanda NAV/balance every
        # cycle when shadow_mode=False so the dashboard/api can show the real
        # broker-side equity instead of the synthetic shadow_cash (which
        # diverges from Oanda once live trading starts). Single-row table
        # (id=1 sentinel); engine UPSERTs on each refresh. last_updated
        # lets readers detect staleness — fall back to shadow math if older
        # than ~5 min.
        c.execute('''
            CREATE TABLE IF NOT EXISTS live_account_cache (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                nav             REAL,     -- net asset value (Oanda's "NAV")
                balance         REAL,     -- realized cash balance
                unrealized_pl   REAL,     -- open-positions mark-to-market
                margin_used     REAL,     -- margin currently used
                margin_avail    REAL,     -- margin still available
                open_trades     INTEGER,  -- Oanda's open-trade count (sanity)
                currency        TEXT,     -- account currency (typically USD)
                last_updated    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE INDEX IF NOT EXISTS idx_pending_events_unbroadcast
            ON pending_events(id) WHERE broadcast_at IS NULL
        ''')

        conn.commit()

        # --- SAFE MIGRATIONS ---
        # Add columns to existing deployments that predate these fields.
        # ALTER TABLE IF NOT EXISTS COLUMN is not supported in SQLite,
        # so we catch the OperationalError when the column already exists.
        for col, definition in [
            ("entry_atr",       "REAL DEFAULT 0.0"),
            ("current_stop",    "REAL DEFAULT 0.0"),
            ("shares",          "REAL DEFAULT 0.0"),
            # Pyramid columns (Phase 6) — single-leg positions get
            # leg_count=1 with avg/last fields equal to entry_price.
            ("leg_count",       "INTEGER NOT NULL DEFAULT 1"),
            ("avg_entry_price", "REAL"),
            ("last_leg_price",  "REAL"),
            ("last_leg_atr",    "REAL"),
        ]:
            try:
                c.execute(f"ALTER TABLE open_positions ADD COLUMN {col} {definition}")
                conn.commit()
                logger.info(f"DB migration: added open_positions.{col}")
            except sqlite3.OperationalError:
                pass  # Column already exists — expected on re-runs

        try:
            c.execute("ALTER TABLE param_snapshots ADD COLUMN baseline_max_trade_id INTEGER DEFAULT 0")
            conn.commit()
            logger.info("DB migration: added param_snapshots.baseline_max_trade_id")
        except sqlite3.OperationalError:
            pass  # Column already exists

        for col, definition in [
            ("volume",     "REAL"),
            ("avg_volume", "REAL"),
        ]:
            try:
                c.execute(f"ALTER TABLE market_states ADD COLUMN {col} {definition}")
                conn.commit()
                logger.info(f"DB migration: added market_states.{col}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to initialize DB: {e}", exc_info=True)


# ==============================================================================
# TRADE LOGGING
# ==============================================================================

def emit_event(event_type: str, payload: dict) -> None:
    """
    Queue an event for the API to broadcast over /ws. The API has a background
    asyncio task that polls pending_events every ~1s, ships unbroadcast rows
    via manager.broadcast(), and stamps broadcast_at.

    Failures are swallowed: a broken event queue must never crash the engine.
    Caller-owned payload is JSON-serialized as-is — keys/values must be
    JSON-safe.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO pending_events (timestamp, event_type, payload) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), event_type, json.dumps(payload)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"emit_event({event_type}) failed: {e}")


# Shadow-mode fee rate (env-overridable). Default 0.0001 = 1 basis point
# per leg, modeling Oanda's ~1-pip spread on EUR/USD and other FX majors
# (0.6-1.5 pips typical on practice + standard accounts ≈ 0.6-1.5 bp).
# Exotic crosses are 3-10× wider; if the active universe pivots to
# minors/exotics, raise SHADOW_FEE_RATE to 0.0005 or higher via env var.
# Added 2026-05-26 after Corinthian's hidden-fee blind spot — same code
# path applied to all 3 bots for consistency.
#
# Not modeled: overnight swap/financing charges on positions held past
# 5pm NY (Oanda credits/debits based on rate differential). For 24-hour-
# average-hold strategies this is small; for multi-day holds it stacks.
# Track separately if Ionic moves to position-hold scales.
SHADOW_FEE_RATE = float(os.getenv("SHADOW_FEE_RATE", "0.0001"))


def log_trade(symbol, action, price, amount, strategy, verdict="", position_size_usd=0.0, fee_usd=0.0):
    """
    Writes a new trade event to the ledger.

    position_size_usd stores the dollar exposure at fill time, independent
    of price drift afterward — useful for the tuner + risk analytics.

    fee_usd is the total cost for THIS fill in USD. Used by core/tax.py
    for §988 ordinary-income attribution. For Phase 1 scaffold the live
    path is 0.0; Phase 2 (Oanda broker integration) will derive it from
    the spread + financing charges in the v20 trade transaction stream.

    For SHADOW * actions where caller passes fee_usd=0, we synthesize a
    fee = position_size_usd * SHADOW_FEE_RATE so the shadow ledger
    reflects realistic spread cost (Oanda's revenue model is spread,
    not commission). Default rate models 1-pip EUR/USD-style spreads.

    Side effect: emits a `trade` WS event for SHADOW BUY / SHADOW SELL /
    SHADOW BUY ADD actions so the dashboard sees fills in real time without
    waiting for the 5s tick. Live-path actions are not surfaced.
    """
    # Synthesize shadow-mode fee when caller didn't pass one.
    if (fee_usd == 0.0
            and isinstance(action, str)
            and action.startswith("SHADOW ")
            and position_size_usd > 0):
        fee_usd = float(position_size_usd) * SHADOW_FEE_RATE

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO trades (symbol, action, price, amount, strategy, verdict, position_size_usd, fee_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, action, price, amount, strategy, verdict, position_size_usd, fee_usd))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to log trade for {symbol}: {e}")
        return

    if isinstance(action, str) and action.startswith("SHADOW "):
        emit_event("trade", {
            "symbol":            symbol,
            "action":            action,
            "price":             price,
            "amount":            amount,
            "strategy":          strategy,
            "verdict":           (verdict or "")[:200],
            "position_size_usd": position_size_usd,
        })


def log_market_state(symbol, price, adx, regime, trend, rsi, volume=None, avg_volume=None):
    """
    Writes the current technical conditions to the ledger.
    Failures are silenced to avoid spamming the console on every loop tick.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO market_states (symbol, price, adx, regime, trend, rsi, volume, avg_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, price, adx, regime, trend, rsi, volume, avg_volume))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ==============================================================================
# OPEN POSITION TRACKING
# ==============================================================================

def record_open_position(symbol, entry_price, strategy, entry_atr=0.0, shares=0.0,
                         leg_count=1, avg_entry_price=None, last_leg_price=None,
                         last_leg_atr=None, position_size_usd=0.0):
    """
    Records which paradigm opened a position so the exit engine
    never has to guess mid-trade. Also stores entry ATR, share count for
    shadow P&L, and pyramid leg metadata (Phase 6 schema).

    For first-leg entries, the pyramid fields default to mirror the entry
    values so single-leg positions read cleanly. Phase 7 layers in the
    actual add-leg logic via a separate add_pyramid_leg helper.
    """
    if avg_entry_price is None:
        avg_entry_price = entry_price
    if last_leg_price is None:
        last_leg_price = entry_price
    if last_leg_atr is None:
        last_leg_atr = entry_atr
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO open_positions
            (symbol, entry_price, strategy, entry_atr, current_stop, shares,
             leg_count, avg_entry_price, last_leg_price, last_leg_atr,
             position_size_usd, original_position_size_usd)
            VALUES (?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, entry_price, strategy, entry_atr, shares,
              leg_count, avg_entry_price, last_leg_price, last_leg_atr,
              position_size_usd, position_size_usd))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to record open position for {symbol}: {e}")


def get_open_position(symbol):
    """
    Looks up the paradigm, entry price, entry ATR, current stop, share
    count, and pyramid leg metadata for a currently held asset.

    Pyramid fields fall back to single-leg defaults when NULL (legacy rows
    or pre-Phase-6 data): leg_count=1, avg_entry_price=entry_price,
    last_leg_price=entry_price, last_leg_atr=entry_atr.

    Returns dict or None.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'SELECT entry_price, strategy, entry_atr, current_stop, shares, '
            'leg_count, avg_entry_price, last_leg_price, last_leg_atr, '
            'position_size_usd, partial_exits_taken '
            'FROM open_positions WHERE symbol = ?',
            (symbol,)
        )
        row = c.fetchone()
        conn.close()
        if row:
            entry_price = row[0]
            entry_atr   = row[2] or 0.0
            return {
                'entry_price':         entry_price,
                'strategy':            row[1],
                'entry_atr':           entry_atr,
                'current_stop':        row[3] or 0.0,
                'shares':              row[4] or 0.0,
                'leg_count':           row[5] or 1,
                'avg_entry_price':     row[6] if row[6] is not None else entry_price,
                'last_leg_price':      row[7] if row[7] is not None else entry_price,
                'last_leg_atr':        row[8] if row[8] is not None else entry_atr,
                'position_size_usd':   row[9] or 0.0,
                'partial_exits_taken': row[10] or 0,
            }
        return None
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to read open position for {symbol}: {e}")
        return None


def add_pyramid_leg(symbol, leg_price, leg_atr, leg_shares) -> bool:
    """
    Adds a leg to an existing pyramided position. Updates:
      - shares: sum
      - avg_entry_price: volume-weighted across all legs
      - last_leg_price / last_leg_atr: this leg's values
      - leg_count: incremented by 1

    entry_price stays at the original first-leg price for strategy-name
    continuity (the exit engine reads `strategy` to dispatch). Returns
    True on success, False if the position doesn't exist.

    Caller must ensure the underlying buy succeeded before calling this
    so we don't record a leg the exchange didn't actually fill.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'SELECT shares, avg_entry_price, entry_price FROM open_positions WHERE symbol = ?',
            (symbol,)
        )
        row = c.fetchone()
        if not row:
            conn.close()
            logger.warning(f"add_pyramid_leg called on missing position {symbol}")
            return False
        cur_shares = row[0] or 0.0
        cur_avg    = row[1] if row[1] is not None else (row[2] or 0.0)
        new_shares = cur_shares + leg_shares
        if new_shares <= 0:
            conn.close()
            logger.error(f"add_pyramid_leg: invalid share total for {symbol}")
            return False
        new_avg = ((cur_shares * cur_avg) + (leg_shares * leg_price)) / new_shares
        c.execute('''
            UPDATE open_positions
            SET shares          = ?,
                avg_entry_price = ?,
                last_leg_price  = ?,
                last_leg_atr    = ?,
                leg_count       = leg_count + 1
            WHERE symbol = ?
        ''', (new_shares, new_avg, leg_price, leg_atr, symbol))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to add pyramid leg to {symbol}: {e}")
        return False


def close_open_position(symbol):
    """
    Removes a position record when a trade is exited.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM open_positions WHERE symbol = ?', (symbol,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to close position record for {symbol}: {e}")


def mark_partial_exit(symbol: str, remaining_shares: float,
                      remaining_size_usd: float, new_stop: float | None = None):
    """
    Records that a partial profit-take has fired. Shrinks the position
    (shares + size_usd), increments the partial counter, and optionally
    moves the stop (typically to break-even).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if new_stop is not None:
            c.execute('''
                UPDATE open_positions
                SET shares = ?,
                    position_size_usd = ?,
                    partial_exits_taken = partial_exits_taken + 1,
                    current_stop = ?
                WHERE symbol = ?
            ''', (remaining_shares, remaining_size_usd, new_stop, symbol))
        else:
            c.execute('''
                UPDATE open_positions
                SET shares = ?,
                    position_size_usd = ?,
                    partial_exits_taken = partial_exits_taken + 1
                WHERE symbol = ?
            ''', (remaining_shares, remaining_size_usd, symbol))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to mark partial exit for {symbol}: {e}")


def update_shadow_stop(symbol: str, new_stop: float):
    """
    Ratchets the current_stop value upward for a shadow position.
    Called by the shadow exit engine each cycle when ATR ratchet improves.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'UPDATE open_positions SET current_stop = ? WHERE symbol = ?',
            (new_stop, symbol)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to update shadow stop for {symbol}: {e}")


def get_all_open_positions() -> list:
    """
    Returns all currently tracked open positions as a list of dicts.
    Used by the shadow exit engine to evaluate all paper holdings each cycle.
    Includes pyramid leg metadata (Phase 6) — single-leg positions get
    leg_count=1 with avg/last fields equal to entry_price/entry_atr.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'SELECT symbol, entry_price, strategy, entry_atr, current_stop, shares, '
            'leg_count, avg_entry_price, last_leg_price, last_leg_atr, '
            'position_size_usd, partial_exits_taken '
            'FROM open_positions'
        )
        rows = c.fetchall()
        conn.close()
        return [
            {
                'symbol':              r[0],
                'entry_price':         r[1],
                'strategy':            r[2],
                'entry_atr':           r[3] or 0.0,
                'current_stop':        r[4] or 0.0,
                'shares':              r[5] or 0.0,
                'leg_count':           r[6] or 1,
                'avg_entry_price':     r[7] if r[7] is not None else r[1],
                'last_leg_price':      r[8] if r[8] is not None else r[1],
                'last_leg_atr':        r[9] if r[9] is not None else (r[3] or 0.0),
                'position_size_usd':   r[10] or 0.0,
                'partial_exits_taken': r[11] or 0,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to fetch all open positions: {e}")
        return []


# ==============================================================================
# SELF-TUNING ENGINE — Parameter Optimization Ledger
# ==============================================================================

def log_tuning_change(symbol, parameter, old_value, new_value, metric_name,
                       metric_value, trade_count, shadow_trades_required=10):
    """Logs a tuning proposal and queues it for shadow validation."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO tuning_log
            (symbol, parameter, old_value, new_value, metric_name, metric_value,
             trade_count, status, shadow_trades_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        ''', (symbol, parameter, old_value, new_value, metric_name,
              metric_value, trade_count, shadow_trades_required))
        log_id = c.lastrowid

        # Record the current max trade ID and count so validation can filter
        # trades that occurred *after* this proposal was created.
        c.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM trades WHERE symbol=? AND action='SHADOW SELL'",
            (symbol,)
        )
        row = c.fetchone()
        baseline_count, baseline_max_id = row[0], row[1]

        c.execute('''
            INSERT INTO param_snapshots
            (tuning_log_id, symbol, parameter, baseline_value, candidate_value,
             shadow_trades_at_propose, shadow_trades_required, baseline_max_trade_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'VALIDATING')
        ''', (log_id, symbol, parameter, old_value, new_value,
              baseline_count, shadow_trades_required, baseline_max_id))

        conn.commit()
        conn.close()
        return log_id
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to log tuning change for {symbol}: {e}")
        return None


def get_closed_shadow_trades(symbol=None, strategy=None, limit=200):
    """
    Returns closed trades (shadow and live) as list of dicts with pnl_pct extracted
    from the verdict field (format: 'TAKE PROFIT: +3.6%', 'STOP HIT: -1.4%',
    or 'Mid BB target hit. Real yield: 3.2%'). Trades whose verdict contains no
    percentage (e.g. 'EOD FORCE-EXIT') are silently dropped from the result.
    Includes both SHADOW SELL and live SELL actions so the tuner keeps learning
    after shadow_mode is set to false.

    Args:
        symbol:   optional filter, exact match
        strategy: optional filter, exact match (e.g. 'VOLATILITY BREAKOUT')
        limit:    max rows (most recent first); None = no limit
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        sql = ("SELECT id, symbol, strategy, verdict, timestamp, amount FROM trades "
               "WHERE action IN ('SHADOW SELL', 'SELL')")
        params: list = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if strategy:
            sql += " AND strategy = ?"
            params.append(strategy)
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        c.execute(sql, params)
        rows = c.fetchall()
        conn.close()

        results = []
        for row in rows:
            try:
                verdict = row[3] or ''
                # Extract numeric P&L from verdict string e.g. '+3.6%' or '-1.4%'
                import re
                match = re.search(r'([+-]?\d+\.?\d*)%', verdict)
                pnl_pct = float(match.group(1)) if match else 0.0
                # On sells the `amount` column stores pnl_usd (see CLAUDE.md)
                try:
                    pnl_usd = float(row[5]) if row[5] is not None else 0.0
                except (TypeError, ValueError):
                    pnl_usd = 0.0
                results.append({
                    'trade_id':  row[0],
                    'symbol':    row[1],
                    'strategy':  row[2],
                    'pnl_pct':   pnl_pct,
                    'pnl_usd':   pnl_usd,
                    'timestamp': row[4],
                })
            except Exception:
                continue
        return results
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to get closed shadow trades: {e}")
        return []


# Legacy alias — keep until all call sites migrate to get_closed_shadow_trades.
get_closed_trades = get_closed_shadow_trades


def increment_shadow_validation_trades(symbol):
    """
    Increments shadow_trades_completed for all VALIDATING snapshots for this
    symbol. Returns list of snapshot IDs that have now reached their required
    count and are ready for promotion decision.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            UPDATE param_snapshots
            SET shadow_trades_completed = shadow_trades_completed + 1
            WHERE symbol = ? AND status = 'VALIDATING'
        ''', (symbol,))
        conn.commit()

        c.execute('''
            SELECT id FROM param_snapshots
            WHERE symbol = ? AND status = 'VALIDATING'
            AND shadow_trades_completed >= shadow_trades_required
        ''', (symbol,))
        ready = [row[0] for row in c.fetchall()]
        conn.close()
        return ready
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to increment shadow validation for {symbol}: {e}")
        return []


def get_pending_shadow_validations():
    """Returns all VALIDATING param snapshots as list of dicts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT id, tuning_log_id, symbol, parameter, baseline_value,
                   candidate_value, shadow_trades_completed, shadow_trades_required,
                   baseline_max_trade_id
            FROM param_snapshots WHERE status = 'VALIDATING'
        ''')
        rows = c.fetchall()
        conn.close()
        return [
            {
                'id':                       r[0],
                'tuning_log_id':            r[1],
                'symbol':                   r[2],
                'parameter':                r[3],
                'baseline_value':           r[4],
                'candidate_value':          r[5],
                'shadow_trades_completed':  r[6],
                'shadow_trades_required':   r[7],
                'baseline_max_trade_id':    r[8] or 0,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to get pending validations: {e}")
        return []


def update_tuning_status(log_id, status):
    """Updates status on both tuning_log and param_snapshots for a given log_id."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE tuning_log SET status=? WHERE id=?", (status, log_id))
        c.execute("UPDATE param_snapshots SET status=? WHERE tuning_log_id=?", (status, log_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to update tuning status for log_id {log_id}: {e}")


# Map paradigm-path prefix → strategy name, mirroring tuner._STRAT_KEY_TO_NAME.
# Duplicated here so database.py doesn't have to import tuner (which imports
# database) and avoid a circular dep.
_TUNING_STRAT_KEY_TO_NAME = {
    'trend_following':    'TREND FOLLOWING',
    'mean_reversion':     'MEAN REVERSION',
    'volatility_breakout':'VOLATILITY BREAKOUT',
    'liquidity_sweep':    'LIQUIDITY SWEEP',
}


def get_candidate_detail(log_id, db_path=None):
    """
    Returns the full forensic picture for one tuning candidate so the operator
    can decide whether to wait, accept (via the shadow gate), or reject (this
    function's caller). Bundles:
      - the tuning_log row (proposal record)
      - the matching param_snapshots row (validation queue state, if any)
      - the trades that drove the proposal (≤ baseline_max_trade_id)
      - the trades that have closed since the proposal (counting toward validation)

    Paradigm-specific parameters (`paradigms.X.Y`) filter their trade pools to
    only the matching strategy; cross-paradigm params (e.g. `trailing_stop_mult`)
    show all strategies for the symbol — same logic the validator uses.

    db_path: optional override so the API can route demo vs admin requests to
    the right DB without mutating the module-level DB_PATH.
    """
    try:
        conn = sqlite3.connect(db_path or DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Proposal row
        log_row = c.execute(
            "SELECT * FROM tuning_log WHERE id=?", (log_id,)
        ).fetchone()
        if not log_row:
            conn.close()
            return None
        log = dict(log_row)

        # Optional snapshot (deleted if the proposal was rejected pre-validation)
        snap_row = c.execute(
            "SELECT * FROM param_snapshots WHERE tuning_log_id=?", (log_id,)
        ).fetchone()
        snap = dict(snap_row) if snap_row else None

        # Determine the strategy filter (if any) from the parameter path
        param_path = log.get('parameter') or ''
        parts = param_path.split('.')
        strategy_filter = None
        if len(parts) >= 3 and parts[0] == 'paradigms':
            strategy_filter = _TUNING_STRAT_KEY_TO_NAME.get(parts[1])

        symbol = log.get('symbol')
        baseline_max_trade_id = (snap or {}).get('baseline_max_trade_id') or 0

        # Driving trades: ≤ baseline_max_trade_id, optional paradigm filter,
        # most-recent-first, capped at trade_count or 20.
        cap = int(log.get('trade_count') or 0) or 20
        sql = ("SELECT id, symbol, strategy, verdict, timestamp, amount "
               "FROM trades WHERE symbol=? AND action IN ('SHADOW SELL','SELL')")
        params = [symbol]
        if baseline_max_trade_id:
            sql += " AND id <= ?"
            params.append(baseline_max_trade_id)
        if strategy_filter:
            sql += " AND strategy = ?"
            params.append(strategy_filter)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(cap)
        driving_rows = c.execute(sql, params).fetchall()

        # Recent trades: > baseline_max_trade_id (validation-counting trades)
        recent_rows = []
        if baseline_max_trade_id:
            sql2 = ("SELECT id, symbol, strategy, verdict, timestamp, amount "
                    "FROM trades WHERE symbol=? AND action IN ('SHADOW SELL','SELL') "
                    "AND id > ?")
            params2 = [symbol, baseline_max_trade_id]
            if strategy_filter:
                sql2 += " AND strategy = ?"
                params2.append(strategy_filter)
            sql2 += " ORDER BY id DESC"
            recent_rows = c.execute(sql2, params2).fetchall()

        conn.close()

        def _trade_dict(r):
            d = dict(r)
            # Extract pnl_pct from verdict text using the same regex the tuner uses
            verdict = d.get('verdict') or ''
            m = re.search(r'([+-]?\d+\.?\d*)%', verdict)
            d['pnl_pct'] = float(m.group(1)) if m else None
            # `amount` column stores pnl_usd on sell rows (per CLAUDE.md)
            try:
                d['pnl_usd'] = float(d.get('amount')) if d.get('amount') is not None else None
            except (TypeError, ValueError):
                d['pnl_usd'] = None
            return d

        return {
            'log':            log,
            'snapshot':       snap,
            'strategy_filter': strategy_filter,
            'driving_trades': [_trade_dict(r) for r in driving_rows],
            'recent_trades':  [_trade_dict(r) for r in recent_rows],
        }
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to get candidate detail for log_id {log_id}: {e}")
        return None


def reject_candidate(log_id, reason=None, db_path=None):
    """
    Operator-driven rejection of a tuning candidate. Sets tuning_log + the
    matching param_snapshots row to REJECTED status and writes the operator's
    reason (if provided) to tuning_log.rejection_reason.

    Distinct from the automated REJECTED status the tuner sets when the metric
    doesn't improve — the rejection_reason text is what tells them apart in
    forensics ("operator manual" vs the absence of a reason for auto-rejection).

    Returns True on success, False if the row doesn't exist or DB hiccups.
    """
    try:
        conn = sqlite3.connect(db_path or DB_PATH)
        c = conn.cursor()
        existing = c.execute(
            "SELECT status FROM tuning_log WHERE id=?", (log_id,)
        ).fetchone()
        if not existing:
            conn.close()
            return False
        c.execute(
            "UPDATE tuning_log SET status='REJECTED', rejection_reason=? WHERE id=?",
            (reason, log_id)
        )
        c.execute(
            "UPDATE param_snapshots SET status='REJECTED' WHERE tuning_log_id=?",
            (log_id,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to reject candidate {log_id}: {e}")
        return False


def get_tuning_summary(limit=50):
    """
    Returns a summary of recent tuning activity for the /pnl command and dashboard.
    `limit` matches Tiberius's signature for cross-bot parity.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT symbol, parameter, old_value, new_value,
                   metric_name, metric_value, status, timestamp
            FROM tuning_log ORDER BY timestamp DESC LIMIT ?
        ''', (limit,))
        rows = c.fetchall()
        conn.close()
        return [
            {
                'symbol':       r[0], 'parameter':    r[1],
                'old_value':    r[2], 'new_value':    r[3],
                'metric_name':  r[4], 'metric_value': r[5],
                'status':       r[6], 'timestamp':    r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to get tuning summary: {e}")
        return []


# ==============================================================================
# DRAWDOWN KILL SWITCH — Equity Peak Watermark
# ==============================================================================

def get_equity_peak() -> float:
    """Returns the current all-time equity peak. Returns 0.0 if not yet seeded."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT peak FROM equity_peak WHERE id = 1')
        row = c.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to get equity peak: {e}")
        return 0.0


def update_equity_peak(new_peak: float):
    """Upserts the equity peak watermark (called when a new high is reached)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO equity_peak (id, peak) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET peak=excluded.peak, updated=CURRENT_TIMESTAMP
        ''', (new_peak,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to update equity peak: {e}")


def reset_equity_peak(current_equity: float):
    """Resets the watermark to current equity on /resume. Same as update."""
    update_equity_peak(current_equity)


# ==============================================================================
# SHADOW ACCOUNT — Synthetic ledger for shadow_mode
# Source of truth for shadow cash + equity. Live Alpaca account is never read
# when shadow_mode is on (the contract enforced in main.py).
# ==============================================================================

def init_shadow_account(initial_capital: float = 10000.0):
    """
    Seeds the shadow_account row if absent. Idempotent — does not overwrite
    an existing balance, so a service restart preserves the running ledger.
    To reset for a fresh shadow run, archive ionic_data.db and let init_db()
    recreate everything.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO shadow_account (id, cash, initial_capital) VALUES (1, ?, ?)",
            (initial_capital, initial_capital)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to init shadow_account: {e}")


def get_shadow_cash() -> float:
    """Returns current shadow cash balance. 0.0 if uninitialized."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT cash FROM shadow_account WHERE id = 1")
        row = c.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to read shadow cash: {e}")
        return 0.0


def get_shadow_initial_capital() -> float:
    """Returns the seeded initial capital for the shadow account."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT initial_capital FROM shadow_account WHERE id = 1")
        row = c.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to read shadow initial_capital: {e}")
        return 0.0


def adjust_shadow_cash(delta: float):
    """
    Atomic cash adjustment. Negative delta on shadow buy, positive on sell.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "UPDATE shadow_account SET cash = cash + ?, updated = CURRENT_TIMESTAMP WHERE id = 1",
            (delta,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to adjust shadow cash by {delta}: {e}")


def init_risk_state():
    """Seed the risk_state row if absent. Idempotent."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO risk_state (id) VALUES (1)")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to init risk_state: {e}")


def get_risk_state() -> dict:
    """
    Returns current risk-state dict: risk_mode, session_date,
    session_start_equity, daily_halt. Missing row returns NORMAL defaults.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT risk_mode, session_date, session_start_equity, daily_halt "
            "FROM risk_state WHERE id = 1"
        )
        row = c.fetchone()
        conn.close()
        if row:
            return {
                'risk_mode':            row[0],
                'session_date':         row[1],
                'session_start_equity': row[2],
                'daily_halt':           bool(row[3]),
            }
        return {
            'risk_mode':            'NORMAL',
            'session_date':         None,
            'session_start_equity': None,
            'daily_halt':           False,
        }
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to read risk_state: {e}")
        return {
            'risk_mode': 'NORMAL', 'session_date': None,
            'session_start_equity': None, 'daily_halt': False,
        }


def update_risk_state(**fields):
    """
    Updates one or more risk_state fields. daily_halt accepts bool, stored as
    INTEGER. Caller passes only the fields that need to change.

    Side effect: emits a `risk_transition` WS event when risk_mode actually
    changes (read-before-write so callers can pass risk_mode='NORMAL'
    idempotently without spamming the queue). daily_halt 0→1 also fires.
    """
    if not fields:
        return
    # _reason rides along for the WS event but isn't a DB column. Pop before
    # building the SET clause so it doesn't blow up as "no such column".
    reason = fields.pop('_reason', '')
    if 'daily_halt' in fields:
        fields['daily_halt'] = 1 if fields['daily_halt'] else 0

    prev_mode, prev_halt = None, None
    if 'risk_mode' in fields or 'daily_halt' in fields:
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT risk_mode, daily_halt FROM risk_state WHERE id = 1"
            ).fetchone()
            conn.close()
            if row:
                prev_mode, prev_halt = row[0], row[1]
        except Exception:
            pass

    cols = list(fields.keys())
    set_clause = ", ".join(f"{c} = ?" for c in cols) + ", updated = CURRENT_TIMESTAMP"
    values = [fields[c] for c in cols]
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"UPDATE risk_state SET {set_clause} WHERE id = 1", values)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DATABASE ERROR: Failed to update risk_state {fields}: {e}")
        return

    new_mode = fields.get('risk_mode')
    if new_mode is not None and new_mode != prev_mode:
        emit_event("risk_transition", {
            "from":    prev_mode or "UNKNOWN",
            "to":      new_mode,
            "reason":  reason,
        })
    new_halt = fields.get('daily_halt')
    if new_halt is not None and new_halt != (prev_halt or 0):
        emit_event("risk_transition", {
            "from":    "NO_DAILY_HALT" if not prev_halt else "DAILY_HALT",
            "to":      "DAILY_HALT" if new_halt else "NO_DAILY_HALT",
            "reason":  reason,
        })


def _fx_position_value_usd(symbol: str, units: float, price: float) -> float:
    """
    Mark-to-market value of an FX position in USD.

    Ionic inherited the equity-mark math from Anton (where positions are stocks
    and `shares × price` correctly yields USD value). For FX, the math depends
    on which side of the pair USD sits:

    - 'X/USD' (USD is the QUOTE currency):  value_usd = units × price
        Example: AUD/USD 1677 units @ 0.715 → 1677 × 0.715 = $1,199 USD.

    - 'USD/X' (USD is the BASE currency):   value_usd = units
        Each unit already IS one USD. Multiplying by price would mis-mark it
        as JPY/CAD/CHF.
        Example: USD/JPY 1199 units → $1,199 USD (NOT 1199 × 159 = $190K).

    Cross-rate FX (no USD leg, e.g. EUR/GBP) would need triangulation; not
    relevant for Ionic's 7-major universe (all pairs include USD).

    NOTE: This treats positions as held-asset value, not full directional PnL.
    For USD-base pairs that means PnL contribution from rate movement is not
    captured here — the position always marks at entry-value. Good enough for
    drawdown tracking (the primary consumer); a full FX PnL pass would need a
    separate calc using entry-price vs current-price ratios.
    """
    if not symbol:
        return float(units or 0) * float(price or 0)
    if symbol.startswith('USD/'):
        return float(units or 0)
    return float(units or 0) * float(price or 0)


def get_shadow_account_state(latest_prices: dict | None = None) -> dict:
    """
    Computes shadow equity from cash + market value of open positions.
    `latest_prices` maps symbol → most recent price; positions without a
    cached price fall back to entry_price (one-cycle stale, acceptable
    for drawdown tracking).

    For FX, the mark uses `_fx_position_value_usd` which handles USD-base
    pairs (USD/JPY, USD/CAD, USD/CHF) correctly — see that helper's docstring
    for why naïve `units × price` mis-marks USD-base pairs and pollutes the
    drawdown peak watermark.

    Returns dict with: equity, cash, held_assets ({symbol: shares})
    """
    cash = get_shadow_cash()
    positions = get_all_open_positions()
    prices = latest_prices or {}
    market_value = sum(
        _fx_position_value_usd(
            p['symbol'],
            p['shares'],
            prices.get(p['symbol'], p['entry_price']),
        )
        for p in positions
    )
    return {
        'equity':      cash + market_value,
        'cash':        cash,
        'held_assets': {p['symbol']: p['shares'] for p in positions},
    }


def upsert_live_account_cache(*, nav: float, balance: float,
                              unrealized_pl: float, margin_used: float = 0.0,
                              margin_avail: float = 0.0,
                              open_trades: int = 0,
                              currency: str = "USD") -> None:
    """Engine writes Oanda's authoritative numbers here once per cycle in
    live mode. Single-row table (id=1). UPSERT pattern."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO live_account_cache
              (id, nav, balance, unrealized_pl, margin_used, margin_avail,
               open_trades, currency, last_updated)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
              nav           = excluded.nav,
              balance       = excluded.balance,
              unrealized_pl = excluded.unrealized_pl,
              margin_used   = excluded.margin_used,
              margin_avail  = excluded.margin_avail,
              open_trades   = excluded.open_trades,
              currency      = excluded.currency,
              last_updated  = CURRENT_TIMESTAMP
        """, (nav, balance, unrealized_pl, margin_used, margin_avail,
              open_trades, currency))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"upsert_live_account_cache failed: {e}")


def get_live_account_cache(max_age_seconds: int = 300) -> dict | None:
    """Return the cached Oanda account state, or None if missing/stale.
    Stale threshold defaults to 5 minutes — readers should fall back to
    shadow math if None is returned (engine likely down or just booted)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT nav, balance, unrealized_pl, margin_used, margin_avail,
                   open_trades, currency,
                   strftime('%s', 'now') - strftime('%s', last_updated) AS age_s
            FROM live_account_cache WHERE id = 1
        """).fetchone()
        conn.close()
    except Exception as e:
        logger.warning(f"get_live_account_cache failed: {e}")
        return None
    if row is None:
        return None
    age = int(row['age_s'] or 0)
    if age > max_age_seconds:
        return None
    return {
        'nav':           row['nav'] or 0.0,
        'balance':       row['balance'] or 0.0,
        'unrealized_pl': row['unrealized_pl'] or 0.0,
        'margin_used':   row['margin_used'] or 0.0,
        'margin_avail':  row['margin_avail'] or 0.0,
        'open_trades':   int(row['open_trades'] or 0),
        'currency':      row['currency'] or 'USD',
        'age_seconds':   age,
    }


def get_account_state(shadow_mode: bool = True,
                      latest_prices: dict | None = None) -> dict:
    """Unified account-state read. Routes by mode:

    - shadow_mode=True: synthetic ledger math (get_shadow_account_state).
    - shadow_mode=False: live Oanda NAV from the cache, with shadow math
      as fallback if the cache is stale or missing.

    Return shape is always: {equity, cash, held_assets, source}
    where `source` is 'shadow' | 'live' | 'live-fallback-shadow' so
    callers can flag stale data in the UI.
    """
    if shadow_mode:
        state = get_shadow_account_state(latest_prices)
        state['source'] = 'shadow'
        return state

    cache = get_live_account_cache()
    if cache is None:
        # Live mode but cache stale → fall back to shadow math so the
        # dashboard isn't blank. UI shows the source so the user knows.
        state = get_shadow_account_state(latest_prices)
        state['source'] = 'live-fallback-shadow'
        return state

    positions = get_all_open_positions()
    return {
        'equity':      cache['nav'],            # NAV = balance + unrealized
        'cash':        cache['balance'],
        'held_assets': {p['symbol']: p['shares'] for p in positions},
        'source':      'live',
        'unrealized_pl': cache['unrealized_pl'],
        'margin_used':   cache['margin_used'],
        'margin_avail':  cache['margin_avail'],
        'last_updated_seconds_ago': cache['age_seconds'],
    }
