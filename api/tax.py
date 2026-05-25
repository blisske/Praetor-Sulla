"""FastAPI router for per-user tax-disposal reporting.

Endpoints (all require auth — per-user via the existing get_current_user
dependency from api.auth):

  GET  /api/user/tax/years
       Lists years with at least one disposable trade. Drives year-tab UI.

  GET  /api/user/tax/disposals?year=2026&method=FIFO&include_shadow=false
       JSON: {disposals, summary, year, method, is_demo, has_live, disclaimer}
       is_demo=true when include_shadow forced surfacing of shadow rows —
       frontend renders the DEMO watermark.

  GET  /api/user/tax/disposals.csv?year=2026&method=FIFO&include_shadow=false
       CSV download with prominent "Not tax advice" header comment.

method defaults to the user's saved preference (per-user Config.yaml
under `tax.method`) or 'FIFO' if unset.

⚠️ Every endpoint surfaces a "Not tax advice — verify with your CPA"
warning. The frontend repeats it on every page.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from shared import auth as core_auth
from core import tax as tax_module
from shared.auth import User
from shared.api_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user/tax", tags=["tax"])


DISCLAIMER = (
    "Not tax advice. Foundation generates these reports mechanically from "
    "your trade history. ⚠️ FX-SPECIFIC: under default IRC §988 treatment "
    "retail spot FX gains are ORDINARY income (not capital gains) — no "
    "short/long-term split, reported on Schedule 1 line 8z or Form 6781 "
    "line 1. The §988(a)(1)(B) opt-out into capital-gains treatment is "
    "available but rare for retail. Verify with a CPA before filing."
)


# ─── Response shapes ───────────────────────────────────────────────────────


class DisposalResponse(BaseModel):
    symbol:         str
    date_acquired:  str
    date_sold:      str
    qty:            float
    proceeds_usd:   float
    cost_basis_usd: float
    gain_loss_usd:  float
    holding_days:   int
    term:           str
    fees_usd:       float


class SummaryResponse(BaseModel):
    """§988 ordinary-income summary — no short/long-term split.

    `realized_gain` and `total_ordinary_gain` are equal by construction
    (both = sum of all gain_loss_usd). Frontend can use either.
    """
    disposal_count:       int
    ordinary_count:       int   # always == disposal_count under §988
    total_proceeds:       float
    total_cost_basis:     float
    realized_gain:        float
    total_ordinary_gain:  float
    total_fees:           float


class TaxReportResponse(BaseModel):
    year:       int
    method:     str
    is_demo:    bool
    has_live:   bool
    disposals:  list[DisposalResponse]
    summary:    SummaryResponse
    disclaimer: str = DISCLAIMER


class YearsResponse(BaseModel):
    years_live:   list[int]
    years_shadow: list[int]
    has_live:     bool


class MethodResponse(BaseModel):
    method: str   # FIFO | LIFO | HIFO


class MethodUpdateRequest(BaseModel):
    method: str   # FIFO | LIFO | HIFO


# ─── Helpers ───────────────────────────────────────────────────────────────


def _user_db_path(user_id: int) -> str:
    """Mirror of api/main.py's get_db_for routing — resolves to operator's
    DB for user_id=1 and the per-user file otherwise."""
    base = Path(os.environ.get("SQLITE_PATH", "/app/data/ionic.db"))
    if user_id == 1:
        return str(base)
    return str(base.parent / "users" / str(user_id) / "ionic.db")


def _user_config_path(user_id: int) -> Path:
    """Path to the user's Config.yaml — for reading saved tax.method."""
    base = Path(os.environ.get("SQLITE_PATH", "/app/data/ionic.db")).parent
    if user_id == 1:
        return base / "Config.yaml"
    return base / "users" / str(user_id) / "Config.yaml"


