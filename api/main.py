"""
Ionic API — FastAPI backend
Serves REST endpoints and WebSocket stream for the Ionic React dashboard.
FX instance — Oanda v20 (Phase 1 scaffold; shadow-only).
"""
import asyncio
import time
from collections import defaultdict
import json
import sqlite3
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
import pytz

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
from dotenv import load_dotenv
# Container-friendly: ENV_FILE override; missing file is tolerated (compose
# typically supplies env vars via env_file: / environment: directly).
load_dotenv(Path(os.environ.get('ENV_FILE', BASE_DIR / 'core' / '.env')))

CORE_DIR = BASE_DIR / "core"
# Container-friendly: SQLITE_PATH points at the bind-mounted runtime DB.
# DEMO_SQLITE_PATH points at the read-only demo DB shipped in the image.
REAL_DB = Path(os.environ.get('SQLITE_PATH',      CORE_DIR / "ionic_data.db"))
DEMO_DB = Path(os.environ.get('DEMO_SQLITE_PATH', CORE_DIR / "demo_data.db"))

# Restart-coordination: the engine watches for this flag file and exits
# cleanly when it appears. Compose `restart: unless-stopped` brings it back
# with the freshly saved Config.yaml. Default sits next to the runtime DB so
# both containers see the same path via the shared bind mount.
RESTART_FLAG_PATH = Path(os.environ.get(
    'RESTART_FLAG_PATH',
    REAL_DB.parent / '.restart_engine'
))

sys.path.insert(0, str(CORE_DIR))
# Also put BASE_DIR (/app) on the path so `from core import auth` etc.
# resolves as a package import — required for the new SaaS routers.
sys.path.insert(0, str(BASE_DIR))

import config_manager
import execution
# Multi-tenant SaaS modules (live as of 2026-05-23 cutover)
from shared import auth as core_auth
from shared.auth import AuthError

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Foundation Ionic API", version="2.0.0")

# Mount the SaaS routers FIRST so their canonical paths take precedence.
# Legacy env-hash /api/auth/login retired in this cutover — both operator
# (user_id=1) and demo (user_id=2) authenticate via the shared
# foundation/data/global.db now.
from shared.api_auth import router as _auth_router, get_current_admin  # noqa: E402 (after sys.path)
from api.byok     import router as _byok_router      # noqa: E402 (Oanda)
from api.mode     import router as _mode_router      # noqa: E402
from api.admin    import router as _admin_router     # noqa: E402
from api.demo     import router as _demo_router      # noqa: E402 (public no-auth)
from api.freeze   import router as _freeze_router    # noqa: E402 (user-side kill switch)
from shared.legal import router as _legal_router     # noqa: E402 (public no-auth — ToS + Privacy)
from api.risk     import router as _risk_router      # noqa: E402 (user-settable risk caps)
from api.totp     import router as _totp_router      # noqa: E402 (2FA enrollment + mgmt)
from api.provision import router as _provision_router # noqa: E402 (click-to-provision)
from api.tax       import router as _tax_router        # noqa: E402 (FX §988 tax-disposal reporting)
app.include_router(_auth_router)
app.include_router(_byok_router)
app.include_router(_mode_router)
app.include_router(_admin_router)
app.include_router(_demo_router)
app.include_router(_freeze_router)
app.include_router(_legal_router)
app.include_router(_risk_router)
app.include_router(_totp_router)
app.include_router(_provision_router)
app.include_router(_tax_router)

@app.on_event("startup")
async def startup():
    if not DEMO_DB.exists() and REAL_DB.exists():
        import shutil
        shutil.copy(REAL_DB, DEMO_DB)
        print(f"✅ Demo DB initialized from real DB snapshot.")

# Container-friendly: comma-separated CORS_ORIGINS env var with sensible
# defaults. In compose, set CORS_ORIGINS to the public hostnames the dashboard
# is served from (Traefik / hopto / future VPS domain).
_default_origins = "http://localhost:5173,http://localhost:3000,http://192.168.0.131,http://192.168.0.135,https://ionic.blisske.hopto.org"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth config ───────────────────────────────────────────────────────────────
# Legacy login-attempt logging (REAL_DB _init_login_log + _log_attempt) and
# the pre-SaaS in-process rate limiter (_failed_attempts / _check_rate_limit /
# _record_failure / _clear_failures) removed 2026-05-25 — all dead code.
# Rate limiting lives in shared.auth.RateLimiter; attempt logging in
# global.db via shared.auth.record_login_attempt() from the SaaS router.
SECRET_KEY                = os.getenv("API_SECRET_KEY", "change-me-in-production")
ALGORITHM                 = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 hours

API_USERNAME  = os.getenv("API_USERNAME",  "admin").strip().lower()
DEMO_USERNAME = os.getenv("DEMO_USERNAME", "demo").strip().lower()
DEMO_EMAIL    = os.getenv("DEMO_EMAIL",    "demo@foundationbots.com").strip().lower()


