"""Auth primitives for the multi-tenant SaaS conversion.

Pure-Python helpers (no FastAPI imports). Lives independent of api/ so it
can be reused by the engine container if needed.

Three concerns covered here:

  1. Password hashing — argon2id default, bcrypt accepted for migration
  2. JWT encoding / decoding — HS256, matches existing api/main.py
     conventions so tokens issued by the new flow work in the legacy
     code paths during transition
  3. DB helpers — users, email_verifications, password_resets,
     login_attempts (all in global.db)

See SAAS_AUTH_PLAN.md for the design rationale.
"""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)


# ─── Config ─────────────────────────────────────────────────────────────────

# JWT — match existing api/main.py conventions so tokens are interchangeable
# during the single-tenant → multi-tenant transition. Same secret, same alg.
JWT_SECRET_KEY = os.getenv("API_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))

# DB path — defaults to /app/foundation/global.db inside containers; ~/swarm/ionic/data/global.db on host
GLOBAL_DB_PATH = os.getenv("GLOBAL_DB_PATH", "/app/foundation/global.db")

# Password hashing — argon2 is the modern default; bcrypt accepted for legacy
# operator (blisske) whose existing password lives in env as bcrypt hash.
# CryptContext.verify() accepts BOTH; CryptContext.hash() produces argon2;
# CryptContext.needs_update() returns True for bcrypt → callers should
# opportunistically re-hash on login to migrate.
#
# argon2 params via passlib defaults are reasonable (memory_cost=65536KiB,
# time_cost=2, parallelism=8). Override if you need to.
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

# Token format for email verification + password reset
TOKEN_BYTES = 32  # → 43-char URL-safe base64 string after token_urlsafe()
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)

# ─── Terms of Service version ──────────────────────────────────────────
# Bump this string whenever the actual TERMS_OF_SERVICE.md document
# changes in a way that materially affects user rights/obligations
# (limitation of liability, dispute resolution, data handling, etc.).
# When this differs from a user's latest accepted version, the
# frontend surfaces a re-acceptance prompt — they keep using the
# product but get blocked from changing their broker/mode/risk
# settings until they re-accept.
#
# Format: YYYY-MM-DD of the doc revision. Semver works too if you
# prefer ("1.0.0", "1.1.0"); the field is just a text comparison.
CURRENT_TOS_VERSION = os.getenv("CURRENT_TOS_VERSION", "2026-05-22")


# ─── Data classes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class User:
    """In-memory representation of a user row from global.db.

    Excludes password_hash by design — handlers that need to verify a
    password should call ``verify_user_password()`` explicitly.
    """
    id: int
    email: str
    email_verified: bool
    is_admin: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str]
    deleted_at: Optional[str]
    recovery_email: Optional[str]


class AuthError(Exception):
    """Raised for auth-related programming errors (not for user-facing 401s)."""


