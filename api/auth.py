"""FastAPI router for the SaaS multi-tenant auth flow.

Implements the 11 endpoints from SAAS_AUTH_PLAN.md. Lives standalone as
an APIRouter that future versions of api/main.py can include via
``app.include_router(auth_router)``. NOT yet wired into the running
production API — existing single-tenant /api/auth/login keeps working
until we explicitly cut over.

All persistence goes through core.auth helpers against global.db.
Outbound emails go through core.email_sender → Postmark.

Endpoints:

  POST   /api/auth/signup
  POST   /api/auth/login
  POST   /api/auth/logout
  GET    /api/auth/me
  POST   /api/auth/verify-email
  POST   /api/auth/resend-verification
  POST   /api/auth/request-password-reset
  POST   /api/auth/reset-password
  POST   /api/auth/change-password
  POST   /api/auth/change-email
  DELETE /api/auth/account

Auth dependency: ``get_current_user()`` extracts JWT from Authorization
header, looks up user in global.db, returns a User dataclass. Reject
deleted users.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field

# Local imports — pure-Python helpers (no FastAPI dependency)
from core import auth as core_auth
from core import email_sender
from core import provisioner_client
from core.auth import AuthError, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# OAuth2 dep used by all protected endpoints. tokenUrl is informational
# (we accept any valid bearer JWT; the form-encoded login endpoint that
# OAuth2 implies isn't actually how callers will log in — they POST JSON
# to /api/auth/login).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ─── Pydantic request / response shapes ────────────────────────────────────


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    # Both default to False so a client that OMITS the field is treated as
    # NOT having checked the box (safer than the old True default which
    # let missing fields silently bypass the explicit-consent check).
    accepted_terms: bool = False               # "I understand this is not financial advice"
    accepted_risk_acknowledgment: bool = False # "I understand operator risk + experimental status"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    user: dict


class TotpRequiredResponse(BaseModel):
    """Returned by /api/auth/login when the user has 2FA enabled.

    The frontend MUST then call /api/auth/login/totp with the partial
    token + the user's TOTP code (or recovery code) to exchange for a
    real session JWT. The partial token expires in 5 minutes and can
    only be used for the TOTP exchange — its `purpose: "totp_pending"`
    claim is rejected by every other endpoint.
    """
    totp_required: bool = True
    partial_token: str
    # Echo recovery-codes-remaining so the UI can warn near zero
    recovery_codes_remaining: int = 0


class TotpLoginRequest(BaseModel):
    partial_token: str
    code: str = Field(min_length=6, max_length=20)


class UserResponse(BaseModel):
    id: int
    email: str
    email_verified: bool
    is_admin: bool
    created_at: str
    last_login_at: Optional[str] = None
    recovery_email: Optional[str] = None
    # ToS state — populated by _user_to_response_dict from the
    # tos_acceptances table. tos_needs_reaccept drives the
    # re-acceptance modal in the frontend.
    tos_version_accepted: Optional[str] = None
    tos_version_current:  Optional[str] = None
    tos_needs_reaccept:   bool = False
    # 2FA state — True once the user has completed TOTP enrollment
    totp_enabled:         bool = False


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class ResendVerificationResponse(BaseModel):
    ok: bool


class RequestResetRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    current_password: str


class DeleteAccountRequest(BaseModel):
    current_password: str
    confirmation_email: EmailStr   # user must re-type their email


class OkResponse(BaseModel):
    ok: bool = True


# ─── Helpers ───────────────────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    """Extract the original client IP. Prefer X-Forwarded-For (Traefik
    sets it); fall back to direct connection IP."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First entry is the original client; later entries are proxies
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _user_to_response_dict(user: User) -> dict:
    """Build the standard user payload shipped to the frontend.

    Includes ToS state so the frontend can surface a re-acceptance
    prompt when current_tos_version drifts from the user's last
    accepted version (e.g. operator bumped the doc after they signed up).
    """
    latest_tos = core_auth.get_latest_tos_acceptance(user.id)
    return {
        "id":               user.id,
        "email":            user.email,
        "email_verified":   user.email_verified,
        "is_admin":         user.is_admin,
        "created_at":       user.created_at,
        "last_login_at":    user.last_login_at,
        "recovery_email":   user.recovery_email,
        "tos_version_accepted": latest_tos,
        "tos_version_current":  core_auth.CURRENT_TOS_VERSION,
        "tos_needs_reaccept":   latest_tos != core_auth.CURRENT_TOS_VERSION,
        "totp_enabled":         core_auth.user_has_totp(user.id),
    }


