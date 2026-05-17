"""
Sulla — Market Data + Indicator Engine.

Fetches OHLCV from Oanda v20, computes the same indicator stack as Anton and
Tiberius (EMA / RSI / ADX / ATR / Bollinger), and returns a single dict with
everything strategy.py / ai_brain.py / tuner.py expect.

The return shape of `fetch_indicators()` is the canonical contract between
the broker adapter and the rest of the engine — keeping it identical to
Anton/Tiberius means strategy.py, the consensus layer, and the dashboard
indicator endpoints all work unchanged.

Phase 2 scope: bar fetch + indicator math only. No FX-specific math yet
(pip values, JPY pair quirks, unit sizing) — those land in Phase 3.
"""

import logging
import asyncio
from datetime import datetime, timezone

import pandas as pd
import pandas_ta as ta

from oanda_client import OandaClient, OandaError, OandaMissingCredentials

logger = logging.getLogger("sulla")


# ── Lazy client init ────────────────────────────────────────────────────────
# Constructed on first use so the engine can boot when creds aren't set yet
# (Phase 2 entry condition). Once OANDA_API_TOKEN / OANDA_ACCOUNT_ID are in
# the environment, the next fetch_indicators() call wires it up.
_client: OandaClient | None = None
_client_err: str | None = None


def get_client() -> OandaClient | None:
    """
    Returns a cached OandaClient instance, or None if credentials aren't set.
    Errors are logged once and cached so the engine doesn't spam log lines
    every cycle when running pre-credentials.
    """
    global _client, _client_err
    if _client is not None:
        return _client
    if _client_err is not None:
        return None  # Already failed to construct; don't keep retrying.
    try:
        _client = OandaClient.from_env()
        logger.info(f"Oanda client ready: {_client!r}")
        return _client
    except OandaMissingCredentials as e:
        _client_err = str(e)
        logger.warning(
            f"Oanda credentials missing — engine will idle until they're set. "
            f"({e})"
        )
        return None
    except OandaError as e:
        _client_err = str(e)
        logger.error(f"Oanda client init failed: {e}")
        return None


def reset_client() -> None:
    """Drop the cached client so the next get_client() retries — useful after
    the user updates the .env and triggers a restart, though normally a full
    container restart picks up the new env automatically."""
    global _client, _client_err
    _client = None
    _client_err = None


