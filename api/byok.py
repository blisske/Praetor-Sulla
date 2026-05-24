"""FastAPI router for Bring-Your-Own-Key (Oanda credential) operations.

Ionic-specific BYOK adapter. Same broker_keys table + same encryption
via broker_crypto as Corinthian/Doric, but with Oanda's auth shape:
  - Single API TOKEN (not key+secret split)
  - Plus an account_id that selects which account under the token
  - Plus an environment flag (practice vs live)

Schema mapping (no migration needed — re-uses Kraken-shaped columns):
  broker_keys.key_enc       ← encrypted Oanda API token
  broker_keys.secret_enc    ← encrypted Oanda account_id
  broker_keys.last_error    ← 'env=practice' or 'env=live' marker
                              (same trick Doric uses for account_kind)
  broker_keys.broker        ← 'oanda'
  broker_keys.scope         ← 'trade' | 'read' | 'unknown' | 'none'

Endpoints:

  POST   /api/byok/oanda/validate   Paste token + account_id + env.
                                    Validates against Oanda, detects
                                    scope, encrypts + UPSERTs.
  GET    /api/byok/oanda            Status: scope, environment,
                                    validated_at, last_used_at,
                                    last_error. Never returns plaintext.
  DELETE /api/byok/oanda            Disconnect: deletes broker_keys row.

All endpoints require a valid JWT. All writes audit-log to
broker_key_events.

Plaintext token + account_id live in process memory ONLY during the
validate request — never written to disk, never logged.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core import auth as core_auth
from core import broker_crypto
from core import email_sender
from core import oanda_validator
from core.auth import User
from api.auth import get_current_user, _client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/byok", tags=["byok"])


# Rate-limit paste attempts: 5 per user per hour. Same budget as other bots.
validate_limiter = core_auth.RateLimiter(max_attempts=5, window_sec=3600)


# ─── Request / response shapes ─────────────────────────────────────────────


class OandaValidateRequest(BaseModel):
    api_token:  str = Field(min_length=16, max_length=512)
    account_id: str = Field(min_length=8,  max_length=64)
    # MUST match the environment the token was generated against.
    # Oanda practice tokens 401 against the live URL and vice versa.
    environment: str = Field(default="practice")   # 'practice' or 'live'


class OandaStatusResponse(BaseModel):
    connected:    bool
    broker:       str  # always 'oanda'
    scope:        Optional[str] = None        # 'read' | 'trade' | None
    environment:  Optional[str] = None        # 'practice' | 'live'
    validated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    last_error:   Optional[str] = None
    created_at:   Optional[str] = None


class OandaValidateResponse(BaseModel):
    ok:          bool
    scope:       str
    environment: str
    detail:      str
    can_live:    bool   # True iff scope == 'trade' AND environment == 'live'


# ─── Event audit helper ────────────────────────────────────────────────────


def _log_event(
    user_id:    int,
    broker:     str,
    event_type: str,
    *,
    detail:     Optional[str] = None,
    ip:         Optional[str] = None,
    db_path:    Optional[str] = None,
) -> None:
    """Append a row to broker_key_events. Never raises (best-effort)."""
    try:
        path = db_path or core_auth.GLOBAL_DB_PATH
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "INSERT INTO broker_key_events (user_id, broker, event_type, detail, ip) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, broker, event_type, (detail or "")[:500], (ip or "")[:64] or None),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to log broker_key_event {event_type} for user {user_id}: {e}")


# ─── DB helpers (broker_keys CRUD) ─────────────────────────────────────────


def _get_broker_key_row(user_id: int, broker: str, *, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    with core_auth.db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT scope, validated_at, last_used_at, last_error, created_at "
            "FROM broker_keys WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        ).fetchone()
    return row


def _upsert_broker_key(
    user_id:      int,
    broker:       str,
    token_enc:    str,
    account_enc:  str,
    scope:        str,
    environment:  str,
    *,
    db_path:      Optional[str] = None,
) -> None:
    """INSERT OR REPLACE the user's Oanda key.

    environment ('practice'/'live') is encoded in last_error as 'env=…' —
    same schema hack Doric uses for account_kind. Real error strings
    overwrite it (acceptable trade-off — when an error happens we want
    to surface that over the static environment label).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    env_marker = f"env={environment}"
    with core_auth.db_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO broker_keys "
            "  (user_id, broker, key_enc, secret_enc, scope, validated_at, last_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, broker) DO UPDATE SET "
            "  key_enc      = excluded.key_enc, "
            "  secret_enc   = excluded.secret_enc, "
            "  scope        = excluded.scope, "
            "  validated_at = excluded.validated_at, "
            "  last_error   = excluded.last_error",
            (user_id, broker, token_enc, account_enc, scope, now_iso, env_marker),
        )
        conn.commit()


def _delete_broker_key(user_id: int, broker: str, *, db_path: Optional[str] = None) -> bool:
    with core_auth.db_connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM broker_keys WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        )
        conn.commit()
        return cur.rowcount > 0


def _parse_environment(last_error_field: Optional[str]) -> Optional[str]:
    """Pull the 'env=practice' / 'env=live' marker out of last_error."""
    if not last_error_field:
        return None
    if last_error_field == "env=practice":
        return "practice"
    if last_error_field == "env=live":
        return "live"
    return None


def _real_last_error(last_error_field: Optional[str]) -> Optional[str]:
    """Return last_error UNLESS it's our env marker (which is bookkeeping)."""
    if not last_error_field or last_error_field.startswith("env="):
        return None
    return last_error_field


