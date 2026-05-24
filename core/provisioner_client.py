"""Provisioner client — API-side helpers that the signup/delete endpoints
call to request engine container provisioning from the host-side daemon.

The pattern is FILE-BASED, not Docker socket: the API container has zero
host privileges and only writes flag files into a shared queue directory.
A separate ``scripts/provisioner_daemon.py`` runs on the host (under systemd
or equivalent), polls that queue, and runs ``docker compose up -d`` to
materialize / tear down per-user engine containers.

Public surface:
    initialize_user_dir(user_id, ...)    Copy the template files into the
                                          user's data dir; touch heartbeat.
                                          Returns the path to the new dir.
    enqueue_provision(user_id, ...)       Drop ``<id>.provision`` flag.
    enqueue_teardown(user_id, ...)        Drop ``<id>.teardown`` flag.
    user_dir_exists(user_id, ...)         True if data/users/<id>/ is present.
    read_engine_heartbeat(user_id, ...)   Returns mtime as a UTC datetime
                                          or None if heartbeat doesn't exist.
                                          Used by the engine-status endpoint
                                          to tell the frontend "your engine
                                          is online" vs "still provisioning."

Why no `await` / no asyncio: the work is pure filesystem ops on a local
bind mount. Sub-millisecond. Anything async would just be ceremony.

Why no Docker calls: see module docstring. Docker access only happens in
the host-side daemon process.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Override these via env in compose; defaults match the in-container layout.
DEFAULT_USER_DATA_DIR  = os.getenv("USER_DATA_DIR",  "/app/data/users")
DEFAULT_TEMPLATE_DIR   = os.getenv("USER_TEMPLATE_DIR", "/app/data/template")
DEFAULT_QUEUE_DIR      = os.getenv("PROVISIONER_QUEUE_DIR", "/app/data/users/_queue")


class ProvisionerError(Exception):
    """Raised when initialize_user_dir or enqueue_* hit a hard failure.

    The signup endpoint catches this and surfaces it to the user as a
    503 "we're having trouble setting up your account, try again later."
    """


# ─── Path helpers ──────────────────────────────────────────────────────────


def _user_dir(user_id: int, base: Optional[str] = None) -> Path:
    return Path(base or DEFAULT_USER_DATA_DIR) / str(user_id)


def _template_dir(base: Optional[str] = None) -> Path:
    return Path(base or DEFAULT_TEMPLATE_DIR)


def _queue_dir(base: Optional[str] = None) -> Path:
    return Path(base or DEFAULT_QUEUE_DIR)


# ─── User-dir initialization ───────────────────────────────────────────────


def initialize_user_dir(
    user_id: int,
    *,
    user_data_dir: Optional[str] = None,
    template_dir:  Optional[str] = None,
    queue_dir:     Optional[str] = None,
    overwrite:     bool = False,
) -> Path:
    """Set up the per-user data directory by copying the template files.

    Layout produced:
        <user_data_dir>/<user_id>/
            ionic.db       (from template; schema-only; engine writes
                                 to it once running)
            Config.yaml         (from template; user editable via the API)
            .engine_heartbeat   (touch so healthcheck has something to read
                                 during the start_period grace window)

    Args:
        user_id: Target user.
        user_data_dir: Override for the parent dir. Defaults to env / /app/data/users.
        template_dir: Where to read template files from. Defaults to env /
            /app/data/template. MUST contain ionic.db + Config.yaml.
        queue_dir: Override the queue dir (used only to ensure it exists; this
            function doesn't write into it — enqueue_provision does).
        overwrite: When False (the default), a pre-existing directory is a
            hard error — ProvisionerError raised. Set True only in admin-side
            reprovision flows.

    Returns:
        The path to the newly-created user directory.

    Raises:
        ProvisionerError if the template is missing or the user dir
        already exists (and overwrite=False).
    """
    user_dir = _user_dir(user_id, user_data_dir)
    tmpl     = _template_dir(template_dir)

    # Validate template — fail fast if operator hasn't set it up
    tmpl_db   = tmpl / "ionic.db"
    tmpl_cfg  = tmpl / "Config.yaml"
    if not tmpl_db.exists():
        raise ProvisionerError(
            f"Template DB missing at {tmpl_db}. "
            "Operator must materialize the template before user signups."
        )
    if not tmpl_cfg.exists():
        raise ProvisionerError(
            f"Template Config.yaml missing at {tmpl_cfg}."
        )

    if user_dir.exists():
        if not overwrite:
            raise ProvisionerError(
                f"User dir already exists: {user_dir}. "
                "Refusing to overwrite (pass overwrite=True for admin reprovision)."
            )
        # Admin reprovision path: wipe + recreate cleanly. The operator's
        # data MUST already be safely captured elsewhere before this is hit.
        logger.warning(f"Overwriting existing user dir {user_dir} (admin reprovision)")
        shutil.rmtree(user_dir)

    user_dir.mkdir(parents=True, exist_ok=False)

    # Copy template files. shutil.copy preserves mode bits.
    shutil.copy(tmpl_db,  user_dir / "ionic.db")
    shutil.copy(tmpl_cfg, user_dir / "Config.yaml")

    # Touch heartbeat so the healthcheck has something to look at during the
    # start_period grace window (engine writes a fresh one on its first cycle).
    (user_dir / ".engine_heartbeat").touch()

    # Make sure the queue dir exists too, since enqueue_provision will write
    # into it momentarily and we want THAT call to never fail on a fresh
    # install where the API container started with no queue dir yet.
    _queue_dir(queue_dir).mkdir(parents=True, exist_ok=True)

    logger.info(f"Initialized user dir for user_id={user_id} at {user_dir}")
    return user_dir


# ─── Flag-file enqueue ─────────────────────────────────────────────────────


def enqueue_provision(
    user_id: int,
    *,
    queue_dir: Optional[str] = None,
) -> Path:
    """Drop a ``<user_id>.provision`` flag file. Provisioner picks it up
    within POLL_INTERVAL seconds (5s default) and spins up the engine."""
    q = _queue_dir(queue_dir)
    q.mkdir(parents=True, exist_ok=True)
    flag = q / f"{user_id}.provision"
    flag.touch()
    logger.info(f"Enqueued provision for user_id={user_id} → {flag}")
    return flag


def enqueue_teardown(
    user_id: int,
    *,
    queue_dir: Optional[str] = None,
) -> Path:
    """Drop a ``<user_id>.teardown`` flag file. Provisioner picks it up,
    stops + removes the container, soft-moves data to _deleted/."""
    q = _queue_dir(queue_dir)
    q.mkdir(parents=True, exist_ok=True)
    flag = q / f"{user_id}.teardown"
    flag.touch()
    logger.info(f"Enqueued teardown for user_id={user_id} → {flag}")
    return flag


# ─── Status helpers (used by /api/user/engine-status) ──────────────────────


def user_dir_exists(user_id: int, *, user_data_dir: Optional[str] = None) -> bool:
    return _user_dir(user_id, user_data_dir).exists()


def read_engine_heartbeat(
    user_id: int,
    *,
    user_data_dir: Optional[str] = None,
) -> Optional[datetime]:
    """Return the mtime of the user's .engine_heartbeat as a UTC datetime,
    or None if the file doesn't exist (user not provisioned yet)."""
    hb = _user_dir(user_id, user_data_dir) / ".engine_heartbeat"
    if not hb.exists():
        return None
    return datetime.fromtimestamp(hb.stat().st_mtime, tz=timezone.utc)


def is_provision_pending(user_id: int, *, queue_dir: Optional[str] = None) -> bool:
    """True if a `.provision` flag exists for this user — i.e., the daemon
    hasn't picked it up yet. Useful for "still provisioning" UI state."""
    return (_queue_dir(queue_dir) / f"{user_id}.provision").exists()


def is_teardown_pending(user_id: int, *, queue_dir: Optional[str] = None) -> bool:
    return (_queue_dir(queue_dir) / f"{user_id}.teardown").exists()
