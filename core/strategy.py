import datetime
import pytz

# ==============================================================================
# SULLA V1 - TRADFI STRATEGY ENGINE
# Evaluates US Equity technical indicators and includes TradFi-specific
# protections like the Morning Settle gap trap defense and the 3:30 PM cutoff.
# ==============================================================================

def check_entry_signals(indicators, config=None):
    """
    Evaluates the current market data to see if any of our paradigms
    are flashing a valid buy setup.
    """
    import config_manager

    # 1. Identify the target asset (defaulting to SPY if not provided)
    symbol = indicators.get('symbol', 'SPY')

    # 2. Dynamically overwrite global settings with the ticker's sniper settings
    if config is not None:
        config = config_manager.get_symbol_config(config, symbol)

    ny_tz = pytz.timezone('America/New_York')

    # Check if the backtester fed us a historical timestamp, otherwise use live time
    if 'timestamp' in indicators:
        now_ny = indicators['timestamp']
        # Ensure the backtester timestamp is tz-aware
        if now_ny.tzinfo is None:
            now_ny = ny_tz.localize(now_ny)
    else:
        now_ny = datetime.datetime.now(ny_tz)

    # -------------------------------------------------------------------------
    # SESSION HOUR GUARD
    # FIX: Original code only blocked 9:30–9:59 AM and allowed everything else,
    # including after-hours. This now enforces the full valid window:
    # entries are only permitted between 9:30 AM and 3:30 PM ET.
    # The 3:30 PM cutoff gives 30 minutes of runway before close to avoid
    # orphaned intraday positions that would count as PDT violations.
    # -------------------------------------------------------------------------
    market_open = now_ny.replace(hour=9,  minute=30, second=0, microsecond=0)
    cutoff      = now_ny.replace(hour=15, minute=30, second=0, microsecond=0)

    if now_ny < market_open or now_ny >= cutoff:
        return False, "NONE"

    # -------------------------------------------------------------------------
    # PARADIGM THRESHOLDS
    # -------------------------------------------------------------------------
    paradigms = (config or {}).get('strategy', {}).get('paradigms', {})

    tf_cfg = paradigms.get('trend_following', {})
    mr_cfg = paradigms.get('mean_reversion', {})
    vb_cfg = paradigms.get('volatility_breakout', {})
    ls_cfg = paradigms.get('liquidity_sweep', {})

    regime = indicators.get('regime', 'RANGING')
    price  = indicators['price']

    # -------------------------------------------------------------------------
    # MULTI-TIMEFRAME REGIME GATE (Phase 4)
    # TREND FOLLOWING and VOLATILITY BREAKOUT additionally require the daily
    # trend to be BULL. Mean Reversion and Liquidity Sweep are deliberately
    # exempt — they're counter-trend paradigms (the whole point is to fade
    # an exhausted move). Defaults to BULL when missing → fail open on data
    # error, so a connectivity blip doesn't block all entries.
    # -------------------------------------------------------------------------
    daily_trend = indicators.get('daily_trend', 'BULL')
    mtf_enabled = (config or {}).get('mtf_filter', {}).get('enabled', True)
    daily_bull  = (daily_trend == 'BULL')

    # -------------------------------------------------------------------------
    # PARADIGM C: VOLATILITY BREAKOUT (The Squeeze)
    # Squeeze must be measured on the PRIOR (closed) bar — once price breaks
    # out, the current bar's bands have already widened. Falls back to current
    # bar if prior values aren't present (older market_data payloads).
    # -------------------------------------------------------------------------
    bb_upper_prev  = indicators.get('bb_upper_prev',  indicators['bb_upper'])
    bb_lower_prev  = indicators.get('bb_lower_prev',  indicators['bb_lower'])
    bb_middle_prev = indicators.get('bb_middle_prev', indicators['bb_middle'])

    bbw_prev = ((bb_upper_prev - bb_lower_prev) / bb_middle_prev) if bb_middle_prev else 0.0
    bbw_threshold = vb_cfg.get('bbw_threshold', 0.10)
    is_squeezed = bbw_prev < bbw_threshold

    # Breakout itself is judged on the current bar's price vs. PRIOR upper band
    # — the level price had to clear is the pre-expansion ceiling.
    is_breaking_out = price > bb_upper_prev

    vb_rsi = vb_cfg.get('rsi_entry', 55)
    has_strong_momentum = indicators['rsi'] > vb_rsi

    if is_squeezed and is_breaking_out and has_strong_momentum:
        if mtf_enabled and not daily_bull:
            pass  # MTF block — fall through, may still match other paradigms
        else:
            return True, "VOLATILITY BREAKOUT"

    # -------------------------------------------------------------------------
    # PARADIGM A: TREND FOLLOWING (The Momentum Catcher)
    # -------------------------------------------------------------------------
    if regime == "TRENDING":
        is_bullish_trend = indicators['trend'] == "BULL"

        tf_rsi = tf_cfg.get('rsi_entry', 45)
        is_oversold_dip = indicators['rsi'] < tf_rsi

        if is_bullish_trend and is_oversold_dip:
            if mtf_enabled and not daily_bull:
                pass  # MTF block — fall through
            else:
                return True, "TREND FOLLOWING"

    # -------------------------------------------------------------------------
    # PARADIGM B & D: RANGE BOUND STRATEGIES
    # -------------------------------------------------------------------------
    elif regime == "RANGING":
        # --- PARADIGM B: MEAN REVERSION ---
        is_at_floor = price <= indicators['bb_lower']

        mr_rsi = mr_cfg.get('rsi_entry', 35)
        is_extreme_exhaustion = indicators['rsi'] < mr_rsi

        if is_at_floor and is_extreme_exhaustion:
            return True, "MEAN REVERSION"

        # --- PARADIGM D: LIQUIDITY SWEEP ---
        # ADX ceiling: optional tighter range filter (Tiber tuning import).
        # Without ls.adx_ceiling set, LS fires on any RANGING bar (ADX <
        # adx_trend_threshold). The 12-month backtest showed 607 fires/yr
        # — Tiber's diagnosis was that this admits weak-trend chop, not
        # genuine ranges. Setting adx_ceiling = 18 (Tiber's pick) only fires
        # LS when ADX is decisively low. Default None preserves prior behavior.
        ls_adx_ceiling = ls_cfg.get('adx_ceiling')
        if ls_adx_ceiling is not None and indicators.get('adx', 0) >= ls_adx_ceiling:
            pass  # ADX too high for genuine range — skip LS
        else:
            candle_low = indicators.get('low', price)

            pierced_floor = candle_low < indicators['bb_lower']
            closed_inside = price > indicators['bb_lower']

            ls_rsi = ls_cfg.get('rsi_entry', 40)
            is_exhausted = indicators['rsi'] < ls_rsi

            if pierced_floor and closed_inside and is_exhausted:
                return True, "LIQUIDITY SWEEP"

    return False, "NONE"


