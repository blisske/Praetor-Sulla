"""FastAPI router for the shadow ↔ live mode toggle.

Implements the auto-approval gate from SAAS_BYOK_PLAN.md §"Auto-approval
to live mode": a user with a valid trade-scope Oanda key can flip
between shadow (paper) and live trading at any time, no manual approval.

Endpoints:

  GET  /api/user/mode    Current mode + can_live capability + gate reason
                         if can_live=False
  POST /api/user/mode    Flip mode to 'shadow' or 'live'. Live requires
                         broker_keys.scope='trade'.

Canonical mode state lives in the user's per-user Config.yaml at
``${USER_DATA_DIR}/<user_id>/Config.yaml`` under ``exchange.shadow_mode``.
Engine reads Config.yaml on startup and after .restart_engine flag.
The endpoint:
  1. Writes shadow_mode = true|false via ruamel.yaml (preserves comments)
  2. Touches the user's .restart_engine flag so the engine picks up
     the new mode on its next cycle

If the user's Config.yaml doesn't exist yet (provisioner hasn't run),
the endpoint returns 503 with a clear message — they're a brand-new
account that's still being set up.

All flips append to broker_key_events for audit (event_type:
mode_flipped_to_live | mode_flipped_to_shadow). Successful live-flips
trigger a confirmation email via core.email_sender.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from shared import auth as core_auth
from core import email_sender
from shared.auth import User
from shared.api_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/mode", tags=["mode"])


# Per-user data root. Provisioner builds /app/data/users/<id>/{Config.yaml,
# ionic.db, .restart_engine}. Override via env for tests + local dev.
USER_DATA_DIR = os.getenv("USER_DATA_DIR", "/app/data/users")


# ─── Request / response shapes ─────────────────────────────────────────────


class ModeStatusResponse(BaseModel):
    mode:            str               # 'shadow' | 'live'
    can_live:        bool
    gate_reason:     Optional[str] = None   # human-readable reason if can_live=False
    broker_scope:    Optional[str] = None   # 'read' | 'trade' | None (no key connected)
    config_present:  bool                   # False until provisioner has run for this user


class ModeFlipRequest(BaseModel):
    mode: str = Field(pattern=r"^(shadow|live)$")
    # 6-digit OTP from the confirmation email — required for shadow→live.
    # If absent, the endpoint issues a code via email and returns 202
    # (confirmation_required=True) instead of flipping.
    confirmation_code: Optional[str] = None


class ModeFlipResponse(BaseModel):
    ok:                     bool
    mode:                   str
    confirmation_required:  bool = False     # True when caller needs to re-POST with a code
    detail:                 Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────


def _user_config_path(user_id: int, *, data_dir: Optional[str] = None) -> Path:
    """Resolve the per-user Config.yaml path. Existence not guaranteed."""
    base = Path(data_dir or USER_DATA_DIR)
    return base / str(user_id) / "Config.yaml"


def _user_restart_flag_path(user_id: int, *, data_dir: Optional[str] = None) -> Path:
    base = Path(data_dir or USER_DATA_DIR)
    return base / str(user_id) / ".restart_engine"


def _read_user_shadow_mode(user_id: int, *, data_dir: Optional[str] = None) -> Optional[bool]:
    """Return the user's current shadow_mode boolean from their Config.yaml.

    Returns None if the config file doesn't exist (user not provisioned yet).
    Raises HTTPException(500) if the file exists but is malformed — that's
    an operator problem, not a user-facing condition.
    """
    cfg_path = _user_config_path(user_id, data_dir=data_dir)
    if not cfg_path.exists():
        return None
    from ruamel.yaml import YAML
    yaml = YAML()
    try:
        with open(cfg_path, "r") as f:
            data = yaml.load(f)
    except Exception as e:
        logger.error(f"Malformed Config.yaml for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your trading config file is unreadable. Contact support.",
        )
    exch = (data or {}).get("exchange") or {}
    # Default to True (shadow) when the key is missing — safer than assuming live
    return bool(exch.get("shadow_mode", True))


def _write_user_shadow_mode(user_id: int, shadow_mode: bool, *, data_dir: Optional[str] = None) -> None:
    """Update exchange.shadow_mode in the user's Config.yaml, preserving comments.

    Backs up the previous file to Config.yaml.bak first (matches the existing
    api/main.py convention). Raises HTTPException(503) if the file is missing.
    """
    cfg_path = _user_config_path(user_id, data_dir=data_dir)
    if not cfg_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Your trading workspace is still being set up. This usually "
                "completes within a minute of signup — refresh and try again."
            ),
        )
    from ruamel.yaml import YAML
    import shutil
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(cfg_path, "r") as f:
        data = yaml.load(f)
    if "exchange" not in data:
        data["exchange"] = {}
    data["exchange"]["shadow_mode"] = bool(shadow_mode)
    # Backup BEFORE writing, in case the write fails mid-flush
    shutil.copy(cfg_path, cfg_path.with_suffix(".yaml.bak"))
    with open(cfg_path, "w") as f:
        yaml.dump(data, f)


def _touch_user_restart_flag(user_id: int, *, data_dir: Optional[str] = None) -> None:
    """Touch the user's .restart_engine — engine picks up new mode on next cycle.

    Quiet on filesystem errors — a successful Config.yaml write is the
    user-visible commit point; if the flag fails to write the worst case is
    the change picks up on the next engine restart cycle (existing fallback).
    """
    flag = _user_restart_flag_path(user_id, data_dir=data_dir)
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch(exist_ok=True)
        # bump mtime so the engine's watcher notices even if file existed
        os.utime(flag, None)
    except OSError as e:
        logger.warning(f"Failed to touch .restart_engine for user {user_id}: {e}")


def _get_broker_scope(user_id: int, broker: str = "oanda", *, db_path: Optional[str] = None) -> Optional[str]:
    """Return the user's connected Oanda key scope, or None if no key."""
    with core_auth.db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT scope FROM broker_keys WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        ).fetchone()
    return row["scope"] if row else None


