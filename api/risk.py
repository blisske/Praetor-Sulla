"""FastAPI router for user-settable risk caps.

Lets users TIGHTEN the risk parameters their bot uses — never loosen
beyond the operator-defined ceilings. The ceilings are the values in
the per-user template config (~/swarm/ionic/data/template/Config.yaml)
that the operator maintains as the "max-allowed-for-any-user" baseline.

Five knobs exposed (sensible subset; not every YAML param):

  risk_per_trade_pct       — capital risked per trade (% of equity).
                              Lower = safer. Floor 0.1, capped at template.
  position_size_max_pct    — max single-position size (% of equity).
                              Lower = safer. Floor 0.5, capped at template.
  max_open_trades          — max concurrent positions.
                              Lower = safer. Floor 1, capped at template.
  drawdown_halt_pct        — peak-DD threshold to hard-halt the bot.
                              LOWER = more protective (tighter halt).
                              Floor 3.0, capped at template.
  daily_halt_pct           — intraday-DD threshold to hard-halt.
                              LOWER = more protective. Floor 3.0, capped at template.

All five are stored under `risk.*` in the user's per-user Config.yaml.
Engine reads them on each cycle (existing path — no new engine code).

Endpoints:
  GET   /api/user/risk-caps   Current values + ceilings + floors
  POST  /api/user/risk-caps   Update (server validates ≤ ceilings,
                               touches .restart_engine)
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from shared import auth as core_auth
from shared.auth import User
from shared.api_auth import get_current_user
from api.mode import _user_config_path, _touch_user_restart_flag, _log_mode_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/risk-caps", tags=["risk-caps"])


# Template Config.yaml — operator-maintained. Source of truth for ceilings.
# Resolves to /app/data/template/Config.yaml inside the api container
# (bind-mounted from ~/swarm/ionic/data/template/Config.yaml).
TEMPLATE_CONFIG_PATH = Path(os.environ.get(
    "USER_TEMPLATE_CONFIG",
    "/app/data/template/Config.yaml",
))


# ─── Defaults if template is missing some keys ─────────────────────────────


# Conservative ceilings used when the operator's template doesn't specify
# a value (defensive — we'd rather degrade to a safe ceiling than expose
# unlimited values). These match the existing operator-default conservative
# profile per CLAUDE.md §Risk Management Rules.
FALLBACK_CEILINGS = {
    "risk_per_trade_pct":    1.5,   # % equity at risk per trade
    "position_size_max_pct": 5.0,   # % equity max single position
    "max_open_trades":       5,     # concurrent positions
    "drawdown_halt_pct":     25.0,  # peak-DD halt
    "daily_halt_pct":        20.0,  # daily-DD halt
}

# Hard floors (user can't set below these — too tight to be useful).
# Floors are NOT user-tunable.
FLOORS = {
    "risk_per_trade_pct":    0.1,
    "position_size_max_pct": 0.5,
    "max_open_trades":       1,
    "drawdown_halt_pct":     3.0,
    "daily_halt_pct":        3.0,
}


# ─── Request / response shapes ─────────────────────────────────────────────


class RiskCapsRequest(BaseModel):
    """All five fields optional — caller can update just the ones they want."""
    risk_per_trade_pct:    Optional[float] = Field(None, gt=0)
    position_size_max_pct: Optional[float] = Field(None, gt=0)
    max_open_trades:       Optional[int]   = Field(None, gt=0)
    drawdown_halt_pct:     Optional[float] = Field(None, gt=0)
    daily_halt_pct:        Optional[float] = Field(None, gt=0)


class RiskCapsResponse(BaseModel):
    # Current values from the user's Config.yaml
    current:  dict
    # Operator-maximum ceilings (from template Config.yaml)
    ceilings: dict
    # Hard floors (constants in this module)
    floors:   dict
    # True if user's Config.yaml exists; False during provisioning
    config_present: bool


class OkResponse(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
    updated: Optional[dict] = None


# ─── Helpers ───────────────────────────────────────────────────────────────


def _read_ceilings() -> dict:
    """Extract ceiling values from the operator's template Config.yaml.

    Missing keys fall back to FALLBACK_CEILINGS so we always return a
    complete dict. Never raises — file-not-found and parse errors both
    surface as a log warning + fallback values.
    """
    if not TEMPLATE_CONFIG_PATH.exists():
        logger.warning(f"Template Config.yaml missing at {TEMPLATE_CONFIG_PATH} — using fallback ceilings")
        return dict(FALLBACK_CEILINGS)

    from ruamel.yaml import YAML
    yaml = YAML()
    try:
        with open(TEMPLATE_CONFIG_PATH, "r") as f:
            data = yaml.load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to parse template Config.yaml: {e} — using fallback ceilings")
        return dict(FALLBACK_CEILINGS)

    risk = data.get("risk") or {}
    strategy = data.get("strategy") or {}
    out = dict(FALLBACK_CEILINGS)
    if "risk_per_trade_pct" in risk:
        out["risk_per_trade_pct"] = float(risk["risk_per_trade_pct"])
    if "position_size_max_pct" in risk:
        out["position_size_max_pct"] = float(risk["position_size_max_pct"])
    if "max_open_trades" in strategy:
        out["max_open_trades"] = int(strategy["max_open_trades"])
    if "drawdown_halt_pct" in risk:
        out["drawdown_halt_pct"] = float(risk["drawdown_halt_pct"])
    if "daily_halt_pct" in risk:
        out["daily_halt_pct"] = float(risk["daily_halt_pct"])
    return out


def _read_user_caps(user_id: int) -> Optional[dict]:
    """Read user's CURRENT risk values from their Config.yaml.

    Returns None if config doesn't exist (user not provisioned yet).
    Missing fields default to the ceiling values (i.e., user inherited
    template defaults and hasn't tightened anything).
    """
    cfg_path = _user_config_path(user_id)
    if not cfg_path.exists():
        return None

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

    ceilings = _read_ceilings()
    risk = data.get("risk") or {}
    strategy = data.get("strategy") or {}
    return {
        "risk_per_trade_pct":    float(risk.get("risk_per_trade_pct",    ceilings["risk_per_trade_pct"])),
        "position_size_max_pct": float(risk.get("position_size_max_pct", ceilings["position_size_max_pct"])),
        "max_open_trades":       int(strategy.get("max_open_trades",     ceilings["max_open_trades"])),
        "drawdown_halt_pct":     float(risk.get("drawdown_halt_pct",     ceilings["drawdown_halt_pct"])),
        "daily_halt_pct":        float(risk.get("daily_halt_pct",        ceilings["daily_halt_pct"])),
    }


def _validate_against_ceilings(req: RiskCapsRequest, ceilings: dict) -> dict:
    """Return a dict of fields that should be written, after validating
    against ceilings + floors. Raises 400 with a clear message on any
    out-of-bounds value.
    """
    out = {}
    for field, value in req.model_dump(exclude_none=True).items():
        ceiling = ceilings[field]
        floor   = FLOORS[field]
        if value < floor:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field} cannot be below {floor} (hard floor).",
            )
        if value > ceiling:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{field}={value} exceeds the maximum allowed value "
                    f"({ceiling}). You can only set this lower than the "
                    f"operator's ceiling — never higher."
                ),
            )
        out[field] = value
    return out


def _write_user_caps(user_id: int, updates: dict) -> None:
    """Apply the validated updates to user's Config.yaml. Writes .yaml.bak
    backup first per the existing convention.

    max_open_trades lives under `strategy.`, the rest under `risk.`.
    """
    cfg_path = _user_config_path(user_id)
    if not cfg_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your trading workspace is still being set up. Try again in a moment.",
        )

    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(cfg_path, "r") as f:
        data = yaml.load(f) or {}
    if "risk" not in data:
        data["risk"] = {}
    if "strategy" not in data:
        data["strategy"] = {}

    for field, value in updates.items():
        if field == "max_open_trades":
            data["strategy"]["max_open_trades"] = int(value)
        else:
            data["risk"][field] = value

    shutil.copy(cfg_path, cfg_path.with_suffix(".yaml.bak"))
    with open(cfg_path, "w") as f:
        yaml.dump(data, f)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── GET /api/user/risk-caps ───────────────────────────────────────────────


@router.get("", response_model=RiskCapsResponse)
async def get_risk_caps(user: User = Depends(get_current_user)):
    """Returns the user's CURRENT risk values + the operator-defined
    ceilings + the hard floors. The UI uses ceilings + floors to render
    proper slider bounds + inline validation."""
    ceilings = _read_ceilings()
    user_caps = _read_user_caps(user.id)
    return RiskCapsResponse(
        current        = user_caps or ceilings,  # if no config: show ceiling as preview
        ceilings       = ceilings,
        floors         = dict(FLOORS),
        config_present = user_caps is not None,
    )


# ─── POST /api/user/risk-caps ──────────────────────────────────────────────


@router.post("", response_model=OkResponse)
async def update_risk_caps(
    req:     RiskCapsRequest,
    request: Request,
    user:    User = Depends(get_current_user),
):
    """Update the user's risk caps. All fields optional — only the ones
    present in the request body are touched. Server validates ≤ ceilings
    and ≥ floors; rejects any out-of-bounds value with 400.

    Side effects on success:
      - Writes ~/swarm/ionic/data/users/<id>/Config.yaml (with .bak)
      - Touches .restart_engine so the engine picks up new values on next cycle
      - Audit-logs 'risk_caps_updated' to broker_key_events
    """
    if not any([
        req.risk_per_trade_pct, req.position_size_max_pct, req.max_open_trades,
        req.drawdown_halt_pct, req.daily_halt_pct,
    ]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    ceilings = _read_ceilings()
    updates  = _validate_against_ceilings(req, ceilings)
    _write_user_caps(user.id, updates)
    _touch_user_restart_flag(user.id)

    _log_mode_event(
        user.id,
        "risk_caps_updated",
        detail=", ".join(f"{k}={v}" for k, v in updates.items()),
        ip=_client_ip(request),
    )

    logger.info(f"User {user.id} updated risk caps: {updates}")
    return OkResponse(
        ok=True,
        detail=f"Updated {len(updates)} risk cap(s). Engine will pick up changes on next cycle.",
        updated=updates,
    )