# ─── Password hashing ──────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id (current default scheme).

    Raises:
        AuthError: If password is empty/None or too long (passlib refuses
            argon2 passwords above ~4KB).
    """
    if not password or not isinstance(password, str):
        raise AuthError("password must be a non-empty string")
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash.

    Accepts both argon2 and bcrypt hashes. Returns False on any
    verification failure (including malformed hash); does not raise.
    """
    if not password or not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        # passlib raises ValueError on malformed hashes; treat as auth fail
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True if hash uses a deprecated scheme and should be re-hashed on next login.

    Caller pattern:
        if verify_password(password, h) and password_needs_rehash(h):
            update_user_password_hash(user_id, hash_password(password))
    """
    if not password_hash:
        return False
    try:
        return pwd_context.needs_update(password_hash)
    except Exception:
        return False


# ─── JWT ────────────────────────────────────────────────────────────────────


def create_jwt(
    user_id: int,
    email: str,
    email_verified: bool,
    is_admin: bool,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """Issue a JWT for the given user.

    Claims:
        sub               user_id (as string per JWT spec convention)
        email             user's email
        email_verified    bool
        is_admin          bool
        iat               issued-at (utc unix seconds)
        exp               expiry (utc unix seconds)

    Any keys in ``extra_claims`` are merged into the JWT payload. Caller
    is responsible for not stomping reserved claim names (sub, iat, exp,
    etc.) — this function does NOT enforce that. Use for ad-hoc flags
    like ``{"is_demo": True}`` minted by the public /api/demo/login route.

    Default expiry is JWT_EXPIRY_DAYS (env-configurable, default 7).
    """
    if expires_delta is None:
        expires_delta = timedelta(days=JWT_EXPIRY_DAYS)
    now = datetime.now(timezone.utc)
    claims = {
        "sub":            str(user_id),
        "email":          email,
        "email_verified": bool(email_verified),
        "is_admin":       bool(is_admin),
        "iat":            int(now.timestamp()),
        "exp":            int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode + verify a JWT. Raises AuthError on any failure.

    Returns the claims dict on success. Caller is responsible for
    further validation (e.g., looking up the user by sub).
    """
    if not token:
        raise AuthError("empty token")
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc


# ─── Random token generation (verification, reset) ─────────────────────────


def generate_token() -> str:
    """Cryptographically random URL-safe token for email links.

    32 random bytes → 43-char base64 string. Sufficient entropy (2^256)
    that even an exhaustive scan of the token space takes forever.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


# ─── DB connection ─────────────────────────────────────────────────────────


@contextmanager
def db_connect(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection to global.db. Foreign keys + Row factory on.

    Caller is responsible for commit; close happens automatically.
    Use as: ``with db_connect() as conn: ...``
    """
    path = db_path or GLOBAL_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


# ─── User helpers ──────────────────────────────────────────────────────────


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id              = row["id"],
        email           = row["email"],
        email_verified  = bool(row["email_verified"]),
        is_admin        = bool(row["is_admin"]),
        created_at      = row["created_at"],
        updated_at      = row["updated_at"],
        last_login_at   = row["last_login_at"],
        deleted_at      = row["deleted_at"],
        recovery_email  = row["recovery_email"],
    )


def get_user_by_id(user_id: int, *, include_deleted: bool = False, db_path: Optional[str] = None) -> Optional[User]:
    with db_connect(db_path) as conn:
        if include_deleted:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str, *, include_deleted: bool = False, db_path: Optional[str] = None) -> Optional[User]:
    """Case-insensitive lookup. Returns None for non-existent or (default) deleted users."""
    if not email:
        return None
    with db_connect(db_path) as conn:
        if include_deleted:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
                (email.strip(),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE AND deleted_at IS NULL",
                (email.strip(),),
            ).fetchone()
    return _row_to_user(row) if row else None


def create_user(email: str, password: str, *, is_admin: bool = False, db_path: Optional[str] = None) -> int:
    """Insert a new user. Returns new user_id.

    Raises sqlite3.IntegrityError if email already exists (caller should
    catch and translate to 409 / "account exists" message).
    """
    if not email or "@" not in email:
        raise AuthError("invalid email")
    if len(password) < 12:
        raise AuthError("password too short (min 12)")

    password_hash = hash_password(password)
    with db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
            (email.strip(), password_hash, 1 if is_admin else 0),
        )
        conn.commit()
        return cur.lastrowid


def update_user_password_hash(user_id: int, new_hash: str, *, db_path: Optional[str] = None) -> None:
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_hash, user_id),
        )
        conn.commit()


def update_user_email(user_id: int, new_email: str, *, db_path: Optional[str] = None) -> None:
    """Change a user's email + clear email_verified flag (they re-verify the new address)."""
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET email = ?, email_verified = 0, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_email.strip(), user_id),
        )
        conn.commit()