# ── Indicator fetch ─────────────────────────────────────────────────────────
def _fetch_indicators_sync(symbol: str, config: dict | None, timeframe: str, limit: int):
    """
    Synchronous worker. Pulls OHLCV from Oanda, computes indicators with
    pandas_ta, returns the canonical indicator dict.

    Args:
        symbol: "EUR/USD" style.
        config: full Config.yaml dict (for indicator periods + ADX threshold).
        timeframe: "1h", "30m", "1d", etc. (mapped to Oanda granularity).
        limit: number of completed bars to request (Oanda max 5000).

    Returns:
        dict with the indicator stack — same shape as Anton/Tiberius.
        Returns None on any failure so callers can `if not d: continue`.
    """
    client = get_client()
    if client is None:
        return None

    try:
        strategy_cfg = (config or {}).get("strategy", {})
        ratchet_cfg  = (config or {}).get("ratchet",  {})

        ema_fast_len  = strategy_cfg.get("ema_fast", 9)
        ema_slow_len  = strategy_cfg.get("ema_slow", 21)
        rsi_len       = strategy_cfg.get("rsi_period", 14)
        atr_len       = ratchet_cfg.get("atr_period",  14)
        adx_threshold = strategy_cfg.get("adx_trend_threshold", 25)

        # Fetch candles from Oanda. Mid prices for indicator math; bid/ask come
        # from the pricing endpoint when we need to size actual fills.
        candles = client.get_candles(symbol, granularity=timeframe, count=limit, price="M")
        if not candles:
            logger.warning(f"[{symbol}] empty candles from Oanda")
            return None

        # Build DataFrame indexed by UTC time.
        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df.set_index("time", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)

        # ── Indicators (identical math to Anton/Tiberius) ───────────────────
        df["ema_fast"]  = ta.ema(df["close"], length=ema_fast_len)
        df["ema_slow"]  = ta.ema(df["close"], length=ema_slow_len)
        adx_df          = ta.adx(df["high"], df["low"], df["close"], length=14)
        df["adx"]       = adx_df["ADX_14"]
        df["rsi"]       = ta.rsi(df["close"], length=rsi_len)
        df["atr"]       = ta.atr(df["high"], df["low"], df["close"], length=atr_len)
        bb_df           = ta.bbands(df["close"], length=20, std=2.0)
        df["bb_lower"]  = bb_df.iloc[:, 0]
        df["bb_middle"] = bb_df.iloc[:, 1]
        df["bb_upper"]  = bb_df.iloc[:, 2]

        df.dropna(inplace=True)
        if df.empty:
            return None

        # Staleness guard — FX trades 24/5 so we only care about weekends and
        # network outages. Threshold of 4× the bar interval is generous enough
        # to avoid false alarms but catches "Oanda is broken" cases.
        bar_minutes_map = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D": 1440}
        from oanda_client import _GRANULARITY_MAP  # internal mapping
        gran = _GRANULARITY_MAP.get(timeframe, timeframe)
        bar_minutes = bar_minutes_map.get(gran, 60)
        latest_bar = df.index[-1]
        age_min = (datetime.now(timezone.utc) - latest_bar.to_pydatetime()).total_seconds() / 60
        # Weekend: Friday 17:00 ET → Sunday 17:00 ET = 48h closed. Anything
        # under 75 hours stale during a normal week is fine.
        # During the active session (Mon-Fri), expect age <= 4 × bar interval.
        weekday = datetime.now(timezone.utc).weekday()  # 0 = Mon, 6 = Sun
        weekend = weekday >= 5  # Saturday or Sunday
        max_age = 4 * bar_minutes if not weekend else 75 * 60
        if age_min > max_age:
            logger.warning(
                f"[{symbol}] STALE DATA ({timeframe}): latest bar is "
                f"{age_min:.0f} min old (limit {max_age} min). Skipping."
            )
            return None

        current = df.iloc[-1]
        prev2   = df.iloc[-3] if len(df) >= 3 else df.iloc[-2]
        closed  = df.iloc[-2]

        prior_bb_lower  = float(closed["bb_lower"])
        prior_bb_middle = float(closed["bb_middle"])
        prior_bb_upper  = float(closed["bb_upper"])
        high_closed     = float(closed["high"])

        volume     = float(closed["volume"])
        avg_volume = (
            float(df.iloc[-21:-1]["volume"].mean()) if len(df) >= 21
            else float(df["volume"].mean())
        )
        rsi_prev2 = float(prev2["rsi"])
        adx_prev2 = float(prev2["adx"])

        return {
            "price":      float(current["close"]),
            "high":       float(current["high"]),
            "low":        float(current["low"]),
            "ema_fast":   float(current["ema_fast"]),
            "ema_slow":   float(current["ema_slow"]),
            "rsi":        float(current["rsi"]),
            "atr":        float(current["atr"]),
            "adx":        float(current["adx"]),
            "bb_lower":   float(current["bb_lower"]),
            "bb_middle":  float(current["bb_middle"]),
            "bb_upper":   float(current["bb_upper"]),
            "regime":     "TRENDING" if current["adx"] > adx_threshold else "RANGING",
            "trend":      "BULL" if current["ema_fast"] > current["ema_slow"] else "BEAR",
            # Consensus-layer values from the most recent closed bar
            "volume":     volume,
            "avg_volume": avg_volume,
            "rsi_prev2":  rsi_prev2,
            "adx_prev2":  adx_prev2,
            # Prior-bar BB for VB squeeze pre-breakout
            "bb_lower_prev":  prior_bb_lower,
            "bb_middle_prev": prior_bb_middle,
            "bb_upper_prev":  prior_bb_upper,
            # Prior-bar high for ratchet calc (avoids in-progress wick spikes)
            "high_closed":    high_closed,
        }

    except OandaError as e:
        logger.error(f"[{symbol}] Oanda fetch error: {e}")
        return None
    except Exception as e:
        logger.error(f"[{symbol}] indicator computation error: {e}", exc_info=True)
        return None


async def fetch_indicators(symbol: str, config: dict | None = None,
                           timeframe: str = "1h", limit: int = 150):
    """
    Async wrapper. Runs the sync fetch on a thread so the engine's main loop
    stays cooperative (lots of small fetches run in parallel via asyncio.gather
    in the cycle loop).
    """
    return await asyncio.to_thread(_fetch_indicators_sync, symbol, config, timeframe, limit)
