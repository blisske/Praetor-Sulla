"""
seed_demo.py — Reseeds core/demo_data.db from REAL Ionic shadow data.

Pulls from the live `ionic.db`:
  - trades           ← trades_archive_v1   (87 real Binance.US-era shadow trades)
  - open_positions   ← derived from FIFO-matching BUYs vs SELLs in the archive,
                       then consolidating pyramided entries to one row per
                       symbol (avg entry, summed size — current schema is
                       single-row PK on symbol)
  - market_states    ← live market_states  (real recent Oanda data, 16 syms)
  - equity_peak      ← computed from the realized-PnL curve over the run
  - meta             ← initial_capital=1000.0 (era), era_label

No fabrication. The credibility line for an external viewer is "this is what
the system actually did" — pad nothing, omit nothing material. Modest absolute
returns are honest. Architecture and discipline are the story.

Run from the praetor root with the venv active:
    python3 scripts/seed_demo.py

Safe to re-run — fully drops and rebuilds demo_data.db.
"""
import sqlite3
import shutil
import sys
from pathlib import Path

CORE   = Path(__file__).parent.parent / 'core'
LIVE   = CORE / 'ionic.db'
DEMO   = CORE / 'demo_data.db'

# Symbols present in trades_archive_v1 (Binance.US era — pre-Oanda migration).
# Used to scope the demo tuner cycle to only what we have closed-trade data for.
ARCHIVE_SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'LINK/USD', 'AVAX/USD']

ERA_INITIAL_CAPITAL = 1000.0
ERA_LABEL = 'Binance.US shadow run, 2026-04-06 → 2026-04-27 (pre-Oanda migration)'


def _drop_and_create_schema(conn):
    c = conn.cursor()
    c.executescript('''
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS open_positions;
        DROP TABLE IF EXISTS market_states;
        DROP TABLE IF EXISTS equity_peak;
        DROP TABLE IF EXISTS tuning_log;
        DROP TABLE IF EXISTS param_snapshots;
        DROP TABLE IF EXISTS meta;

        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            action TEXT,
            price REAL,
            amount REAL,
            strategy TEXT,
            verdict TEXT,
            position_size_usd REAL DEFAULT 0.0
        );

        CREATE TABLE open_positions (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            strategy TEXT,
            current_stop REAL DEFAULT 0.0,
            entry_atr REAL DEFAULT 0.0,
            entry_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            position_size_usd REAL DEFAULT 0.0
        );

        CREATE TABLE market_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            price REAL,
            adx REAL,
            regime TEXT,
            trend TEXT,
            rsi REAL,
            volume REAL,
            avg_volume REAL
        );

        CREATE TABLE equity_peak (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            peak_equity REAL NOT NULL,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE tuning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,
            parameter TEXT,
            old_value REAL,
            new_value REAL,
            metric_name TEXT,
            metric_value REAL,
            trade_count INTEGER,
            status TEXT DEFAULT 'SHADOW_PENDING'
        );

        CREATE TABLE param_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tuning_log_id INTEGER,
            symbol TEXT,
            parameter TEXT,
            candidate_value REAL,
            baseline_value REAL,
            shadow_trades_required INTEGER,
            shadow_trades_completed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Per-DB demo metadata. /api/equity reads initial_capital from here
        -- when present so the demo's $1K era stays decoupled from live Config.yaml.
        CREATE TABLE meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            initial_capital REAL,
            era_label TEXT
        );
    ''')
    conn.commit()


def _seed_meta(demo):
    demo.execute(
        "INSERT INTO meta (id, initial_capital, era_label) VALUES (1, ?, ?)",
        (ERA_INITIAL_CAPITAL, ERA_LABEL),
    )
    demo.commit()
    print(f"✅ meta: initial_capital=${ERA_INITIAL_CAPITAL:.2f}  era={ERA_LABEL!r}")