def mark_email_verified(user_id: int, *, db_path: Optional[str] = None) -> None:
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET email_verified = 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        conn.commit()


def update_last_login(user_id: int, *, db_path: Optional[str] = None) -> None:
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        conn.commit()


def soft_delete_user(user_id: int, *, db_path: Optional[str] = None) -> None:
    """Mark a user as deleted. Rename email to <id>@deleted.foundationbots.com
    so the original email is free for reuse.
    """
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET deleted_at = CURRENT_TIMESTAMP, "
            "email = ? WHERE id = ?",
            (f"{user_id}@deleted.foundationbots.com", user_id),
        )
        conn.commit()


def get_user_password_hash(user_id: int, *, db_path: Optional[str] = None) -> Optional[str]:
    """Returns the stored password hash for verify_password() comparisons.

    Kept separate from get_user_by_id() so the User dataclass never
    carries the hash around in memory.
    """
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
    return row["password_hash"] if row else None


# ─── Email verification tokens ─────────────────────────────────────────────


def create_email_verification(user_id: int, *, db_path: Optional[str] = None) -> str:
    """Generate + store a verification token for the user. Returns the token string."""
    token = generate_token()
    expires_at = datetime.now(timezone.utc) + EMAIL_VERIFICATION_TTL
    with db_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO email_verifications (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at.isoformat()),
        )
        conn.commit()
    return token


def consume_email_verification(token: str, *, db_path: Optional[str] = None) -> Optional[int]:
    """Validate + consume a verification token. Returns user_id on success.

    Returns None for unknown/expired/already-consumed tokens. Single-use:
    marks the token consumed before returning.
    """
    if not token:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, consumed_at FROM email_verifications WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["consumed_at"] is not None:
            return None  # already used
        if row["expires_at"] < now_iso:
            return None  # expired
        conn.execute(
            "UPDATE email_verifications SET consumed_at = ? WHERE token = ?",
            (now_iso, token),
        )
        conn.commit()
        return row["user_id"]


# ─── Password reset tokens ─────────────────────────────────────────────────


def create_password_reset(user_id: int, *, db_path: Optional[str] = None) -> str:
    token = generate_token()
    expires_at = datetime.now(timezone.utc) + PASSWORD_RESET_TTL
    with db_connect(db_path) as conn:
        conn.execute(
            "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at.isoformat()),
        )
        conn.commit()
    return token


def consume_password_reset(token: str, *, db_path: Optional[str] = None) -> Optional[int]:
    """Validate + consume a password reset token. Returns user_id on success."""
    if not token:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, consumed_at FROM password_resets WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["consumed_at"] is not None:
            return None
        if row["expires_at"] < now_iso:
            return None
        conn.execute(
            "UPDATE password_resets SET consumed_at = ? WHERE token = ?",
            (now_iso, token),
        )
        conn.commit()
        return row["user_id"]


# ─── Terms of Service acceptance tracking ──────────────────────────────────


