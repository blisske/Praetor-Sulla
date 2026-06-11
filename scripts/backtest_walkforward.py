"""
Ionic WALK-FORWARD backtest — rolling-window validation with go/no-go, on
Oanda H1 mid candles (count-capped at 5000 bars ≈ 10 months of FX trading
hours), through the REAL deployed strategy module.

Sibling of the Corinthian harness (which found NO-GO in both configs, with
VOLATILITY BREAKOUT the bleeder at PF 0.47-0.52 on ~75% of flow) and the
Pantheon one (which falsified its Tier-1 tune). Ionic question: do the FX
majors — slow, mean-reverting, long-only unleveraged — carry any edge in
this engine, and did the Tier-1 threshold port help or hurt?

Ionic-faithful structure:
  - Long-only spot, 7 majors from strategy.active_symbols, H1 bars.
  - P&L in % of price per flat $1k notional. (USD-base pairs' quote-ccy
    conversion divides by exit price — a ~constant factor that cancels in
    PF; fine for edge measurement.)
  - exit models: legacy (TP gate 2.0%, no HOLD_AND_TIGHTEN) vs live
    (config TP gate, H&T) — both with the trail.
  - threshold A/B via BT_CONFIG=/app/data/Config.yaml.bak-tier1.
  - costs: Oanda spread modeled as FEE_SIDE 0.01% (1bp) per side + 0.5bp
    slip — generous for EUR/USD (~0.6bp half-spread), about right for the
    crosses.
  - AI veto + daily MTF gate pass-through (daily_trend="BULL"), consistent
    across cells; live takes fewer trades. Macro-calendar blackouts NOT
    modeled (live skips a handful of NFP/FOMC hours).

Run from the host:
    docker exec -i -e BT_CACHE=/app/data/bt_hist.db \
        ionic-engine python3 -u - < scripts/backtest_walkforward.py
Knobs: BT_REFETCH, BT_SKIP_FETCH, BT_CONFIG, BT_WINDOWS, BT_REPORT
(default /app/data/walkforward_report.txt)
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys

import pandas as pd
import pandas_ta as ta
import yaml

sys.path.insert(0, "/app/core")
import strategy as strat                 # noqa: E402

CACHE_DB    = os.environ.get("BT_CACHE", "/app/data/bt_hist.db")
CONFIG_PATH = os.environ.get("BT_CONFIG", "/app/data/Config.yaml")
CAPITAL     = float(os.environ.get("BT_CAPITAL", "10000"))
NOTIONAL    = float(os.environ.get("BT_NOTIONAL", "1000"))
FEE_SIDE    = float(os.environ.get("BT_FEE_SIDE_PCT", "0.01")) / 100.0
SLIP_BPS    = float(os.environ.get("BT_SLIP_BPS", "0.5"))
N_WINDOWS   = int(os.environ.get("BT_WINDOWS", "6"))
N_BOOT      = 2000
MIN_SUPPORT_SCORE = 2
BAR_COUNT   = int(os.environ.get("BT_BARS", "5000"))   # Oanda hard max


def _symbols(cfg):
    return list(cfg.get("strategy", {}).get("active_symbols", []))


def _fetch_all(symbols) -> None:
    from market_data import get_client
    client = get_client()
    if client is None:
        print("[data] no Oanda client — check OANDA_API_TOKEN", flush=True)
        sys.exit(1)
    con = sqlite3.connect(CACHE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS bars(symbol TEXT, ts INT, open REAL, "
                "high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, ts))")
    for sym in symbols:
        n_have = con.execute("SELECT COUNT(*) FROM bars WHERE symbol=?", (sym,)).fetchone()[0]
        if n_have > 1000 and not os.environ.get("BT_REFETCH"):
            print(f"[data] {sym}: cached ({n_have} bars) — skip", flush=True)
            continue
        try:
            candles = client.get_candles(sym, granularity="H1", count=BAR_COUNT, price="M")
        except Exception as e:
            print(f"[data] {sym} err: {repr(e)[:90]}", flush=True)
            continue
        if not candles:
            print(f"[data] {sym}: empty", flush=True)
            continue
        rows = [(sym, int(pd.Timestamp(c["time"]).timestamp()),
                 c["open"], c["high"], c["low"], c["close"], float(c["volume"]))
                for c in candles]
        con.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
        con.commit()
        print(f"[data] {sym}: {len(rows)} bars", flush=True)
    con.close()


def ensure_data(symbols) -> None:
    if os.environ.get("BT_SKIP_FETCH"):
        print("[data] BT_SKIP_FETCH=1 — using cache as-is", flush=True)
        return
    _fetch_all(symbols)


def load_bars(con, sym: str) -> pd.DataFrame:
    rows = con.execute("SELECT ts,open,high,low,close,volume FROM bars "
                       "WHERE symbol=? ORDER BY ts ASC", (sym,)).fetchall()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ta.ema(df["close"], length=9)
    df["ema_slow"] = ta.ema(df["close"], length=21)
    df["adx"] = ta.adx(df["high"], df["low"], df["close"], length=14)["ADX_14"]
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    bb = ta.bbands(df["close"], length=20, std=2.0)
    df["bb_lower"], df["bb_mid"], df["bb_upper"] = bb.iloc[:, 0], bb.iloc[:, 1], bb.iloc[:, 2]
    df["avg_volume"] = df["volume"].rolling(20).mean()
    return df.dropna().reset_index(drop=True)


def indic(df, i, sym, adx_thr) -> dict:
    r, rp, r2 = df.iloc[i], df.iloc[i - 1], df.iloc[i - 2]
    return {
        "symbol": sym, "price": float(r["close"]),
        "low": float(r["low"]), "high": float(r["high"]),
        "rsi": float(r["rsi"]), "adx": float(r["adx"]), "atr": float(r["atr"]),
        "regime": "TRENDING" if r["adx"] > adx_thr else "RANGING",
        "trend": "BULL" if r["ema_fast"] > r["ema_slow"] else "BEAR",
        "bb_upper": float(r["bb_upper"]), "bb_lower": float(r["bb_lower"]),
        "bb_middle": float(r["bb_mid"]),
        "bb_upper_prev": float(rp["bb_upper"]), "bb_lower_prev": float(rp["bb_lower"]),
        "bb_middle_prev": float(rp["bb_mid"]),
        "volume": float(r["volume"]), "avg_volume": float(r["avg_volume"]),
        "rsi_prev2": float(r2["rsi"]), "adx_prev2": float(r2["adx"]),
        "daily_trend": "BULL",   # MTF pass-through — consistent across cells
        "timestamp": r["dt"],    # backtester hook — session gates use bar time
    }


def run_symbol(df: pd.DataFrame, sym: str, cfg: dict, exit_model: str) -> list[dict]:
    import config_manager as cm
    sym_cfg = cm.get_symbol_config(cfg, sym)
    adx_thr = sym_cfg.get("strategy", {}).get("adx_trend_threshold", 25)
    rc = sym_cfg.get("ratchet", {})
    ini = float(rc.get("initial_stop_mult", 2.0))
    trl = float(rc.get("trailing_stop_mult", 2.5))

    if exit_model == "legacy":
        ecfg = yaml.safe_load(yaml.safe_dump(cfg))
        ecfg.setdefault("strategy", {})["tp_min_yield_pct"] = 2.0
    else:
        ecfg = cfg

    trades, pos = [], None
    for i in range(2, len(df)):
        r = df.iloc[i]
        price = float(r["close"])

        if pos is None:
            ind = indic(df, i, sym, adx_thr)
            fired, paradigm = strat.check_entry_signals(ind, cfg)
            if not fired:
                continue
            score, _ = strat.check_supporting_signals(ind, paradigm, cfg)
            if score < MIN_SUPPORT_SCORE:
                continue
            atr = float(r["atr"])
            pos = {"entry": price, "atr": atr, "stop": price - atr * ini,
                   "paradigm": paradigm, "i0": i, "t0": r["dt"]}
            continue

        entry, atr = pos["entry"], pos["atr"]

        def book(exit_px, reason):
            trades.append({"symbol": sym, "paradigm": pos["paradigm"],
                           "entry": entry, "exit": exit_px,
                           "pct": (exit_px - entry) / entry * 100.0,
                           "bars": i - pos["i0"], "t0": pos["t0"],
                           "t_exit": r["dt"], "reason": reason})

        cand = price - atr * trl
        if cand > pos["stop"]:
            pos["stop"] = cand

        if float(r["low"]) <= pos["stop"]:
            book(min(pos["stop"], price), "stop")
            pos = None
            continue

        ind = indic(df, i, sym, adx_thr)
        ex = strat.check_exit_signals(ind, pos["paradigm"], pos["stop"],
                                      entry_price=entry, config=ecfg)
        action = ex.get("action")
        if action in ("TAKE_PROFIT", "PARTIAL_TAKE_PROFIT"):
            book(price, "tp")
            pos = None
            continue
        if exit_model == "live" and action == "HOLD_AND_TIGHTEN" and atr > 0:
            tight = max(entry, price - atr)
            if tight > pos["stop"] and tight < price:
                pos["stop"] = tight
    # mark a still-open position closed at the final bar so it books
    if pos is not None:
        r = df.iloc[-1]
        trades.append({"symbol": sym, "paradigm": pos["paradigm"],
                       "entry": pos["entry"], "exit": float(r["close"]),
                       "pct": (float(r["close"]) - pos["entry"]) / pos["entry"] * 100.0,
                       "bars": len(df) - 1 - pos["i0"], "t0": pos["t0"],
                       "t_exit": r["dt"], "reason": "end"})
    return trades


def net_usd(t, slip_bps):
    cost_pct = 2 * FEE_SIDE * 100.0 + 2 * slip_bps / 100.0
    return NOTIONAL * (t["pct"] - cost_pct) / 100.0


def pf(trades, slip):
    w = sum(n for n in (net_usd(t, slip) for t in trades) if n > 0)
    l = abs(sum(n for n in (net_usd(t, slip) for t in trades) if n <= 0))
    return (w / l) if l else (float("inf") if w else 0.0)


def summarize(trades, slip, capital, span_days):
    n = len(trades)
    if not n:
        return {"n": 0, "pf": 0.0, "net": 0.0, "win_rate": 0.0, "expectancy": 0.0,
                "maxdd": 0.0, "ann_ret_pct": 0.0}
    nets = [net_usd(t, slip) for t in trades]
    wins = [x for x in nets if x > 0]
    eq = peak = capital
    maxdd = 0.0
    for t in sorted(trades, key=lambda t: t["t_exit"]):
        eq += net_usd(t, slip)
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    net = eq - capital
    yrs = max(span_days / 365.25, 1e-9)
    return {"n": n, "win_rate": len(wins) / n * 100, "net": net, "pf": pf(trades, slip),
            "expectancy": net / n, "maxdd": maxdd,
            "ann_ret_pct": net / capital / yrs * 100}


def bootstrap_pf_ci(trades, slip, n_boot=N_BOOT, seed=42):
    if len(trades) < 5:
        return None
    rng = random.Random(seed)
    nets = [net_usd(t, slip) for t in trades]
    pfs = []
    for _ in range(n_boot):
        s = [nets[rng.randrange(len(nets))] for _ in range(len(nets))]
        w = sum(x for x in s if x > 0)
        l = abs(sum(x for x in s if x <= 0))
        pfs.append((w / l) if l else 10.0)
    pfs.sort()
    return (pfs[int(0.05 * n_boot)], pfs[int(0.50 * n_boot)], pfs[int(0.95 * n_boot)])


def window_split(trades, n_windows):
    if not trades:
        return []
    ts = sorted(trades, key=lambda t: t["t_exit"])
    t0, t1 = ts[0]["t_exit"], ts[-1]["t_exit"]
    span = (t1 - t0) or pd.Timedelta(seconds=1)
    out = [[] for _ in range(n_windows)]
    for t in ts:
        out[min(int((t["t_exit"] - t0) / span * n_windows), n_windows - 1)].append(t)
    return out


def main():
    cfg = yaml.safe_load(open(CONFIG_PATH)) or {}
    symbols = _symbols(cfg)
    ensure_data(symbols)
    con = sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True)

    books = {"legacy": [], "live": []}
    spans = []
    for sym in symbols:
        raw = load_bars(con, sym)
        if len(raw) < 100:
            print(f"  {sym}: too few bars ({len(raw)}) — skip", flush=True)
            continue
        df = add_indicators(raw)
        for model in books:
            books[model] += run_symbol(df, sym, cfg, model)
        spans.append((df["dt"].iloc[-1] - df["dt"].iloc[0]).days)
    con.close()
    span_days = max(spans) if spans else 1
    SLIP = SLIP_BPS

    L = []
    def p(s=""):
        L.append(s)
        print(s, flush=True)

    p("=" * 78)
    p("IONIC WALK-FORWARD — rolling windows · bootstrap CI · go/no-go")
    p(f"span ~{span_days}d · {N_WINDOWS} windows · ${NOTIONAL:,.0f}/signal flat · "
      f"{FEE_SIDE*10000:.0f}bp fee + {SLIP_BPS:.1f}bp slip per side · H1 mid")
    p(f"config: {CONFIG_PATH} (adx={cfg.get('strategy',{}).get('adx_trend_threshold')}, "
      f"tf_rsi={cfg.get('strategy',{}).get('paradigms',{}).get('trend_following',{}).get('rsi_entry')}, "
      f"tp_gate={cfg.get('strategy',{}).get('tp_min_yield_pct')})")
    p("=" * 78)

    p("\nA · EXIT-MODEL A/B — legacy (TP@2%, no H&T) vs live (config TP, H&T)")
    p(f"  {'model':<10}{'trades':>7}{'win%':>7}{'PF':>7}{'net $':>11}{'maxDD$':>10}{'ann%':>7}")
    for model in ("legacy", "live"):
        s = summarize(books[model], SLIP, CAPITAL, span_days)
        p(f"  {model:<10}{s['n']:>7}{s['win_rate']:>7.1f}{s['pf']:>7.2f}"
          f"{s['net']:>11,.0f}{s['maxdd']:>10,.0f}{s['ann_ret_pct']:>7.1f}")

    longs = books["live"]
    p(f"\nB · WALK-FORWARD — live exits, {N_WINDOWS} windows")
    p(f"  {'window':<10}{'trades':>7}{'win%':>7}{'PF':>7}{'net $':>11}{'expect$':>9}")
    wpfs = []
    for k, w in enumerate(window_split(longs, N_WINDOWS)):
        s = summarize(w, SLIP, CAPITAL, span_days / max(N_WINDOWS, 1))
        if s["n"]:
            wpfs.append(s["pf"])
            p(f"  W{k+1:<9}{s['n']:>7}{s['win_rate']:>7.1f}{s['pf']:>7.2f}"
              f"{s['net']:>11,.0f}{s['expectancy']:>9.2f}")
    ci = bootstrap_pf_ci(longs, SLIP)
    med = sorted(wpfs)[len(wpfs) // 2] if wpfs else 0.0
    frac = (sum(1 for x in wpfs if x > 1.0) / len(wpfs)) if wpfs else 0.0
    p(f"\n  window PF: median {med:.2f} · {frac*100:.0f}% of windows > 1.0")
    if ci:
        p(f"  bootstrap PF ({N_BOOT} draws): 5th {ci[0]:.2f} · median {ci[1]:.2f} · 95th {ci[2]:.2f}")
    if wpfs and ci:
        if med >= 1.05 and frac >= 0.60 and ci[0] > 0.90:
            verdict = "GO — edge holds across windows with CI support"
        elif med < 1.00 or ci[2] < 1.00:
            verdict = "NO-GO — edge does not survive out-of-window / CI excludes profitability"
        else:
            verdict = "INCONCLUSIVE — keep soaking; re-run as data grows"
    else:
        verdict = "INCONCLUSIVE — not enough trades"
    p(f"\n  VERDICT (live exits, this config): {verdict}")

    p("\nC · PER PARADIGM + EXIT REASON (live exits)")
    p(f"  {'key':<26}{'trades':>7}{'win%':>7}{'PF':>7}{'net $':>11}")
    by = {}
    for t in longs:
        by.setdefault("· " + t["paradigm"], []).append(t)
        by.setdefault("exit: " + t["reason"], []).append(t)
    for k in sorted(by):
        s = summarize(by[k], SLIP, CAPITAL, span_days)
        p(f"  {k:<26}{s['n']:>7}{s['win_rate']:>7.1f}{s['pf']:>7.2f}{s['net']:>11,.0f}")

    p("\nNOTES")
    p("  · AI veto + daily MTF gate pass-through (consistent across cells).")
    p("  · Macro-calendar blackouts NOT modeled (live skips a few event hours).")
    p("  · Threshold A/B: BT_CONFIG=/app/data/Config.yaml.bak-tier1 (pre-Tier-1).")
    p("=" * 78)

    out = os.environ.get("BT_REPORT", "/app/data/walkforward_report.txt")
    open(out, "w").write("\n".join(L) + "\n")
    print(f"[report] wrote {out}", flush=True)


if __name__ == "__main__":
    main()
