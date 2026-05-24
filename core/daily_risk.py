"""Daily-drawdown circuit breaker.

Independent of the existing peak-drawdown system. Catches the case where
a user has a single bad day that doesn't exceed the all-time peak
drawdown threshold (e.g., dropped 15% from yesterday but only 5% from
all-time peak — still terrible, but the peak-DD halt wouldn't fire).

Three tiers (all env-tunable via Config.yaml under `risk.`):

  daily_alert_pct   (default 5.0)   → log + email; no behavior change
  daily_derisk_pct  (default 10.0)  → halve risk_per_trade for the day
  daily_halt_pct    (default 20.0)  → hard halt; require manual /resume

Baseline "starting equity for today" is captured at the FIRST evaluation
after midnight (operator's TZ). Persisted via database.set_daily_baseline
so an engine restart mid-day doesn't lose the baseline (which would let
the user trade through a halt that should still be active).

Usage from the trading loop (see core/main.py):

    from core import daily_risk

    state = daily_risk.evaluate(
        current_equity = shadow_equity_or_live_equity,
        config         = config,
    )
    if state.action == 'halt':
        trading_halted = True
        # notify, emit event, continue (skip this cycle's trade entries)
    elif state.action == 'derisk':
        derisk_active = True
    # state.alert  → fire one-shot notification (caller dedupes)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Result shape ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DailyRiskState:
    """Outcome of a single daily-risk evaluation."""

    # Today's tracking
    baseline_equity:    Optional[float]   # equity at start of today (None on first-ever call)
    current_equity:     float
    drawdown_pct:       float             # 0.0 if equity has gone UP today

    # Tier thresholds active in the current config
    alert_pct:          float
    derisk_pct:         float
    halt_pct:           float

    # Action to take. 'none' | 'alert' | 'derisk' | 'halt'
    # Caller uses this to drive trading_halted / derisk_active in main.
    action:             str

    # True if today's baseline was just freshly snapshotted (date rollover
    # or first-ever call). Caller can use this to suppress alerts on the
    # first cycle of a new day (no drawdown vs. itself).
    baseline_was_fresh: bool


# ─── Date helpers ──────────────────────────────────────────────────────────


def _today_str(tz_name: str = "America/Denver") -> str:
    """Today's date in the operator's TZ, formatted YYYY-MM-DD.

    Falls back to UTC if pytz unavailable / TZ name unknown — the daily
    reset still works, it just rolls over at UTC midnight instead of
    operator-local.
    """
    try:
        import pytz
        tz = pytz.timezone(tz_name)
        return datetime.now(tz).strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


# ─── Core evaluator ────────────────────────────────────────────────────────


def evaluate(
    *,
    current_equity:  float,
    config:          dict,
    db = None,
    today_str_fn = _today_str,
) -> DailyRiskState:
    """Evaluate today's drawdown vs the daily baseline.

    Args:
        current_equity: User's current portfolio equity (shadow or live).
        config: Loaded Config.yaml dict — we read `risk.daily_*` keys.
        db: Optional override of the `core.database` module (for tests).
        today_str_fn: Optional override of the date function (for tests).

    Returns a DailyRiskState describing what action (if any) the caller
    should take. The evaluator NEVER mutates trading state directly —
    that's the caller's responsibility so we don't dual-write the
    risk flags from two places.

    Side effect (intentional): if today's date doesn't match the stored
    baseline date, we snapshot current_equity as the new baseline + write
    it to DB. This is the daily reset.
    """
    risk_cfg   = config.get("risk", {})
    tz_name    = risk_cfg.get("daily_baseline_tz", os.environ.get("TZ", "America/Denver"))
    alert_pct  = float(risk_cfg.get("daily_alert_pct",   5.0))
    derisk_pct = float(risk_cfg.get("daily_derisk_pct", 10.0))
    halt_pct   = float(risk_cfg.get("daily_halt_pct",   20.0))

    if db is None:
        # Dual import path — per-user engines have /app/core on sys.path
        # (not /app), so `from core import …` fails. Try absolute first.
        try:
            from core import database as db
        except ImportError:
            import database as db

    today = today_str_fn(tz_name)
    baseline_eq, baseline_date = db.get_daily_baseline()

    # ── Daily reset ─────────────────────────────────────────────────────
    # First call ever, or date has rolled over → snapshot today's start.
    baseline_was_fresh = False
    if baseline_eq is None or baseline_date != today:
        logger.info(
            f"Daily baseline reset: {baseline_date} → {today} | "
            f"new baseline = ${current_equity:.2f}"
        )
        db.set_daily_baseline(current_equity, today)
        baseline_eq = current_equity
        baseline_was_fresh = True

    # ── Compute drawdown ─────────────────────────────────────────────────
    if baseline_eq <= 0:
        # Defensive: avoid division by zero on a zero/negative baseline
        dd_pct = 0.0
    else:
        dd_pct = max(0.0, (baseline_eq - current_equity) / baseline_eq * 100.0)

    # ── Tier evaluation ──────────────────────────────────────────────────
    # Most severe wins. Suppress alerts on a fresh baseline (no real
    # drawdown to speak of — we just set baseline = current).
    if baseline_was_fresh:
        action = "none"
    elif dd_pct >= halt_pct:
        action = "halt"
    elif dd_pct >= derisk_pct:
        action = "derisk"
    elif dd_pct >= alert_pct:
        action = "alert"
    else:
        action = "none"

    return DailyRiskState(
        baseline_equity    = baseline_eq,
        current_equity     = current_equity,
        drawdown_pct       = dd_pct,
        alert_pct          = alert_pct,
        derisk_pct         = derisk_pct,
        halt_pct           = halt_pct,
        action             = action,
        baseline_was_fresh = baseline_was_fresh,
    )
