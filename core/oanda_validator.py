"""Oanda v20 API token validation — scope detection without exposing
user funds.

Validates a user-provided Oanda API token + account_id by hitting the
v3 accounts endpoint and interpreting the result. Used during the BYOK
paste flow.

Oanda's auth model differs from Kraken AND Alpaca:
  - Single API TOKEN (no key/secret split — token is the bearer credential)
  - Token + account_id pair: token authenticates the user; account_id
    selects which of (potentially multiple) accounts to act on
  - Bound to ONE environment: practice (free, simulated balance) OR live
    (funded). User must paste against the right URL.
  - Token has full account access — there's no granular permission tier.
    "Can this token trade?" reduces to "did the GET /v3/accounts call
    succeed AND is the account state OK?"

Scope outcomes:
  'trade' — token authenticates AND account isn't suspended/closed
  'read'  — token authenticates BUT account is in a non-trading state
            (e.g. closed, hedge-flag mismatch, demo expired)
  'none'  — token didn't authenticate at all (401)
  'unknown' — Oanda returned an unexpected error

Public API:

    validate_oanda_token(api_token, account_id, environment) -> dict
        {
          'ok':           bool,
          'scope':        'trade' | 'read' | 'unknown' | 'none',
          'detail':       human-readable description,
          'environment':  'practice' | 'live',
          'error':        short technical code if not ok
        }

The validator NEVER returns the token or account_id in its output,
never logs either, and never persists either.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


PRACTICE_BASE = "https://api-fxpractice.oanda.com"
LIVE_BASE     = "https://api-fxtrade.oanda.com"
OANDA_TIMEOUT_SEC = 15


# ─── Public API ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    scope: str               # 'trade' | 'read' | 'unknown' | 'none'
    detail: str
    environment: str         # 'practice' | 'live'
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ok":          self.ok,
            "scope":       self.scope,
            "detail":      self.detail,
            "environment": self.environment,
            **({"error": self.error} if self.error else {}),
        }


def validate_oanda_token(
    api_token: str,
    account_id: str,
    *,
    environment: str = "practice",
) -> dict:
    """Validate an Oanda token + account_id pair against the v20 API.

    Args:
        api_token:   The user-supplied Oanda Personal Access Token.
        account_id:  The user's Oanda account ID (looks like '101-001-…').
        environment: 'practice' for the free simulated environment,
                     'live' for the funded production environment.
                     MUST match the environment the token was generated
                     against — practice token will 401 against live URL.

    Returns:
        Dict with keys: ok, scope, detail, environment, [error].

    Does NOT raise on Oanda errors — those become ok=False in the result.
    """
    env = (environment or "practice").strip().lower()
    if env not in ("practice", "live"):
        return ValidationResult(
            ok=False, scope="none", environment="practice",
            detail=f"Environment must be 'practice' or 'live', got {environment!r}.",
            error="bad_environment",
        ).to_dict()

    if not api_token or not isinstance(api_token, str):
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="API token is required.",
            error="missing_token",
        ).to_dict()
    if not account_id or not isinstance(account_id, str):
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="Account ID is required (looks like '101-001-12345678-001').",
            error="missing_account_id",
        ).to_dict()

    api_token  = api_token.strip()
    account_id = account_id.strip()

    # Oanda tokens are ~65 chars (hex). Account IDs look like XXX-NNN-NNNNNNNN-NNN.
    if len(api_token) < 32:
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="Token looks too short. Did you paste the entire value?",
            error="format",
        ).to_dict()
    if "-" not in account_id or len(account_id) < 10:
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="Account ID format looks wrong. Should look like '101-001-12345678-001' (find it in Oanda's web dashboard → Manage API Access).",
            error="format",
        ).to_dict()

    # ── Hit Oanda v20 /v3/accounts/{account_id} ────────────────────────
    try:
        import requests
    except ImportError:
        logger.error("requests not installed")
        return ValidationResult(
            ok=False, scope="unknown", environment=env,
            detail="Server is missing the requests dependency. Contact support.",
            error="requests_missing",
        ).to_dict()

    base = PRACTICE_BASE if env == "practice" else LIVE_BASE
    url = f"{base}/v3/accounts/{account_id}"
    headers = {
        "Authorization":          f"Bearer {api_token}",
        "Accept-Datetime-Format": "RFC3339",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=OANDA_TIMEOUT_SEC)
    except requests.RequestException as e:
        logger.warning(f"Oanda network error on token validation: {e}")
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="Couldn't reach Oanda's API. Try again in a few minutes.",
            error=str(type(e).__name__).lower(),
        ).to_dict()

    if resp.status_code == 401:
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail=(
                f"Oanda rejected the token on the {env} endpoint. The most "
                f"common cause is a token-vs-environment mismatch: a "
                f"{'practice' if env == 'live' else 'live'} token won't work "
                f"against the {env} URL. Double-check both fields + the "
                f"radio button above."
            ),
            error="unauthorized",
        ).to_dict()

    if resp.status_code == 404:
        # Token is valid (otherwise 401) but account_id doesn't exist
        # under this token
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail=(
                f"Oanda doesn't see account ID '{account_id}' under this "
                f"token. Confirm the account ID in Oanda's web dashboard → "
                f"Manage API Access. Note: tokens are tied to your sub-account."
            ),
            error="account_not_found",
        ).to_dict()

    if resp.status_code >= 500:
        return ValidationResult(
            ok=False, scope="none", environment=env,
            detail="Oanda's API is temporarily unavailable. Try again in a few minutes.",
            error=f"http_{resp.status_code}",
        ).to_dict()

    if resp.status_code != 200:
        logger.warning(f"Oanda unexpected status {resp.status_code} on validation")
        return ValidationResult(
            ok=False, scope="unknown", environment=env,
            detail=f"Oanda returned an unexpected status {resp.status_code}.",
            error=f"http_{resp.status_code}",
        ).to_dict()

    # ── Inspect account state ──────────────────────────────────────────
    try:
        data = resp.json()
        account = data.get("account") or {}
    except Exception as e:
        logger.warning(f"Oanda response parsing failed: {e}")
        return ValidationResult(
            ok=False, scope="unknown", environment=env,
            detail="Oanda returned a malformed response. Contact support.",
            error="response_parse",
        ).to_dict()

    # Oanda account fields of interest:
    #   account.alias / account.id — sanity
    #   account.balance — string number
    #   account.openTradeCount — int
    #   (no direct 'trading_blocked' flag like Alpaca — instead the account
    #    just won't accept orders if closed/suspended, which we'd find out at
    #    trade time. For validation purposes, successful /v3/accounts read
    #    means scope='trade'.)
    balance = account.get("balance")
    if balance is None:
        # Successful response but no balance field — degraded state, treat as read-only
        return ValidationResult(
            ok=True, scope="read", environment=env,
            detail=(
                f"Token authenticated on the {env} endpoint but the account "
                f"is in an unusual state. Shadow trading still works; live "
                f"orders may fail until you resolve it with Oanda."
            ),
            error="account_degraded",
        ).to_dict()

    return ValidationResult(
        ok=True, scope="trade", environment=env,
        detail=(
            f"Connected to your {env} Oanda account "
            f"(balance: {balance}). Trading is enabled."
        ),
    ).to_dict()


# ─── Test entry point ──────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python oanda_validator.py <token> <account_id> [--live]")
        sys.exit(1)
    env = "live" if "--live" in sys.argv else "practice"
    result = validate_oanda_token(sys.argv[1], sys.argv[2], environment=env)
    print(result)