def get_db(user: str = "admin") -> sqlite3.Connection:
    """DEPRECATED — legacy string-based DB router. Use get_db_for(ctx)
    for per-user routing. Kept only for backward compat with any leftover
    callsites in this module.
    """
    if user == DEMO_USERNAME:
        try:
            shadow_on = config_manager.load_engine_config().get('oanda', {}).get('shadow_mode', False)
        except Exception:
            shadow_on = False
        path = REAL_DB if shadow_on else DEMO_DB
    else:
        path = REAL_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Per-user routing — same shape as Corinthian + Doric. See those repos'
# api/main.py for the canonical reasoning. Bug fixed in this initial
# Ionic SaaS port (rather than introduced + patched): per-user data
# is isolated from operator data from day one.
from dataclasses import dataclass
@dataclass(frozen=True)
class AuthCtx:
    user_id:         int
    legacy_username: str


def _per_user_config_path(user_id: int) -> Path:
    """Where a multi-tenant user's per-user Config.yaml lives on the
    bind-mounted data dir. Provisioner copies the template here at
    signup. Mirrors _per_user_db_path."""
    return REAL_DB.parent / "users" / str(user_id) / "Config.yaml"


def load_engine_config_for(ctx: 'AuthCtx') -> dict:
    """Return the engine config dict belonging to the auth context.

    Routing mirrors get_db_for(ctx):
      user_id=1 (operator)   → operator's /app/data/Config.yaml
      user_id=2 (demo)       → operator's config (demo shares it)
      user_id>=3 (tenant)    → /app/data/users/{user_id}/Config.yaml

    Falls back to operator's config if the per-user file is missing
    (provisioner race) or unreadable (corrupt yaml). Availability over
    isolation in that narrow window.

    Added 2026-05-25 — tenant /api/equity + WS tick were displaying
    operator's risk.initial_capital and drawdown thresholds.
    """
    if ctx.user_id <= 2:
        return config_manager.load_engine_config()
    per_user_path = _per_user_config_path(ctx.user_id)
    if not per_user_path.exists():
        return config_manager.load_engine_config()
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(per_user_path, 'r') as f:
            data = yaml.load(f)
        return dict(data) if data else {}
    except Exception:
        return config_manager.load_engine_config()


def _per_user_db_path(user_id: int) -> Path:
    """Where a multi-tenant user's per-user ionic.db lives on the
    bind-mounted data dir."""
    return REAL_DB.parent / "users" / str(user_id) / "ionic.db"


