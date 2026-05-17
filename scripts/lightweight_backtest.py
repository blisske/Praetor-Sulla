"""
lightweight_backtest.py — Sulla historical backtest harness.

Pattern ported from Tiberius's pre-flip tuning pass. Runs Sulla's production
strategy code verbatim — `strategy.check_entry_signals`,
`strategy.check_supporting_signals`, the inlined MTF gate, the consensus score
gate, ATR-stop sizing, ratchet trailing stop, EOD force-exit, tiered
drawdown, and the daily-session-loss circuit — over historical 30-min OHLCV
fetched from Alpaca's paper data feed.

Per-paradigm gate funnel reporting is mandatory. Aggregate counters lie:
Tiberius's first cut showed "X fires, Y consensus passes, Z MTF blocks" and
hid the actual diagnosis (TF was being killed by the consensus stack, not
MTF). The funnel reports per-paradigm `fires → consensus_pass → mtf_blocked
→ entered → exit_outcomes`.

────────────────────────────────────────────────────────────────────────────
EXPLICITLY NOT MODELED (caveats):
  * AI veto (Gemma 4 BEARISH abort) — pass-through. Will overstate fire counts
    by however many would have been killed by sentiment in production.
  * Slippage / commissions — Alpaca Paper is fee-free, but live will have
    micro-slippage on stops. Estimate ~1-3 bps drag per round trip.
  * Earnings blackout — yfinance is live-only, can't replay. Earnings-day
    entries WILL appear in the backtest that wouldn't fire in production.
  * Pyramiding — flag default-off; harness assumes single-leg entries.
  * Correlation-aware sizing — modeled (uses live config curve).
  * Tiered drawdown / daily session loss — modeled.

USAGE:
  python scripts/lightweight_backtest.py                       # 12mo, all symbols, current Config
  python scripts/lightweight_backtest.py --start 2025-10-01 --end 2026-04-25
  python scripts/lightweight_backtest.py --symbols SPY QQQ NVDA
  python scripts/lightweight_backtest.py --params strategy.paradigms.trend_following.rsi_entry=55
  python scripts/lightweight_backtest.py --output report.md
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import pandas_ta as ta
import pytz
import yaml

# Make Sulla's core/ importable so we run production strategy code verbatim.
ROOT      = Path(__file__).resolve().parent.parent
CORE_DIR  = ROOT / 'core'
sys.path.insert(0, str(CORE_DIR))

import strategy as sulla_strategy  # noqa: E402
import config_manager              # noqa: E402

from alpaca.data.requests import StockBarsRequest          # noqa: E402
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit # noqa: E402
from alpaca.data.enums import DataFeed                     # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ET_TZ          = pytz.timezone('America/New_York')
CACHE_DIR      = ROOT / 'scripts' / 'backtest_cache'
RESULTS_DIR    = ROOT / 'scripts' / 'backtest_results'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Sulla paradigms — must match strategy.py return strings.
PARADIGMS = ['TREND FOLLOWING', 'MEAN REVERSION', 'VOLATILITY BREAKOUT', 'LIQUIDITY SWEEP']

# Map paradigm name → which symbols the MTF gate applies to.
MTF_GATED_PARADIGMS = {'TREND FOLLOWING', 'VOLATILITY BREAKOUT'}

# Bar minutes used to size lookback windows.
BAR_MINUTES = 30

# ─────────────────────────────────────────────────────────────────────────────
# Data layer
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(symbol: str, tf_str: str, start: datetime, end: datetime) -> Path:
    s = start.strftime('%Y%m%d')
    e = end.strftime('%Y%m%d')
    return CACHE_DIR / f"{symbol}_{tf_str}_{s}_{e}.pkl"


def _alpaca_tf(tf_str: str) -> TimeFrame:
    if tf_str == '30m':
        return TimeFrame(30, TimeFrameUnit.Minute)
    if tf_str == '1d':
        return TimeFrame.Day
    if tf_str == '1h':
        return TimeFrame.Hour
    raise ValueError(f"Unsupported timeframe: {tf_str}")


def fetch_bars(symbol: str, tf_str: str, start: datetime, end: datetime,
               *, use_cache: bool = True) -> pd.DataFrame:
    """
    Fetch historical OHLCV bars from Alpaca, caching to parquet for fast reruns.
    Returns a tz-aware (ET) DataFrame indexed by bar timestamp with columns
    ['open','high','low','close','volume','trade_count','vwap'].
    """
    cache_path = _cache_key(symbol, tf_str, start, end)
    if use_cache and cache_path.exists():
        return pd.read_pickle(cache_path)

    client  = config_manager.get_data_client()
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=_alpaca_tf(tf_str),
        start=start,
        end=end,
        feed=DataFeed.IEX,    # paper account is IEX-only; matches live engine
    )
    bars = client.get_stock_bars(request)
    if bars.df.empty:
        empty = pd.DataFrame(columns=['open','high','low','close','volume'])
        empty.index = pd.DatetimeIndex([], tz='America/New_York', name='time')
        return empty

    df = bars.df.reset_index()
    df = df.rename(columns={'timestamp': 'time'})
    df.set_index('time', inplace=True)
    df.index = df.index.tz_convert('America/New_York')
    if 'symbol' in df.columns:
        df = df.drop(columns=['symbol'])

    df.to_pickle(cache_path)
    return df


def add_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Mirrors core/market_data.py:_fetch_indicators_sync. Computes EMA / ADX /
    RSI / ATR / BB on the bar series, then returns a copy with NaN rows
    dropped. Empty in → empty out.
    """
    if df.empty or len(df) < 25:
        return df.iloc[0:0].copy()

    s_cfg = config.get('strategy', {})
    r_cfg = config.get('ratchet', {})

    ema_fast = s_cfg.get('ema_fast', 9)
    ema_slow = s_cfg.get('ema_slow', 21)
    rsi_len  = s_cfg.get('rsi_period', 14)
    atr_len  = r_cfg.get('atr_period', 14)

    out = df.copy()
    out['ema_fast'] = ta.ema(out['close'], length=ema_fast)
    out['ema_slow'] = ta.ema(out['close'], length=ema_slow)

    adx_df = ta.adx(out['high'], out['low'], out['close'], length=14)
    out['adx'] = adx_df['ADX_14'] if adx_df is not None else float('nan')

    out['rsi'] = ta.rsi(out['close'], length=rsi_len)
    out['atr'] = ta.atr(out['high'], out['low'], out['close'], length=atr_len)

    bb = ta.bbands(out['close'], length=20, std=2.0)
    out['bb_lower']  = bb.iloc[:, 0]
    out['bb_middle'] = bb.iloc[:, 1]
    out['bb_upper']  = bb.iloc[:, 2]

    out.dropna(inplace=True)
    return out