def _resolve_method(user_id: int, requested: Optional[str]) -> str:
    """Method precedence: query param → user Config.yaml tax.method → 'FIFO'."""
    if requested:
        m = requested.upper()
        if m in ("FIFO", "LIFO", "HIFO"):
            return m
    cfg_path = _user_config_path(user_id)
    if cfg_path.exists():
        try:
            from ruamel.yaml import YAML
            with open(cfg_path, "r") as f:
                data = YAML().load(f) or {}
            saved = (data.get("tax", {}) or {}).get("method", "FIFO")
            m = str(saved).upper()
            if m in ("FIFO", "LIFO", "HIFO"):
                return m
        except Exception as e:
            logger.warning(f"Could not read tax.method from {cfg_path}: {e}")
    return "FIFO"


def _resolve_year(requested: Optional[int]) -> int:
    if requested is not None:
        return int(requested)
    return datetime.now(timezone.utc).year


def _should_demo(db_path: str, include_shadow: bool) -> bool:
    """We're in DEMO mode iff the user has zero live trades AND we surfaced
    shadow data. Frontend uses this to render the DEMO watermark."""
    if not include_shadow:
        return False
    return not tax_module.has_live_trades(db_path)


# ─── GET / POST /api/user/tax/method ───────────────────────────────────────


@router.get("/method", response_model=MethodResponse)
async def get_method(user: User = Depends(get_current_user)):
    """Return the user's saved lot-matching preference. Defaults to FIFO
    when nothing is set in their Config.yaml."""
    return MethodResponse(method=_resolve_method(user.id, None))


@router.post("/method", response_model=MethodResponse)
async def set_method(req: MethodUpdateRequest, user: User = Depends(get_current_user)):
    """Persist the user's lot-matching preference to their Config.yaml.

    Refuses unknown methods with 400. Updates user Config.yaml under
    `tax.method`. Idempotent. Does NOT trigger an engine restart — tax
    reports are computed on-demand, not engine-resident.
    """
    from fastapi import HTTPException, status
    m = (req.method or "").upper()
    if m not in ("FIFO", "LIFO", "HIFO"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"method must be FIFO, LIFO, or HIFO (got {req.method!r}).",
        )
    cfg_path = _user_config_path(user.id)
    if not cfg_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your trading workspace is still being set up. Try again in a moment.",
        )
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(cfg_path, "r") as f:
            data = yaml.load(f) or {}
        data.setdefault("tax", {})["method"] = m
        # Backup-before-write convention used by other Config-touching endpoints
        import shutil
        shutil.copy(cfg_path, cfg_path.with_suffix(".yaml.bak"))
        with open(cfg_path, "w") as f:
            yaml.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to write tax.method for user {user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save your preference. Please try again.",
        )
    return MethodResponse(method=m)


# ─── GET /api/user/tax/years ───────────────────────────────────────────────


@router.get("/years", response_model=YearsResponse)
async def list_years(user: User = Depends(get_current_user)):
    db = _user_db_path(user.id)
    return YearsResponse(
        years_live   = tax_module.available_years(db, include_shadow=False),
        years_shadow = tax_module.available_years(db, include_shadow=True),
        has_live     = tax_module.has_live_trades(db),
    )


# ─── GET /api/user/tax/disposals ───────────────────────────────────────────


@router.get("/disposals", response_model=TaxReportResponse)
async def get_disposals(
    year:           Optional[int] = Query(None, description="Calendar year UTC. Defaults to current."),
    method:         Optional[str] = Query(None, description="FIFO | LIFO | HIFO."),
    include_shadow: bool          = Query(False, description="Surface shadow trades as DEMO preview."),
    user:           User          = Depends(get_current_user),
):
    """Per-user tax disposal records + summary. Disclaimer included."""
    db        = _user_db_path(user.id)
    yr        = _resolve_year(year)
    m         = _resolve_method(user.id, method)
    has_live  = tax_module.has_live_trades(db)
    # Auto-include shadow if user has no live data + caller didn't explicitly
    # set include_shadow=true → makes the page useful at zero-state
    if not has_live and not include_shadow:
        include_shadow = True

    disposals = tax_module.compute_disposals(
        db, year=yr, method=m, include_shadow=include_shadow,
    )
    summary = tax_module.summarize(disposals)
    return TaxReportResponse(
        year       = yr,
        method     = m,
        is_demo    = _should_demo(db, include_shadow),
        has_live   = has_live,
        disposals  = [DisposalResponse(**d.to_dict()) for d in disposals],
        summary    = SummaryResponse(**summary),
        disclaimer = DISCLAIMER,
    )


