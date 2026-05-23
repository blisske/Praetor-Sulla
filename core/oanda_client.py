"""
Minimal Oanda v20 REST client for Ionic.

Deliberately avoids the official `oandapyV20` SDK — fewer moving parts to debug
through, and we only need a handful of endpoints. Uses `requests` directly with
explicit error handling.

Endpoints implemented:
  - GET /v3/accounts/{account_id}                — account info + balance
  - GET /v3/instruments/{instrument}/candles     — OHLCV bars
  - GET /v3/accounts/{account_id}/pricing        — current bid/ask for symbols

Phase 3 adds:
  - POST /v3/accounts/{account_id}/orders        — place a market order
  - PUT  /v3/accounts/{account_id}/positions/... — close a position

References:
  https://developer.oanda.com/rest-live-v20/instrument-ep/
  https://developer.oanda.com/rest-live-v20/account-ep/
  https://developer.oanda.com/rest-live-v20/pricing-ep/

Symbol convention: Ionic's internal universe uses standard FX notation
("EUR/USD", "USD/JPY"). Oanda's API uses underscore notation ("EUR_USD",
"USD_JPY"). The client transparently converts at the boundary so the rest
of the engine never has to think about it.
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger("ionic")


# ── Granularity mapping ─────────────────────────────────────────────────────
# Ionic's internal timeframe strings (matching Anton/Tiberius convention)
# mapped to Oanda's granularity codes.
_GRANULARITY_MAP = {
    "5m":   "M5",
    "15m":  "M15",
    "30m":  "M30",
    "1h":   "H1",
    "4h":   "H4",
    "1d":   "D",
    "1w":   "W",
}


def _to_oanda_instrument(symbol: str) -> str:
    """EUR/USD → EUR_USD. Idempotent: EUR_USD passes through unchanged."""
    return symbol.replace("/", "_")


def _to_internal_symbol(instrument: str) -> str:
    """EUR_USD → EUR/USD. Idempotent for already-internal symbols."""
    return instrument.replace("_", "/")


# ── Error types ─────────────────────────────────────────────────────────────
class OandaError(Exception):
    """Raised for any Oanda API failure (HTTP error, bad payload, no creds)."""


class OandaMissingCredentials(OandaError):
    """Raised when OANDA_API_TOKEN or OANDA_ACCOUNT_ID is unset."""


# ── Client ──────────────────────────────────────────────────────────────────
class OandaClient:
    """
    Minimal Oanda v20 REST client.

    Construct from environment variables by default:
        client = OandaClient.from_env()

    Or construct explicitly:
        client = OandaClient(token=..., account_id=..., environment="practice")
    """

    PRACTICE_BASE = "https://api-fxpractice.oanda.com"
    LIVE_BASE     = "https://api-fxtrade.oanda.com"

    def __init__(
        self,
        token: str,
        account_id: str,
        environment: str = "practice",
        timeout: float = 15.0,
    ):
        if not token:
            raise OandaMissingCredentials("OANDA_API_TOKEN is empty or unset")
        if not account_id:
            raise OandaMissingCredentials("OANDA_ACCOUNT_ID is empty or unset")

        self.token       = token
        self.account_id  = account_id
        self.environment = environment.lower()
        self.timeout     = timeout

        if self.environment == "live":
            self.base_url = self.LIVE_BASE
        elif self.environment == "practice":
            self.base_url = self.PRACTICE_BASE
        else:
            raise OandaError(
                f"OANDA_ENVIRONMENT must be 'practice' or 'live'; got {environment!r}"
            )

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization":   f"Bearer {self.token}",
            "Content-Type":    "application/json",
            "Accept-Datetime-Format": "RFC3339",
        })

    @classmethod
    def from_env(cls, timeout: float = 15.0) -> "OandaClient":
        """
        Build a client from env vars. Raises OandaMissingCredentials if
        OANDA_API_TOKEN or OANDA_ACCOUNT_ID is missing — caller decides whether
        to crash or degrade gracefully (engine idles in Phase 2 when creds
        aren't set yet).
        """
        return cls(
            token=os.environ.get("OANDA_API_TOKEN", ""),
            account_id=os.environ.get("OANDA_ACCOUNT_ID", ""),
            environment=os.environ.get("OANDA_ENVIRONMENT", "practice"),
            timeout=timeout,
        )

    # ── Internal helper ─────────────────────────────────────────────────────
    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise OandaError(f"Network error on GET {path}: {e}") from e

        if resp.status_code == 401:
            raise OandaError(
                f"Oanda rejected the token (HTTP 401 on {path}). "
                "Check OANDA_API_TOKEN and OANDA_ENVIRONMENT (practice token "
                "won't work against the live API and vice versa)."
            )
        if resp.status_code == 404:
            raise OandaError(
                f"Oanda 404 on {path} — usually means OANDA_ACCOUNT_ID is "
                "wrong, or the instrument name isn't tradeable on this account."
            )
        if not resp.ok:
            raise OandaError(
                f"Oanda {resp.status_code} on {path}: {resp.text[:300]}"
            )

        try:
            return resp.json()
        except ValueError as e:
            raise OandaError(f"Oanda returned non-JSON on {path}: {e}") from e

    # ── Public methods ──────────────────────────────────────────────────────
    def get_account(self) -> dict:
        """Account summary — balance, NAV, margin, open trade count, etc."""
        return self._get(f"/v3/accounts/{self.account_id}")

    def get_candles(
        self,
        symbol: str,
        granularity: str = "H1",
        count: int = 150,
        price: str = "M",
    ) -> list[dict]:
        """
        Fetch OHLCV candles for one instrument.

        Args:
            symbol: "EUR/USD" or "EUR_USD" — either works.
            granularity: Oanda granularity code ("H1", "M30", "D", etc.) or
                Ionic's internal timeframe string ("1h", "30m", "1d"); the
                latter is mapped before the request.
            count: Number of bars to return (max 5000).
            price: "M" = mid, "B" = bid, "A" = ask, or combinations ("MBA").

        Returns:
            List of candle dicts, oldest-first. Each candle has keys:
                time   — RFC3339 timestamp
                volume — int
                open/high/low/close — floats (extracted from `mid` for M price)
        """
        instrument = _to_oanda_instrument(symbol)
        gran = _GRANULARITY_MAP.get(granularity, granularity)
        params = {
            "granularity": gran,
            "count":       count,
            "price":       price,
        }
        payload = self._get(f"/v3/instruments/{instrument}/candles", params=params)
        raw = payload.get("candles", [])

        # Flatten the mid/bid/ask object into top-level OHLC for downstream
        # pandas consumption. We default to mid prices unless the caller asked
        # for bid or ask explicitly.
        price_key = "mid" if "M" in price else ("bid" if "B" in price else "ask")
        out = []
        for c in raw:
            if not c.get("complete", True):
                # Drop the live in-progress bar; it always has partial volume
                # and would skew indicators (same reason Anton/Tiberius do this).
                continue
            ohlc = c.get(price_key, {})
            out.append({
                "time":   c["time"],
                "volume": int(c.get("volume", 0)),
                "open":   float(ohlc.get("o", 0)),
                "high":   float(ohlc.get("h", 0)),
                "low":    float(ohlc.get("l", 0)),
                "close":  float(ohlc.get("c", 0)),
            })
        return out

    def get_pricing(self, symbols: list[str]) -> dict[str, dict]:
        """
        Current bid/ask for one or more instruments.

        Returns:
            {symbol_internal: {"bid": float, "ask": float, "spread": float}, ...}
        """
        if not symbols:
            return {}
        instruments = ",".join(_to_oanda_instrument(s) for s in symbols)
        payload = self._get(
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instruments},
        )
        out = {}
        for p in payload.get("prices", []):
            inst = p.get("instrument", "")
            bids = p.get("bids", [])
            asks = p.get("asks", [])
            if not bids or not asks:
                continue
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            out[_to_internal_symbol(inst)] = {
                "bid":    bid,
                "ask":    ask,
                "spread": ask - bid,
            }
        return out

    def __repr__(self) -> str:
        return (
            f"OandaClient(account={self.account_id!r}, "
            f"environment={self.environment!r}, base_url={self.base_url!r})"
        )