def check_supporting_signals(indicators, strategy_type, config=None):
    """
    Layer 2 of the 2+1+1 consensus model. Evaluates three independent
    confirmations that are derivable from single-pass data already fetched.

    Returns:
        tuple: (int: score 0-3, list: reason strings for logging)
    """
    score   = 0
    reasons = []

    # --- SIGNAL 1: Volume Participation ---
    # Uses last CLOSED candle volume vs 20-candle average to avoid live-candle
    # near-zero readings at the top of each bar period.
    volume     = indicators.get('volume', 0)
    avg_volume = indicators.get('avg_volume', 1)
    vol_ratio  = (volume / avg_volume) if avg_volume > 0 else 0
    vol_threshold = 0.8

    if vol_ratio >= vol_threshold:
        score += 1
        reasons.append(f"VOL OK ({vol_ratio:.1f}x avg)")
    else:
        reasons.append(f"VOL WEAK ({vol_ratio:.1f}x avg, need {vol_threshold}x)")

    # --- SIGNAL 2: RSI Momentum Direction ---
    # RSI must be rising over the last 2 candles — confirms selling exhaustion.
    rsi       = indicators.get('rsi', 50)
    rsi_prev2 = indicators.get('rsi_prev2', rsi)

    if rsi > rsi_prev2:
        score += 1
        reasons.append(f"RSI RISING ({rsi_prev2:.1f}→{rsi:.1f})")
    else:
        reasons.append(f"RSI FALLING ({rsi_prev2:.1f}→{rsi:.1f})")

    # --- SIGNAL 3: ADX Regime Conviction ---
    # TRENDING: ADX must be above threshold AND rising (strengthening trend).
    # RANGING:  ADX must be below 20 AND falling (consolidating range).
    adx       = indicators.get('adx', 0)
    adx_prev2 = indicators.get('adx_prev2', adx)
    regime    = indicators.get('regime', 'RANGING')

    if regime == "TRENDING":
        adx_min = (config or {}).get('strategy', {}).get('adx_trend_threshold', 25)
        if adx > adx_min and adx > adx_prev2:
            score += 1
            reasons.append(f"ADX STRONG+RISING ({adx:.1f})")
        else:
            reasons.append(f"ADX WEAK ({adx:.1f}, need >{adx_min} and rising)")
    else:
        adx_range_max = (config or {}).get('strategy', {}).get('adx_range_threshold', 20)
        if adx < adx_range_max and adx < adx_prev2:
            score += 1
            reasons.append(f"ADX LOW+FALLING ({adx:.1f})")
        else:
            reasons.append(f"ADX NOT RANGING ({adx:.1f}, need <{adx_range_max} and falling)")

    return score, reasons


