"""FastAPI router for the per-user FROZEN kill switch.

Semantics:
    User can freeze their own account at any time. Once frozen, the
    engine still runs but takes NO new trade entries — existing
    positions continue to be managed (trailing stops, take-profits,
    exits) but the bot won't put on any new size.

    UNFREEZING is operator-only. This is deliberate: a panicked user
    who froze in the middle of a market move shouldn't be able to
    immediately unfreeze themselves. Forces a cool-down + a real
    conversation with the operator before live trading resumes.

    For shadow-mode users, freeze is mostly symbolic — but consistent
    UX (same toggle works regardless of mode).

Endpoints:
  GET   /api/user/freeze              Current state + freeze metadata
  POST  /api/user/freeze              User freezes themselves
                                       (returns 200; engine picks up
                                       within next cycle via .restart_engine)
  POST  /api/admin/users/{id}/unfreeze (operator-only — separate file)

Implementation:
  The frozen flag lives at `risk.frozen` in the user's per-user
  Config.yaml. Engine reads it each cycle (via load_engine_config) and
  skips trade entries when True. Mirrors the mode-toggle pattern —
  ruamel.yaml round-trip preserves comments + a .yaml.bak backup is
  written before each change.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from core import auth as core_auth
from core import email_sender
from core.auth import User
from api.auth import get_current_user
# Reuse the per-user Config.yaml helpers from the mode router — they
# already handle path resolution, file existence, ruamel round-trip,
# and .restart_engine flag touch.
from api.mode import (
    _user_config_path,
    _touch_user_restart_flag,
    _log_mode_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/freeze", tags=["freeze"])


# ─── Response shapes ───────────────────────────────────────────────────────


class FreezeStatusResponse(BaseModel):
    frozen:         bool
    frozen_at:      Optional[str] = None   # ISO timestamp when set
    frozen_reason:  Optional[str] = None
    can_unfreeze:   bool = False           # always False for user; admin endpoint sets it true
    config_present: bool


class FreezeRequest(BaseModel):
    reason: Optional[str] = None  # optional user-supplied reason ("market panic", etc.)


class OkResponse(BaseModel):
    ok: bool = True
    detail: Optional[str] = None


# ─── Config.yaml helpers (freeze-specific) ─────────────────────────────────


def _read_freeze_state(user_id: int) -> tuple[Optional[bool], Optional[str], Optional[str]]:
    """Returns (frozen, frozen_at, frozen_reason) from Config.yaml.

    All None if config file doesn't exist (user not provisioned yet).
    """
    cfg_path = _user_config_path(user_id)
    if not cfg_path.exists():
        return (None, None, None)
    from ruamel.yaml import YAML
    yaml = YAML()
    try:
        with open(cfg_path, "r") as f:
            data = yaml.load(f) or {}
    except Exception as e:
        logger.error(f"Malformed Config.yaml for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your trading config file is unreadable. Contact support.",
        )
    risk = data.get("risk") or {}
    return (
        bool(risk.get("frozen", False)),
        risk.get("frozen_at"),
        risk.get("frozen_reason"),
    )


def _write_freeze_state(user_id: int, frozen: bool, reason: Optional[str] = None) -> None:
    """Set `risk.frozen` + frozen_at + frozen_reason in user's Config.yaml.

    Writes .yaml.bak first per the existing convention. Raises 503 if
    no Config.yaml exists for the user.
    """
    cfg_path = _user_config_path(user_id)
    if not cfg_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your trading workspace is still being set up. Try again in a moment.",
        )
    from ruamel.yaml import YAML
    import shutil
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(cfg_path, "r") as f:
        data = yaml.load(f) or {}
    if "risk" not in data:
        data["risk"] = {}
    data["risk"]["frozen"] = bool(frozen)
    if frozen:
        data["risk"]["frozen_at"] = datetime.now(timezone.utc).isoformat()
        if reason:
            data["risk"]["frozen_reason"] = reason[:200]
        elif "frozen_reason" in data["risk"]:
            # Don't carry an old reason into a fresh freeze
            del data["risk"]["frozen_reason"]
    else:
        # Unfreeze: clear metadata for cleanliness (audit trail lives in
        # broker_key_events anyway)
        for k in ("frozen_at", "frozen_reason"):
            if k in data["risk"]:
                del data["risk"][k]
    shutil.copy(cfg_path, cfg_path.with_suffix(".yaml.bak"))
    with open(cfg_path, "w") as f:
        yaml.dump(data, f)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── GET /api/user/freeze ──────────────────────────────────────────────────


@router.get("", response_model=FreezeStatusResponse)
async def get_freeze_state(user: User = Depends(get_current_user)):
    """Current frozen state for the authenticated user. Always
    can_unfreeze=False from this endpoint — user can't unfreeze
    themselves (operator-only via /api/admin/users/<id>/unfreeze)."""
    frozen, frozen_at, frozen_reason = _read_freeze_state(user.id)
    return FreezeStatusResponse(
        frozen        = bool(frozen),
        frozen_at     = frozen_at,
        frozen_reason = frozen_reason,
        can_unfreeze  = False,
        config_present= frozen is not None,
    )


# ─── POST /api/user/freeze ─────────────────────────────────────────────────


@router.post("", response_model=OkResponse)
async def freeze_account(
    req:     FreezeRequest,
    request: Request,
    user:    User = Depends(get_current_user),
):
    """User-initiated freeze. Writes risk.frozen=true to their Config.yaml,
    touches .restart_engine so the engine picks up on next cycle, audit-
    logs the event, sends a confirmation email.

    NO unfreeze path here — that's operator-only via /api/admin.

    Idempotent: re-freezing an already-frozen account refreshes the
    frozen_at timestamp + replaces the reason but does NOT email again
    (avoid spam if a user clicks the button twice).
    """
    ip = _client_ip(request)
    already_frozen, _, _ = _read_freeze_state(user.id)
    _write_freeze_state(user.id, frozen=True, reason=req.reason)
    _touch_user_restart_flag(user.id)

    _log_mode_event(
        user.id,
        "account_frozen",
        detail=f"reason={(req.reason or '(none)')[:100]}",
        ip=ip,
    )

    # Send confirmation email only on the FIRST freeze (not the re-freeze
    # of an already-frozen account — avoid spam if user double-clicks).
    if not already_frozen:
        try:
            email_sender.send_email(
                to=user.email,
                subject="Your Foundation account is now frozen",
                text_body=(
                    "Hi,\n\n"
                    "You just froze your Foundation account. Your trading bot will "
                    "still manage existing positions (trailing stops, take-profits, "
                    "exits) but will NOT take any new trade entries until your "
                    "account is unfrozen.\n\n"
                    "Unfreezing requires operator review — this is intentional. "
                    "Email support@foundationbots.com when you're ready and an "
                    "operator will work through the unfreeze with you.\n\n"
                    "If this wasn't you, change your Foundation password "
                    "immediately and contact support — your account may have "
                    "been compromised.\n\n"
                    "— Foundation"
                ),
                tag="account-frozen",
            )
        except Exception as e:
            logger.warning(f"Freeze confirmation email failed for user {user.id}: {e}")

    logger.info(f"Account frozen: user_id={user.id} ip={ip} reason={(req.reason or '')[:60]}")
    return OkResponse(ok=True, detail="Account frozen. New trades blocked until operator unfreezes.")