# ─── Auth dependency ───────────────────────────────────────────────────────


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    authorization: Optional[str] = Header(None),
) -> User:
    """FastAPI dependency: decode JWT, load user, return User dataclass.

    Tries OAuth2 dependency first (for tools that expect that pattern),
    falls back to raw Authorization: Bearer <token> header (for typical
    SPA clients).

    Raises 401 on any failure.
    """
    # Resolve the actual token bytes from either source
    bearer = token
    if not bearer and authorization:
        if authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = core_auth.decode_jwt(bearer)
    except AuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Reject partial-purpose tokens (e.g. totp_pending) — they were minted
    # by /api/auth/login as a one-shot handoff to /api/auth/login/totp and
    # must not authenticate any normal session endpoint. Without this
    # check, a 2FA-enrolled user could skip the TOTP step.
    if claims.get("purpose"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This token can't be used for general access.",
        )

    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad token claims")

    user = core_auth.get_user_by_id(user_id)
    if not user:
        # User soft-deleted or never existed — same as invalid token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or deleted",
        )
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ─── Endpoint: signup ──────────────────────────────────────────────────────


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest, request: Request):
    """Create a new user account, send verification email, issue JWT.

    Returns 201 + {token, user}. Caller stores token in localStorage.

    Note: provisioner-side container spinup is NOT triggered here.
    Future integration with provisioner_daemon (per SAAS_PROVISIONER_PLAN.md)
    happens via a separate flag-file drop after this endpoint returns.
    """
    ip = _client_ip(request)

    if not req.accepted_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must accept the terms to create an account.",
        )
    if not req.accepted_risk_acknowledgment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must acknowledge the trading + operator risk to create an account.",
        )

    if not core_auth.signup_limiter.allow(f"ip:{ip}"):
        retry = core_auth.signup_limiter.retry_after(f"ip:{ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup attempts from this IP. Please wait.",
            headers={"Retry-After": str(retry)},
        )

    email = req.email.lower().strip()

    # Pre-flight check for existing email — gives a clean 409 rather than
    # a DB IntegrityError later.
    existing = core_auth.get_user_by_email(email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists. Try logging in or resetting your password.",
        )

    try:
        user_id = core_auth.create_user(email, req.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except sqlite3.IntegrityError:
        # Race condition: someone signed up with the same email between
        # the pre-flight and our INSERT. Translate to 409.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    # Send verification email — best-effort; signup succeeds even if
    # Postmark hiccups (user can request resend later).
    try:
        token = core_auth.create_email_verification(user_id)
        email_result = email_sender.send_verify_email(email, token)
        if not email_result["ok"]:
            logger.warning(f"Failed to send verify email to {email}: {email_result.get('error')}")
    except Exception as exc:
        logger.warning(f"Email verification setup failed for user {user_id}: {exc}")

    # Issue JWT immediately — user can use the dashboard while waiting
    # to verify their email.
    user = core_auth.get_user_by_id(user_id)
    jwt_token = core_auth.create_jwt(
        user_id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        is_admin=user.is_admin,
    )

    core_auth.record_login_attempt(email, ip, "signup_success")

    # Record their ToS + risk-acknowledgment acceptance for the audit
    # trail. Best-effort: failures don't block signup.
    core_auth.record_tos_acceptance(
        user_id=user.id,
        version=core_auth.CURRENT_TOS_VERSION,
        ip=ip,
    )

    # Provisioning leg — initialize the user's data dir + drop a flag for
    # the host-side provisioner daemon to spin up their engine container.
    # Best-effort: failures here are LOGGED but don't fail the signup;
    # the dashboard's onboarding banner will surface "engine not yet
    # ready" via the heartbeat poll path, and operator can investigate
    # via /admin/provisioner.
    try:
        provisioner_client.initialize_user_dir(user.id)
        provisioner_client.enqueue_provision(user.id)
        logger.info(f"Provisioning enqueued for user_id={user.id}")
    except Exception as exc:
        # ProvisionerError (missing template) OR generic IO error.
        # Don't fail the signup — user has account + JWT; engine can be
        # provisioned manually by operator from /admin/users/{id} if needed.
        logger.warning(f"Provisioning failed for user_id={user.id}: {exc}")

    return {
        "token": jwt_token,
        "user":  _user_to_response_dict(user),
    }


# ─── Endpoint: login ───────────────────────────────────────────────────────


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    """Verify email + password, issue JWT.

    Includes opportunistic bcrypt → argon2 rehash on success: if the
    stored hash uses the deprecated bcrypt scheme (operator's legacy
    hash, or pre-migration data), it gets re-hashed with argon2 on
    successful login.
    """
    ip = _client_ip(request)
    email = req.email.lower().strip()
    limit_key = f"{email}:{ip}"

    if not core_auth.login_limiter.allow(limit_key):
        retry = core_auth.login_limiter.retry_after(limit_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {retry} seconds.",
            headers={"Retry-After": str(retry)},
        )

    user = core_auth.get_user_by_email(email)
    # Consistent 401 regardless of "no such user" or "wrong password" —
    # don't leak which case it is
    bad_creds_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )
    if not user:
        core_auth.record_login_attempt(email, ip, "no_such_user")
        raise bad_creds_exception

    stored_hash = core_auth.get_user_password_hash(user.id)
    if not stored_hash or not core_auth.verify_password(req.password, stored_hash):
        core_auth.record_login_attempt(email, ip, "wrong_password")
        raise bad_creds_exception

    # Auth succeeded — clear the rate limit counter, log success
    core_auth.login_limiter.reset(limit_key)
    core_auth.record_login_attempt(email, ip, "success")
    core_auth.update_last_login(user.id)

    # Opportunistic re-hash if stored hash is deprecated (bcrypt → argon2)
    if core_auth.password_needs_rehash(stored_hash):
        try:
            new_hash = core_auth.hash_password(req.password)
            core_auth.update_user_password_hash(user.id, new_hash)
            logger.info(f"Migrated bcrypt → argon2 hash for user_id={user.id}")
        except Exception as exc:
            logger.warning(f"Hash migration failed for user_id={user.id}: {exc}")

    # Re-fetch with the updated last_login_at
    user = core_auth.get_user_by_id(user.id)

    # ── TOTP gate ─────────────────────────────────────────────────────────
    # If the user has 2FA enrolled, password-success does NOT issue a
    # session JWT. Instead we mint a short-lived "partial token" that
    # only /api/auth/login/totp will accept, and require the user to
    # submit a TOTP code (or recovery code) to complete the login.
    if core_auth.user_has_totp(user.id):
        partial = core_auth.create_jwt(
            user_id        = user.id,
            email          = user.email,
            email_verified = user.email_verified,
            is_admin       = user.is_admin,
            expires_delta  = timedelta(minutes=5),
            extra_claims   = {"purpose": "totp_pending"},
        )
        remaining = core_auth.count_active_recovery_codes(user.id)
        return TotpRequiredResponse(
            partial_token            = partial,
            recovery_codes_remaining = remaining,
        )

    jwt_token = core_auth.create_jwt(
        user_id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        is_admin=user.is_admin,
    )

    return {
        "token": jwt_token,
        "user":  _user_to_response_dict(user),
    }