def check_exit_signals(indicators, strategy_type, current_stop_price, entry_price=None, config=None):
    """
    Evaluates open positions to determine if it is time to take profit or
    ratchet up our trailing stop-loss.
    """
    import config_manager

    # Identify the target asset and merge config, future-proofing for ATR stop logic
    symbol = indicators.get('symbol', 'SPY')
    if config is not None:
        config = config_manager.get_symbol_config(config, symbol)

    price = indicators['price']

    # -------------------------------------------------------------------------
    # EXIT A: MEAN REVERSION & LIQUIDITY SWEEPS (Take Profit at the Mean)
    # MANUAL OVERRIDE shares this exit floor — without it, manual buys would
    # never auto-take-profit and could only exit via stop or kill switch.
    # -------------------------------------------------------------------------
    if strategy_type in ["MEAN REVERSION", "LIQUIDITY SWEEP", "MANUAL OVERRIDE"]:
        cost_basis = entry_price if entry_price else indicators['bb_lower']
        real_yield = ((price - cost_basis) / cost_basis) * 100

        if price >= indicators['bb_middle'] and real_yield > 2.0:
            return {
                'action': 'TAKE_PROFIT',
                'reason': f'Mid BB target hit. Real yield: {real_yield:.1f}%'
            }
        elif price >= indicators['bb_upper']:
            return {
                'action': 'TAKE_PROFIT',
                'reason': f'Upper BB resistance hit. Real yield: {real_yield:.1f}%'
            }
        elif price >= indicators['bb_middle'] and real_yield <= 0:
            return {
                'action': 'HOLD',
                'reason': f'Mid BB reached but position underwater ({real_yield:.1f}%). Holding.'
            }

    # -------------------------------------------------------------------------
    # EXIT B: TREND FOLLOWING & BREAKOUTS (The Ratchet)
    # MANUAL OVERRIDE also gets ratchet treatment so manual buys can ride a
    # trend up with a trailing stop.
    # -------------------------------------------------------------------------
    if strategy_type in ["TREND FOLLOWING", "VOLATILITY BREAKOUT", "MANUAL OVERRIDE"]:
        if indicators['regime'] == "RANGING":
            return {
                'action': 'HOLD_AND_TIGHTEN',
                'reason': 'Momentum slowing, regime shifted to RANGING'
            }

    return {
        'action': 'HOLD',
        'reason': 'No exit conditions met'
    }
