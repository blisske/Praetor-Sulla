# CLAUDE.md — Ionic / Foundation FX Project Context

> This file gives Claude Code the authoritative context for working on this project.
> Read this file first, then `WORKING_STATE.md` for current state.

---

## Project Identity

**Foundation** is the platform. Three trading instances live under it:

| Instance | Role | Status |
|---|---|---|
| **Doric** | TradFi — Alpaca paper | Shadow mode, live deployment gates open |
| **Corinthian** | Crypto — Kraken | Shadow mode, live deployment gates open |
| **Ionic** | FX — Oanda | **Phase 1 scaffold** (this codebase) |

This codebase is **Ionic** specifically. All three bots run as Docker containers
on the WSL2 host `battlemage` (LAN `192.168.0.135`), orchestrated by the
swarm-root compose at `~/swarm/docker-compose.yml`.

---

## Project Goal

Autonomous long-only spot FX trading bot designed to fill the time-zone gap
left by Doric (US-equity session) and Corinthian (crypto). Trades the 7 major
currency pairs unleveraged via Oanda's v20 REST API.

**Mandate:** consistent, risk-adjusted growth that compounds over years.
Long-only spot, no leverage, no shorts. Macro-driven moves over technical
chop. Same growth-disciplined posture as Doric and Corinthian.

---

## Stack

