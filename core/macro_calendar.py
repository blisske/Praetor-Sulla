"""
Macro-event calendar + blackout check for Sulla.

The FX equivalent of Anton's earnings blackout. The big single-event movers
(NFP, FOMC, CPI, ECB / BoJ / BoE rate decisions) routinely produce stop-
hunting volatility that blows through ATR-based stops. We don't want the
engine entering positions in the window around these prints; we let them
land, the dust settle, then resume scanning.

Data source: ForexFactory's weekly JSON feed at
  https://nfs.faireconomy.media/ff_calendar_thisweek.json

Why ForexFactory:
  - Free, no auth required
  - Hand-curated by humans → impact classifications are realistic, not
    just "every release is medium"
  - 3-letter currency codes (USD/GBP/JPY/etc.) line up directly with our
    pair-name math
  - Stable schema for ~8 years now
  - Same data every retail FX bot uses; if it goes down it's industry-wide
    news, not a Sulla-specific issue

Trade-offs:
  - Unofficial endpoint; could be moved or deprecated without notice. We
    fail-open (no blackout) on fetch failure rather than blocking trading
    on calendar outages.
  - Only "this week" — refresh ~weekly. We refresh every 30 min anyway to
    pick up any late additions / corrections.

Configuration lives under `macro_blackout` in Config.yaml:
  enabled:           true / false      (master switch)
  minutes_before:    int               (block this many minutes before event)
  minutes_after:     int               (... and after)
  importance_min:    'Medium' or 'High' (filters the impact column;
                                          'High' is the default — too aggressive
                                          a filter and the bot rarely trades)
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger("sulla")


_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_CACHE_TTL_SECONDS = 30 * 60   # 30-minute cache
_FETCH_TIMEOUT = 10            # seconds

# ── Impact filter ranking ───────────────────────────────────────────────────
# ForexFactory uses string values ("Low", "Medium", "High", "Holiday").
# Map them to ints so config can compare "is this event at least High?".
_IMPACT_RANK = {"Low": 1, "Medium": 2, "High": 3, "Holiday": 0}


# ── In-memory cache ─────────────────────────────────────────────────────────
_cache_events: list[dict] = []
_cache_fetched_at: float = 0.0
_cache_error: Optional[str] = None


def _fetch_now() -> list[dict]:
    """
    Hit the ForexFactory feed and return the parsed list of events.
    Each event dict has at minimum: title, country, date, impact.
    Raises requests.RequestException on network failure; caller catches.
    """
    resp = requests.get(
        _FEED_URL,
        headers={"User-Agent": "Mozilla/5.0 (Sulla FX bot)"},
        timeout=_FETCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected feed shape: {type(data).__name__}")
    return data


def _refresh_if_stale() -> list[dict]:
    """
    Returns cached events if fresh; otherwise re-fetches. On fetch failure,
    keeps serving stale cache and logs (so a temporary outage doesn't kill
    blackouts entirely — better to have day-old data than no data).
    """
    global _cache_events, _cache_fetched_at, _cache_error
    now = time.time()
    if _cache_events and (now - _cache_fetched_at) < _CACHE_TTL_SECONDS:
        return _cache_events

    try:
        events = _fetch_now()
        _cache_events = events
        _cache_fetched_at = now
        _cache_error = None
        logger.info(
            f"Macro calendar refreshed: {len(events)} events this week "
            f"({sum(1 for e in events if e.get('impact') == 'High')} high-impact)"
        )
        return events
    except Exception as e:
        _cache_error = str(e)
        if _cache_events:
            age_min = (now - _cache_fetched_at) / 60
            logger.warning(
                f"Macro calendar refresh failed: {e}. Serving cache (age "
                f"{age_min:.0f} min)."
            )
            return _cache_events
        logger.warning(f"Macro calendar fetch failed and no cache: {e}. Fail-open.")
        return []


def reset_cache() -> None:
    """Drop the in-memory cache so the next call re-fetches. Test utility."""
    global _cache_events, _cache_fetched_at, _cache_error
    _cache_events = []
    _cache_fetched_at = 0.0
    _cache_error = None


def cache_status() -> dict:
    """Surface cache state for /report and `/calendar` Telegram cmd."""
    return {
        "events":     len(_cache_events),
        "fetched_at": _cache_fetched_at,
        "age_seconds": time.time() - _cache_fetched_at if _cache_fetched_at else None,
        "last_error": _cache_error,
    }


# ── Event parsing ───────────────────────────────────────────────────────────
def _parse_event_time(event: dict) -> Optional[datetime]:
    """
    Parse the ISO-8601 date string into a UTC-aware datetime. ForexFactory
    publishes with the US Eastern offset (e.g. '2026-05-20T14:00:00-04:00');
    Python's fromisoformat handles that correctly on 3.11+.
    """
    raw = event.get("date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        # Always return UTC for downstream comparisons
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _event_affects_currency(event: dict, currency: str) -> bool:
    """True if the event is for the given 3-letter currency code (USD/GBP/etc.)."""
    return (event.get("country") or "").upper() == currency.upper()


def _event_meets_min_impact(event: dict, min_impact: str) -> bool:
    """True if event's impact rank ≥ min_impact's rank."""
    event_rank = _IMPACT_RANK.get(event.get("impact", "Low"), 0)
    min_rank   = _IMPACT_RANK.get(min_impact, 3)
    return event_rank >= min_rank


# ── Public API ──────────────────────────────────────────────────────────────
def get_blackout_status(
    symbol: str,
    macro_cfg: dict,
    now: Optional[datetime] = None,
) -> tuple[bool, Optional[dict]]:
    """
    Returns (is_blackout, triggering_event_or_None) for the given pair.

    A pair is in blackout when ANY currency in it (base OR quote) has a
    macro event within the (now - minutes_after, now + minutes_before)
    window. The window is intentionally one-sided around `now`:
      - if event is in the future within minutes_before → blackout (we're
        approaching the event)
      - if event was in the past within minutes_after  → blackout (still
        digesting volatility)

    Args:
        symbol:    "EUR/USD" canonical form
        macro_cfg: cfg.get('macro_blackout', {}) dict
        now:       defaults to datetime.now(timezone.utc); passable for tests

    Returns:
        (False, None) if blackout disabled, calendar empty, or no event
            in window
        (True, event_dict) if any event triggers the blackout
    """
    if not macro_cfg.get("enabled", True):
        return False, None

    minutes_before = int(macro_cfg.get("minutes_before", 60))
    minutes_after  = int(macro_cfg.get("minutes_after",  120))
    importance_min = macro_cfg.get("importance_min", "High")

    if now is None:
        now = datetime.now(timezone.utc)

    base, _, quote = symbol.partition("/")
    relevant_currencies = {base.upper(), quote.upper()}

    events = _refresh_if_stale()
    if not events:
        return False, None

    window_start = now - timedelta(minutes=minutes_after)   # past-side
    window_end   = now + timedelta(minutes=minutes_before)  # future-side

    for e in events:
        if not _event_meets_min_impact(e, importance_min):
            continue
        if (e.get("country") or "").upper() not in relevant_currencies:
            continue
        dt = _parse_event_time(e)
        if dt is None:
            continue
        if window_start <= dt <= window_end:
            return True, e

    return False, None


def upcoming_events(
    symbols: list[str],
    macro_cfg: dict,
    look_ahead_hours: int = 48,
    now: Optional[datetime] = None,
) -> list[dict]:
    """
    Returns all events that match the importance filter, affect at least
    one currency in `symbols`, and fall in the next `look_ahead_hours`.
    Sorted soonest-first. Useful for the /calendar Telegram command.
    """
    importance_min = macro_cfg.get("importance_min", "High")
    if now is None:
        now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=look_ahead_hours)

    relevant = set()
    for s in symbols:
        if "/" in s:
            b, q = s.split("/")
            relevant.add(b.upper())
            relevant.add(q.upper())

    out = []
    for e in _refresh_if_stale():
        if not _event_meets_min_impact(e, importance_min):
            continue
        if (e.get("country") or "").upper() not in relevant:
            continue
        dt = _parse_event_time(e)
        if dt is None or dt < now or dt > horizon:
            continue
        out.append({
            "title":    e.get("title", "?"),
            "currency": e.get("country", "?"),
            "impact":   e.get("impact", "?"),
            "time_utc": dt,
            "forecast": e.get("forecast") or "—",
            "previous": e.get("previous") or "—",
        })
    out.sort(key=lambda x: x["time_utc"])
    return out