# ─── Endpoint: login/totp (2FA second step) ───────────────────────────────


@router.post("/login/totp", response_model=TokenResponse)
async def login_totp(req: TotpLoginRequest, request: Request):
    """Step 2 of 2FA login: exchange (partial_token, code) → full JWT.

    The partial_token was issued by /api/auth/login when the user has
    2FA enabled. It carries the user's identity but a `purpose:
    "totp_pending"` claim that every other endpoint refuses to honor.

    `code` may be either a 6-digit TOTP code OR an unused recovery code.
    Recovery codes are single-use — consuming one decrements the user's
    remaining count.
    """
    ip = _client_ip(request)
    try:
        claims = core_auth.decode_jwt(req.partial_token)
    except core_auth.AuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login session expired. Sign in again.",
        )
    if claims.get("purpose") != "totp_pending":
        # Don't let arbitrary session JWTs upgrade themselves here
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login session.",
        )
    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid login session.")
    user = core_auth.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Account not found.")
    if not core_auth.user_has_totp(user.id):
        # Edge case: user disabled 2FA between login() and login_totp().
        # Just refuse and tell them to start over — they don't need TOTP now.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="2FA was disabled. Sign in again.")

    # Apply per-(email,ip) login limiter to the TOTP step too so brute-
    # forcing the 6-digit code is gated the same way as password retry.
    limit_key = f"{user.email}:{ip}:totp"
    if not core_auth.login_limiter.allow(limit_key):
        retry = core_auth.login_limiter.retry_after(limit_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many 2FA attempts. Try again in {retry} seconds.",
            headers={"Retry-After": str(retry)},
        )

    code_clean = req.code.strip()
    method = None
    # 6-digit numeric → TOTP path
    if len(code_clean) == 6 and code_clean.isdigit():
        secret = core_auth.get_user_totp_secret(user.id)
        if secret and core_auth.verify_totp_code(secret, code_clean):
            method = "totp"
    if method is None:
        if core_auth.consume_recovery_code(user.id, code_clean):
            method = "recovery"
    if method is None:
        core_auth.record_login_attempt(user.email, ip, "totp_wrong_code")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Code did not verify.")

    core_auth.login_limiter.reset(limit_key)
    core_auth.record_login_attempt(user.email, ip, f"totp_success_via_{method}")

    jwt_token = core_auth.create_jwt(
        user_id        = user.id,
        email          = user.email,
        email_verified = user.email_verified,
        is_admin       = user.is_admin,
    )
    return {
        "token": jwt_token,
        "user":  _user_to_response_dict(user),
    }


