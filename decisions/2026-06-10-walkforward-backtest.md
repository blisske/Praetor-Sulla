# 2026-06-10 — Walk-forward backtest: NO-GO both configs; worst in fleet

**Instrument:** `scripts/backtest_walkforward.py` — sibling of the Corinthian /
Doric / Pantheon harnesses. ~10 months of Oanda H1 mid candles (7 majors ×
4,999 bars — Oanda's 5,000-bar count cap), real `core/strategy.py` logic,
$1k-flat per signal, 1bp fee + 0.5bp slip per side, 6 rolling windows,
2000-draw bootstrap PF CI. Long-only spot, as deployed.

Go/no-go rule (fleet-wide): GO = median window PF ≥ 1.05 AND ≥ 60% of windows
> 1.0 AND bootstrap 5th-pct > 0.90. NO-GO = median < 1.0 OR 95th < 1.0.

## Results — 2×2 (config × exit model)

| Cell | Trades | Win% | PF | Net ($1k flat) | Verdict |
|---|---|---|---|---|---|
| New cfg (adx 22 / tf 55 / tp 0.1) · live exits | 752 | 25.9 | 0.47 | −$579 | **NO-GO** |
| New cfg · legacy exits | 618 | 29.1 | 0.59 | −$469 | — |
| Old cfg (adx 30 / tf 45 / tp 2.0) · live exits | 752 | 25.1 | 0.48 | −$529 | **NO-GO** |
| Old cfg · legacy exits | 599 | 32.2 | 0.64 | −$380 | — |

Walk-forward (new cfg, live exits): **0 of 6 windows above PF 1.0** (range
0.34–0.73, median 0.44). Bootstrap PF: 5th 0.38 · median 0.47 · **95th 0.57**.
Old config the same (median 0.53, 95th 0.58). This is the most decisive NO-GO
in the fleet — Corinthian and Doric at least had breakeven paradigms; Ionic
has none.

## Per-paradigm — nothing is above water

| Paradigm (new cfg, live exits) | Trades | PF | Net |
|---|---|---|---|
| VOLATILITY BREAKOUT | 489 (65%) | 0.49 | −$363 |
| TREND FOLLOWING | 168 | 0.38 | −$149 |
| LIQUIDITY SWEEP | 79 | 0.57 | −$53 |
| MEAN REVERSION | 16 | 0.45 | −$14 |

By exit reason: 707 of 752 exits are ATR stops at 21% win / PF 0.40. The
structural read: **long-only unleveraged FX majors on H1 with ATR-trail exits
is chop-death** — majors mean-revert on this horizon, the trail gives back
every excursion, and there is no EOD flatten (Doric) or multi-day crypto trend
(Corinthian) to harvest. The engine's long-only constraint also throws away
half the signal space in a symmetric asset class.

## Action taken + the open question

`strategy.paradigms.volatility_breakout.enabled: false` in the live
`data/Config.yaml` (hot-reloaded; same operator-approved remedy as
Corinthian/Doric — VB was the largest bleeder by volume). Shadow-only,
one-line revert.

⚠️ **But benching VB does not fix Ionic** — the remaining paradigms tested PF
0.38–0.57. Operator decision needed on the bot's direction. Options:

- **A. Keep soaking as-is** (VB benched). Zero capital at risk in shadow mode;
  ghost ledger + tuner keep accumulating evidence. Cheapest option, but the
  backtest says the soak will keep bleeding slowly.
- **B. Structural rework** — e.g. longer bars (H4/D1) to escape the chop the
  H1 trail keeps donating to, and/or wider stops with smaller size. Re-run
  this harness (`BT_BARS`, threshold knobs) before deploying anything.
- **C. Park the engine** (entries off) until a structure passes the harness.

## Caveats

- AI veto + daily MTF gate pass-through (consistent across cells); macro
  blackouts not modeled (live skips a handful of NFP/FOMC hours). Neither can
  plausibly flip PF 0.47 over 750 trades.
- Oanda's count cap limits the test to ~10 months; quarterly re-runs will
  roll the window forward.
- P&L measured in % of price per flat notional; USD-base pairs' quote-ccy
  conversion is a near-constant factor that cancels in PF.