def build_indicator_dict(panel: pd.DataFrame, i: int, *, daily_trend: str,
                         adx_threshold: int) -> dict | None:
    """
    Build the per-bar indicator dict that strategy.check_entry_signals expects.
    Mirrors the slicing in market_data.py: current = bar[i], closed = bar[i-1],
    prev2 = bar[i-2]. Returns None if there isn't enough lookback.

    Includes the `timestamp` field — strategy.py:27-31 reads this when the
    backtester is feeding it, falling back to wall-clock time only in live
    production. Without it, the script's wall-clock time would trip the
    9:30/15:30 RTH guard and reject every bar.
    """
    if i < 21:
        return None
    current = panel.iloc[i]
    closed  = panel.iloc[i - 1]
    prev2   = panel.iloc[i - 2]

    avg_volume_window = panel.iloc[max(0, i - 21):i]['volume']
    avg_volume = float(avg_volume_window.mean()) if len(avg_volume_window) else 1.0

    bar_ts = panel.index[i]
    if bar_ts.tzinfo is None:
        bar_ts = ET_TZ.localize(bar_ts)

    return {
        'timestamp':      bar_ts.to_pydatetime() if hasattr(bar_ts, 'to_pydatetime') else bar_ts,
        'price':          float(current['close']),
        'high':           float(current['high']),
        'low':            float(current['low']),
        'ema_fast':       float(current['ema_fast']),
        'ema_slow':       float(current['ema_slow']),
        'rsi':            float(current['rsi']),
        'atr':            float(current['atr']),
        'adx':            float(current['adx']),
        'bb_lower':       float(current['bb_lower']),
        'bb_middle':      float(current['bb_middle']),
        'bb_upper':       float(current['bb_upper']),
        'regime':         "TRENDING" if current['adx'] > adx_threshold else "RANGING",
        'trend':          "BULL" if current['ema_fast'] > current['ema_slow'] else "BEAR",
        'volume':         float(closed['volume']),
        'avg_volume':     avg_volume,
        'rsi_prev2':      float(prev2['rsi']),
        'adx_prev2':      float(prev2['adx']),
        'bb_lower_prev':  float(closed['bb_lower']),
        'bb_middle_prev': float(closed['bb_middle']),
        'bb_upper_prev':  float(closed['bb_upper']),
        'high_closed':    float(closed['high']),
        'daily_trend':    daily_trend,
    }


def precompute_daily_trend(daily_panel: pd.DataFrame) -> dict[str, str]:
    """
    Returns {iso_date: 'BULL'|'BEAR'} based on EMA9 vs EMA21 of the daily bars.
    Matches Sulla's _get_daily_trend() in main.py — daily MTF is computed once
    per ET trading day.
    """
    if daily_panel.empty:
        return {}
    out = {}
    for ts, row in daily_panel.iterrows():
        iso = ts.tz_convert(ET_TZ).date().isoformat()
        out[iso] = 'BULL' if row['ema_fast'] > row['ema_slow'] else 'BEAR'
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Engine state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol:    str
    paradigm:  str
    entry_ts:  datetime
    entry_px:  float
    entry_atr: float
    shares:    int
    stop:      float
    peak_px:   float = 0.0          # for ratchet
    notional:  float = 0.0


