"""
Minimal Oanda v20 REST client for Ionic.

Deliberately avoids the official `oandapyV20` SDK — fewer moving parts to debug
through, and we only need a handful of endpoints. Uses `requests` directly with
explicit error handling.

Endpoints implemented:
  - GET  /v3/accounts/{account_id}                            — account info + balance
  - GET  /v3/accounts/{account_id}/positions                  — list open positions
  - GET  /v3/accounts/{account_id}/openTrades                 — list open trades
  - GET  /v3/accounts/{account_id}/trades/{trade_id}          — trade detail (for fee capture)
  - GET  /v3/instruments/{instrument}/candles                 — OHLCV bars
  - GET  /v3/accounts/{account_id}/pricing                    — current bid/ask
  - POST /v3/accounts/{account_id}/orders                     — place market order (with attached stop)
  - PUT  /v3/accounts/{account_id}/trades/{trade_id}/orders   — modify stop/take-profit on existing trade
  - PUT  /v3/accounts/{account_id}/positions/{inst}/close     — flat a position (market close)

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
        to crash or degrade gracefully (engine idles when creds aren't set yet).
        """
        return cls(
            token=os.environ.get("OANDA_API_TOKEN", ""),
            account_id=os.environ.get("OANDA_ACCOUNT_ID", ""),
            environment=os.environ.get("OANDA_ENVIRONMENT", "practice"),
            timeout=timeout,
        )

    @classmethod
    def from_user(cls, user_id: int, timeout: float = 15.0) -> "OandaClient":
        """Build a per-user client by decrypting the user's broker_keys row
        from global.db.

        Schema convention (mirrors api/byok.py):
            key_enc      → AES-256-GCM-encrypted Oanda API token
            secret_enc   → AES-256-GCM-encrypted Oanda account_id
            last_error   → environment marker ('env=practice' or 'env=live')

        Raises OandaMissingCredentials if no broker_keys row exists for this
        user — caller (typically market_data.get_client) handles that as
        "user hasn't connected Oanda yet; engine idles."
        """
        import sqlite3
        # broker_crypto lives at /app/shared/broker_crypto.py via the
        # bind-mount the engine container's compose declares. The bare
        # `from broker_crypto import ...` line that used to be here
        # never worked — the shared lib isn't a sibling. Crashed every
        # per-tenant engine on boot until 2026-05-25 (bug-hunt task #149).
        # Canonical ionic-engine survived because operator never reaches
        # this from_user code path.
        try:
            from shared.broker_crypto import decrypt_credential, BrokerCryptoError
        except ImportError:
            # Sibling fallback for any dev environment where the shared
            # lib happens to live alongside core/.
            from broker_crypto import decrypt_credential, BrokerCryptoError  # type: ignore[no-redef]

        db_path = os.environ.get("GLOBAL_DB_PATH", "/app/foundation/global.db")
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError as e:
            raise OandaMissingCredentials(
                f"Couldn't open global.db at {db_path}: {e}"
            ) from e
        try:
            row = conn.execute(
                "SELECT key_enc, secret_enc, last_error "
                "FROM broker_keys WHERE user_id = ? AND broker = 'oanda'",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise OandaMissingCredentials(
                f"No Oanda credentials found for user_id={user_id} "
                f"(user hasn't connected at /settings/broker yet)."
            )

        key_enc, secret_enc, last_error = row
        if not key_enc or not secret_enc:
            raise OandaMissingCredentials(
                f"broker_keys row for user_id={user_id} is missing key_enc or "
                f"secret_enc — likely a corrupted row, ask user to reconnect."
            )

        try:
            token      = decrypt_credential(key_enc)
            account_id = decrypt_credential(secret_enc)
        except BrokerCryptoError as e:
            raise OandaMissingCredentials(
                f"Could not decrypt Oanda creds for user_id={user_id}: {e}. "
                f"Likely BROKER_KEY_MASTER mismatch — ask the operator."
            ) from e

        # Environment marker convention: 'env=practice' / 'env=live'.
        # Default to practice for safety if missing/malformed.
        environment = "practice"
        if last_error == "env=live":
            environment = "live"
        elif last_error == "env=practice":
            environment = "practice"

        return cls(
            token=token,
            account_id=account_id,
            environment=environment,
            timeout=timeout,
        )

    # ── Internal helpers ────────────────────────────────────────────────────
    def _request(self, method: str, path: str,
                 params: Optional[dict] = None,
                 json_body: Optional[dict] = None) -> dict:
        """Single chokepoint for every Oanda REST call. Uniform error mapping.

        method: 'GET' | 'POST' | 'PUT' | 'DELETE'
        json_body: serialized to JSON if provided (POST/PUT)
        """
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.request(
                method, url,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise OandaError(f"Network error on {method} {path}: {e}") from e

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
                f"Oanda {resp.status_code} on {method} {path}: {resp.text[:300]}"
            )

        # Some Oanda endpoints (e.g. successful close) return empty body
        if not resp.content:
            return {}

        try:
            return resp.json()
        except ValueError as e:
            raise OandaError(f"Oanda returned non-JSON on {path}: {e}") from e

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body: dict) -> dict:
        return self._request("POST", path, json_body=json_body)

    def _put(self, path: str, json_body: Optional[dict] = None) -> dict:
        return self._request("PUT", path, json_body=json_body or {})

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

    # ── Position + trade reads ──────────────────────────────────────────────
    def get_open_positions(self) -> list[dict]:
        """List all open positions (per-instrument net). Returns Oanda's raw
        position dicts; caller does field extraction.

        Each position has long/short sub-objects with `units`, `averagePrice`,
        `unrealizedPL`, and the position-level `instrument` + `pl` (realized).
        """
        payload = self._get(f"/v3/accounts/{self.account_id}/openPositions")
        return payload.get("positions", [])

    def get_open_trades(self) -> list[dict]:
        """List all open trades (per-fill, not per-instrument). Used when we
        need trade IDs for modifying stops on individual fills."""
        payload = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        return payload.get("trades", [])

    def get_trade(self, trade_id: str) -> dict:
        """Get a single trade by Oanda trade ID. Used to confirm fill +
        extract fee/spread data after order submission."""
        payload = self._get(f"/v3/accounts/{self.account_id}/trades/{trade_id}")
        return payload.get("trade", {})

    # ── Order placement ─────────────────────────────────────────────────────
    def place_market_order(
        self,
        symbol: str,
        units: int,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Submit a MARKET order with optional attached stop + take-profit.

        Oanda's order shape is signed-units: positive = long, negative = short.
        Stop/TP attached to the parent order fire atomically with the fill —
        no separate "place then attach" race (Kraken's pain point).

        Args:
            symbol:            "EUR/USD" or "EUR_USD"
            units:             signed int. Positive = buy (long), negative = sell (short).
            stop_loss_price:   if set, attaches a stopLossOnFill at this price.
            take_profit_price: if set, attaches a takeProfitOnFill at this price.
            client_order_id:   idempotency key — Oanda surfaces it as clientExtensions.id

        Returns the parsed response. Key fields:
            orderCreateTransaction       — the order we submitted
            orderFillTransaction         — the fill if it filled immediately
                                            (typically present for MARKET orders)
            orderCancelTransaction       — if it didn't fill (rare for market)
            relatedTransactionIDs        — list of all txn IDs from this submit

        Raises OandaError on any HTTP failure.
        """
        instrument = _to_oanda_instrument(symbol)
        order: dict = {
            "instrument":  instrument,
            "units":       str(int(units)),
            "type":        "MARKET",
            "timeInForce": "FOK",   # Fill Or Kill — atomic. No partial.
            "positionFill": "DEFAULT",
        }
        if stop_loss_price is not None:
            order["stopLossOnFill"] = {
                "price":       _format_price(stop_loss_price),
                "timeInForce": "GTC",
            }
        if take_profit_price is not None:
            order["takeProfitOnFill"] = {
                "price":       _format_price(take_profit_price),
                "timeInForce": "GTC",
            }
        if client_order_id:
            order["clientExtensions"] = {"id": client_order_id}
        body = {"order": order}
        return self._post(f"/v3/accounts/{self.account_id}/orders", body)

    def close_position(self, symbol: str, side: str = "long") -> dict:
        """Flat a position at market (close ALL units on the given side).

        Args:
            symbol: "EUR/USD" or "EUR_USD"
            side:   "long" or "short" — which side of the net position to close.
                    Most Ionic positions are long (we don't short FX in this
                    strategy); the `side` arg is here for forward-compat.

        Returns the close transaction. Key fields under longOrderFillTransaction
        (or shortOrderFillTransaction): `pl` (realized P&L), `commission`,
        `financing`, `price` (fill price), `units` (signed close size).
        """
        instrument = _to_oanda_instrument(symbol)
        body = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
        return self._put(
            f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            json_body=body,
        )

    def close_partial_position(self, symbol: str, units: int,
                               side: str = "long") -> dict:
        """Close PART of a position at market — partial take-profit path.

        Oanda accepts either the literal string "ALL" or a stringified positive
        integer for longUnits/shortUnits. We pass the unit count as a string.
        The remaining units stay open and continue to be managed by the
        attached stop-loss server-side.

        Args:
            symbol: "EUR/USD" or "EUR_USD"
            units:  POSITIVE integer — quantity of units to close. The Oanda
                    API expects positive on the close side regardless of long
                    or short. Caller passes whatever the partial size should be.
            side:   "long" or "short"

        Returns the close transaction with the same shape as close_position()
        but only the partial units are closed.
        """
        if units <= 0:
            raise OandaError(f"close_partial_position: units must be > 0; got {units}")
        instrument = _to_oanda_instrument(symbol)
        unit_str = str(int(units))
        body = {"longUnits": unit_str} if side == "long" else {"shortUnits": unit_str}
        return self._put(
            f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            json_body=body,
        )

    def modify_trade_stop(
        self,
        trade_id: str,
        new_stop_price: float,
    ) -> dict:
        """Move the stop-loss on an existing trade. Used for the ratchet."""
        body = {
            "stopLoss": {
                "price":       _format_price(new_stop_price),
                "timeInForce": "GTC",
            }
        }
        return self._put(
            f"/v3/accounts/{self.account_id}/trades/{trade_id}/orders",
            json_body=body,
        )

    def __repr__(self) -> str:
        return (
            f"OandaClient(account={self.account_id!r}, "
            f"environment={self.environment!r}, base_url={self.base_url!r})"
        )


# ── Module-level helpers (used by execution.py for tax fee_usd) ───────────────
def _format_price(price: float) -> str:
    """Oanda rejects floats with more than ~5 decimal places on most pairs.
    JPY pairs allow 3. We format conservatively to 5 dp — Oanda handles
    rounding to the instrument's pricePrecision server-side."""
    return f"{float(price):.5f}"


def extract_fill_fee_usd(order_response: dict) -> float:
    """Extract total USD-equivalent fee from a place_market_order() response.

    Oanda's fill transaction carries:
      - commission:    explicit commission in account currency (usually 0 on
                       standard accounts — Oanda's cost is in the spread)
      - financing:     overnight rollover charges (rare on fresh entries)

    We sum both — they're in account currency, which for US users is USD.
    Returns 0.0 on any extraction failure (defensive — fee accuracy must
    not break the ledger write).
    """
    if not isinstance(order_response, dict):
        return 0.0
    fill = order_response.get("orderFillTransaction") or {}
    if not isinstance(fill, dict):
        return 0.0
    total = 0.0
    for key in ("commission", "financing", "halfSpreadCost"):
        try:
            total += abs(float(fill.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
    return total


def extract_fill_price(order_response: dict, fallback: float = 0.0) -> float:
    """Extract average fill price from a place_market_order() response.

    For MARKET orders, Oanda reports the actual fill price in
    orderFillTransaction.price (the limit price you would have submitted,
    but for market it's the actual fill).
    """
    if not isinstance(order_response, dict):
        return fallback
    fill = order_response.get("orderFillTransaction") or {}
    try:
        return float(fill.get("price", fallback))
    except (TypeError, ValueError):
        return fallback


def extract_close_fee_usd(close_response: dict, side: str = "long") -> float:
    """Extract fee from a close_position() response.

    Close response shape is either longOrderFillTransaction or
    shortOrderFillTransaction (depending on side). Same fields as a fill:
    commission, financing, halfSpreadCost.
    """
    if not isinstance(close_response, dict):
        return 0.0
    key = "longOrderFillTransaction" if side == "long" else "shortOrderFillTransaction"
    fill = close_response.get(key) or {}
    if not isinstance(fill, dict):
        return 0.0
    total = 0.0
    for k in ("commission", "financing", "halfSpreadCost"):
        try:
            total += abs(float(fill.get(k, 0) or 0))
        except (TypeError, ValueError):
            continue
    return total


def extract_close_pl_usd(close_response: dict, side: str = "long") -> float:
    """Extract realized P&L from a close_position() response. Positive
    = profitable close, negative = loss. In account currency (USD for US)."""
    if not isinstance(close_response, dict):
        return 0.0
    key = "longOrderFillTransaction" if side == "long" else "shortOrderFillTransaction"
    fill = close_response.get(key) or {}
    try:
        return float(fill.get("pl", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