def record_tos_acceptance(
    user_id: int,
    version: str,
    ip: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> None:
    """Append a row to tos_acceptances. One row per acceptance — signup
    creates the first row; every re-accept (after a version bump) adds
    another. Never deleted (audit trail).

    Never raises — failures are logged and swallowed so a transient DB
    issue doesn't block signup.
    """
    try:
        with db_connect(db_path) as conn:
            conn.execute(
                "INSERT INTO tos_acceptances (user_id, tos_version, ip) VALUES (?, ?, ?)",
                (user_id, version[:32], (ip or "")[:64] or None),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record ToS acceptance for user {user_id}: {e}")


def get_latest_tos_acceptance(
    user_id: int,
    *,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Return the most recent tos_version accepted by this user, or None
    if they never accepted (shouldn't happen in normal signup flow but
    handles the legacy/seeded user case)."""
    try:
        with db_connect(db_path) as conn:
            row = conn.execute(
                "SELECT tos_version FROM tos_acceptances "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row["tos_version"] if row else None
    except Exception as e:
        logger.warning(f"Failed to read latest ToS for user {user_id}: {e}")
        return None


def user_needs_tos_reaccept(
    user_id: int,
    *,
    db_path: Optional[str] = None,
) -> bool:
    """True if user's last-accepted ToS version differs from current.

    Returns True for users with NO acceptance on record (seeded users,
    operator, demo — they should re-accept the live ToS on next login).
    """
    latest = get_latest_tos_acceptance(user_id, db_path=db_path)
    return latest != CURRENT_TOS_VERSION


# ─── Live-mode confirmation codes (6-digit OTP) ────────────────────────────


LIVE_MODE_CONFIRMATION_TTL = timedelta(minutes=15)
LIVE_MODE_MAX_ATTEMPTS     = 5


def _generate_6_digit_code() -> str:
    """6-digit zero-padded numeric code. Cryptographically random."""
    n = int.from_bytes(secrets.token_bytes(3), "big") % 1_000_000
    return f"{n:06d}"


def create_live_mode_confirmation(user_id: int, *, db_path: Optional[str] = None) -> str:
    """Generate + persist a fresh confirmation code for the user.

    If a non-consumed code already exists for this user, it's
    invalidated first (we mark it consumed so it can't be used).
    Returns the plaintext 6-digit code so the caller can email it.

    No rate limit here — the caller (api.mode.flip_to_live) is
    responsible for that.
    """
    code = _generate_6_digit_code()
    expires_at = datetime.now(timezone.utc) + LIVE_MODE_CONFIRMATION_TTL
    with db_connect(db_path) as conn:
        # Invalidate any existing unconsumed codes for this user
        conn.execute(
            "UPDATE live_mode_confirmations SET consumed_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO live_mode_confirmations (user_id, code, expires_at, attempts_remaining) "
            "VALUES (?, ?, ?, ?)",
            (user_id, code, expires_at.isoformat(), LIVE_MODE_MAX_ATTEMPTS),
        )
        conn.commit()
    return code


def consume_live_mode_confirmation(user_id: int, code: str, *, db_path: Optional[str] = None) -> tuple[bool, str]:
    """Verify + consume a confirmation code.

    Returns (success, reason). On success the row is marked consumed.
    On failure with attempts_remaining > 1 the count is decremented
    (lets the user retry); on the last bad attempt the row is
    invalidated outright (user has to request a new code).

    Possible reasons:
      'ok'              → success
      'no_pending'      → no unconsumed code for this user
      'expired'         → code exists but past expires_at
      'wrong_code'      → code mismatch + attempts remain
      'too_many_wrong'  → code mismatch + no attempts left (invalidated)
    """
    if not code or not isinstance(code, str):
        return (False, "wrong_code")
    code = code.strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, code, expires_at, attempts_remaining "
            "FROM live_mode_confirmations "
            "WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return (False, "no_pending")
        if row["expires_at"] < now_iso:
            # Invalidate the expired code so the next request creates a fresh one
            conn.execute(
                "UPDATE live_mode_confirmations SET consumed_at = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
            conn.commit()
            return (False, "expired")
        if row["code"] == code:
            conn.execute(
                "UPDATE live_mode_confirmations SET consumed_at = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
            conn.commit()
            return (True, "ok")
        # Wrong code — decrement attempts
        remaining = row["attempts_remaining"] - 1
        if remaining <= 0:
            conn.execute(
                "UPDATE live_mode_confirmations SET consumed_at = ?, attempts_remaining = 0 "
                "WHERE id = ?",
                (now_iso, row["id"]),
            )
            conn.commit()
            return (False, "too_many_wrong")
        conn.execute(
            "UPDATE live_mode_confirmations SET attempts_remaining = ? WHERE id = ?",
            (remaining, row["id"]),
        )
        conn.commit()
        return (False, "wrong_code")


# ─── Login attempt logging ─────────────────────────────────────────────────


def record_login_attempt(username: str, ip: str, result: str, *, db_path: Optional[str] = None) -> None:
    """Audit log every login attempt. Never raises (failures are swallowed)."""
    try:
        with db_connect(db_path) as conn:
            conn.execute(
                "INSERT INTO login_attempts (username, ip, result) VALUES (?, ?, ?)",
                (username[:200] if username else None, ip[:64] if ip else None, result[:32] if result else None),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record login attempt: {e}")


# ─── In-memory rate limiter ─────────────────────────────────────────────────


class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Per-process state — counters reset on container restart, which is
    acceptable at F&F scale (worst case: legitimate user gets an extra
    chance a few seconds early).

    Usage:
        login_limiter = RateLimiter(max_attempts=5, window_sec=300)
        if not login_limiter.allow(f"{email}:{ip}"):
            raise HTTPException(429, ...)
    """

    def __init__(self, max_attempts: int, window_sec: int):
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        """Record an attempt for `key` and return True if it's within the limit.

        Returns False if the attempt exceeds the threshold.
        """
        now = time.time()
        cutoff = now - self.window_sec
        history = self._attempts.get(key, [])
        # Prune timestamps older than the window
        history = [t for t in history if t > cutoff]
        if len(history) >= self.max_attempts:
            self._attempts[key] = history
            return False
        history.append(now)
        self._attempts[key] = history
        return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest in-window attempt expires; suitable for Retry-After header."""
        history = self._attempts.get(key, [])
        if not history:
            return 0
        return max(0, int(history[0] + self.window_sec - time.time()))

    def reset(self, key: str) -> None:
        """Clear attempts for a key — call on successful login."""
        self._attempts.pop(key, None)


# Rate-limiter instances. Defaults are conservative; the most-likely-to-
# trip-legit-users ones (login + signup) are env-tunable so the operator
# can loosen them during a promo or tighten during abuse — no rebuild
# needed, just restart ionic-api.
#
# Window is in SECONDS. Both vars take effect the next time the api
# container starts (limiter is per-process, in-memory).
LOGIN_RATE_LIMIT          = int(os.getenv("AUTH_LOGIN_RATE_LIMIT",         "5"))
LOGIN_RATE_WINDOW_SEC     = int(os.getenv("AUTH_LOGIN_RATE_WINDOW_SEC",    "300"))    # 5 min
SIGNUP_RATE_LIMIT         = int(os.getenv("AUTH_SIGNUP_RATE_LIMIT",        "10"))     # bumped from 3 (2026-05-22)
SIGNUP_RATE_WINDOW_SEC    = int(os.getenv("AUTH_SIGNUP_RATE_WINDOW_SEC",   "3600"))   # 1 hr

login_limiter            = RateLimiter(max_attempts=LOGIN_RATE_LIMIT,  window_sec=LOGIN_RATE_WINDOW_SEC)
signup_limiter           = RateLimiter(max_attempts=SIGNUP_RATE_LIMIT, window_sec=SIGNUP_RATE_WINDOW_SEC)
# Non-tunable (these are abuse-prevention, not legit-user gates):
verification_resend_lim  = RateLimiter(max_attempts=3,  window_sec=3600)   # 3 per hour per user_id
password_reset_req_lim   = RateLimiter(max_attempts=3,  window_sec=3600)   # 3 per hour per email
password_reset_apply_lim = RateLimiter(max_attempts=5,  window_sec=900)    # 5 per 15 min per ip
verification_apply_lim   = RateLimiter(max_attempts=10, window_sec=900)    # 10 per 15 min per ip


# ─── TOTP (RFC 6238) — 2FA ─────────────────────────────────────────────────
#
# Standard 30-second window, 6-digit codes, SHA-1 (per RFC 6238 default —
# what every authenticator app expects). Compatible with Google
# Authenticator, Authy, 1Password, Bitwarden, etc.
#
# Enrollment is a 2-step flow:
#   1. start  : server generates a secret, stores it in users.totp_secret
#               but leaves totp_enrolled_at NULL (pending state)
#   2. confirm: user submits a code from their app. If it verifies,
#               totp_enrolled_at is set + 10 recovery codes are minted.
#
# A user with totp_secret set but totp_enrolled_at NULL is treated as
# "2FA NOT enabled" by every other check — the pending secret is purely
# transient state that the user can either confirm or abandon.

import pyotp

TOTP_ISSUER = os.getenv("TOTP_ISSUER", "Foundation")
TOTP_DIGITS = 6
TOTP_PERIOD = 30
# Accept ±1 step (i.e. 30s past or future) to forgive clock drift
TOTP_VALID_WINDOW = 1


def generate_totp_secret() -> str:
    """Generate a fresh base32-encoded TOTP secret (160 bits, 32 chars).

    Per RFC 4226 §4: "The shared secret is used by both the server and
    the user's token." pyotp.random_base32() emits a 160-bit secret by
    default, which matches authenticator-app expectations.
    """
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, *, account_label: str) -> str:
    """Build the otpauth:// URI authenticator apps consume via QR scan.

    Format: otpauth://totp/<issuer>:<account>?secret=...&issuer=<issuer>
    The label visible in the user's app is "<issuer> (<account>)" — the
    account_label is typically their email.
    """
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_label,
        issuer_name=TOTP_ISSUER,
    )


def verify_totp_code(secret: str, code: str) -> bool:
    """Constant-time verification of a 6-digit code against the secret.

    Accepts the current 30-second window plus ±TOTP_VALID_WINDOW steps
    on either side to forgive small clock drift between the user's
    device and the server. False on malformed input — never raises.
    """
    if not secret or not code:
        return False
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW)
    except Exception:
        return False


def user_has_totp(user_id: int, *, db_path: Optional[str] = None) -> bool:
    """True iff the user has COMPLETED TOTP enrollment (not just pending)."""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT totp_enrolled_at FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
    return bool(row and row["totp_enrolled_at"])


def get_user_totp_secret(user_id: int, *, db_path: Optional[str] = None) -> Optional[str]:
    """Fetch the raw TOTP secret. Returns None if user has no secret set
    (which covers both 'never enrolled' and 'fully disabled'). Used by
    the login flow + the enroll-confirm endpoint.
    """
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT totp_secret FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
    return row["totp_secret"] if row else None


def stash_pending_totp_secret(user_id: int, secret: str, *, db_path: Optional[str] = None) -> None:
    """Save a freshly generated secret as the user's PENDING enrollment.

    Sets users.totp_secret but leaves totp_enrolled_at untouched. If the
    user already has a confirmed enrollment, this is a no-op refusal —
    they must disable first before re-enrolling, otherwise we'd silently
    invalidate their existing authenticator-app entry.
    """
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT totp_enrolled_at FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        if not row:
            raise AuthError("user not found")
        if row["totp_enrolled_at"]:
            raise AuthError("2FA already enrolled — disable first to re-enroll")
        conn.execute(
            "UPDATE users SET totp_secret = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (secret, user_id),
        )
        conn.commit()


def confirm_totp_enrollment(user_id: int, code: str, *, db_path: Optional[str] = None) -> list[str]:
    """Final step of enrollment. Verifies the code against the PENDING
    secret; on success sets totp_enrolled_at + generates 10 recovery
    codes. Returns the plaintext recovery codes (caller must surface them
    once — they're hashed at rest).

    Raises AuthError on:
      - user has no pending secret (never called start)
      - user already enrolled (idempotency guard)
      - code didn't verify
    """
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT totp_secret, totp_enrolled_at FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        if not row:
            raise AuthError("user not found")
        if row["totp_enrolled_at"]:
            raise AuthError("2FA already enrolled")
        secret = row["totp_secret"]
        if not secret:
            raise AuthError("no pending enrollment — call start first")
        if not verify_totp_code(secret, code):
            raise AuthError("code did not verify")

        conn.execute(
            "UPDATE users SET totp_enrolled_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        # Wipe any stale recovery codes from a prior enrollment cycle
        conn.execute("DELETE FROM totp_recovery_codes WHERE user_id = ?", (user_id,))
        plaintext_codes = _generate_recovery_codes(10)
        for code_plain in plaintext_codes:
            conn.execute(
                "INSERT INTO totp_recovery_codes (user_id, code_hash) VALUES (?, ?)",
                (user_id, _hash_recovery_code(code_plain)),
            )
        conn.commit()
    return plaintext_codes


def disable_totp(user_id: int, *, db_path: Optional[str] = None) -> None:
    """Clear secret + enrolled_at + all recovery codes. Idempotent."""
    with db_connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET totp_secret = NULL, totp_enrolled_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,),
        )
        conn.execute("DELETE FROM totp_recovery_codes WHERE user_id = ?", (user_id,))
        conn.commit()


def regenerate_recovery_codes(user_id: int, *, db_path: Optional[str] = None) -> list[str]:
    """Wipe + reissue 10 recovery codes for a user who already has TOTP
    enrolled. Returns the plaintext codes for one-time display. Raises
    AuthError if user isn't enrolled (callers should gate on user_has_totp).
    """
    if not user_has_totp(user_id, db_path=db_path):
        raise AuthError("2FA not enrolled")
    with db_connect(db_path) as conn:
        conn.execute("DELETE FROM totp_recovery_codes WHERE user_id = ?", (user_id,))
        plaintext_codes = _generate_recovery_codes(10)
        for code_plain in plaintext_codes:
            conn.execute(
                "INSERT INTO totp_recovery_codes (user_id, code_hash) VALUES (?, ?)",
                (user_id, _hash_recovery_code(code_plain)),
            )
        conn.commit()
    return plaintext_codes


def consume_recovery_code(user_id: int, code: str, *, db_path: Optional[str] = None) -> bool:
    """Verify + single-use-consume a recovery code. Returns True on success.

    Recovery codes are stored hashed (argon2 — same scheme as passwords)
    so we have to fetch ALL active codes and verify against each. Acceptable
    since a user only ever has 10 active codes at a time.
    """
    if not code:
        return False
    code = code.strip().replace(" ", "").replace("-", "").lower()
    if not code:
        return False
    with db_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, code_hash FROM totp_recovery_codes "
            "WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchall()
        for row in rows:
            try:
                if pwd_context.verify(code, row["code_hash"]):
                    conn.execute(
                        "UPDATE totp_recovery_codes SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (row["id"],),
                    )
                    conn.commit()
                    return True
            except Exception:
                # Malformed hash row — keep checking the others
                continue
    return False


def count_active_recovery_codes(user_id: int, *, db_path: Optional[str] = None) -> int:
    """How many unused recovery codes the user has left. Surfaced in the
    UI so they know to regenerate before running out."""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM totp_recovery_codes "
            "WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _generate_recovery_codes(n: int = 10) -> list[str]:
    """Generate n recovery codes in `xxxx-xxxx` format — 8 lowercase
    alphanumeric chars split by a dash for readability. Total entropy
    per code: 32**8 ≈ 1.1 trillion = enough for the use case."""
    # Crockford-ish alphabet (no 0/o/1/l confusion) — easier to copy down
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(n):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def _hash_recovery_code(code: str) -> str:
    """Hash a recovery code with the same argon2 scheme used for passwords.

    The verify side strips dashes/spaces/case-folds, so we do the same
    before hashing to keep verify and hash symmetric.
    """
    canon = code.strip().replace("-", "").replace(" ", "").lower()
    return pwd_context.hash(canon)
