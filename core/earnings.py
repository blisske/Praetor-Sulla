import logging
import datetime
from datetime import date
import pytz

logger = logging.getLogger("sulla")

# ==============================================================================
# SULLA V1 - EARNINGS BLACKOUT ENGINE
# Blocks new entries within N calendar days of a symbol's earnings date.
# Uses yfinance for the earnings calendar — no API key required.
# ==============================================================================

# Module-level cache so we don't hit yfinance every single cycle.
# Structure: { 'AAPL': {'date': date(2026,4,25), 'fetched': date(2026,4,7)} }
_earnings_cache: dict = {}
_CACHE_TTL_DAYS = 1   # Refresh earnings date at most once per calendar day


def _fetch_earnings_date(symbol: str) -> date | None:
    """
    Fetches the next earnings date for a symbol via yfinance. Returns a date
    object or None if unavailable.

    Modern yfinance (>= 0.2.x) returns ticker.calendar as a dict like
    `{'Earnings Date': [datetime.date(...)], ...}`. Older versions returned a
    pandas DataFrame indexed by row label. Both shapes are handled here so the
    earnings blackout doesn't silently fail-open after a yfinance upgrade.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal    = ticker.calendar
        if not cal:
            return None

        # Modern shape: dict with 'Earnings Date' → list of date(s)
        if isinstance(cal, dict):
            val = cal.get('Earnings Date')
            if isinstance(val, list):
                val = val[0] if val else None
            if val is None:
                return None
            if isinstance(val, date):
                return val
            if hasattr(val, 'date') and callable(val.date):  # datetime / Timestamp
                return val.date()
            return None

        # Legacy shape: pandas DataFrame with 'Earnings Date' as a row label
        if hasattr(cal, 'empty') and cal.empty:
            return None
        if hasattr(cal, 'index') and 'Earnings Date' in cal.index:
            val = cal.loc['Earnings Date'].iloc[0]
            if isinstance(val, date):
                return val
            if hasattr(val, 'date') and callable(val.date):
                return val.date()
        return None
    except Exception as e:
        logger.warning(f"[EARNINGS] Could not fetch earnings date for {symbol}: {e}")
        return None


def get_earnings_date(symbol: str) -> date | None:
    """
    Returns the next earnings date for a symbol, using the module cache.
    Refreshes once per day.
    """
    today  = datetime.datetime.now(pytz.timezone('America/New_York')).date()
    cached = _earnings_cache.get(symbol)

    if cached and (today - cached['fetched']).days < _CACHE_TTL_DAYS:
        return cached['date']

    earnings_date = _fetch_earnings_date(symbol)
    _earnings_cache[symbol] = {'date': earnings_date, 'fetched': today}
    logger.info(f"[EARNINGS] {symbol} next earnings: {earnings_date}")
    return earnings_date


def is_earnings_blackout(symbol: str, blackout_days: int = 2) -> bool:
    """
    Returns True if today falls within the blackout window before earnings.
    New entries are blocked when:
        0 < days_until_earnings <= blackout_days

    Args:
        symbol        (str): Ticker e.g. 'AAPL'
        blackout_days (int): Calendar days before earnings to block entries

    Returns:
        bool: True = blocked, False = clear to trade
    """
    earnings_date = get_earnings_date(symbol)
    if earnings_date is None:
        # If we can't determine earnings, allow trading (fail open)
        return False

    today            = datetime.datetime.now(pytz.timezone('America/New_York')).date()
    days_until       = (earnings_date - today).days

    if 0 < days_until <= blackout_days:
        logger.info(
            f"[EARNINGS] {symbol} BLACKOUT — earnings in {days_until} day(s) "
            f"({earnings_date}). Blocking new entry."
        )
        return True

    return False


def get_blackout_summary(symbols: list, blackout_days: int = 2) -> dict:
    """
    Returns a dict of {symbol: days_until_earnings} for any symbol
    currently in blackout. Used by /report command.
    """
    today   = datetime.datetime.now(pytz.timezone('America/New_York')).date()
    blocked = {}
    for sym in symbols:
        earnings_date = get_earnings_date(sym)
        if earnings_date is None:
            continue
        days_until = (earnings_date - today).days
        if 0 < days_until <= blackout_days:
            blocked[sym] = days_until
    return blocked


def should_force_exit(symbol: str, exit_days_before: int = 1) -> bool:
    """
    Returns True if an existing position should be force-exited because
    earnings are tomorrow or sooner.

    Args:
        symbol          (str): Ticker
        exit_days_before (int): Force-exit when earnings are this many days away

    Returns:
        bool: True = force exit now
    """
    earnings_date = get_earnings_date(symbol)
    if earnings_date is None:
        return False

    today      = datetime.datetime.now(pytz.timezone('America/New_York')).date()
    days_until = (earnings_date - today).days

    if 0 <= days_until <= exit_days_before:
        logger.info(
            f"[EARNINGS] {symbol} FORCE-EXIT — earnings in {days_until} day(s) "
            f"({earnings_date}). Closing existing position."
        )
        return True

    return False
