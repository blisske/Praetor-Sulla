"""FastAPI router for operator-only admin endpoints.

All routes require an admin JWT (`get_current_admin` dependency from
api.auth). 403 for non-admin authenticated users; 401 for missing /
invalid tokens.

Endpoints:

  GET    /api/admin/users                  List all users + their broker /
                                            mode / engine summary.
  GET    /api/admin/users/{user_id}        Single user detail + recent
                                            broker_key_events.
  GET    /api/admin/users/{user_id}/login-attempts
                                            Recent login attempts for the
                                            user (by email match).
  GET    /api/admin/provisioner            Daemon status + recent audit
                                            log records.
  POST   /api/admin/users/{user_id}/restart-engine
                                            Touch the user's .restart_engine
                                            flag so their engine picks up
                                            config changes on next cycle.

This is operator-facing first; once real F&F users are on the system
it doubles as the support tooling for "did my bot really place that
trade?" style questions.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from core import auth as core_auth
from core import provisioner_client
from core.auth import User
from api.auth import get_current_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# Path to the provisioner's audit log. Override via env for tests; defaults
# to the in-container layout where /app/data/users/_queue/audit.log is the
# bind-mounted on-disk file the daemon writes from the host side.
AUDIT_LOG_PATH = os.getenv("PROVISIONER_AUDIT_LOG", "/app/data/users/_queue/audit.log")


# ─── Response shapes ───────────────────────────────────────────────────────


class BrokerSummary(BaseModel):
    connected:    bool
    scope:        Optional[str] = None
    validated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    last_error:   Optional[str] = None


class EngineSummary(BaseModel):
    config_present: bool
    heartbeat:      Optional[str] = None     # ISO timestamp or None
    heartbeat_age_seconds: Optional[int] = None
    provision_pending: bool
    teardown_pending:  bool


class AdminUserRow(BaseModel):
    id:             int
    email:          str
    email_verified: bool
    is_admin:       bool
    created_at:     str
    last_login_at:  Optional[str] = None
    deleted_at:     Optional[str] = None
    broker:         BrokerSummary
    engine:         EngineSummary


class AdminUserDetail(AdminUserRow):
    # Same fields as the list row PLUS recent audit + login events
    broker_events: list[dict]
    recent_logins: list[dict] = []


class ProvisionerAuditRecord(BaseModel):
    ts:      str
    action:  str
    user_id: int
    ok:      bool
    detail:  Optional[str] = None


class ProvisionerStatus(BaseModel):
    audit_log_present: bool
    audit_log_path:    str
    recent:            list[ProvisionerAuditRecord]
    queue_pending:     list[str]   # filenames of unresolved .provision/.teardown flags


class OkResponse(BaseModel):
    ok: bool = True
    detail: Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────


def _broker_summary(user_id: int, db_path: Optional[str] = None) -> BrokerSummary:
    """Read the user's broker_keys row (if any) into a Summary shape."""
    with core_auth.db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT scope, validated_at, last_used_at, last_error "
            "FROM broker_keys WHERE user_id = ? AND broker = 'oanda'",
            (user_id,),
        ).fetchone()
    if not row:
        return BrokerSummary(connected=False)
    return BrokerSummary(
        connected    = True,
        scope        = row["scope"],
        validated_at = row["validated_at"],
        last_used_at = row["last_used_at"],
        last_error   = row["last_error"],
    )


def _engine_summary(user_id: int) -> EngineSummary:
    """Per-user engine status: config file present + heartbeat freshness."""
    cfg_present = provisioner_client.user_dir_exists(user_id)
    hb = provisioner_client.read_engine_heartbeat(user_id)
    age = None
    hb_iso = None
    if hb:
        hb_iso = hb.isoformat()
        age = int((datetime.now(timezone.utc) - hb).total_seconds())
    return EngineSummary(
        config_present      = cfg_present,
        heartbeat           = hb_iso,
        heartbeat_age_seconds = age,
        provision_pending   = provisioner_client.is_provision_pending(user_id),
        teardown_pending    = provisioner_client.is_teardown_pending(user_id),
    )


def _row_to_admin_user(row: sqlite3.Row) -> AdminUserRow:
    return AdminUserRow(
        id             = row["id"],
        email          = row["email"],
        email_verified = bool(row["email_verified"]),
        is_admin       = bool(row["is_admin"]),
        created_at     = row["created_at"],
        last_login_at  = row["last_login_at"],
        deleted_at     = row["deleted_at"],
        broker         = _broker_summary(row["id"]),
        engine         = _engine_summary(row["id"]),
    )


# ─── GET /api/admin/users ──────────────────────────────────────────────────


@router.get("/users", response_model=list[AdminUserRow])
async def list_users(
    include_deleted: bool = Query(False, description="Show soft-deleted users too"),
    _admin: User = Depends(get_current_admin),
):
    """Return all users + their broker/engine summaries.

    Sorted by id ASC so the operator (id=1) is always first.
    """
    with core_auth.db_connect() as conn:
        if include_deleted:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE deleted_at IS NULL ORDER BY id"
            ).fetchall()
    return [_row_to_admin_user(r) for r in rows]


# ─── GET /api/admin/users/{user_id} ────────────────────────────────────────


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def user_detail(
    user_id: int,
    _admin: User = Depends(get_current_admin),
):
    """Full detail for a single user, including recent broker_key_events."""
    with core_auth.db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        # Most recent 50 events for this user
        events = conn.execute(
            "SELECT id, timestamp, broker, event_type, detail, ip "
            "FROM broker_key_events WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user_id,),
        ).fetchall()

    base = _row_to_admin_user(row)
    return AdminUserDetail(
        **base.model_dump(),
        broker_events = [dict(e) for e in events],
        recent_logins = [],   # populated by /login-attempts endpoint
    )