def _log_mode_event(
    user_id:    int,
    event_type: str,
    *,
    detail:     Optional[str] = None,
    ip:         Optional[str] = None,
    db_path:    Optional[str] = None,
) -> None:
    """Append a row to broker_key_events. broker='oanda' since that's the
    only broker that gates live mode v1. Best-effort; never raises."""
    try:
        path = db_path or core_auth.GLOBAL_DB_PATH
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "INSERT INTO broker_key_events (user_id, broker, event_type, detail, ip) "
                "VALUES (?, 'oanda', ?, ?, ?)",
                (user_id, event_type, (detail or "")[:500], (ip or "")[:64] or None),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to log mode event {event_type} for user {user_id}: {e}")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── GET /api/user/mode ────────────────────────────────────────────────────


@router.get("", response_model=ModeStatusResponse)
async def get_mode(user: User = Depends(get_current_user)):
    """Return the user's current mode + whether they can flip to live."""
    shadow_mode = _read_user_shadow_mode(user.id)
    config_present = shadow_mode is not None
    # When config is missing, default-report mode='shadow' for UI sensibility
    current_mode = "shadow" if (shadow_mode is None or shadow_mode) else "live"

    broker_scope = _get_broker_scope(user.id)
    can_live = (broker_scope == "trade") and config_present
    gate_reason = None
    if not can_live:
        if not config_present:
            gate_reason = "Your trading workspace is still being set up."
        elif broker_scope is None:
            gate_reason = (
                "Connect a Oanda API key with trading permission before "
                "switching to live mode."
            )
        elif broker_scope == "read":
            gate_reason = (
                "Your Oanda key only has read permission. Replace it with "
                "a key that has 'Create & modify orders' enabled to unlock "
                "live mode."
            )

    return ModeStatusResponse(
        mode            = current_mode,
        can_live        = can_live,
        gate_reason     = gate_reason,
        broker_scope    = broker_scope,
        config_present  = config_present,
    )


# ─── POST /api/user/mode ───────────────────────────────────────────────────


