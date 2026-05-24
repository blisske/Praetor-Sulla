"""FastAPI router for 2FA TOTP enrollment + management.

Endpoints (all under /api/auth/totp, all require an authenticated session
EXCEPT the login-flow completion endpoint which uses a short-lived
partial-token):

  GET  /api/auth/totp/status                Current 2FA state for the user
  POST /api/auth/totp/enroll/start          Begin enrollment; returns QR + secret
  POST /api/auth/totp/enroll/confirm        Verify a code, finalize enrollment, return recovery codes
  POST /api/auth/totp/disable               Disable 2FA (requires password + TOTP code or recovery code)
  POST /api/auth/totp/regenerate-codes      Reissue recovery codes (requires password + TOTP)

Login flow extension lives in api/auth.py:
  POST /api/auth/login           — when TOTP is enabled, returns 200 with
                                    {totp_required: true, partial_token: ...}
                                    instead of the full JWT
  POST /api/auth/login/totp      — exchange (partial_token, code) for a real JWT
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core import auth as core_auth
from core.auth import User, AuthError
from api.auth import get_current_user, _client_ip
from api.mode import _log_mode_event   # reuses broker_key_events audit table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/totp", tags=["totp"])


# ─── Request / response shapes ─────────────────────────────────────────────


class StatusResponse(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class EnrollStartRequest(BaseModel):
    current_password: str = Field(min_length=1)


class EnrollStartResponse(BaseModel):
    # Authenticator-app-friendly otpauth:// URI for the QR code
    provisioning_uri: str
    # Base32 secret for manual entry (when QR scan isn't an option)
    secret_base32: str
    # PNG bytes encoded as a data: URL — convenient for <img src={...}>
    qr_png_data_url: str


class EnrollConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class EnrollConfirmResponse(BaseModel):
    ok: bool = True
    # 10 plaintext recovery codes — shown ONCE. Server stores hashes only.
    recovery_codes: list[str]


class DisableRequest(BaseModel):
    current_password: str = Field(min_length=1)
    # Either a 6-digit TOTP code OR a recovery code (auto-detected on length)
    code: str = Field(min_length=6, max_length=20)


class RegenerateRequest(BaseModel):
    current_password: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=8)   # TOTP only, not recovery


class RegenerateResponse(BaseModel):
    ok: bool = True
    recovery_codes: list[str]


class OkResponse(BaseModel):
    ok: bool = True
    detail: Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────


def _verify_password_for_user(user: User, password: str) -> None:
    """Raise 401 if the password doesn't match the user's stored hash.
    Centralized so every 'sensitive action' endpoint uses identical
    error text + status code."""
    stored = core_auth.get_user_password_hash(user.id)
    if not stored or not core_auth.verify_password(password, stored):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password incorrect.",
        )


def _verify_totp_or_recovery(user_id: int, code: str) -> str:
    """Verify either a 6-digit TOTP code or a recovery code. Returns the
    string 'totp' or 'recovery' on success, raises 401 otherwise."""
    code_clean = (code or "").strip()
    # 6-digit numeric → TOTP path
    if len(code_clean) == 6 and code_clean.isdigit():
        secret = core_auth.get_user_totp_secret(user_id)
        if secret and core_auth.verify_totp_code(secret, code_clean):
            return "totp"
    # Else try recovery code
    if core_auth.consume_recovery_code(user_id, code_clean):
        return "recovery"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="2FA code did not verify.",
    )


def _qr_png_data_url(uri: str) -> str:
    """Render an otpauth:// URI as a base64-encoded PNG suitable for
    `<img src={data_url}>`. PNG (not SVG) because authenticator apps
    don't care and PNG renders identically across every email client +
    browser without security headers blocking inline SVG.
    """
    import qrcode
    img = qrcode.make(uri, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ─── GET /api/auth/totp/status ─────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(user: User = Depends(get_current_user)):
    """Drives the Settings → Account 2FA section. Surfaces enrolled
    state + remaining recovery-code count."""
    enrolled = core_auth.user_has_totp(user.id)
    remaining = core_auth.count_active_recovery_codes(user.id) if enrolled else 0
    return StatusResponse(enabled=enrolled, recovery_codes_remaining=remaining)


# ─── POST /api/auth/totp/enroll/start ──────────────────────────────────────


@router.post("/enroll/start", response_model=EnrollStartResponse)
async def enroll_start(
    req: EnrollStartRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Begin TOTP enrollment.

    Requires password re-entry (sensitive action). Generates a new secret,
    stashes it as the PENDING enrollment, and returns the provisioning
    URI + QR + secret for the user to scan into their authenticator app.

    Refuses if the user is already enrolled — they must disable first.
    """
    _verify_password_for_user(user, req.current_password)
    if core_auth.user_has_totp(user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is already enabled. Disable it first to re-enroll.",
        )
    secret = core_auth.generate_totp_secret()
    try:
        core_auth.stash_pending_totp_secret(user.id, secret)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    uri = core_auth.totp_provisioning_uri(secret, account_label=user.email)
    _log_mode_event(user.id, "totp_enroll_started", ip=_client_ip(request))
    return EnrollStartResponse(
        provisioning_uri = uri,
        secret_base32    = secret,
        qr_png_data_url  = _qr_png_data_url(uri),
    )