- **Language:** Python 3.12 (async architecture)
- **Broker:** Oanda v20 REST API. Practice account is free + unlimited;
  live account is paid + funded. Token-based OAuth, no GUI dependencies
  (unlike IBKR's TWS/IBGateway socket protocol).
- **Backend:** FastAPI on `0.0.0.0:8002` inside `ionic-api` (published to
  host loopback `127.0.0.1:8002` for debugging; external traffic flows via
  swarm Traefik).
- **Frontend:** React + Vite + Tailwind v4, built into `ionic-web` (nginx
  serves `dist/`, proxies `/api/` and `/ws` to `ionic-api:8002`).
- **Database:** SQLite at `/app/data/ionic.db` inside the container,
  bind-mounted from `~/swarm/ionic/data/ionic.db` on the host. WAL mode
  enabled.
- **AI Sentiment Layer:** Gemma 4 26B on Battlemage B70 GPU via LM Studio —
  reached from inside containers at `http://host.docker.internal:1234/v1`.
- **News source:** Brave Search API (same as Doric/Corinthian).
- **Command & Control:** Telegram bot — Phase 2+.
- **Infrastructure:** Docker Compose stack on Windows 11 + WSL2 + Docker
  Desktop. Three containers (`ionic-engine`, `ionic-api`, `ionic-web`).
- **Host:** `battlemage` — LAN IP `192.168.0.135`. Same machine runs Doric,
  Corinthian, Milton, Fixit, and LM Studio.

---

## Directory Layout

```
~/swarm/ionic/                  # On the host (battlemage)
├── repo/                       # git clone of blisske/Foundation-Ionic
│   ├── core/                   # Trading engine source
│   │   ├── main.py             # Phase 1 placeholder (heartbeat + idle loop)
│   │   ├── _main_anton_reference.py  # Doric's main.py preserved as Phase 2 porting reference
│   │   ├── market_data.py, strategy.py, execution.py, ai_brain.py, etc.
│   │   ├── config_manager.py
│   │   ├── database.py
│   │   ├── tuner.py
│   │   └── demo_data.db        # Static demo snapshot (baked into image)
│   ├── api/                    # FastAPI source
│   ├── web/                    # React + Vite source + nginx Dockerfile
│   ├── Dockerfile              # Multi-stage: targets `engine` and `api`
│   ├── docker-compose.yml      # Three services: ionic-engine, ionic-api, ionic-web
│   ├── requirements.txt
│   └── WORKING_STATE.md
├── data/                       # Bind-mounted into engine + api at /app/data
│   ├── ionic.db                # Live SQLite DB (created on first engine boot)
│   ├── Config.yaml             # Live, editable; survives image rebuilds
│   ├── .restart_engine         # API touches → engine exits → compose restarts
│   └── .engine_heartbeat       # Engine touches per cycle
└── .env                        # Container runtime secrets (Oanda token, Telegram, JWT, bcrypt)
```

The swarm root at `~/swarm/docker-compose.yml` `include:`s this compose file
alongside Doric's, Corinthian's, Milton's, and Fixit's. **All routine deploys
run from `~/swarm/`** — running compose from `~/swarm/ionic/repo/` directly
uses a different compose project and collides with the swarm-managed
`container_name:` declarations.

---

## Services

Three docker containers, all on a per-bot bridge `ionic-net` (engine + api
private to the bot, web multi-homed onto `swarm-net` so Traefik can reach it):

| Container | Image target | Bind | Role |
|---|---|---|---|
| `ionic-engine` | `Dockerfile` target `engine` | none | Trading daemon + Telegram bot (Phase 2+) |
| `ionic-api` | `Dockerfile` target `api` | `127.0.0.1:8002` | FastAPI dashboard backend + WebSocket |
| `ionic-web` | `web/Dockerfile` | `:8085` (LAN debug) | React SPA via nginx; proxies `/api/`, `/ws` to `ionic-api:8002` |

```bash
# Canonical: run from the swarm root (~/swarm/)
docker compose up -d --build ionic-engine
docker compose restart ionic-engine
docker compose logs -f ionic-engine
docker compose logs ionic-api --tail 50
```

The API config-save endpoint coordinates engine restarts via a flag file
(`/app/data/.restart_engine`) — engine watches the flag at the top of every
loop iteration, deletes it, and **calls `os._exit(0)` so compose's
`restart: unless-stopped` actually fires**. Don't switch to the "graceful
break out of inner loop" pattern; that's the bug Doric/Corinthian hit on
2026-05-12 — the process kept running on Telegram polling alone after the
loop ended.

---

## External Access

- **Domain:** `ionic.blisske.hopto.org` (No-IP dynamic DNS).
- **Reverse proxy:** Traefik v3 at `~/swarm/proxy/` (file provider — routes
  in `~/swarm/proxy/dynamic/ionic.yml`).
- **SSL:** Let's Encrypt via Traefik HTTP-01 challenge.
- **LAN debug:** `http://192.168.0.135:8085` (ionic-web direct publish) and
  `http://127.0.0.1:8002` (ionic-api loopback). Remove both `ports:` blocks
  once Traefik fronting is fully verified.

---

## Auth

| Role | Username | Access |
|---|---|---|
| Admin | `admin` | Full — can restart, save config |
| Demo | `demo` | Read-only — uses static demo DB snapshot |

Password hashes stored in `~/swarm/ionic/.env` (bcrypt 4.0.1 — pinned for
passlib compatibility). API secret key is a 64-char random hex string.

---

## Core Design Pillars (inherited from Doric/Corinthian)

Ionic shares the Foundation architecture wholesale:

1. **Multi-paradigm signal engine** — Trend Following, Mean Reversion,
   Volatility Breakout, Liquidity Sweep. Regime gate via ADX.
2. **2+1+1 consensus** — Primary paradigm + 2-of-3 supporting signals + AI
   verdict (BEARISH = veto) + score gate. Min consensus default 3.
3. **Self-tuning** — 10 closed shadow trades per (symbol, paradigm) triggers
   a proposal. 10 more validate. PF improvement ≥ 5% → PROMOTED.
4. **Session-aware** — FX is 24/5 (Sun 17:00 ET → Fri 17:00 ET). Macro
   blackout calendar (Phase 4) skips entries around NFP/FOMC/ECB/CPI prints.

---

## FX-specific differences (vs Doric/Corinthian)

| | Corinthian | Doric | Ionic |
|---|---|---|---|
| Broker | Kraken/CCXT | Alpaca | Oanda v20 |
| Hours | 24/7 | 9:30–4 ET | 24/5 |
| Bar | 1h | 30min | 1h |
| Sizing | USD notional | Whole shares | Units of base currency |
| Pip math | n/a | n/a | 0.0001 (0.01 for JPY pairs) |
| Calendar | none | Earnings (yfinance) | Macro events (Phase 4 TBD) |
| Force-exit | none | 3:50 PM ET | Optional Friday flatten |
| API port | 8000 | 8001 | **8002** |
| Web port | 8082 | 8080 | **8085** (8084 is fixit-api) |

---

## Phase Status

- ✅ **Phase 1** — Repo + infra scaffold. Containers boot, dashboard loads,
  restart flow verified.
- ✅ **Phase 2** — Oanda broker adapter. Real OHLCV bars + token auth,
  full order/position support, per-user credential dispatch.
- ✅ **Phase 2.5** — main.py live-path wiring (autonomous BUY, manual /buy,
  pyramid, TP, ratchet, reconciliation) — `oanda.shadow_mode: false` now
  actually trades on Oanda.
- ✅ **Phase 3** — FX math (pip values, JPY handling, unit sizing) —
  `core/fx_math.py` shipped.
- ✅ **Phase 4** — Macro calendar blackout — `core/macro_calendar.py`
  shipped; blocks entries around HIGH-impact NFP/FOMC/ECB/CPI prints.
- ✅ **Phase 5** — Shadow contract (every shadow path branches mode-aware),
  Telegram cmds wired (`/buy`, `/kill`, `/confirm_kill`, `/protect`,
  `/apply`, `/report`, `/pnl`, `/indicators`, `/resume`, `/restart`,
  `/calendar`), partial-TP + kill-switch live paths, Oanda equity
  reconciliation (live_account_cache table), tuner promotion on close,
  Guide page updated for SaaS era + §988 tax.
- ⏳ **Phase 6** — Soak + live deployment gates. See below.

---

## Live Deployment Gates (ALL must clear before `shadow_mode: false`)

Mirrors the gates Corinthian + Doric use, adapted for FX:

1. **Operator action: BotFather token in `~/swarm/ionic/.env`** as
   `TELEGRAM_BOT_TOKEN=<token>`. Engine starts polling automatically
   after restart.
2. **Per-user Oanda token connected** at `/settings/broker` and validated
   (scope='trade'). Account ID format (`101-...` practice vs `001-...`
   live) confirms the right environment.
3. **7-14 day shadow soak** with ALL of:
   - ≥5 closed shadow trades across multiple pairs/paradigms
   - ≥1 successful pyramid sequence (entry + at least one add — requires
     `strategy.pyramiding.enabled: true` during soak)
   - ≥1 simulated tiered-drawdown event (manually move peak via DB or
     wait for natural drawdown crossing 8% / 15% / 25% tiers)
   - ≥1 self-tuning cycle complete (10+ closed shadow trades per symbol
     triggers tuner; another 10 to validate; promotion requires PF ≥+5%)
   - No circuit-breaker trips during the soak (frozen mode, daily-loss
     halt, or peak-drawdown halt should all stay clear)
   - No orphan Oanda positions (account check shows 0 unexplained
     positions at end of soak)
4. **Config alignment** per `core/Config.live.example.yaml` — lower
   `risk_per_trade_pct` from paper-aggressive (5%) to live-conservative
   (1.5%), `position_size_max_pct` from 12% to 8%, enable
   `correlation_aware_sizing` (FX majors are USD-correlated).
5. **No active drawdown halt** at the moment of flip — `risk_state.risk_mode`
   must be `NORMAL` (not `ALERT`, `DERISK`, or `HALT`).
6. **Service restart confirmed clean** — full `docker compose up -d
   --build ionic-engine` cycles with no errors in logs.
7. **`oanda.shadow_mode: true → false`** in Config.yaml as the final step.
   Engine restart picks it up. Next cycle's BUYs go through
   `execute_buy_with_stop` against the real Oanda account.

After flip: watch the dashboard's account-state source indicator —
if it shows `live-fallback-shadow` instead of `live`, the Oanda NAV
cache is stale (engine cycle hasn't completed or Oanda is unreachable).

---

## Do Not Re-Suggest (Inherited Lessons)

These are lessons learned by Doric and Corinthian the hard way. Ionic starts
with them baked in:

- **Engine restart-flag flow** — `_check_restart_flag()` must call
  `os._exit(0)` after consuming the flag. Setting a `system_active = False`
  and `break`ing only exits the inner loop; the main coroutine keeps the
  process alive on Telegram polling and compose never restarts. The web
  Restart button + `/restart` Telegram command depend on this.
- **nginx DNS-cache 502** — Don't use the static `upstream … { server
  ionic-api:8002; }` form. Every `-api` rebuild gets a new bridge-network
  IP and nginx will silently cache the dead old one. Use `resolver
  127.0.0.11 valid=10s ipv6=off;` + `set $upstream_api http://ionic-api:8002;
  proxy_pass $upstream_api;` (the variable in `proxy_pass` is what triggers
  per-request re-resolution).
- **Compose-from-repo-dir collision** — Always run `docker compose` from
  `~/swarm/`. Running from `~/swarm/ionic/repo/` creates a different
  compose project that collides with the swarm-managed `container_name:`
  declarations (`Error response from daemon: Conflict. The container name
  "/ionic-engine" is already in use`).
- **Telegram bot token uniqueness** — Each bot needs its own BotFather
  token. Reusing Doric's or Corinthian's token would cause polling conflicts
  (Telegram allows only one polling client per bot). Phase 1 engine doesn't
  poll Telegram at all to avoid this; Phase 2+ wires in a dedicated Ionic
  bot.
- **Leverage is intentionally absent** — Oanda accounts default to margin.
  Ionic deliberately trades unleveraged (1:1 sizing). Smaller positions for
  the same risk; slower compounding; fine. The whole point of picking spot
  in Corinthian (and cash account in Doric) was tail-risk discipline; same
  principle here. Don't propose enabling margin to "boost returns."

---

## Working Protocol

### Phase-by-phase
1. Propose a plan
2. Get approval
3. Execute one phase
4. Verify success
5. Move to next phase

### Before every change
- Read `WORKING_STATE.md` first
- Don't rebuild something already built
- Don't re-open resolved bugs (see "Do Not Re-Suggest")
- For bugs: identify root cause before writing a fix
- Explain the "why" not just the "what"

### After every session
- Update `WORKING_STATE.md`
- Commit and push to `github.com/blisske/Foundation-Ionic`

### Flag with ⚠️
Any suggestion that could affect live trading capital. (Ionic is
pre-broker-integration so live-trading risk is zero in Phase 1; this will
matter from Phase 2 onward.)

---

## How to Resume With Claude

1. Upload this CLAUDE.md + WORKING_STATE.md at session start
2. SSH into the host: `ssh blisske@192.168.0.135`
3. `cd ~/swarm/` (always — compose runs from the swarm root)
4. For one-off Python: `docker exec -it ionic-engine python3` (limited to
   stdlib in Phase 1 — no oanda/telegram libs in requirements.txt yet)
5. Doric and Corinthian are sister bots in the same swarm; their repos are at
   `~/swarm/doric/repo` and `~/swarm/corinthian/repo`. The Doric Guide page
   (`web/src/pages/Guide.jsx`) is the closest reference for the Ionic Guide
   we'll write in Phase 5.