@dataclass
class Trade:
    symbol:    str
    paradigm:  str
    entry_ts:  datetime
    entry_px:  float
    exit_ts:   datetime
    exit_px:   float
    shares:    int
    pnl_usd:   float
    pnl_pct:   float
    exit_reason: str
    bars_held: int

    @property
    def is_win(self) -> bool:
        return self.pnl_usd > 0


@dataclass
class Funnel:
    """Per-paradigm gate funnel counters. Mirrors Tiberius's report shape."""
    fires:           int = 0
    mtf_blocked:     int = 0
    consensus_pass:  int = 0
    consensus_fail:  int = 0
    bearish_abort:   int = 0  # always 0 here; AI is pass-through
    entered:         int = 0
    exit_reasons:    dict = field(default_factory=lambda: {})

    def add_exit(self, reason: str) -> None:
        self.exit_reasons[reason] = self.exit_reasons.get(reason, 0) + 1


@dataclass
class EngineState:
    config:        dict
    initial_cap:   float
    cash:          float
    peak_equity:   float
    risk_mode:     str = 'NORMAL'         # NORMAL / ALERT / DERISK / HALT
    daily_halt:    bool = False
    session_date:  str | None = None
    session_start_eq: float = 0.0
    open_positions: dict[str, Position] = field(default_factory=dict)
    closed_trades:  list[Trade] = field(default_factory=list)
    funnels:        dict[str, Funnel] = field(default_factory=lambda: {p: Funnel() for p in PARADIGMS})
    equity_curve:   list[tuple[datetime, float]] = field(default_factory=list)
    halt_events:    list[tuple[datetime, str]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Engine helpers — mirror core/main.py + core/execution.py
# ─────────────────────────────────────────────────────────────────────────────

def _get_sector(symbol: str, sectors_map: dict) -> str:
    for sector_name, syms in sectors_map.items():
        if symbol in syms:
            return sector_name
    return 'Uncategorized'


def correlation_multiplier(symbol: str, held: dict[str, Position], config: dict) -> float:
    corr_cfg = config.get('correlation_aware_sizing', {})
    if not corr_cfg.get('enabled', True):
        return 1.0
    sectors = corr_cfg.get('sectors', {})
    curve   = corr_cfg.get('curve', [1.0, 0.85, 0.70, 0.55, 0.40])
    target  = _get_sector(symbol, sectors)
    if target == 'Uncategorized' or not curve:
        return 1.0
    n = sum(1 for s in held if s != symbol and _get_sector(s, sectors) == target)
    n = min(n, len(curve) - 1)
    return float(curve[n])


def calc_position_size_shares(equity: float, atr: float, price: float,
                              risk_cfg: dict, ratchet_cfg: dict,
                              risk_scalar: float) -> tuple[int, float]:
    """
    Mirrors core/execution.py:calculate_position_size, but returns whole shares
    + notional. risk_scalar composes DERISK and correlation multipliers.
    """
    risk_pct  = risk_cfg.get('risk_per_trade_pct', 1.0) / 100.0
    risk_usd  = equity * risk_pct * risk_scalar
    stop_mult = ratchet_cfg.get('initial_stop_mult', 2.0)

    stop_dist = atr * stop_mult
    if stop_dist <= 0:
        stop_dist = price * 0.01

    raw_shares    = risk_usd / stop_dist
    actual_shares = math.floor(raw_shares)
    if actual_shares < 1:
        return 0, 0.0

    notional = actual_shares * price
    min_size = risk_cfg.get('min_trade_size_usd', 12.0)
    max_pct  = risk_cfg.get('position_size_max_pct', 5.0) / 100.0
    max_size = equity * max_pct
    if notional > max_size:
        capped = math.floor(max_size / price)
        if capped < 1:
            return 0, 0.0
        actual_shares = capped
        notional      = capped * price
    if notional < min_size:
        return 0, 0.0
    return actual_shares, notional


def update_drawdown_state(state: EngineState, equity: float, ts: datetime) -> None:
    """Mirrors the tiered-drawdown block in main.py:880-905."""
    risk_cfg   = state.config.get('risk', {})
    alert_pct  = risk_cfg.get('drawdown_alert_pct',    8.0)
    derisk_pct = risk_cfg.get('drawdown_derisk_pct',   15.0)
    halt_pct   = risk_cfg.get('drawdown_halt_pct',     25.0)
    recover_pct= risk_cfg.get('drawdown_recovery_pct', 10.0)

    if equity > state.peak_equity:
        state.peak_equity = equity
    drawdown = ((state.peak_equity - equity) / state.peak_equity * 100.0) if state.peak_equity > 0 else 0.0

    new_mode = state.risk_mode
    if state.risk_mode == 'HALT':
        # No auto-recovery from HALT in production; backtest mirrors that.
        return
    if drawdown >= halt_pct:
        new_mode = 'HALT'
    elif drawdown >= derisk_pct:
        new_mode = 'DERISK'
    elif drawdown >= alert_pct:
        new_mode = 'ALERT' if state.risk_mode != 'DERISK' or drawdown >= derisk_pct else state.risk_mode
    else:
        # Hysteresis: DERISK only releases when drawdown shrinks below recovery_pct.
        if state.risk_mode == 'DERISK' and drawdown >= recover_pct:
            new_mode = 'DERISK'
        else:
            new_mode = 'NORMAL'

    if new_mode != state.risk_mode:
        state.halt_events.append((ts, f"{state.risk_mode}→{new_mode} (DD {drawdown:.1f}%)"))
        state.risk_mode = new_mode


def should_skip_entries(state: EngineState) -> bool:
    return state.risk_mode == 'HALT' or state.daily_halt


def daily_session_check(state: EngineState, equity: float, ts_et: datetime) -> None:
    """
    Mirrors main.py:858-940 — captures session-start equity at first cycle of
    each ET day, then trips the daily halt if intraday drawdown crosses the
    limit. Auto-clears next session.
    """
    risk_cfg = state.config.get('risk', {})
    iso_today = ts_et.date().isoformat()
    if state.session_date != iso_today:
        state.session_date     = iso_today
        state.session_start_eq = equity
        state.daily_halt       = False
        return
    if state.daily_halt or state.session_start_eq <= 0:
        return
    daily_lim = risk_cfg.get('daily_session_loss_pct', 3.0)
    sse_dd    = ((state.session_start_eq - equity) / state.session_start_eq * 100.0)
    if sse_dd >= daily_lim:
        state.daily_halt = True
        state.halt_events.append((ts_et, f"DAILY_HALT (intraday DD {sse_dd:.1f}%)"))


def mark_to_market(state: EngineState, last_prices: dict[str, float]) -> float:
    mv = 0.0
    for sym, pos in state.open_positions.items():
        px = last_prices.get(sym, pos.entry_px)
        mv += pos.shares * px
    return state.cash + mv


# ─────────────────────────────────────────────────────────────────────────────
# Core engine loop
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(panels: dict[str, pd.DataFrame],
                 daily_trends: dict[str, dict[str, str]],
                 timeline: pd.DatetimeIndex,
                 config: dict) -> EngineState:
    """
    Iterate a unified timeline of 30-min bar timestamps. At each timestamp:
      1. Update every open position's mark price + ratchet trailing stop.
      2. Process exits (stop hit, take-profit on MR/LS, EOD force-close).
      3. Compute equity, update peak, update drawdown state, daily halt.
      4. Process candidate entries for each symbol that has a bar at this ts.
    """
    initial = config.get('risk', {}).get('initial_capital', 10000.0)
    state = EngineState(
        config=config,
        initial_cap=initial,
        cash=initial,
        peak_equity=initial,
    )

    # Precompute per-symbol bar-index lookup so we can O(1) the bar at ts.
    symbol_index_lookup: dict[str, dict[pd.Timestamp, int]] = {}
    for sym, panel in panels.items():
        symbol_index_lookup[sym] = {ts: i for i, ts in enumerate(panel.index)}

    consensus_min = config.get('consensus', {}).get('min_consensus_score', 3)
    eod_hour      = config.get('strategy', {}).get('eod_exit_hour',   15)
    eod_minute    = config.get('strategy', {}).get('eod_exit_minute', 50)
    entry_cutoff_h = 15
    entry_cutoff_m = 30
    market_open_h  = 9
    market_open_m  = 30

    last_prices: dict[str, float] = {}

    for ts in timeline:
        ts_et = ts.tz_convert(ET_TZ) if ts.tz else ET_TZ.localize(ts)
        # Skip non-RTH bars (just in case the feed includes any).
        bar_minutes = ts_et.hour * 60 + ts_et.minute
        if bar_minutes < market_open_h * 60 + market_open_m:
            continue
        if bar_minutes >= 16 * 60:
            continue

        is_eod = (ts_et.hour, ts_et.minute) >= (eod_hour, eod_minute)
        is_after_entry_cutoff = (ts_et.hour, ts_et.minute) >= (entry_cutoff_h, entry_cutoff_m)

        # Refresh prices we have at this ts.
        bars_now: dict[str, pd.Series] = {}
        for sym, panel in panels.items():
            i = symbol_index_lookup[sym].get(ts)
            if i is not None and i >= 21:
                bars_now[sym] = panel.iloc[i]
                last_prices[sym] = float(panel.iloc[i]['close'])

        # ── Daily session reset / halt check (uses current MTM equity) ──
        equity_now = mark_to_market(state, last_prices)
        daily_session_check(state, equity_now, ts_et)

        # ── 1. Ratchet trailing stops on open positions ──
        for sym, pos in list(state.open_positions.items()):
            if sym not in bars_now:
                continue
            row     = bars_now[sym]
            sym_cfg = config_manager.get_symbol_config(config, sym)
            trail_mult = sym_cfg.get('ratchet', {}).get('trailing_stop_mult', 2.5)
            # Power Hour buffer
            if sym_cfg.get('ratchet', {}).get('power_hour_defense', {}).get('enabled', True) and ts_et.hour == 15:
                trail_mult += sym_cfg.get('ratchet', {}).get('power_hour_defense', {}).get('atr_buffer', 0.5)

            atr_now  = float(row['atr'])
            high_now = float(row['high'])
            if high_now > pos.peak_px:
                pos.peak_px = high_now
            new_stop = float(row['close']) - (atr_now * trail_mult)
            if new_stop > pos.stop:
                pos.stop = new_stop

        # ── 2. Process exits ──
        for sym, pos in list(state.open_positions.items()):
            if sym not in bars_now:
                continue
            row    = bars_now[sym]
            low_n  = float(row['low'])
            high_n = float(row['high'])
            close_n = float(row['close'])

            exit_px:  float | None = None
            exit_reason = ''

            # 2a. Stop hit (intrabar low touches stop)
            if pos.stop > 0 and low_n <= pos.stop:
                exit_px = pos.stop
                exit_reason = 'STOP_HIT'

            # 2b. Take-profit on MR / LS (BB middle / upper, mirrors strategy.py)
            if exit_px is None and pos.paradigm in ('MEAN REVERSION', 'LIQUIDITY SWEEP'):
                bb_mid = float(row['bb_middle'])
                bb_up  = float(row['bb_upper'])
                yield_pct = ((close_n - pos.entry_px) / pos.entry_px) * 100
                if high_n >= bb_up:
                    exit_px = max(bb_up, close_n)
                    exit_reason = 'TP_BB_UPPER'
                elif close_n >= bb_mid and yield_pct > 2.0:
                    exit_px = close_n
                    exit_reason = 'TP_BB_MIDDLE'

            # 2c. EOD force-exit at 15:50 ET
            if exit_px is None and is_eod:
                exit_px = close_n
                exit_reason = 'EOD'

            if exit_px is not None:
                pnl_usd = (exit_px - pos.entry_px) * pos.shares
                pnl_pct = ((exit_px - pos.entry_px) / pos.entry_px) * 100
                bars_held = symbol_index_lookup[sym][ts] - symbol_index_lookup[sym].get(pos.entry_ts, symbol_index_lookup[sym][ts])
                trade = Trade(
                    symbol=sym, paradigm=pos.paradigm,
                    entry_ts=pos.entry_ts, entry_px=pos.entry_px,
                    exit_ts=ts_et, exit_px=exit_px, shares=pos.shares,
                    pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                    exit_reason=exit_reason, bars_held=bars_held,
                )
                state.closed_trades.append(trade)
                state.funnels[pos.paradigm].add_exit(exit_reason)
                state.cash += pos.shares * exit_px
                del state.open_positions[sym]

        # Recompute equity after any exits
        equity_now = mark_to_market(state, last_prices)
        update_drawdown_state(state, equity_now, ts_et)
        state.equity_curve.append((ts_et, equity_now))

        # Don't open new entries past the cutoff or under HALT/daily_halt
        if is_after_entry_cutoff or should_skip_entries(state):
            continue

        # ── 3. Process candidate entries ──
        max_open = config.get('strategy', {}).get('max_open_trades', 5)
        if len(state.open_positions) >= max_open:
            continue

        for sym, row in bars_now.items():
            if sym in state.open_positions:
                continue
            i = symbol_index_lookup[sym][ts]
            sym_cfg = config_manager.get_symbol_config(config, sym)

            adx_threshold_sym = sym_cfg.get('strategy', {}).get('adx_trend_threshold', 25)
            iso = ts_et.date().isoformat()
            daily_trend = daily_trends.get(sym, {}).get(iso, 'BULL')  # fail-open like prod

            ind = build_indicator_dict(panels[sym], i, daily_trend=daily_trend,
                                       adx_threshold=adx_threshold_sym)
            if ind is None:
                continue
            ind['symbol'] = sym

            # ── Layer 1: Paradigm fire ──
            try:
                fired, paradigm = sulla_strategy.check_entry_signals(ind, sym_cfg)
            except Exception:
                fired, paradigm = False, "NONE"
            if not fired or paradigm not in PARADIGMS:
                continue
            funnel = state.funnels[paradigm]
            funnel.fires += 1

            # MTF gate (re-enforced here for funnel reporting; check_entry_signals
            # already filters TF/VB but a daily_trend miss falls through to other
            # paradigms — we want to count the block).
            mtf_enabled = sym_cfg.get('mtf_filter', {}).get('enabled', True)
            if mtf_enabled and paradigm in MTF_GATED_PARADIGMS and daily_trend != 'BULL':
                funnel.mtf_blocked += 1
                continue

            # ── Layer 2: Supporting signals (2 of 3) ──
            try:
                support_score, _reasons = sulla_strategy.check_supporting_signals(
                    ind, paradigm, sym_cfg
                )
            except Exception:
                support_score = 0
            consensus_score = 1 + support_score  # primary paradigm = +1
            if consensus_score < consensus_min:
                funnel.consensus_fail += 1
                continue
            funnel.consensus_pass += 1

            # ── Layer 3: AI veto — pass-through (caveat in report) ──

            # ── Sizing ──
            risk_scalar = 1.0
            if state.risk_mode == 'DERISK':
                risk_scalar *= state.config.get('risk', {}).get('derisk_size_multiplier', 0.5)
            risk_scalar *= correlation_multiplier(sym, state.open_positions, state.config)

            shares, notional = calc_position_size_shares(
                equity=equity_now,
                atr=ind['atr'],
                price=ind['price'],
                risk_cfg=sym_cfg.get('risk', {}),
                ratchet_cfg=sym_cfg.get('ratchet', {}),
                risk_scalar=risk_scalar,
            )
            if shares < 1 or notional > state.cash:
                continue

            stop_mult = sym_cfg.get('ratchet', {}).get('initial_stop_mult', 2.0)
            entry_stop = ind['price'] - (ind['atr'] * stop_mult)

            pos = Position(
                symbol=sym, paradigm=paradigm,
                entry_ts=ts, entry_px=ind['price'], entry_atr=ind['atr'],
                shares=shares, stop=entry_stop, peak_px=ind['high'],
                notional=notional,
            )
            state.open_positions[sym] = pos
            state.cash -= shares * ind['price']
            funnel.entered += 1

            if len(state.open_positions) >= max_open:
                break

    # Final mark-to-market: liquidate any remaining positions at last close
    if timeline.size and state.open_positions:
        last_ts_et = timeline[-1].tz_convert(ET_TZ)
        for sym, pos in list(state.open_positions.items()):
            px = last_prices.get(sym, pos.entry_px)
            pnl_usd = (px - pos.entry_px) * pos.shares
            pnl_pct = ((px - pos.entry_px) / pos.entry_px) * 100
            state.closed_trades.append(Trade(
                symbol=sym, paradigm=pos.paradigm,
                entry_ts=pos.entry_ts, entry_px=pos.entry_px,
                exit_ts=last_ts_et, exit_px=px, shares=pos.shares,
                pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                exit_reason='BACKTEST_END', bars_held=0,
            ))
            state.funnels[pos.paradigm].add_exit('BACKTEST_END')
            state.cash += pos.shares * px
        state.open_positions.clear()

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def _profit_factor(trades: list[Trade]) -> float:
    gross_w = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_l = -sum(t.pnl_usd for t in trades if t.pnl_usd < 0)
    if gross_l <= 0:
        return float('inf') if gross_w > 0 else 0.0
    return gross_w / gross_l


def _max_drawdown(equity_curve: list[tuple[datetime, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _ts, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
    return max_dd


def render_report(state: EngineState, *, start: datetime, end: datetime,
                  symbols: list[str], param_overrides: list[str]) -> str:
    """Produces the Markdown report. Per-paradigm funnel is the centrepiece."""
    initial = state.initial_cap
    final   = state.equity_curve[-1][1] if state.equity_curve else initial
    total_return_pct = (final - initial) / initial * 100
    total_return_usd = final - initial
    pf       = _profit_factor(state.closed_trades)
    max_dd   = _max_drawdown(state.equity_curve)
    n_trades = len(state.closed_trades)
    wins     = sum(1 for t in state.closed_trades if t.is_win)
    win_rate = (wins / n_trades * 100) if n_trades else 0.0
    avg_r    = (sum(t.pnl_pct for t in state.closed_trades) / n_trades) if n_trades else 0.0

    lines = []
    lines.append(f"# Sulla Backtest Report — {start.date()} → {end.date()}")
    lines.append("")
    lines.append("## Caveats (read these first)")
    lines.append("")
    lines.append("- **AI veto pass-through.** Gemma 4 BEARISH aborts not modeled. Production fires will be lower than this report shows.")
    lines.append("- **No slippage/fees.** Paper account is fee-free; live will pay micro-slippage on stops.")
    lines.append("- **No earnings blackout.** yfinance is live-only — earnings-day entries appear here that wouldn't fire in production.")
    lines.append("- **Single-leg only.** Pyramiding flag default-off; harness assumes one leg per position.")
    lines.append("- **Stop fills assumed at trigger price.** Real fills can slip on gap-down opens.")
    lines.append("- **IEX feed only.** Matches paper account; SIP would have ~30x more volume signal but costs $$.")
    if param_overrides:
        lines.append("")
        lines.append(f"- **Param overrides applied:** `{', '.join(param_overrides)}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Initial capital | ${initial:,.2f} |")
    lines.append(f"| Final equity | ${final:,.2f} |")
    lines.append(f"| Total return | {total_return_pct:+.2f}% (${total_return_usd:+,.2f}) |")
    lines.append(f"| Max drawdown | {max_dd:.2f}% |")
    lines.append(f"| Profit factor | {pf:.2f}" + (" (no losses)" if pf == float('inf') else "") + " |")
    lines.append(f"| Trades | {n_trades} |")
    lines.append(f"| Win rate | {win_rate:.1f}% ({wins}/{n_trades}) |")
    lines.append(f"| Avg return per trade | {avg_r:+.2f}% |")
    lines.append(f"| Symbols | {len(symbols)} ({', '.join(symbols)}) |")
    lines.append("")

    # Per-paradigm funnel — THE methodology insight
    lines.append("## Per-Paradigm Gate Funnel")
    lines.append("")
    lines.append("Aggregate counters lie. This is what you actually need.")
    lines.append("")
    lines.append("| Paradigm | Fires | MTF Block | Consensus Pass | Consensus Fail | Entered | Pass Rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for p in PARADIGMS:
        f = state.funnels[p]
        pass_rate = (f.entered / f.fires * 100) if f.fires else 0.0
        lines.append(f"| {p} | {f.fires} | {f.mtf_blocked} | {f.consensus_pass} | {f.consensus_fail} | {f.entered} | {pass_rate:.1f}% |")
    lines.append("")

    # Per-paradigm trade quality
    lines.append("## Per-Paradigm Trade Quality")
    lines.append("")
    lines.append("| Paradigm | Trades | Wins | Win Rate | Avg R | Gross Win | Gross Loss | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p in PARADIGMS:
        ptrades = [t for t in state.closed_trades if t.paradigm == p]
        if not ptrades:
            lines.append(f"| {p} | 0 | — | — | — | — | — | — |")
            continue
        pw = sum(1 for t in ptrades if t.is_win)
        wr = pw / len(ptrades) * 100
        avg = sum(t.pnl_pct for t in ptrades) / len(ptrades)
        gw  = sum(t.pnl_usd for t in ptrades if t.pnl_usd > 0)
        gl  = -sum(t.pnl_usd for t in ptrades if t.pnl_usd < 0)
        pfr = (gw / gl) if gl > 0 else float('inf') if gw > 0 else 0.0
        pfs = f"{pfr:.2f}" if pfr != float('inf') else "∞"
        lines.append(f"| {p} | {len(ptrades)} | {pw} | {wr:.1f}% | {avg:+.2f}% | ${gw:,.2f} | ${gl:,.2f} | {pfs} |")
    lines.append("")

    # Per-paradigm exit reasons
    lines.append("## Per-Paradigm Exit Reasons")
    lines.append("")
    lines.append("| Paradigm | STOP_HIT | TP_BB_UPPER | TP_BB_MIDDLE | EOD | BACKTEST_END |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for p in PARADIGMS:
        e = state.funnels[p].exit_reasons
        lines.append(f"| {p} | {e.get('STOP_HIT', 0)} | {e.get('TP_BB_UPPER', 0)} | {e.get('TP_BB_MIDDLE', 0)} | {e.get('EOD', 0)} | {e.get('BACKTEST_END', 0)} |")
    lines.append("")

    # Risk events
    if state.halt_events:
        lines.append("## Risk Mode Transitions")
        lines.append("")
        lines.append("| Timestamp (ET) | Event |")
        lines.append("|---|---|")
        for ts_et, evt in state.halt_events:
            lines.append(f"| {ts_et.strftime('%Y-%m-%d %H:%M')} | {evt} |")
        lines.append("")

    # Per-symbol activity
    lines.append("## Per-Symbol Activity")
    lines.append("")
    lines.append("| Symbol | Trades | Win Rate | Net P&L |")
    lines.append("|---|---:|---:|---:|")
    syms_with_trades = sorted({t.symbol for t in state.closed_trades})
    for sym in syms_with_trades:
        st = [t for t in state.closed_trades if t.symbol == sym]
        sw = sum(1 for t in st if t.is_win)
        wr = sw / len(st) * 100
        np = sum(t.pnl_usd for t in st)
        lines.append(f"| {sym} | {len(st)} | {wr:.1f}% | ${np:+,.2f} |")
    lines.append("")

    # First/last 10 trades (for sanity check)
    lines.append("## Trade Log (first 10)")
    lines.append("")
    lines.append("| Date | Symbol | Paradigm | Entry | Exit | Shares | P&L $ | P&L % | Reason |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
    for t in state.closed_trades[:10]:
        lines.append(f"| {t.entry_ts.strftime('%Y-%m-%d %H:%M')} | {t.symbol} | {t.paradigm} | ${t.entry_px:.2f} | ${t.exit_px:.2f} | {t.shares} | ${t.pnl_usd:+,.2f} | {t.pnl_pct:+.2f}% | {t.exit_reason} |")
    lines.append("")

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI / orchestration
# ─────────────────────────────────────────────────────────────────────────────

def apply_param_overrides(config: dict, overrides: list[str]) -> dict:
    """Lets the operator tune from CLI without editing Config.yaml.
    e.g. --params strategy.paradigms.trend_following.rsi_entry=55"""
    cfg = copy.deepcopy(config)
    for kv in overrides:
        if '=' not in kv:
            continue
        path, raw = kv.split('=', 1)
        try:
            val = int(raw)
        except ValueError:
            try:
                val = float(raw)
            except ValueError:
                val = raw
        node = cfg
        keys = path.split('.')
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = val
    return cfg


def build_unified_timeline(panels: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Union of all symbol bar timestamps, sorted."""
    if not panels:
        return pd.DatetimeIndex([], tz='America/New_York')
    union = panels[next(iter(panels))].index
    for panel in panels.values():
        union = union.union(panel.index)
    return union.sort_values()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1] if __doc__ else "Sulla backtest")
    parser.add_argument('--start', type=str, default=None, help='YYYY-MM-DD (default: 12 months ago)')
    parser.add_argument('--end',   type=str, default=None, help='YYYY-MM-DD (default: today)')
    parser.add_argument('--symbols', nargs='+', default=None, help='Override symbols to test')
    parser.add_argument('--params', nargs='+', default=[], help='Param overrides like strategy.paradigms.trend_following.rsi_entry=55')
    parser.add_argument('--no-cache', action='store_true', help='Force re-fetch from Alpaca')
    parser.add_argument('--output', type=str, default=None, help='Markdown report path (default: scripts/backtest_results/<ts>.md)')
    args = parser.parse_args()

    # Load Sulla's live Config.yaml as baseline.
    config_path = CORE_DIR / 'Config.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    if args.params:
        config = apply_param_overrides(config, args.params)

    # Date window: default 12mo back from today, ending yesterday (avoid live in-progress bar).
    end_dt = (datetime.fromisoformat(args.end) if args.end
              else datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=timezone.utc)
    start_dt = (datetime.fromisoformat(args.start) if args.start
                else end_dt - timedelta(days=365)).replace(tzinfo=timezone.utc) if args.start else end_dt - timedelta(days=365)
    if args.start:
        start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)

    symbols = args.symbols or config.get('strategy', {}).get('active_symbols', [])
    print(f"[backtest] window: {start_dt.date()} → {end_dt.date()}", flush=True)
    print(f"[backtest] symbols: {len(symbols)} ({', '.join(symbols)})", flush=True)

    # ── Fetch ──
    panels       = {}
    daily_trends = {}
    t0 = time_module.time()
    for i, sym in enumerate(symbols, 1):
        print(f"[fetch] {i}/{len(symbols)} {sym} 30m + 1d", flush=True)
        df30 = fetch_bars(sym, '30m', start_dt, end_dt, use_cache=not args.no_cache)
        df1d = fetch_bars(sym, '1d',  start_dt, end_dt, use_cache=not args.no_cache)
        if df30.empty:
            print(f"  ! {sym}: no 30m bars, skipping", flush=True)
            continue
        panel30 = add_indicators(df30, config)
        panel1d = add_indicators(df1d, config)
        if panel30.empty:
            print(f"  ! {sym}: 30m panel empty after indicators, skipping", flush=True)
            continue
        panels[sym] = panel30
        daily_trends[sym] = precompute_daily_trend(panel1d)
    print(f"[fetch] done in {time_module.time() - t0:.1f}s — {len(panels)} symbols ready", flush=True)

    if not panels:
        print("[backtest] no symbols loaded; aborting", flush=True)
        return 1

    # ── Engine ──
    timeline = build_unified_timeline(panels)
    print(f"[engine] running over {len(timeline):,} timestamps × {len(panels)} symbols", flush=True)
    t0 = time_module.time()
    state = run_backtest(panels, daily_trends, timeline, config)
    print(f"[engine] done in {time_module.time() - t0:.1f}s — {len(state.closed_trades)} trades", flush=True)

    # ── Report ──
    report = render_report(state, start=start_dt, end=end_dt,
                           symbols=list(panels.keys()),
                           param_overrides=args.params)
    if args.output:
        out_path = Path(args.output)
    else:
        ts_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = RESULTS_DIR / f'report_{ts_tag}.md'
    out_path.write_text(report)
    print(f"[report] {out_path}", flush=True)

    # Trade log JSON for downstream analysis
    json_path = out_path.with_suffix('.trades.json')
    json_path.write_text(json.dumps([{
        'symbol': t.symbol, 'paradigm': t.paradigm,
        'entry_ts': t.entry_ts.isoformat(), 'entry_px': t.entry_px,
        'exit_ts': t.exit_ts.isoformat(), 'exit_px': t.exit_px,
        'shares': t.shares, 'pnl_usd': t.pnl_usd, 'pnl_pct': t.pnl_pct,
        'exit_reason': t.exit_reason, 'bars_held': t.bars_held,
    } for t in state.closed_trades], indent=2))
    print(f"[report] {json_path}", flush=True)

    # Brief stdout summary so the operator doesn't have to open the file
    print()
    print("─" * 70)
    final  = state.equity_curve[-1][1] if state.equity_curve else state.initial_cap
    ret    = (final - state.initial_cap) / state.initial_cap * 100
    max_dd = _max_drawdown(state.equity_curve)
    pf     = _profit_factor(state.closed_trades)
    print(f"  Return:        {ret:+.2f}%  (${final - state.initial_cap:+,.2f})")
    print(f"  Max drawdown:  {max_dd:.2f}%")
    print(f"  Profit factor: {pf:.2f}")
    print(f"  Trades:        {len(state.closed_trades)}")
    print("─" * 70)
    print()
    print("Per-paradigm funnel:")
    for p in PARADIGMS:
        f = state.funnels[p]
        rate = (f.entered / f.fires * 100) if f.fires else 0.0
        print(f"  {p:<22} fires={f.fires:<5} mtf={f.mtf_blocked:<4} cpass={f.consensus_pass:<4} entered={f.entered:<4} ({rate:.1f}%)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