def _copy_archive_trades(live, demo):
    """Copy all rows from live trades_archive_v1 into demo trades — preserves
    timestamps, prices, AI verdicts, paradigms, and position_size_usd verbatim."""
    rows = live.execute("""
        SELECT timestamp, symbol, action, price, amount, strategy, verdict, position_size_usd
        FROM trades_archive_v1 ORDER BY id ASC
    """).fetchall()
    demo.executemany("""
        INSERT INTO trades (timestamp, symbol, action, price, amount, strategy, verdict, position_size_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    demo.commit()
    n_buy  = sum(1 for r in rows if r[2] == 'SHADOW BUY')
    n_sell = sum(1 for r in rows if r[2] == 'SHADOW SELL')
    print(f"✅ trades: copied {len(rows)} archive rows ({n_buy} BUY / {n_sell} SELL)")
    return rows


def _seed_open_positions(demo, archive_rows):
    """FIFO-match BUYs vs SELLs across the archive run; whatever's left
    unmatched at archive close is open exposure. Multiple unmatched BUYs
    on the same symbol (pyramid legs) are consolidated to one row with
    capital-weighted avg entry and summed size_usd."""
    buy_queue = {}  # symbol -> list of dicts (in chronological order)
    for ts, sym, act, price, amount, strat, verdict, size_usd in archive_rows:
        if act == 'SHADOW BUY':
            buy_queue.setdefault(sym, []).append({
                'ts': ts, 'price': price, 'strategy': strat, 'size_usd': size_usd,
            })
        elif act == 'SHADOW SELL':
            if buy_queue.get(sym):
                buy_queue[sym].pop(0)

    inserted = 0
    for sym in sorted(buy_queue.keys()):
        legs = [b for b in buy_queue[sym] if b['size_usd'] > 0]
        if not legs:
            continue
        total_size = sum(b['size_usd'] for b in legs)
        # Capital-weighted avg entry — single number that represents the
        # net exposure cost basis if all legs were treated as one position.
        avg_entry  = sum(b['price'] * b['size_usd'] for b in legs) / total_size
        # Most recent leg drives the strategy/timestamp shown.
        latest     = legs[-1]
        # Stop placeholder: 5% below avg entry. Archive doesn't preserve
        # the actual ratchet stop in force at close; this is a conservative
        # static proxy. Live engine would re-derive on next ratchet pass.
        stop       = round(avg_entry * 0.95, 2)
        demo.execute("""
            INSERT INTO open_positions
                (symbol, entry_price, strategy, current_stop, entry_atr, entry_timestamp, position_size_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (sym, round(avg_entry, 2), latest['strategy'], stop, 0.0, latest['ts'], round(total_size, 2)))
        inserted += 1
        print(f"   {sym:8s}  avg_entry=${avg_entry:>10,.2f}  stop=${stop:>10,.2f}  size=${total_size:>7.2f}  legs={len(legs)}  ({latest['strategy']})")
    demo.commit()
    print(f"✅ open_positions: {inserted} consolidated (from FIFO-derived archive residual)")


def _seed_equity_peak(demo, archive_rows):
    """Walk realized PnL forward from era_initial; take the max as peak."""
    running = ERA_INITIAL_CAPITAL
    peak    = running
    peak_ts = None
    for ts, sym, act, price, amount, strat, verdict, size_usd in archive_rows:
        if act == 'SHADOW SELL':
            running += amount
            if running > peak:
                peak    = running
                peak_ts = ts
    demo.execute(
        "INSERT INTO equity_peak (id, peak_equity, recorded_at) VALUES (1, ?, ?)",
        (round(peak, 2), peak_ts or '2026-04-27 00:00:00'),
    )
    demo.commit()
    print(f"✅ equity_peak: ${peak:,.2f} at {peak_ts}")


def _copy_market_states(live, demo):
    """Copy ALL real Oanda market_states from live DB. Shows the current
    Oanda universe (16 symbols) on the demo Market page — even though the
    historical trades are from the Binance.US era. Honest framing: archive
    is past venue, charts are current venue post-migration."""
    rows = live.execute("""
        SELECT timestamp, symbol, price, adx, regime, trend, rsi, volume, avg_volume
        FROM market_states ORDER BY id ASC
    """).fetchall()
    demo.executemany("""
        INSERT INTO market_states (timestamp, symbol, price, adx, regime, trend, rsi, volume, avg_volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    demo.commit()
    n_syms = len({r[1] for r in rows})
    print(f"✅ market_states: copied {len(rows)} rows across {n_syms} symbols (current Oanda universe)")


def _seed_tuning_proposals(symbols, min_trades_demo=5):
    """Run the REAL tuner against demo_data.db so the Tuning page reflects
    genuine self-tuning logic, not synthetic illustration.

    The live tuner gate is `min_trades_to_tune=10` per (symbol, strategy)
    pair. The archive only reaches 7 trades on the most-traded pair
    (ETH/VBO), so a live gate produces zero proposals. We temporarily
    lower the gate to 5 here so the engine surfaces something — every
    other input (profit factor, win rate, candidate parameter selection,
    bounds checks, cooling-off logic) is unmodified. Real metrics, real
    candidate selection, real candidate values — only the trade-count
    floor differs from production.

    Safety: only `run_tuning_cycle` is invoked. `check_promotions` (which
    calls `_apply_to_config` and writes to live Config.yaml on PROMOTED
    decisions) is NEVER called. All proposals end up SHADOW_PENDING and
    are confined to demo_data.db.
    """
    if str(CORE) not in sys.path:
        sys.path.insert(0, str(CORE))
    import database
    import tuner

    orig_db_path        = database.DB_PATH
    orig_tuner_db_path  = tuner.DB_PATH
    orig_get_cfg        = tuner._get_tuning_config

    def _demo_tuning_cfg():
        cfg = dict(orig_get_cfg() or {})
        cfg['min_trades_to_tune'] = min_trades_demo
        cfg['cooling_off_hours']  = 0  # demo tuning_log starts empty anyway
        return cfg

    database.DB_PATH         = DEMO
    tuner.DB_PATH            = DEMO
    tuner._get_tuning_config = _demo_tuning_cfg
    try:
        n = tuner.run_tuning_cycle(symbols)
    finally:
        database.DB_PATH         = orig_db_path
        tuner.DB_PATH            = orig_tuner_db_path
        tuner._get_tuning_config = orig_get_cfg

    print(f"✅ tuning_log: {n} proposal(s) generated by real tuner "
          f"(gate temporarily lowered: min_trades_to_tune={min_trades_demo} for demo)")
    return n


if __name__ == '__main__':
    if not LIVE.exists():
        raise SystemExit(f"Live DB not found at {LIVE} — cannot reseed demo from real data.")

    print(f"Source: {LIVE}")
    print(f"Target: {DEMO}\n")

    # Hard reset — wipe whatever's there and rebuild from scratch.
    if DEMO.exists():
        backup = DEMO.with_suffix('.db.bak')
        shutil.copy(DEMO, backup)
        print(f"📦 Backed up existing demo to {backup}")
        DEMO.unlink()

    live = sqlite3.connect(LIVE)
    demo = sqlite3.connect(DEMO)
    try:
        _drop_and_create_schema(demo)
        _seed_meta(demo)
        archive_rows = _copy_archive_trades(live, demo)
        _seed_open_positions(demo, archive_rows)
        _seed_equity_peak(demo, archive_rows)
        _copy_market_states(live, demo)
    finally:
        live.close()
        demo.close()

    # Tuner runs against demo_data.db with its own connections via core/
    # database.py module functions — main demo conn must be closed first
    # to avoid lock contention.
    _seed_tuning_proposals(ARCHIVE_SYMBOLS)

    print(f"\n🎉 Demo database rebuilt from real archive at {DEMO}")
