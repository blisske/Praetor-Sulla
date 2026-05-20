"""
Sulla API — FastAPI backend
Serves REST endpoints and WebSocket stream for the Sulla React dashboard.
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
REAL_DB = Path(os.environ.get('SQLITE_PATH',      CORE_DIR / "sulla_data.db"))
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
import config_manager
import execution

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sulla API", version="1.0.0")

@app.on_event("startup")
async def startup():
    _init_login_log()

# Container-friendly: comma-separated CORS_ORIGINS env var with sensible
# defaults. In compose, set CORS_ORIGINS to the public hostnames the dashboard
# is served from (Traefik / hopto / future VPS domain).
_default_origins = "http://localhost:5173,http://localhost:3000,http://192.168.0.131,http://192.168.0.135,https://sulla.blisske.hopto.org"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiter ───────────────────────────────────────────────────────────────
_failed_attempts: dict = defaultdict(list)
RATE_LIMIT_MAX    = 5
RATE_LIMIT_WINDOW = 900  # 15 minutes

def _check_rate_limit(ip: str):
    now = time.time()
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_failed_attempts[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {RATE_LIMIT_WINDOW // 60} minutes."
        )

def _record_failure(ip: str):
    _failed_attempts[ip].append(time.time())

def _clear_failures(ip: str):
    _failed_attempts.pop(ip, None)

# ── Login attempt logging ─────────────────────────────────────────────────────
def _init_login_log():
    try:
        conn = sqlite3.connect(REAL_DB)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS login_attempts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                username  TEXT,
                ip        TEXT,
                result    TEXT,
                detail    TEXT
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️  Could not init login_attempts table: {e}")

def _log_attempt(username: str, ip: str, result: str, detail: str = ""):
    try:
        conn = sqlite3.connect(REAL_DB)
        conn.execute(
            "INSERT INTO login_attempts (username, ip, result, detail) VALUES (?, ?, ?, ?)",
            (username, ip, result, detail)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── Auth config ───────────────────────────────────────────────────────────────
SECRET_KEY                = os.getenv("API_SECRET_KEY", "change-me-in-production")
ALGORITHM                 = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 hours

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Usernames normalized at load — login + JWT sub are lowercased so all
# downstream user == API_USERNAME / DEMO_USERNAME comparisons are case-insensitive.
API_USERNAME      = os.getenv("API_USERNAME", "admin").strip().lower()
API_PASSWORD_HASH = os.getenv("API_PASSWORD_HASH", "")
DEMO_USERNAME     = os.getenv("DEMO_USERNAME", "demo").strip().lower()
DEMO_PASSWORD_HASH = os.getenv("DEMO_PASSWORD_HASH", "")

def get_db(user: str = "admin") -> sqlite3.Connection:
    """Return a DB connection scoped to user role + bot mode.

    Admin always sees the live runtime DB. The demo user sees:
      - live DB while shadow_mode is on (paper trading — safe to share)
      - static demo_data.db once shadow_mode is off (real money → privacy)
    """
    if user == DEMO_USERNAME:
        try:
            shadow_on = config_manager.load_engine_config().get('oanda', {}).get('shadow_mode', False)
        except Exception:
            shadow_on = False  # fail closed — better to show stale demo than risk leaking live data
        path = REAL_DB if shadow_on else DEMO_DB
    else:
        path = REAL_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

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

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception

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

# ── Auth endpoint ─────────────────────────────────────────────────────────────
@app.post("/api/auth/login", response_model=Token)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    ip = request.client.host
    _check_rate_limit(ip)

    # Case-insensitive username — preserve original for the audit log
    submitted = (form_data.username or "").strip().lower()
    authenticated = False
    if submitted == API_USERNAME:
        if API_PASSWORD_HASH and verify_password(form_data.password, API_PASSWORD_HASH):
            authenticated = True
    elif submitted == DEMO_USERNAME:
        if DEMO_PASSWORD_HASH and verify_password(form_data.password, DEMO_PASSWORD_HASH):
            authenticated = True

    if not authenticated:
        _record_failure(ip)
        remaining = RATE_LIMIT_MAX - len(_failed_attempts[ip])
        detail = f"Incorrect username or password ({remaining} attempt{'s' if remaining != 1 else ''} remaining)"
        _log_attempt(form_data.username, ip, "FAIL", detail)
        raise HTTPException(status_code=400, detail=detail)

    _clear_failures(ip)
    _log_attempt(form_data.username, ip, "SUCCESS")

    token = create_access_token(
        data={"sub": submitted},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": token, "token_type": "bearer"}

# ── REST endpoints ────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "Sulla API", "version": "1.0.0"}

@app.get("/api/session")
async def get_session(user: str = Depends(get_current_user)):
    """FX market session status. Sulla + Anton both expose /api/session
    since both asset classes have session boundaries; Tiberius doesn't
    (crypto trades 24/7)."""
    return _market_session_status()

@app.get("/api/trades")
async def get_trades(limit: int = 50, user: str = Depends(get_current_user)):
    conn = get_db(user)
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"trades": [_row(r) for r in rows]}
    finally:
        conn.close()

@app.get("/api/positions")
async def get_positions(user: str = Depends(get_current_user)):
    """
    Open positions with live enrichment:
      - mark_price / mark_value_usd: most-recent market_states.price × shares
      - unrealized_pnl_usd / pct: vs avg_entry_price (pyramid-aware) or entry_price
      - hours_held / days_held: wall-clock from entry_timestamp
      - stop_distance_pct: how far the current_stop sits below the mark
    Falls back to entry_price as the mark when no market data exists yet, so
    every numeric field is non-null for the frontend.
    """
    conn = get_db(user)
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


def _compute_shadow_equity(conn, initial_fallback: float) -> tuple[float, float, float]:
    """
    Returns (initial_capital, shadow_equity, pnl_usd).

    Prefers the post-Phase-1 shadow_account ledger when present:
      equity = cash + market_value(open positions at most-recent market_states price)
    Falls back to the legacy "initial + sum(realized)" calc when shadow_account
    is missing — keeps the API working against pre-pivot DBs.
    """
    shadow_row = conn.execute(
        "SELECT cash, initial_capital FROM shadow_account WHERE id=1"
    ).fetchone() if _table_exists(conn, "shadow_account") else None

    if shadow_row:
        cash    = float(shadow_row[0])
        initial = float(shadow_row[1])
        # Market value: shares × most-recent market_states.price per symbol;
        # falls back to entry_price when no market data exists yet.
        positions = conn.execute("""
            SELECT op.shares, op.entry_price,
                   (SELECT price FROM market_states WHERE symbol=op.symbol ORDER BY id DESC LIMIT 1)
            FROM open_positions op
        """).fetchall()
        market_value = sum(
            float(shares or 0) * (float(latest) if latest is not None else float(entry or 0))
            for (shares, entry, latest) in positions
        )
        equity  = cash + market_value
        pnl_usd = equity - initial
        return initial, equity, pnl_usd

    # Legacy path
    pnl = conn.execute(
        "SELECT COALESCE(SUM(amount), 0.0) FROM trades WHERE action='SHADOW SELL'"
    ).fetchone()[0]
    return initial_fallback, initial_fallback + pnl, pnl


@app.get("/api/equity")
async def get_equity(user: str = Depends(get_current_user)):
    conn = get_db(user)
    try:
        peak = conn.execute(
            "SELECT peak FROM equity_peak WHERE id=1"
        ).fetchone()
        config = config_manager.load_engine_config()
        cfg_initial = config.get("risk", {}).get("initial_capital", 25000.0)
        initial, shadow_equity, pnl = _compute_shadow_equity(conn, cfg_initial)
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
            "pnl_usd":              round(pnl, 2),
            "pnl_pct":              round((pnl / initial) * 100, 2) if initial else 0,
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

@app.get("/api/config")
async def get_config(user: str = Depends(get_current_user)):
    config = config_manager.load_engine_config()
    return {"config": config}

_CONFIG_REQUIRED_KEYS = {"oanda", "strategy", "risk", "ratchet", "ai_agent"}

@app.post("/api/config")
async def save_config(payload: dict, user: str = Depends(get_current_user)):
    if user == DEMO_USERNAME:
        raise HTTPException(status_code=403, detail="Demo account is read-only")
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
async def restart_service(user: str = Depends(get_current_user)):
    """
    Cross-container engine restart. Writes a flag file that the engine's main
    loop watches; the engine deletes the flag and exits cleanly, then docker
    compose `restart: unless-stopped` brings it back with the freshly saved
    Config.yaml. No host-side privileges (sudo/systemctl) required.
    """
    if user == DEMO_USERNAME:
        raise HTTPException(status_code=403, detail="Demo account is read-only")
    try:
        RESTART_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_FLAG_PATH.touch()
        return {"status": "restarting", "flag": str(RESTART_FLAG_PATH)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to signal engine: {e}")

@app.get("/api/tuning")
async def get_tuning(user: str = Depends(get_current_user)):
    conn = get_db(user)
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


def _db_path_for_user(user: str) -> str:
    """Pick the DB file path the get_db dispatch would use for this user."""
    conn = get_db(user)
    try:
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


@app.get("/api/tuning/candidate/{log_id}")
async def get_tuning_candidate(log_id: int, user: str = Depends(get_current_user)):
    """
    Forensic detail for a single tuning candidate: proposal record, snapshot
    state, the trades that drove the proposal, and any trades since (counting
    toward the shadow validation window). Powers the Tuning page's Inspect
    modal so the operator can decide whether to reject manually or wait for the
    validator to make the call.
    """
    import database as _db
    detail = _db.get_candidate_detail(log_id, db_path=_db_path_for_user(user))
    if detail is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return detail


@app.post("/api/tuning/candidate/{log_id}/reject")
async def reject_tuning_candidate(
    log_id: int,
    body: RejectCandidateBody,
    user: str = Depends(get_current_user),
):
    """
    Operator-driven candidate rejection. Admin-only.

    Does NOT bypass the shadow validation gate — this is the OPPOSITE of
    bypass (manual veto BEFORE the gate completes). The 'Never bypass this
    gate' CLAUDE.md rule is about promotion, not rejection.
    """
    if user == DEMO_USERNAME:
        raise HTTPException(status_code=403, detail="Demo account is read-only")
    import database as _db
    ok = _db.reject_candidate(log_id, reason=body.reason, db_path=_db_path_for_user(user))
    if not ok:
        raise HTTPException(status_code=404, detail="candidate not found or DB error")
    return {"status": "rejected", "log_id": log_id, "reason": body.reason}


@app.get("/api/market")
async def get_market(user: str = Depends(get_current_user), hours: int = 24):
    conn = get_db(user)
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
async def get_watchlist(user: str = Depends(get_current_user)):
    """Returns the active symbol watchlist from Config.yaml."""
    config  = config_manager.load_engine_config()
    symbols = config.get("strategy", {}).get("active_symbols", [])
    return {"symbols": symbols}

# ── WebSocket ─────────────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        # Each entry is (ws, user) so broadcasts can be scoped by role.
        # pending_events lives only in the real DB; demo sockets must not
        # see real-trade fills bleeding through manager.broadcast().
        self.active: list[tuple[WebSocket, str]] = []

    async def connect(self, ws: WebSocket, user: str):
        await ws.accept()
        self.active.append((ws, user))

    def disconnect(self, ws: WebSocket):
        self.active = [(w, u) for (w, u) in self.active if w is not ws]

    async def broadcast(self, data: dict, *, only_user: Optional[str] = None):
        """Send `data` to all connected sockets, or just those for `only_user`."""
        msg = json.dumps(data)
        dead: list[WebSocket] = []
        for ws, user in list(self.active):
            if only_user is not None and user != only_user:
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
    Background task: drains the engine's pending_events queue and broadcasts
    each row over /ws via manager.broadcast(). The engine writes events
    (trade fills, risk-mode transitions) inside database.log_trade() and
    database.update_risk_state(); this task ships them to connected admin
    clients with ~1s latency vs. waiting up to 5s for the next tick.

    Failures are swallowed and retried on the next iteration — a transient
    DB lock or JSON glitch must not take the API down. Old rows are pruned
    after 7 days so the table doesn't grow unbounded.
    """
    import logging
    log = logging.getLogger("sulla.api.drain")
    prune_counter = 0
    while True:
        try:
            conn = sqlite3.connect(REAL_DB)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, event_type, payload FROM pending_events "
                "WHERE broadcast_at IS NULL ORDER BY id LIMIT 50"
            ).fetchall()
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
                # pending_events lives in REAL_DB only — scope the broadcast
                # to admin sockets so demo dashboards keep showing demo data.
                await manager.broadcast(msg, only_user=API_USERNAME)
                conn.execute(
                    "UPDATE pending_events SET broadcast_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), r["id"]),
                )
            conn.commit()

            prune_counter += 1
            if prune_counter >= 60:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                conn.execute(
                    "DELETE FROM pending_events WHERE broadcast_at IS NOT NULL AND broadcast_at < ?",
                    (cutoff,),
                )
                conn.commit()
                prune_counter = 0

            conn.close()
        except Exception as e:
            log.warning(f"pending_events drain iteration failed: {e}")
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def _start_event_drain():
    asyncio.create_task(_drain_pending_events())

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    # Authenticate before accepting the connection
    user = None
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username in (API_USERNAME, DEMO_USERNAME):
            user = username
    except Exception:
        pass
    if user is None:
        await ws.close(code=1008)
        return

    await manager.connect(ws, user)
    try:
        while True:
            conn = get_db(user)
            try:
                config    = config_manager.load_engine_config()
                cfg_init  = config.get("risk", {}).get("initial_capital", 25000.0)
                initial, shadow_equity, pnl = _compute_shadow_equity(conn, cfg_init)
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
                "pnl_usd":        round(pnl, 2),
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