# ─── Endpoint: logout ─────────────────────────────────────────────────────


@router.post("/logout", response_model=OkResponse)
async def logout(user: User = Depends(get_current_user)):
    """No-op server-side at v1. Client drops the JWT from localStorage.

    Future v2: add a `revoked_tokens` table keyed on jti and check
    on every authenticated request. For now, the token remains
    technically valid until its exp; mitigation is the short
    JWT_EXPIRY_DAYS (default 7).
    """
    logger.info(f"User {user.id} logged out (client-side token discard)")
    return {"ok": True}


# ─── Endpoint: me ──────────────────────────────────────────────────────────


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    """Return the current user's profile. Frontend calls this on app boot
    to refresh user state after browser reload."""
    return _user_to_response_dict(user)


# ─── Endpoint: verify-email ────────────────────────────────────────────────


@router.post("/verify-email", response_model=OkResponse)
async def verify_email(req: VerifyEmailRequest, request: Request):
    """Consume a verification token and mark the user's email verified.

    Public endpoint (no auth) — token IS the credential.
    """
    ip = _client_ip(request)
    if not core_auth.verification_apply_lim.allow(f"ip:{ip}"):
        retry = core_auth.verification_apply_lim.retry_after(f"ip:{ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification attempts from this IP.",
            headers={"Retry-After": str(retry)},
        )

    user_id = core_auth.consume_email_verification(req.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or already-used verification link. Request a new one from Settings.",
        )

    core_auth.mark_email_verified(user_id)
    return {"ok": True}


# ─── Endpoint: resend-verification ─────────────────────────────────────────


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(user: User = Depends(get_current_user)):
    """Generate a fresh verification token and send the verify email again.

    Rate-limited per user_id. If user is already verified, returns
    {ok: true} without sending (idempotent UX).
    """
    if user.email_verified:
        return {"ok": True}

    if not core_auth.verification_resend_lim.allow(f"user:{user.id}"):
        retry = core_auth.verification_resend_lim.retry_after(f"user:{user.id}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification email resends. Try again later.",
            headers={"Retry-After": str(retry)},
        )

    token = core_auth.create_email_verification(user.id)
    email_result = email_sender.send_verify_email(user.email, token)
    if not email_result["ok"]:
        logger.warning(f"Resend verify failed for user {user.id}: {email_result.get('error')}")
        # Still return ok — the token IS created in DB; the user can
        # request another resend if email genuinely didn't arrive.
    return {"ok": True}


# ─── Endpoint: request-password-reset ──────────────────────────────────────


@router.post("/request-password-reset", response_model=OkResponse)
async def request_password_reset(req: RequestResetRequest, request: Request):
    """Send a reset email if the address exists.

    ALWAYS returns 200 {ok: true} regardless of whether the email
    exists — anti-enumeration. The only way to know if an email is
    registered is to actually have access to that inbox.
    """
    email = req.email.lower().strip()

    # Rate limit per email (not per IP, to prevent a single attacker
    # spraying many different emails from the same IP)
    if not core_auth.password_reset_req_lim.allow(f"email:{email}"):
        # Still return ok (don't reveal anything)
        return {"ok": True}

    user = core_auth.get_user_by_email(email)
    if user:
        token = core_auth.create_password_reset(user.id)
        email_result = email_sender.send_password_reset_email(user.email, token)
        if not email_result["ok"]:
            logger.warning(f"Failed to send reset email to {email}: {email_result.get('error')}")

    return {"ok": True}


# ─── Endpoint: reset-password ──────────────────────────────────────────────


