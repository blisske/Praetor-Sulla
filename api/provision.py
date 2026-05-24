"""FastAPI router for click-to-provision (per-user engine workspace).

When a user signs into a bot they haven't been provisioned for yet (no
per-user data dir at /app/data/users/<id>/), they see an empty-state on
the dashboard. Clicking "Enable Ionic for your account →" hits
POST /api/user/provision, which enqueues a `.provision` flag for the
provisioner daemon to pick up. Daemon spawns ionic-engine-<id> with the
user's USER_ID env set; engine boots in shadow mode using the operator's
market-data pool key, ready to paper-trade on Ionic's ledger.

GET /api/user/provision returns current state:
  {
    'provisioned': bool,        # user dir exists
    'engine_alive': bool,       # heartbeat seen in last 2 min
    'pending':     bool,        # .provision flag still in queue
    'ready':       bool,        # provisioned + engine_alive + !pending
  }

The UI polls this every ~3s while pending; once ready, swaps in the
normal dashboard.

Idempotent: POST is a no-op (returns current state) if user is already
provisioned or has a pending flag.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from core import auth as core_auth
from core import provisioner_client
from core.auth import User
from api.auth import get_current_user, _client_ip
from api.mode import _log_mode_event   # reuses broker_key_events audit table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/provision", tags=["provision"])


# Heartbeat freshness window — engine is considered "alive" if it
# touched .engine_heartbeat within the last N seconds. Ionic engine
# cycle can run 30s–4min depending on session activity + AI sentiment
# round-trips; 6 min gives generous slack so a healthy mid-cycle
# engine isn't reported as dead.
ALIVE_WINDOW_SEC = 360


# Operator and demo are pre-provisioned via the legacy single-tenant
# data dir layout — they don't need (and shouldn't get) per-user engines.
PRE_PROVISIONED_USER_IDS = {1, 2}


# ─── Response shape ────────────────────────────────────────────────────────


class ProvisionStatus(BaseModel):
    provisioned:  bool
    engine_alive: bool
    pending:      bool
    ready:        bool
    # Human-friendly status string for the empty-state UI
    detail:       str


def _build_status(user_id: int) -> ProvisionStatus:
    """Inspect the filesystem + queue to determine current provision state."""
    if user_id in PRE_PROVISIONED_USER_IDS:
        # Operator + demo always render the legacy dashboard, never the
        # empty-state. Mark them ready.
        return ProvisionStatus(
            provisioned=True, engine_alive=True, pending=False, ready=True,
            detail="Pre-provisioned operator/demo workspace.",
        )

    # Use Config.yaml presence (created by provisioner_client.initialize_user_dir)
    # as the "actually provisioned" signal. user_dir_exists alone returns
    # True even when the dir was created as a side-effect of per-user DB
    # schema seeding (api/main.py's _ensure_per_user_db_schema), which
    # doesn't mean the engine has been provisioned.
    user_dir = Path(os.environ.get("USER_DATA_DIR", "/app/data/users")) / str(user_id)
    provisioned = (user_dir / "Config.yaml").exists()
    pending     = provisioner_client.is_provision_pending(user_id)

    engine_alive = False
    if provisioned:
        hb = provisioner_client.read_engine_heartbeat(user_id)
        if hb is not None:
            from datetime import datetime, timezone
            age_sec = (datetime.now(timezone.utc) - hb).total_seconds()
            engine_alive = age_sec <= ALIVE_WINDOW_SEC

    ready = provisioned and engine_alive and not pending

    if ready:
        detail = "Your workspace is ready."
    elif pending:
        detail = "Workspace queued for provisioning — engine starts in ~5 seconds."
    elif provisioned and not engine_alive:
        detail = "Workspace exists but engine isn't sending heartbeats yet (give it a minute)."
    else:
        detail = "Click to enable this bot for your account."
    return ProvisionStatus(
        provisioned=provisioned, engine_alive=engine_alive,
        pending=pending, ready=ready, detail=detail,
    )


# ─── GET /api/user/provision ───────────────────────────────────────────────


@router.get("", response_model=ProvisionStatus)
async def get_provision_status(user: User = Depends(get_current_user)):
    """Return current per-user-engine state. UI polls this from the
    empty-state component while pending; switches to the normal
    dashboard once `ready=true`."""
    return _build_status(user.id)


# ─── POST /api/user/provision ──────────────────────────────────────────────


@router.post("", response_model=ProvisionStatus)
async def enqueue_provision(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Click-to-provision. Drops a .provision flag for the daemon.

    Idempotent: returns current state if user is already provisioned or
    has a pending flag — never errors. The provisioner daemon picks up
    the flag within ~5s, materializes /app/data/users/<id>/, copies the
    Config.yaml template, generates the docker-compose fragment, and
    runs `docker compose up -d ionic-engine-<id>`. Engine boots in
    shadow mode and starts writing market data to the per-user DB.
    """
    if user.id in PRE_PROVISIONED_USER_IDS:
        # No-op for operator + demo — they always use the legacy data dir
        return _build_status(user.id)

    current = _build_status(user.id)

    # If already provisioned + engine alive, nothing to do
    if current.provisioned and current.engine_alive:
        return current
    # If a provision flag is already in the queue, don't double-enqueue
    if current.pending:
        return current

    # Two-step: materialize the user dir (Config.yaml + empty schema DB),
    # then drop the .provision flag for the daemon to spawn the container.
    # initialize_user_dir is idempotent — safe to call even if a stale
    # partial dir exists from a previous attempt.
    try:
        provisioner_client.initialize_user_dir(user.id)
        provisioner_client.enqueue_provision(user.id)
        _log_mode_event(
            user.id, "provision_enqueued",
            detail=f"bot={os.environ.get('AGENT_NAME', '?')}",
            ip=_client_ip(request),
        )
    except Exception as e:
        logger.error(f"Failed to enqueue provision for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not start provisioning. Try again, or contact support.",
        )

    # Return the new state — likely pending=True now
    return _build_status(user.id)