# ─── GET /api/admin/users/{user_id}/login-attempts ─────────────────────────


@router.get("/users/{user_id}/login-attempts")
async def user_login_attempts(
    user_id: int,
    limit: int = Query(50, ge=1, le=500),
    _admin: User = Depends(get_current_admin),
):
    """Login attempts matching this user's email (most recent first).

    login_attempts is a global table indexed by username/email string;
    we match by the user's current email. If the user has changed email
    historically we may miss those (acceptable — operators can query
    by email directly if needed).
    """
    with core_auth.db_connect() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        attempts = conn.execute(
            "SELECT id, timestamp, username, ip, result "
            "FROM login_attempts WHERE LOWER(username) = LOWER(?) "
            "ORDER BY id DESC LIMIT ?",
            (row["email"], limit),
        ).fetchall()
    return {"attempts": [dict(a) for a in attempts]}


# ─── GET /api/admin/provisioner ────────────────────────────────────────────


@router.get("/provisioner", response_model=ProvisionerStatus)
async def provisioner_status(
    tail: int = Query(50, ge=1, le=500, description="How many audit lines to return"),
    _admin: User = Depends(get_current_admin),
):
    """Provisioner daemon status: recent audit log records + pending queue.

    Doesn't actually probe the daemon process — that lives on the host
    outside the API container. Instead we read the audit log it writes
    (best-effort signal that it's alive + processing flags) and list
    any unresolved flag files in the queue dir.
    """
    audit_path = Path(AUDIT_LOG_PATH)
    recent: list[ProvisionerAuditRecord] = []
    if audit_path.exists():
        try:
            # Read the last `tail` lines, in chronological order
            lines = audit_path.read_text(errors="replace").splitlines()[-tail:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    recent.append(ProvisionerAuditRecord(
                        ts      = rec.get("ts", ""),
                        action  = rec.get("action", ""),
                        user_id = int(rec.get("user_id", -1)),
                        ok      = bool(rec.get("ok", False)),
                        detail  = rec.get("detail"),
                    ))
                except (json.JSONDecodeError, ValueError, KeyError):
                    # Skip malformed lines; the log is best-effort anyway
                    continue
        except OSError as e:
            logger.warning(f"Failed to read provisioner audit log: {e}")

    # Pending queue entries (flag files the daemon hasn't picked up yet)
    queue_dir = Path(os.getenv("PROVISIONER_QUEUE_DIR", "/app/data/users/_queue"))
    pending: list[str] = []
    if queue_dir.exists():
        try:
            for entry in sorted(queue_dir.iterdir()):
                if entry.suffix in (".provision", ".teardown"):
                    pending.append(entry.name)
        except OSError as e:
            logger.warning(f"Failed to read queue dir: {e}")

    return ProvisionerStatus(
        audit_log_present = audit_path.exists(),
        audit_log_path    = str(audit_path),
        recent            = recent,
        queue_pending     = pending,
    )


# ─── POST /api/admin/users/{user_id}/unfreeze ──────────────────────────────


@router.post("/users/{user_id}/unfreeze", response_model=OkResponse)
async def unfreeze_user(
    user_id: int,
    _admin: User = Depends(get_current_admin),
):
    """Operator-only unfreeze. Counterpart to user-initiated
    POST /api/user/freeze in api/freeze.py.

    Operator-gated by design: a panicked user who froze in a market move
    shouldn't be able to immediately un-panic and resume trading. The
    "you have to talk to a human" friction is the feature.

    Audits to broker_key_events as 'account_unfrozen_by_admin'.
    """
    from api.freeze import _read_freeze_state, _write_freeze_state
    from api.mode import _touch_user_restart_flag

    target = core_auth.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    frozen, _, _ = _read_freeze_state(user_id)
    if frozen is None:
        return OkResponse(ok=False, detail=f"User {user_id} has no provisioned workspace.")
    if not frozen:
        return OkResponse(ok=True, detail=f"User {user_id} is not currently frozen.")

    _write_freeze_state(user_id, frozen=False)
    _touch_user_restart_flag(user_id)

    try:
        conn = sqlite3.connect(core_auth.GLOBAL_DB_PATH)
        conn.execute(
            "INSERT INTO broker_key_events (user_id, broker, event_type, detail) "
            "VALUES (?, 'system', 'account_unfrozen_by_admin', ?)",
            (user_id, f"unfrozen by admin_id={_admin.id}"),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to log unfreeze for user {user_id}: {e}")

    logger.info(f"Admin {_admin.email} (id={_admin.id}) unfroze user_id={user_id} ({target.email})")
    return OkResponse(ok=True, detail=f"Unfroze user {user_id} ({target.email})")


# ─── POST /api/admin/users/{user_id}/restart-engine ────────────────────────


@router.post("/users/{user_id}/restart-engine", response_model=OkResponse)
async def restart_user_engine(
    user_id: int,
    _admin: User = Depends(get_current_admin),
):
    """Touch the user's .restart_engine flag. Their engine will exit
    cleanly on its next cycle and compose will bring it back up.

    No-op if the user dir doesn't exist (returns ok=False).
    """
    user_data_dir = os.getenv("USER_DATA_DIR", "/app/data/users")
    user_dir = Path(user_data_dir) / str(user_id)
    if not user_dir.exists():
        return OkResponse(ok=False, detail=f"No user dir at {user_dir}")
    flag = user_dir / ".restart_engine"
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch(exist_ok=True)
        os.utime(flag, None)
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to touch restart flag: {e}",
        )
    return OkResponse(ok=True, detail=f"Flag touched at {flag}")