# ─── GET /api/user/tax/disposals.csv ───────────────────────────────────────


@router.get("/disposals.csv")
async def get_disposals_csv(
    year:           Optional[int] = Query(None),
    method:         Optional[str] = Query(None),
    include_shadow: bool          = Query(False),
    user:           User          = Depends(get_current_user),
):
    """CSV export. First lines are commented headers — many import tools
    treat any line starting with '#' as a comment. Under §988 default
    treatment for retail spot FX, the aggregated gain_loss_usd is
    ordinary income (Schedule 1 line 8z or Form 6781 line 1) — NOT
    a Form 8949 / Schedule D line item."""
    db        = _user_db_path(user.id)
    yr        = _resolve_year(year)
    m         = _resolve_method(user.id, method)
    has_live  = tax_module.has_live_trades(db)
    if not has_live and not include_shadow:
        include_shadow = True

    disposals = tax_module.compute_disposals(
        db, year=yr, method=m, include_shadow=include_shadow,
    )
    summary = tax_module.summarize(disposals)

    buf = io.StringIO()
    # Disclaimer + metadata as comment lines
    is_demo = _should_demo(db, include_shadow)
    buf.write(f"# Foundation Ionic — tax report (FX, §988 ordinary income)\n")
    buf.write(f"# Year: {yr}    Method: {m}    User: {user.email}\n")
    if is_demo:
        buf.write("# ⚠️  DEMO MODE — derived from shadow (paper) trades, NOT real money. Do not file.\n")
    buf.write(f"# {DISCLAIMER}\n")
    buf.write(f"# Generated: {datetime.now(timezone.utc).isoformat()}\n")
    buf.write("#\n")
    buf.write(f"# Summary: {summary['disposal_count']} disposals (all §988 ordinary)\n")
    buf.write(f"# Total proceeds:   ${summary['total_proceeds']:.2f}\n")
    buf.write(f"# Total cost basis: ${summary['total_cost_basis']:.2f}\n")
    buf.write(f"# Realized G/L:     ${summary['realized_gain']:.2f}\n")
    buf.write(f"# Ordinary G/L:    ${summary['total_ordinary_gain']:.2f}  (Schedule 1 line 8z or Form 6781 line 1)\n")
    buf.write(f"# Total fees:       ${summary['total_fees']:.2f}\n")
    buf.write("#\n")

    # §988 disposal columns. Same column shape as the cap-gains bots for
    # CPA familiarity, but Term is always 'ordinary' — no Box A/B/D/E
    # routing because §988 doesn't use Form 8949.
    writer = csv.writer(buf)
    writer.writerow([
        "Symbol",
        "Date Acquired (UTC)",
        "Date Sold (UTC)",
        "Quantity (base ccy units)",
        "Proceeds (USD)",
        "Cost Basis (USD)",
        "Gain/Loss (USD)",
        "Holding Days",                # informational under §988
        "Term",                        # always 'ordinary'
        "Fees (USD)",
    ])
    for d in disposals:
        writer.writerow([
            d.symbol,
            d.date_acquired,
            d.date_sold,
            f"{d.qty:.10g}",
            f"{d.proceeds_usd:.4f}",
            f"{d.cost_basis_usd:.4f}",
            f"{d.gain_loss_usd:.4f}",
            d.holding_days,
            d.term,
            f"{d.fees_usd:.4f}",
        ])

    csv_bytes = buf.getvalue().encode("utf-8")
    suffix = "-DEMO" if is_demo else ""
    filename = f"foundation-doric-tax-{yr}-{m}{suffix}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