# ─── Endpoint: POST /api/byok/oanda/validate ───────────────────────────────


@router.post("/oanda/validate", response_model=OandaValidateResponse)
async def oanda_validate(
    req:     OandaValidateRequest,
    request: Request,
    user:    User = Depends(get_current_user),
):
    """Validate an Oanda token + account_id + environment, detect scope,
    encrypt + upsert. Same endpoint handles initial paste AND rotation.
    """
    ip = _client_ip(request)
    rate_key = f"user:{user.id}"
    if not validate_limiter.allow(rate_key):
        retry = validate_limiter.retry_after(rate_key)
        _log_event(user.id, "oanda", "key_validation_rate_limited", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many validation attempts. Please wait and try again.",
            headers={"Retry-After": str(retry)},
        )

    existing = _get_broker_key_row(user.id, "oanda")
    is_rotation = existing is not None

    result = oanda_validator.validate_oanda_token(
        req.api_token, req.account_id, environment=req.environment,
    )

    if not result.get("ok"):
        _log_event(
            user.id, "oanda", "key_validation_failed",
            detail=f"{result.get('error', '?')}: {result.get('detail', '?')[:200]}",
            ip=ip,
        )
        err = result.get("error", "")
        if err in ("connectionerror", "timeout", "requests_missing") or err.startswith("http_5"):
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            http_status = status.HTTP_400_BAD_REQUEST
        raise HTTPException(
            status_code=http_status,
            detail=result.get("detail", "Oanda rejected this token."),
        )

    scope       = result.get("scope", "unknown")
    environment = result.get("environment", req.environment)

    if scope not in ("read", "trade"):
        _log_event(user.id, "oanda", "key_validation_scope_unknown",
                   detail=str(result), ip=ip)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Oanda accepted the token but we couldn't determine its scope. "
                   "Contact support — this is unusual.",
        )

    # Encrypt + persist. Plaintext goes out of scope immediately after.
    try:
        token_enc   = broker_crypto.encrypt_credential(req.api_token)
        account_enc = broker_crypto.encrypt_credential(req.account_id)
    except broker_crypto.BrokerCryptoError:
        logger.error("BROKER_KEY_MASTER misconfiguration — encryption refused")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server-side encryption is misconfigured. Please contact support.",
        )

    _upsert_broker_key(user.id, "oanda", token_enc, account_enc, scope, environment)
    _log_event(
        user.id, "oanda",
        "key_rotated" if is_rotation else "key_added",
        detail=f"scope={scope} env={environment}",
        ip=ip,
    )
    _log_event(user.id, "oanda", f"scope_detected_{scope}", ip=ip)

    can_live = (scope == "trade" and environment == "live")
    if can_live:
        detail = (
            "Live Oanda account connected with trading enabled. "
            "You can switch to live mode any time from the Mode page."
        )
    elif scope == "trade" and environment == "practice":
        detail = (
            "Practice Oanda account connected. Ionic will paper-trade "
            "with the simulated funds in this practice account. To go "
            "live with real money, generate a token against your funded "
            "Oanda live account and paste it here."
        )
    else:
        detail = (
            f"Oanda {environment} token connected but account is in a "
            "non-trading state. Shadow trading still works on Ionic's "
            "own ledger. Resolve the account state with Oanda before "
            "going live."
        )

    return OandaValidateResponse(
        ok=True,
        scope=scope,
        environment=environment,
        detail=detail,
        can_live=can_live,
    )


# ─── Endpoint: GET /api/byok/oanda ─────────────────────────────────────────


@router.get("/oanda", response_model=OandaStatusResponse)
async def oanda_status(user: User = Depends(get_current_user)):
    """Return BYOK status for the authenticated user's Oanda connection.
    Never returns token/account_id plaintext or ciphertext."""
    row = _get_broker_key_row(user.id, "oanda")
    if not row:
        return OandaStatusResponse(connected=False, broker="oanda")

    return OandaStatusResponse(
        connected    = True,
        broker       = "oanda",
        scope        = row["scope"],
        environment  = _parse_environment(row["last_error"]),
        validated_at = row["validated_at"],
        last_used_at = row["last_used_at"],
        last_error   = _real_last_error(row["last_error"]),
        created_at   = row["created_at"],
    )


# ─── Endpoint: DELETE /api/byok/oanda ──────────────────────────────────────


@router.delete("/oanda", status_code=status.HTTP_200_OK)
async def oanda_disconnect(
    request: Request,
    user:    User = Depends(get_current_user),
):
    """Disconnect the user's Oanda token. Idempotent."""
    ip = _client_ip(request)
    deleted = _delete_broker_key(user.id, "oanda")

    if deleted:
        _log_event(user.id, "oanda", "key_disconnected", ip=ip)
        try:
            email_sender.send_email(
                to=user.email,
                subject="Your Oanda connection has been removed",
                text_body=(
                    "Hi,\n\n"
                    "You disconnected your Oanda API token from Foundation. "
                    "Live trading on Ionic is now disabled for your account; "
                    "your bot will continue running in shadow (paper) mode "
                    "on Ionic's own ledger.\n\n"
                    "If this wasn't you, change your Foundation password "
                    "immediately and revoke the token in Oanda's web "
                    "dashboard → Manage API Access.\n\n"
                    "— Foundation"
                ),
                tag="oanda-key-disconnected",
            )
        except Exception as e:
            logger.warning(f"Failed to send disconnect confirmation email to user {user.id}: {e}")

    return {"ok": True, "deleted": deleted}