@router.post("", response_model=ModeFlipResponse)
async def flip_mode(
    req:     ModeFlipRequest,
    request: Request,
    user:    User = Depends(get_current_user),
):
    """Flip the user's trading mode.

    To shadow: always allowed.
    To live:   requires connected Oanda key with scope='trade'.
    """
    ip = _client_ip(request)

    if req.mode == "live":
        # ── Gate 0: workspace must exist (no point asking for OTP if we
        # have nothing to flip). Check the Config.yaml path before the
        # broker/OTP gates.
        if not _user_config_path(user.id).exists():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Your trading workspace is still being set up. This usually "
                    "completes within a minute of signup — refresh and try again."
                ),
            )

        # ── Gate: must have a trade-scope Oanda key ─────────────────────
        scope = _get_broker_scope(user.id)
        if scope is None:
            _log_mode_event(
                user.id, "mode_flip_to_live_blocked",
                detail="no_oanda_key", ip=ip,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Connect a Oanda API key with trading permission first.",
            )
        if scope != "trade":
            _log_mode_event(
                user.id, "mode_flip_to_live_blocked",
                detail=f"scope={scope}", ip=ip,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Your Oanda key is read-only. Generate a new key with "
                    "trading permission and paste it under Settings → Oanda."
                ),
            )

        # ── OTP CONFIRMATION GATE (added 2026-05-22) ─────────────────────
        # Even with a valid trade-scope key, the FIRST shadow→live flip
        # requires an email-delivered 6-digit code. Last "are you sure"
        # before real Oanda orders start flowing.
        if not req.confirmation_code:
            # No code supplied → mint one + email it + return 202-style
            try:
                code = core_auth.create_live_mode_confirmation(user.id)
            except Exception as e:
                logger.error(f"Failed to create live-mode confirmation for user {user.id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Could not start the live-mode confirmation flow. Try again.",
                )
            try:
                email_sender.send_live_mode_confirmation_code(user.email, code)
            except Exception as e:
                logger.warning(f"Failed to email live-mode confirmation code to user {user.id}: {e}")
                # We still return success — email might be in spam, etc.
                # User can request a new code by clicking the button again.
            _log_mode_event(user.id, "live_mode_confirmation_sent", ip=ip)
            return ModeFlipResponse(
                ok=True,
                mode="shadow",  # NOT flipped yet
                confirmation_required=True,
                detail=(
                    "We just emailed you a 6-digit code. Enter it below to "
                    "complete the switch to live mode (valid for 15 minutes)."
                ),
            )

        # Code provided → verify + consume
        ok, reason = core_auth.consume_live_mode_confirmation(user.id, req.confirmation_code)
        if not ok:
            _log_mode_event(
                user.id, "mode_flip_to_live_blocked",
                detail=f"otp_{reason}", ip=ip,
            )
            user_msg = {
                "no_pending":     "No active confirmation code. Click 'Switch to Live' again to send a new one.",
                "expired":        "That code expired. Click 'Switch to Live' again to send a new one.",
                "wrong_code":     "Wrong code. Check the most recent email — codes are 6 digits.",
                "too_many_wrong": "Too many wrong attempts. Click 'Switch to Live' to send a fresh code.",
            }.get(reason, "Code rejected. Please try again.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=user_msg,
            )

        # Code verified — actually flip
        _write_user_shadow_mode(user.id, shadow_mode=False)
        _touch_user_restart_flag(user.id)
        _log_mode_event(user.id, "mode_flipped_to_live", ip=ip)

        # Confirmation email — best effort, never blocks the response
        try:
            email_sender.send_live_mode_activated_email(user.email)
        except Exception as e:
            logger.warning(f"Failed to send live-mode email to user {user.id}: {e}")

        return ModeFlipResponse(ok=True, mode="live")

    else:
        # ── Flip to shadow: unconditional ─────────────────────────────────
        _write_user_shadow_mode(user.id, shadow_mode=True)
        _touch_user_restart_flag(user.id)
        _log_mode_event(user.id, "mode_flipped_to_shadow", ip=ip)
        return ModeFlipResponse(ok=True, mode="shadow")