def _ensure_per_user_db_schema(per_user_path: Path) -> None:
    """Seed DDL from operator's DB into a fresh per-user file so SELECTs
    return 0 rows instead of erroring before the per-user engine has booted.

    Idempotent: if the file exists, has the trades table, AND is empty,
    this is a no-op. Defense-in-depth scrub against template leaks (see
    Doric incident 2026-05-25): if any rows show up, log loudly and
    DELETE them all before returning.
    """
    per_user_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        c = sqlite3.connect(per_user_path)
        existing = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
        ).fetchone()
        if existing:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )]
            row_total = 0
            for t in tables:
                try:
                    row_total += c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                except sqlite3.OperationalError:
                    pass
            if row_total == 0:
                c.close()
                return
            try:
                import logging
                logging.getLogger(__name__).error(
                    "Per-user DB %s had %d leaked rows across %d tables; "
                    "scrubbing in-place. Investigate the template.",
                    per_user_path, row_total, len(tables),
                )
            except Exception:
                pass
            for t in tables:
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            try:
                c.execute("DELETE FROM sqlite_sequence")
            except sqlite3.OperationalError:
                pass
            c.commit()
            c.close()
            return
        c.close()
    except sqlite3.Error:
        pass
    try:
        src = sqlite3.connect(REAL_DB)
        ddls = [r[0] for r in src.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL "
            "AND type IN ('table','index') "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        src.close()
        dst = sqlite3.connect(per_user_path)
        for ddl in ddls:
            try:
                dst.execute(ddl)
            except sqlite3.OperationalError:
                pass
        dst.commit()
        dst.close()
    except sqlite3.Error:
        pass


def get_db_for(ctx: 'AuthCtx') -> sqlite3.Connection:
    """Route per-user.
    user_id=1 (operator)   → REAL_DB
    user_id=2 (demo)       → DEMO_DB or REAL_DB depending on shadow_mode
    user_id>=3 (real user) → /app/data/users/{user_id}/ionic.db
                             (never falls back to REAL_DB — schema seeded
                              from REAL_DB so SELECTs return 0 rows
                              instead of erroring)
    """
    if ctx.user_id == 2 or ctx.legacy_username == DEMO_USERNAME:
        try:
            shadow_on = config_manager.load_engine_config().get('oanda', {}).get('shadow_mode', False)
        except Exception:
            shadow_on = False
        path = REAL_DB if shadow_on else DEMO_DB
    elif ctx.user_id == 1:
        path = REAL_DB
    else:
        per_user = _per_user_db_path(ctx.user_id)
        _ensure_per_user_db_schema(per_user)
        path = per_user
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_legacy_username(user_email: str) -> str:
    """Map a global.db user's email to the legacy 'admin'/'demo' string."""
    if user_email == DEMO_EMAIL:
        return DEMO_USERNAME
    return API_USERNAME

# ── Timestamp normalization ───────────────────────────────────────────────────
# SQLite stores CURRENT_TIMESTAMP as UTC text like "2026-04-27 18:50:43" with
# no timezone marker. JavaScript's `new Date(s)` parses such strings as LOCAL
# time, which silently corrupts every timestamp we render in the dashboard.
# This helper rewrites those values as ISO 8601 with a Z suffix so toLocaleString
# in the browser displays in the user's true local zone.
_TS_FIELDS = ('timestamp', 'entry_timestamp', 'updated', 'fetched')

def _normalize_ts(d: dict) -> dict:
    for k in _TS_FIELDS:
        v = d.get(k)
        if isinstance(v, str) and v and 'Z' not in v and '+' not in v:
            d[k] = v.replace(' ', 'T') + 'Z'
    return d

def _row(row) -> dict:
    """SQLite Row → dict with UTC timestamps normalized to ISO 8601 Z."""
    return _normalize_ts(dict(row))

# ── Pydantic models ───────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class RejectCandidateBody(BaseModel):
    reason: Optional[str] = None

# ── Auth helpers ──────────────────────────────────────────────────────────────
def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def _decode_bearer_ctx(token: str) -> AuthCtx:
    """Decode SaaS JWT → AuthCtx (user_id + legacy username).
    Raises HTTPException(401) on any failure. Shared by HTTP + WS."""
    try:
        claims = core_auth.decode_jwt(token)
    except AuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or deleted")
    return AuthCtx(
        user_id         = user_id,
        legacy_username = _resolve_legacy_username(user.email),
    )


from fastapi import Header

async def get_auth_ctx(authorization: Optional[str] = Header(None)) -> AuthCtx:
    """FastAPI dependency: decode JWT → AuthCtx with both user_id + legacy string."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    return _decode_bearer_ctx(token)


async def get_current_user(ctx: AuthCtx = Depends(get_auth_ctx)) -> str:
    """Backwards-compat wrapper — returns just the legacy username string.
    Endpoints that need per-user DB routing should depend on get_auth_ctx
    directly and call get_db_for(ctx)."""
    return ctx.legacy_username

# ── Market session helper ─────────────────────────────────────────────────────
# FX is 24/5: open Sun 17:00 ET → Fri 17:00 ET. execution.is_market_open() is
# a pure datetime check (no broker round-trip), so no caching needed even
# though this is called every 5s by the WebSocket tick.
def _market_session_status() -> dict:
    """Returns current FX session status for the dashboard.
    Open continuously Sun 17:00 ET → Fri 17:00 ET; closed weekends only."""
    ny  = pytz.timezone("America/New_York")
    now = datetime.now(ny)

    if execution.is_market_open():
        # Next Friday 17:00 ET is the weekly close. Only surface
        # closes_in_minutes when it's within 24h — otherwise the badge is
        # noisy ("closes in 4382m" on Monday morning).
        days_to_friday = (4 - now.weekday()) % 7
        friday_close = (now + timedelta(days=days_to_friday)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
        if friday_close <= now:
            friday_close += timedelta(days=7)

        mins = int((friday_close - now).total_seconds() // 60)
        if mins <= 24 * 60:
            return {"open": True, "status": "Open", "closes_in_minutes": mins}
        return {"open": True, "status": "Open"}

    # Closed — compute next Sunday 17:00 ET. Closed states are: Fri ≥17:00,
    # all of Sat, Sun <17:00. In every case the next open is the upcoming
    # Sunday 17:00 ET.
    days_to_sunday = (6 - now.weekday()) % 7
    sunday_open = (now + timedelta(days=days_to_sunday)).replace(
        hour=17, minute=0, second=0, microsecond=0
    )
    if sunday_open <= now:
        sunday_open += timedelta(days=7)

    mins = int((sunday_open - now).total_seconds() // 60)
    return {"open": False, "status": "Closed", "opens_in_minutes": mins}

# Legacy /api/auth/login + env-hash bcrypt creds retired on 2026-05-23
# (Ionic SaaS port). Path is now owned by api.auth.router (mounted above).
# Both operator (user_id=1) and demo (user_id=2) live in the shared
# foundation/data/global.db and authenticate through the new SaaS flow
# with argon2id passwords, email verification, 2FA, etc.

# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "Ionic API", "version": "1.0.0"}

@app.get("/api/session")
async def get_session(user: str = Depends(get_current_user)):
    """FX market session status. Ionic + Anton both expose /api/session
    since both asset classes have session boundaries; Tiberius doesn't
    (crypto trades 24/7)."""
    return _market_session_status()

@app.get("/api/trades")
async def get_trades(limit: int = 50, ctx: AuthCtx = Depends(get_auth_ctx)):
    conn = get_db_for(ctx)
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"trades": [_row(r) for r in rows]}
    finally:
        conn.close()

@app.get("/api/positions")
async def get_positions(ctx: AuthCtx = Depends(get_auth_ctx)):
    """
    Open positions with live enrichment:
      - mark_price / mark_value_usd: most-recent market_states.price × shares
      - unrealized_pnl_usd / pct: vs avg_entry_price (pyramid-aware) or entry_price
      - hours_held / days_held: wall-clock from entry_timestamp
      - stop_distance_pct: how far the current_stop sits below the mark
    Falls back to entry_price as the mark when no market data exists yet, so
    every numeric field is non-null for the frontend.
    """
    conn = get_db_for(ctx)
    try:
        rows = conn.execute("""
            SELECT op.*,
                   (SELECT price FROM market_states
                      WHERE symbol = op.symbol
                      ORDER BY id DESC LIMIT 1) AS _latest_price
            FROM open_positions op
        """).fetchall()
        now = datetime.now(timezone.utc)
        positions = []
        for r in rows:
            d = _row(r)
            entry  = d.get('entry_price')         or 0.0
            avg    = d.get('avg_entry_price')     or entry
            shares = d.get('shares')              or 0.0
            stop   = d.get('current_stop')        or 0.0
            mark   = d.pop('_latest_price', None) or entry

            mark_value = float(shares) * float(mark)
            cost_basis = float(shares) * float(avg)
            pnl_usd    = mark_value - cost_basis
            pnl_pct    = ((mark - avg) / avg * 100.0) if avg else 0.0
            stop_dist  = ((mark - stop) / mark * 100.0) if mark and stop else None

            ts = d.get('entry_timestamp')
            hours_held = None
            days_held  = None
            if ts:
                try:
                    entry_dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    delta = now - entry_dt
                    hours_held = delta.total_seconds() / 3600.0
                    days_held  = hours_held / 24.0
                except (ValueError, TypeError):
                    pass

            d.update({
                'mark_price':         float(mark),
                'mark_value_usd':     round(mark_value, 2),
                'unrealized_pnl_usd': round(pnl_usd, 2),
                'unrealized_pnl_pct': round(pnl_pct, 2),
                'hours_held':         round(hours_held, 2) if hours_held is not None else None,
                'days_held':          round(days_held, 2)  if days_held  is not None else None,
                'stop_distance_pct':  round(stop_dist, 2)  if stop_dist  is not None else None,
            })
            positions.append(d)
        return {"positions": positions}
    finally:
        conn.close()

def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _risk_state_snapshot(conn) -> dict:
    """Read risk_state row + shadow_account cash. Defaults are safe for legacy
    DBs that pre-date the Phase 1/3 migrations."""
    risk_row = conn.execute(
        "SELECT risk_mode, daily_halt, session_start_equity FROM risk_state WHERE id=1"
    ).fetchone() if _table_exists(conn, "risk_state") else None
    shadow_row = conn.execute(
        "SELECT cash, initial_capital FROM shadow_account WHERE id=1"
    ).fetchone() if _table_exists(conn, "shadow_account") else None
    return {
        "risk_mode":             risk_row[0]            if risk_row else "NORMAL",
        "daily_halt":            bool(risk_row[1])      if risk_row else False,
        "session_start_equity":  float(risk_row[2])     if risk_row and risk_row[2] is not None else None,
        "shadow_cash":           float(shadow_row[0])   if shadow_row else None,
        "shadow_initial":        float(shadow_row[1])   if shadow_row else None,
    }


def _fx_position_value_usd(symbol: str, units: float, price: float) -> float:
    """
    Mark-to-market value of an FX position in USD. Mirrors the helper in
    core/database.py (same logic — kept here to avoid an import dependency
    from api → core).

    - 'X/USD' (USD quote): value = units × price (price is USD per X)
    - 'USD/X' (USD base):  value = units (each unit is already 1 USD)
    """
    if not symbol:
        return float(units or 0) * float(price or 0)
    if symbol.startswith('USD/'):
        return float(units or 0)
    return float(units or 0) * float(price or 0)


def _compute_shadow_equity(conn, initial_fallback: float) -> tuple[float, float, float, float, float]:
    """
    Returns (initial_capital, shadow_equity_NET, pnl_NET_usd, gross_pnl_usd, fees_paid_usd).

    Equity = cash + market_value(open positions) − fees_paid_to_date.
    Fees come from trades.fee_usd, auto-recorded at log time via
    database.SHADOW_FEE_RATE (default 1 bp per leg modeling Oanda's
    ~1-pip spread on EUR/USD and similar majors). Added 2026-05-26 after
    Corinthian's hidden-fee blind spot — same pattern across all 3 bots.

    FX mark uses `_fx_position_value_usd` so USD-base pairs (USD/JPY, USD/CAD,
    USD/CHF) don't get inflated by units × price (1199 USD × 159 JPY/USD =
    $190K nonsense). See helper's docstring for the math.
    """
    fees_paid = float(conn.execute(
        "SELECT COALESCE(SUM(fee_usd), 0.0) FROM trades"
    ).fetchone()[0])

    shadow_row = conn.execute(
        "SELECT cash, initial_capital FROM shadow_account WHERE id=1"
    ).fetchone() if _table_exists(conn, "shadow_account") else None

    if shadow_row:
        cash    = float(shadow_row[0])
        initial = float(shadow_row[1])
        positions = conn.execute("""
            SELECT op.symbol, op.shares, op.entry_price,
                   (SELECT price FROM market_states WHERE symbol=op.symbol ORDER BY id DESC LIMIT 1)
            FROM open_positions op
        """).fetchall()
        market_value = sum(
            _fx_position_value_usd(
                sym, shares, latest if latest is not None else entry
            )
            for (sym, shares, entry, latest) in positions
        )
        equity_gross = cash + market_value
        equity_net   = equity_gross - fees_paid
        gross_pnl    = equity_gross - initial
        net_pnl      = equity_net   - initial
        return initial, equity_net, net_pnl, gross_pnl, fees_paid

    # Legacy path — pre-shadow_account DBs
    gross_pnl = float(conn.execute(
        "SELECT COALESCE(SUM(amount), 0.0) FROM trades WHERE action='SHADOW SELL'"
    ).fetchone()[0])
    net_pnl = gross_pnl - fees_paid
    return initial_fallback, initial_fallback + net_pnl, net_pnl, gross_pnl, fees_paid


@app.get("/api/equity")
async def get_equity(ctx: AuthCtx = Depends(get_auth_ctx)):
    conn = get_db_for(ctx)
    try:
        peak = conn.execute(
            "SELECT peak FROM equity_peak WHERE id=1"
        ).fetchone()
        # Per-tenant config — was leaking operator's initial_capital +
        # drawdown thresholds into every tenant's dashboard until the
        # 2026-05-25 bug-hunt caught it.
        config = load_engine_config_for(ctx)
        cfg_initial = config.get("risk", {}).get("initial_capital", 25000.0)
        initial, shadow_equity, net_pnl, gross_pnl, fees_paid = _compute_shadow_equity(conn, cfg_initial)
        peak_equity = float(peak[0]) if peak and peak[0] else initial

        # Drawdown computed against peak watermark (matches the autonomous loop's
        # tiered drawdown logic in main.py).
        drawdown_pct = max(0.0, (peak_equity - shadow_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
        risk = _risk_state_snapshot(conn)

        # Drawdown threshold echoes — same shape as Anton/Tiberius so the
        # frontend can render the same drawdown banner everywhere.
        risk_cfg = config.get("risk", {})
        return {
            "initial_capital":      round(initial, 2),
            "shadow_equity":        round(shadow_equity, 2),
            # pnl_usd is NET of fees; gross + fees surfaced separately
            # (added 2026-05-26 after Corinthian's hidden-fee finding).
            "pnl_usd":              round(net_pnl, 2),
            "gross_pnl_usd":        round(gross_pnl, 2),
            "fees_paid_usd":        round(fees_paid, 2),
            "pnl_pct":              round((net_pnl / initial) * 100, 2) if initial else 0,
            "peak_equity":          round(peak_equity, 2),
            "drawdown_pct":         round(drawdown_pct, 2),
            "risk_mode":            risk["risk_mode"],
            "daily_halt":           risk["daily_halt"],
            "session_start_equity": risk["session_start_equity"],
            "shadow_cash":          risk["shadow_cash"],
            "halt_pct":             float(risk_cfg.get("drawdown_halt_pct", 25.0)),
            "derisk_pct":           float(risk_cfg.get("drawdown_derisk_pct", 15.0)),
            "alert_pct":            float(risk_cfg.get("drawdown_alert_pct", 8.0)),
            "recover_pct":          float(risk_cfg.get("drawdown_recovery_pct", 10.0)),
        }
    finally:
        conn.close()


@app.get("/api/equity/curve")
async def get_equity_curve(ctx: AuthCtx = Depends(get_auth_ctx)):
    """Full shadow-equity timeseries — net of fees. Walks every trade row
    chronologically, subtracting each leg's fee_usd; SHADOW SELL legs
    additionally add their amount (realized gross P&L).

    Fees auto-recorded at log time via database.SHADOW_FEE_RATE (default
    1 bp per leg, modeling Oanda's ~1-pip spread on EUR/USD majors).
    """
    conn = get_db_for(ctx)
    try:
        config = load_engine_config_for(ctx)
        initial = config.get("risk", {}).get("initial_capital", 10000.0)
        rows = conn.execute(
            "SELECT timestamp, symbol, action, amount, fee_usd FROM trades "
            "ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    running = float(initial)
    points = []
    for r in rows:
        fee   = float(r["fee_usd"] or 0.0)
        delta = -fee
        if r["action"] == "SHADOW SELL":
            delta += float(r["amount"] or 0.0)
        elif r["action"] not in ("SHADOW BUY", "SHADOW BUY ADD", "SHADOW PARTIAL SELL"):
            if fee == 0:
                continue
        running += delta
        if r["action"] == "SHADOW SELL":
            ts = r["timestamp"]
            if isinstance(ts, str) and ts and 'Z' not in ts and '+' not in ts:
                ts = ts.replace(' ', 'T') + 'Z'
            points.append({
                "timestamp": ts,
                "symbol":    r["symbol"],
                "delta":     round(delta, 2),
                "equity":    round(running, 2),
            })
    return {
        "initial_capital":   float(initial),
        "points":            points,
        "shadow_equity_now": round(running, 2),
    }


@app.get("/api/config")
async def get_config(_admin = Depends(get_current_admin)):
    """Operator's engine config. Admin-only — non-operator tenants were
    seeing this until the 2026-05-25 bug-hunt caught the gap. Per-user
    config UX is a separate feature not yet built."""
    config = config_manager.load_engine_config()
    return {"config": config}

_CONFIG_REQUIRED_KEYS = {"oanda", "strategy", "risk", "ratchet", "ai_agent"}

@app.post("/api/config")
async def save_config(payload: dict, _admin = Depends(get_current_admin)):
    """Write operator's engine config. Admin-only.

    Pre-2026-05-25 the gate was a coarse demo-only block (`user ==
    DEMO_USERNAME`), which let every signed-up tenant POST this and
    clobber the operator's bind-mounted Config.yaml — caught by the
    bug-hunt harness against Doric. Same fix applied here for parity.
    """
    new_cfg = payload.get("config", {})
    missing = _CONFIG_REQUIRED_KEYS - set(new_cfg.keys())
    if missing:
        raise HTTPException(status_code=422, detail=f"Config missing required top-level keys: {sorted(missing)}")
    from ruamel.yaml import YAML
    import shutil
    # Honors CONFIG_PATH env var (matches config_manager.CONFIG_PATH) so the
    # API writes to whichever Config.yaml the engine reads — bind-mounted in
    # containers, source-tree default under systemd.
    config_path = Path(os.environ.get('CONFIG_PATH', CORE_DIR / "Config.yaml"))
    tmp_path    = config_path.with_suffix(".yaml.tmp")
    shutil.copy(config_path, config_path.with_suffix(".yaml.bak"))
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(tmp_path, "w") as f:
        yaml.dump(new_cfg, f)
    tmp_path.replace(config_path)
    return {"status": "saved"}

@app.post("/api/restart")
async def restart_service(_admin = Depends(get_current_admin)):
    """Cross-container engine restart for the OPERATOR's bot. Admin-only.

    Pre-2026-05-25 the gate was a demo-only block, which let every
    signed-up SaaS tenant touch the operator's flag file and force the
    operator's engine to exit + restart. Spammed, that's a cheap DoS.
    Same fix as Corinthian's 8037a97 and Doric's restart.

    Per-tenant engine restarts go through
    /api/admin/users/{id}/restart-engine (also admin-gated).
    """
    try:
        RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_FLAG_PATH.touch()
        return {"status": "restarting", "flag": str(RESTART_FLAG_PATH)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to signal engine: {e}")

@app.get("/api/tuning")
async def get_tuning(ctx: AuthCtx = Depends(get_auth_ctx)):
    conn = get_db_for(ctx)
    try:
        log = conn.execute(
            "SELECT * FROM tuning_log ORDER BY id DESC LIMIT 50"
        ).fetchall()
        snapshots = conn.execute(
            "SELECT * FROM param_snapshots ORDER BY id DESC LIMIT 20"
        ).fetchall()
        # Per (symbol × paradigm) closed-trade counts for the dashboard progress panel.
        progress = conn.execute(
            "SELECT symbol, strategy, COUNT(*) AS closed_count "
            "FROM trades WHERE action IN ('SHADOW SELL', 'SELL') "
            "GROUP BY symbol, strategy "
            "ORDER BY closed_count DESC, symbol, strategy"
        ).fetchall()
        return {
            "log": [_row(r) for r in log],
            "snapshots": [_row(r) for r in snapshots],
            "progress": [_row(r) for r in progress],
        }
    finally:
        conn.close()


def _db_path_for_ctx(ctx: AuthCtx) -> str:
    """Resolve the per-user DB file path get_db_for(ctx) would use."""
    conn = get_db_for(ctx)
    try:
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


@app.get("/api/tuning/candidate/{log_id}")
async def get_tuning_candidate(log_id: int, ctx: AuthCtx = Depends(get_auth_ctx)):
    """Forensic detail for a single tuning candidate."""
    import database as _db
    detail = _db.get_candidate_detail(log_id, db_path=_db_path_for_ctx(ctx))
    if detail is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return detail


@app.post("/api/tuning/candidate/{log_id}/reject")
async def reject_tuning_candidate(
    log_id: int,
    body: RejectCandidateBody,
    ctx: AuthCtx = Depends(get_auth_ctx),
):
    """Operator-driven candidate rejection. Admin-only."""
    if ctx.legacy_username == DEMO_USERNAME:
        raise HTTPException(status_code=403, detail="Demo account is read-only")
    import database as _db
    ok = _db.reject_candidate(log_id, reason=body.reason, db_path=_db_path_for_ctx(ctx))
    if not ok:
        raise HTTPException(status_code=404, detail="candidate not found or DB error")
    return {"status": "rejected", "log_id": log_id, "reason": body.reason}


@app.get("/api/market")
async def get_market(ctx: AuthCtx = Depends(get_auth_ctx), hours: int = 24):
    conn = get_db_for(ctx)
    try:
        rows = conn.execute("""
            SELECT symbol, price, adx, regime, trend, rsi, volume, avg_volume, timestamp
            FROM market_states
            WHERE timestamp >= datetime('now', ? || ' hours')
            ORDER BY symbol, timestamp ASC
        """, (f'-{hours}',)).fetchall()
        latest  = {}
        history = {}
        for r in rows:
            d   = _row(r)
            sym = d['symbol']
            latest[sym] = d
            if sym not in history:
                history[sym] = []
            history[sym].append(d)
        return {"latest": latest, "history": history}
    finally:
        conn.close()

@app.get("/api/watchlist")
async def get_watchlist(_admin = Depends(get_current_admin)):
    """Returns the OPERATOR's active symbol watchlist from Config.yaml.
    Admin-only — same fix as /api/config gate (42da616). Per-user
    watchlist UX is a separate feature not yet built."""
    config  = config_manager.load_engine_config()
    symbols = config.get("strategy", {}).get("active_symbols", [])
    return {"symbols": symbols}

# ── WebSocket ─────────────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        # Each entry is (ws, user_id, legacy_username). Scoping by
        # legacy_username alone was NOT enough — every non-demo user gets
        # legacy_username="admin", so a broadcast(only_user=API_USERNAME)
        # reached every signed-up user's socket and leaked the operator's
        # pending_events. Now we also track user_id so per-tenant scoping
        # is possible via only_user_id=. Bug caught 2026-05-25 by the
        # bug-hunt WS isolation harness.
        self.active: list[tuple[WebSocket, int, str]] = []

    async def connect(self, ws: WebSocket, user_id: int, user: str):
        await ws.accept()
        self.active.append((ws, user_id, user))

    def disconnect(self, ws: WebSocket):
        self.active = [(w, uid, u) for (w, uid, u) in self.active if w is not ws]

    async def broadcast(
        self,
        data: dict,
        *,
        only_user: Optional[str] = None,
        only_user_id: Optional[int] = None,
    ):
        """Send `data` to all connected sockets, or just to the ones that
        match the optional filters.

        - only_user:    legacy username string ("admin" | "demo"). Coarse;
                        matches every non-demo user when set to "admin".
                        Kept for backward compat with old call sites.
        - only_user_id: per-user_id scoping. Use this for operator-only
                        broadcasts (only_user_id=1) so pending_events from
                        REAL_DB don't leak to every tenant.
        """
        msg = json.dumps(data)
        dead: list[WebSocket] = []
        for ws, user_id, user in list(self.active):
            if only_user is not None and user != only_user:
                continue
            if only_user_id is not None and user_id != only_user_id:
                continue
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()


async def _drain_pending_events():
    """
    Background task: drains pending_events from every per-tenant DB plus
    operator's REAL_DB, broadcasts each row to the owning tenant's WS
    sockets via manager.broadcast(only_user_id=...).

    Per-tenant walking added 2026-05-25 — pre-fix this task only read
    REAL_DB so tenant fills never reached tenant dashboards.
    """
    import logging
    log = logging.getLogger("ionic.api.drain")
    users_dir = REAL_DB.parent / "users"

    async def _drain_one(db_path, broadcast_user_id):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id, timestamp, event_type, payload FROM pending_events "
                    "WHERE broadcast_at IS NULL ORDER BY id LIMIT 50"
                ).fetchall()
            except sqlite3.OperationalError:
                conn.close()
                return
            for r in rows:
                try:
                    payload = json.loads(r["payload"])
                except Exception:
                    payload = {}
                msg = {
                    "type":      r["event_type"],
                    "timestamp": r["timestamp"],
                    **payload,
                }
                await manager.broadcast(msg, only_user_id=broadcast_user_id)
                conn.execute(
                    "UPDATE pending_events SET broadcast_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), r["id"]),
                )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            log.warning(f"drain {db_path} user_id={broadcast_user_id}: {exc}")

    prune_counter = 0
    while True:
        try:
            await _drain_one(REAL_DB, 1)
            if users_dir.exists():
                for entry in users_dir.iterdir():
                    if not entry.is_dir() or not entry.name.isdigit():
                        continue
                    uid = int(entry.name)
                    if uid <= 2:
                        continue
                    per_user_db = entry / "ionic.db"
                    if per_user_db.exists():
                        await _drain_one(per_user_db, uid)

            prune_counter += 1
            if prune_counter >= 60:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                with sqlite3.connect(REAL_DB) as pconn:
                    pconn.execute(
                        "DELETE FROM pending_events WHERE broadcast_at IS NOT NULL AND broadcast_at < ?",
                        (cutoff,),
                    )
                    pconn.commit()
                prune_counter = 0
        except Exception as e:
            log.warning(f"pending_events drain iteration failed: {e}")
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def _start_event_drain():
    asyncio.create_task(_drain_pending_events())

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    # Authenticate via the new SaaS JWT; build full AuthCtx so WS ticks
    # route per-user (each tenant sees their own DB).
    try:
        claims = core_auth.decode_jwt(token)
        if claims.get("purpose"):
            await ws.close(code=1008, reason="Partial token cannot open WS")
            return
        user_id = int(claims["sub"])
    except (AuthError, KeyError, ValueError, TypeError):
        await ws.close(code=1008, reason="Invalid token")
        return
    user_obj = core_auth.get_user_by_id(user_id)
    if not user_obj:
        await ws.close(code=1008, reason="Account not found")
        return
    ws_ctx = AuthCtx(
        user_id         = user_id,
        legacy_username = _resolve_legacy_username(user_obj.email),
    )
    user = ws_ctx.legacy_username

    # Tag the socket with BOTH user_id and legacy username so broadcasts
    # can scope by either. user_id matters for tenant-isolated drains
    # like _drain_pending_events (operator-only).
    await manager.connect(ws, ws_ctx.user_id, user)
    try:
        while True:
            conn = get_db_for(ws_ctx)
            try:
                # Per-tenant config (ws_ctx) — same fix as /api/equity.
                config    = load_engine_config_for(ws_ctx)
                cfg_init  = config.get("risk", {}).get("initial_capital", 25000.0)
                initial, shadow_equity, net_pnl, gross_pnl, fees_paid = _compute_shadow_equity(conn, cfg_init)
                positions = conn.execute("SELECT * FROM open_positions").fetchall()
                recent    = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT 5"
                ).fetchall()
                peak_row  = conn.execute("SELECT peak FROM equity_peak WHERE id=1").fetchone()
                risk_snap = _risk_state_snapshot(conn)
            finally:
                conn.close()

            peak_equity = float(peak_row[0]) if peak_row and peak_row[0] else initial
            dd_pct      = max(0.0, (peak_equity - shadow_equity) / peak_equity * 100) if peak_equity > 0 else 0.0

            await ws.send_text(json.dumps({
                "type":           "tick",
                "timestamp":      datetime.now(timezone.utc).isoformat(),
                "shadow_equity":  round(shadow_equity, 2),
                "pnl_usd":        round(net_pnl, 2),
                "gross_pnl_usd":  round(gross_pnl, 2),
                "fees_paid_usd":  round(fees_paid, 2),
                "open_positions": len(positions),
                "recent_trades":  [_row(r) for r in recent],
                "session":        _market_session_status(),
                "risk_mode":      risk_snap["risk_mode"],
                "daily_halt":     risk_snap["daily_halt"],
                "drawdown_pct":   round(dd_pct, 2),
                "shadow_cash":    risk_snap["shadow_cash"],
            }))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(ws)

# ── Entry point ───────────────────────────────────────────────────────────────
# AGENT_HOST defaults to 0.0.0.0 (container-friendly). For local-only systemd
# style runs, override with AGENT_HOST=127.0.0.1.
#
# We pass the in-memory `app` object instead of the "main:app" import string
# because this file inserts CORE_DIR onto sys.path (above), and a fresh
# `import main` from uvicorn would resolve to core/main.py instead of this
# api/main.py. The container CMD uses `uvicorn main:app` directly (uvicorn
# imports first, sys.path side-effect comes second) so this only matters
# for `python api/main.py` direct runs.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("AGENT_HOST", "0.0.0.0"),
        port=int(os.getenv("AGENT_PORT", "8001")),
    )
