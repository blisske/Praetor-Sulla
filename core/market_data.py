import pandas as pd
import pandas_ta as ta
import asyncio
import pytz
from datetime import datetime, timedelta, timezone, time
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
import config_manager

# ==============================================================================
# SULLA V1 - MARKET DATA & INDICATOR ENGINE
# Replaces Binance CCXT with Alpaca Institutional US Equity feeds.
# ==============================================================================

# Use the centralized Alpaca Data Client from config_manager
data_client = config_manager.get_data_client()

def _fetch_indicators_sync(symbol, config, timeframe_str, limit):
    """
    Synchronous worker function to fetch Alpaca data and crunch pandas_ta math.
    Isolated from the main loop to prevent API blocking.
    """
    try:
        strategy_cfg = (config or {}).get('strategy', {})
        ratchet_cfg = (config or {}).get('ratchet', {})
        
        ema_fast_len = strategy_cfg.get('ema_fast', 9)
        ema_slow_len = strategy_cfg.get('ema_slow', 21)
        rsi_len = strategy_cfg.get('rsi_period', 14)
        atr_len = ratchet_cfg.get('atr_period', 14)
        adx_threshold = strategy_cfg.get('adx_trend_threshold', 25)

        # Map the Config.yaml timeframe string to Alpaca's strict TimeFrame object
        if timeframe_str == '1h':
            tf = TimeFrame.Hour
        elif timeframe_str == '30m':
            tf = TimeFrame(30, TimeFrameUnit.Minute)
        elif timeframe_str == '15m':
            tf = TimeFrame(15, TimeFrameUnit.Minute)
        elif timeframe_str == '1d':
            tf = TimeFrame.Day
        else:
            tf = TimeFrame.Hour  # Fallback safety

        # Lookback: 30-min bars = 13 candles/day. Need more calendar days than 1H.
        if tf == TimeFrame.Day:
            days_back = int(limit * 1.5)
        elif timeframe_str in ('30m', '15m'):
            days_back = int((limit / 13 * 1.5) + 5)
        else:
            days_back = int((limit / 6.5 * 1.5) + 5)
        days_back = max(days_back, 30)
        end_time   = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days_back)

        # 2. Request Data from Wall Street
        request_params = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start_time,
            end=end_time,
            feed=DataFeed.IEX
        )

        bars = data_client.get_stock_bars(request_params)
        
        if bars.df.empty:
            return None

        # 3. Format the DataFrame
        # Alpaca returns a MultiIndex DataFrame (symbol, timestamp). We must flatten it.
        df = bars.df.reset_index()
        df = df.rename(columns={'timestamp': 'time'})
        df.set_index('time', inplace=True)
        
        # TradFi runs on NY time. Convert UTC timestamps to Eastern Time.
        df.index = df.index.tz_convert('America/New_York')

        # -------------------------------------------------------------------------
        # MATHEMATICAL INDICATORS (The Exact Same Math as Tiberius)
        # -------------------------------------------------------------------------
        df['ema_fast'] = ta.ema(df['close'], length=ema_fast_len)
        df['ema_slow'] = ta.ema(df['close'], length=ema_slow_len)
        
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx_df['ADX_14']
        
        df['rsi'] = ta.rsi(df['close'], length=rsi_len)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=atr_len)
        
        bb_df = ta.bbands(df['close'], length=20, std=2.0)
        df['bb_lower'] = bb_df.iloc[:, 0]
        df['bb_middle'] = bb_df.iloc[:, 1]
        df['bb_upper'] = bb_df.iloc[:, 2]
        
        # Drop NaN rows caused by the 21-candle indicator lookback periods
        df.dropna(inplace=True)
        
        if df.empty:
            return None

        # Staleness guard: reject data if the latest bar is too old.
        # Threshold scales with timeframe — a daily bar during session is naturally
        # several hours old (its timestamp is start-of-day), and would always trip
        # a 45-min limit calibrated for 30m bars. Outside session, skip the check
        # entirely (pre-market / after-hours is expected to lag).
        latest_bar_time = df.index[-1]
        ny_tz  = pytz.timezone('America/New_York')
        now_et = datetime.now(ny_tz)
        today  = now_et.date()
        market_open_et  = ny_tz.localize(datetime.combine(today, time(9, 30)))
        market_close_et = ny_tz.localize(datetime.combine(today, time(16, 0)))
        during_session  = market_open_et <= now_et <= market_close_et
        if during_session:
            if tf == TimeFrame.Day:
                max_stale_minutes = 24 * 60          # daily bar can be ~24h, fine
            elif timeframe_str == '30m':
                max_stale_minutes = 45
            elif timeframe_str == '15m':
                max_stale_minutes = 30
            else:
                max_stale_minutes = 90               # 1h or other
            bar_age_minutes = (now_et - latest_bar_time).total_seconds() / 60
            if bar_age_minutes > max_stale_minutes:
                import logging as _log
                _log.getLogger("sulla").warning(
                    f"[{symbol}] STALE DATA ({timeframe_str}): latest bar is "
                    f"{bar_age_minutes:.0f} min old (limit {max_stale_minutes} min). Skipping."
                )
                return None

        current = df.iloc[-1]

        # Consensus layer indicators — use CLOSED candles only (iloc[-2] and earlier)
        # to avoid the live in-progress candle which always starts with near-zero volume.
        prev2   = df.iloc[-3] if len(df) >= 3 else df.iloc[-2]  # 2 candles ago
        closed  = df.iloc[-2]                                     # last completed candle

        # Prior-bar Bollinger values for the Volatility Breakout squeeze test.
        # The squeeze MUST be measured on the bar BEFORE the breakout, because a
        # real breakout expands the bands — by the time the current bar's close
        # clears the upper band, current-bar BBW is no longer compressed.
        prior_bb_lower  = float(closed['bb_lower'])
        prior_bb_middle = float(closed['bb_middle'])
        prior_bb_upper  = float(closed['bb_upper'])
        high_closed     = float(closed['high'])   # last completed bar's high for ratchet calc

        volume     = float(closed['volume'])
        avg_volume = float(df.iloc[-21:-1]['volume'].mean()) if len(df) >= 21 else float(df['volume'].mean())
        rsi_prev2  = float(prev2['rsi'])
        adx_prev2  = float(prev2['adx'])

        # 4. Return the standardized dictionary to the orchestrator
        return {
            'price':      float(current['close']),
            'high':       float(current['high']),
            'low':        float(current['low']),
            'ema_fast':   float(current['ema_fast']),
            'ema_slow':   float(current['ema_slow']),
            'rsi':        float(current['rsi']),
            'atr':        float(current['atr']),
            'adx':        float(current['adx']),
            'bb_lower':   float(current['bb_lower']),
            'bb_middle':  float(current['bb_middle']),
            'bb_upper':   float(current['bb_upper']),
            'regime':     "TRENDING" if current['adx'] > adx_threshold else "RANGING",
            'trend':      "BULL" if current['ema_fast'] > current['ema_slow'] else "BEAR",
            # --- CONSENSUS LAYER ---
            'volume':     volume,
            'avg_volume': avg_volume,
            'rsi_prev2':  rsi_prev2,
            'adx_prev2':  adx_prev2,
            # --- PRIOR-BAR BB (for VB squeeze pre-breakout) ---
            'bb_lower_prev':  prior_bb_lower,
            'bb_middle_prev': prior_bb_middle,
            'bb_upper_prev':  prior_bb_upper,
            # --- PRIOR-BAR HIGH (for ratchet — avoids in-progress wick spikes) ---
            'high_closed':    high_closed,
        }
    except Exception as e:
        import logging
        logging.getLogger("sulla").error(f"Alpaca Data Fetch Error for {symbol}: {e}", exc_info=True)
        return None

async def fetch_indicators(symbol, config=None, timeframe='1h', limit=150):
    """
    Asynchronous wrapper. Takes the heavy lifting and puts it on a background 
    thread so the main Telegram bot stays completely responsive.
    """
    # NOTE: We removed the 'exchange' argument because Alpaca is hardcoded inside.
    return await asyncio.to_thread(_fetch_indicators_sync, symbol, config, timeframe, limit)