# ─── POST /api/auth/totp/enroll/confirm ────────────────────────────────────


@router.post("/enroll/confirm", response_model=EnrollConfirmResponse)
async def enroll_confirm(
    req: EnrollConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Verify a code from the user's authenticator app + finalize.

    On success, marks the user fully enrolled and returns 10 plaintext
    recovery codes. These are shown ONCE — the server only stores hashes.
    """
    try:
        recovery_codes = core_auth.confirm_totp_enrollment(user.id, req.code.strip())
    except AuthError as e:
        msg = str(e)
        if "did not verify" in msg:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Code did not verify. Check that your device clock is accurate, then try the next code.")
        if "already enrolled" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)
        if "no pending" in msg:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    _log_mode_event(user.id, "totp_enrolled", ip=_client_ip(request))
    return EnrollConfirmResponse(recovery_codes=recovery_codes)


# ─── POST /api/auth/totp/disable ───────────────────────────────────────────


@router.post("/disable", response_model=OkResponse)
async def disable(
    req: DisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Turn off 2FA. Requires the user's password AND either a current
    TOTP code or an unused recovery code — both factors required so a
    stolen password alone can't disable 2FA.

    Once disabled, the secret + recovery codes are wiped. The user can
    re-enroll with a fresh secret at any time.
    """
    _verify_password_for_user(user, req.current_password)
    if not core_auth.user_has_totp(user.id):
        # Idempotent — disabling already-disabled is fine
        return OkResponse(detail="2FA was already off.")
    method = _verify_totp_or_recovery(user.id, req.code)
    core_auth.disable_totp(user.id)
    _log_mode_event(user.id, "totp_disabled", detail=f"verified_via={method}",
                    ip=_client_ip(request))
    return OkResponse(detail="2FA disabled.")


# ─── POST /api/auth/totp/regenerate-codes ──────────────────────────────────


@router.post("/regenerate-codes", response_model=RegenerateResponse)
async def regenerate(
    req: RegenerateRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Wipe + reissue the user's 10 recovery codes. Requires password +
    a current TOTP code (not a recovery code — that'd be circular).

    Used when the user has burned through most of their codes, or thinks
    the originals may have leaked."""
    _verify_password_for_user(user, req.current_password)
    if not core_auth.user_has_totp(user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled.",
        )
    # Recovery codes can't authenticate this operation — must be TOTP
    secret = core_auth.get_user_totp_secret(user.id)
    if not secret or not core_auth.verify_totp_code(secret, req.code.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="2FA code did not verify.",
        )
    try:
        codes = core_auth.regenerate_recovery_codes(user.id)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    _log_mode_event(user.id, "totp_recovery_codes_regenerated",
                    ip=_client_ip(request))
    return RegenerateResponse(recovery_codes=codes)