@router.post("/reset-password", response_model=OkResponse)
async def reset_password(req: ResetPasswordRequest, request: Request):
    """Consume a reset token + set new password."""
    ip = _client_ip(request)
    if not core_auth.password_reset_apply_lim.allow(f"ip:{ip}"):
        retry = core_auth.password_reset_apply_lim.retry_after(f"ip:{ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reset attempts.",
            headers={"Retry-After": str(retry)},
        )

    user_id = core_auth.consume_password_reset(req.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or already-used reset link. Request a new one.",
        )

    try:
        new_hash = core_auth.hash_password(req.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    core_auth.update_user_password_hash(user_id, new_hash)

    # Send security notification — "your password was just changed"
    user = core_auth.get_user_by_id(user_id)
    if user:
        try:
            email_sender.send_password_changed_notification(user.email)
        except Exception:
            pass  # Don't fail the reset if notification can't send

    return {"ok": True}


# ─── Endpoint: change-password (authenticated) ─────────────────────────────


@router.post("/change-password", response_model=OkResponse)
async def change_password(req: ChangePasswordRequest, user: User = Depends(get_current_user)):
    """Change password for the logged-in user. Requires current password."""
    stored_hash = core_auth.get_user_password_hash(user.id)
    if not stored_hash or not core_auth.verify_password(req.current_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )

    try:
        new_hash = core_auth.hash_password(req.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if core_auth.verify_password(req.new_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from current password.",
        )

    core_auth.update_user_password_hash(user.id, new_hash)
    try:
        email_sender.send_password_changed_notification(user.email)
    except Exception:
        pass
    return {"ok": True}


# ─── Endpoint: change-email (authenticated) ────────────────────────────────


@router.post("/change-email", response_model=OkResponse)
async def change_email(req: ChangeEmailRequest, user: User = Depends(get_current_user)):
    """Change the user's email. Resets email_verified flag; sends:
       - verification email to NEW address
       - notification email to OLD address (security best-practice)
    """
    stored_hash = core_auth.get_user_password_hash(user.id)
    if not stored_hash or not core_auth.verify_password(req.current_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )

    new_email = req.new_email.lower().strip()
    if new_email == user.email.lower().strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must differ from current email.",
        )

    existing = core_auth.get_user_by_email(new_email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    old_email = user.email
    try:
        core_auth.update_user_email(user.id, new_email)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already taken.")

    # Send verification to new address
    try:
        verify_token = core_auth.create_email_verification(user.id)
        email_sender.send_verify_email(new_email, verify_token)
    except Exception as exc:
        logger.warning(f"Verify email failed after email change for user {user.id}: {exc}")

    # Notify old address
    try:
        email_sender.send_email_changed_notification(old_email, new_email)
    except Exception:
        pass

    return {"ok": True}


# ─── Endpoint: accept ToS (authenticated, idempotent) ─────────────────────


class AcceptTosRequest(BaseModel):
    version: str = Field(min_length=1, max_length=32)


@router.post("/accept-tos", response_model=OkResponse)
async def accept_tos(
    req:     AcceptTosRequest,
    request: Request,
    user:    User = Depends(get_current_user),
):
    """Record acceptance of a specific ToS version.

    Usually called by the frontend when the user clicks "I accept" on a
    re-acceptance modal after the operator bumped CURRENT_TOS_VERSION.
    Also wired into the signup flow (which uses record_tos_acceptance
    directly to avoid an extra round-trip).

    Idempotent — re-posting the same version just appends another row
    to tos_acceptances (audit-friendly; we'd rather have N rows than
    risk losing the most recent one).

    Refuses to accept a version that doesn't match the current server
    version, to prevent a stale client from silently re-accepting an
    outdated copy. Forces the client to refresh + read the latest doc.
    """
    if req.version != core_auth.CURRENT_TOS_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"That ToS version ({req.version}) is not the current one "
                f"({core_auth.CURRENT_TOS_VERSION}). Reload the page to see "
                f"the latest terms."
            ),
        )

    ip = _client_ip(request)
    core_auth.record_tos_acceptance(
        user_id = user.id,
        version = req.version,
        ip      = ip,
    )
    logger.info(f"User {user.id} accepted ToS version {req.version}")
    return OkResponse(ok=True, detail=f"Accepted ToS version {req.version}")


# ─── Endpoint: account delete (authenticated) ──────────────────────────────


@router.delete("/account", response_model=OkResponse)
async def delete_account(req: DeleteAccountRequest, user: User = Depends(get_current_user)):
    """Soft-delete the user's account. Requires password + email-confirmation."""
    if req.confirmation_email.lower().strip() != user.email.lower().strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation email does not match your account email.",
        )

    stored_hash = core_auth.get_user_password_hash(user.id)
    if not stored_hash or not core_auth.verify_password(req.current_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password is incorrect.",
        )

    # Future: drop a .teardown flag for the provisioner to spin down the
    # user's engine container (per SAAS_PROVISIONER_PLAN.md). For now,
    # just soft-delete the row.
    core_auth.soft_delete_user(user.id)
    logger.info(f"User {user.id} ({user.email}) soft-deleted")
    return {"ok": True}
