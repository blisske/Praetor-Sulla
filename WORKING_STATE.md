# WORKING_STATE.md — Sulla V1 Session Log

> Maintained by Claude. Read at the start of every new conversation.
> Last updated: 2026-05-17 (Phase 1 — infrastructure scaffold landed)

---

## Current System State

| Item | Value |
|---|---|
| Phase | **1 — scaffold complete** |
| Engine mode | Placeholder (touches heartbeat + honors restart flag; no trading) |
| Broker | Oanda v20 (planned; not yet integrated) |
| Universe | EUR/USD, USD/JPY, GBP/USD, USD/CHF, AUD/USD, USD/CAD, NZD/USD (Phase 2+) |
| Timeframe | 1h (planned) |
| Shadow ledger | Empty (`/app/data/sulla.db` schema initialized, no trades yet) |
| Telegram | Off (Phase 2+) |
| Dashboard | Reachable at `http://192.168.0.135:8085/` (LAN debug) and `https://sulla.blisske.hopto.org/` (Traefik + Let's Encrypt) |

---

## Phase 1 — Infrastructure Scaffold (2026-05-17)

Cloned Anton's repo structure as the starting point, renamed everywhere
(anton→sulla, ports 8001→8002 and 8080→8085), wired into the swarm.

### What landed

- **Compose stack** — three containers `sulla-engine` / `sulla-api` /
  `sulla-web` with per-bot bridge net `sulla-net` and shared `swarm-net`
  for Traefik. Container names `container_name:` baked in so the swarm-root
  compose project owns them.
- **Ports** — sulla-api on `127.0.0.1:8002` (loopback debug),
  sulla-web on `:8085` (LAN debug). 8084 was skipped because fixit-api owns
  it. The canonical public ingress is Traefik at `sulla.blisske.hopto.org`.
- **Engine** — `core/main.py` is a Phase 1 placeholder that touches
  `.engine_heartbeat`, watches `.restart_engine`, and calls `os._exit(0)` on
  flag detection so compose `restart: unless-stopped` actually fires.
  Initializes the empty DB schema on boot so the dashboard endpoints don't
  500 on "table not found." No Oanda calls, no Telegram polling, no trading
  logic.
- **Anton reference preserved** — `core/_main_anton_reference.py` is Anton's
  full 1,700-line engine, kept verbatim as the Phase 2 porting source.
  Renamed off `.bak` so git tracks it.
- **API** — FastAPI backend boots clean. `/api/health` → 200,
  `/api/config` → 401 (auth required), `/api/login` accepts the existing
  admin/demo creds.
- **Web** — React SPA serves at port 8085. Login page rebranded ("Sulla
  trading dashboard", "Autonomous FX intelligence"), sidebar header reads
  "Sulla · FX". Other pages (Dashboard / Trades / Tuning / Market / Config)
  inherited unchanged from Anton — they read from the empty Sulla DB and
  render with empty-state placeholders.
- **nginx** — `web/nginx.conf` uses the `resolver 127.0.0.11` + variable
  in `proxy_pass` pattern (the May-12 Anton/Tiberius fix), so future
  `sulla-api` rebuilds won't 502 the dashboard.
- **Traefik route** — `~/swarm/proxy/dynamic/sulla.yml` registered;
  file-provider picked it up automatically. Let's Encrypt cert issued for
  `sulla.blisske.hopto.org`. Confirmed by a `curl https://sulla.blisske.hopto.org/`
  returning 200.
- **Swarm root** — `~/swarm/docker-compose.yml` `include:` list extended
  with `./sulla/repo/docker-compose.yml`. `~/swarm/.env` extended with
  `SULLA_DATA_DIR`, `SULLA_ENV_FILE`, `SULLA_HOSTNAME` for compose
  interpolation.
- **Repo identity** — Anton-specific docs (CLAUDE/WORKING_STATE/AGGRESSIVE/PIVOT/MIGRATION)
  removed, Sulla CLAUDE.md and this file written. Backtest results from
  Anton's tuning campaigns deleted (`scripts/backtest_results/`,
  `scripts/backtest_cache/`).
- **Verification** —
  - `sulla-api` loopback `/api/health` → 200
  - `sulla-web` LAN `/api/health` → 200 (nginx proxy hits API)
  - `sulla-web` LAN `/api/config` → 401 (auth working)
  - `https://sulla.blisske.hopto.org/` → 200 (Traefik + Let's Encrypt working)
  - All three containers healthy per docker healthcheck
  - Engine logs show DB schema initialization + cycle heartbeat

### What did NOT land

- No Oanda integration. No real broker calls of any kind.
- No Telegram bot. The token slot in `.env` is empty so Phase 1 engine
  doesn't try to poll (which would conflict with Anton's or Tiberius's
  token if reused).
- No FX-specific math. `database.py` and the strategy files still carry
  Anton's equity-oriented logic; they'll get reworked in Phase 3.
- No macro calendar. Anton's earnings blackout (`earnings.py`) is still
  shipped in the image but won't be wired up; Phase 4 replaces it with an
  FX equivalent.
- No bot-specific Guide page. The `web/src/pages/Guide.jsx` is still the
  Anton TradFi guide; Phase 5 ports it to FX.

### Open Phase 1 follow-ups

1. **Decide Telegram bot name** — create one via @BotFather when Phase 2
   lands and add the token to `~/swarm/sulla/.env`.
2. **Oanda practice-account credentials** — sign up, generate personal
   access token, grab account ID. Phase 2 entry condition.
3. **Cosmetic** — the Login page still has Anton-vintage subtext ("Sign in
   to your Sulla trading dashboard"). Fine, but the marketing copy could be
   FX-specific in Phase 5.
4. **Demo data** — `core/demo_data.db` is Anton's. Demo login still works
   but shows TradFi positions. Phase 5 reseed with FX data once we have a
   shadow run.

---

## How to Resume With Claude

1. Upload this WORKING_STATE.md at session start
2. SSH into the host: `ssh blisske@192.168.0.135`
3. `cd ~/swarm/` (always — compose runs from the swarm root; running from
   `~/swarm/sulla/repo/` creates a different compose project and collides
   with the swarm-managed `container_name:` declarations)
4. For one-off Python: `docker exec -it sulla-engine python3` (limited to
   stdlib + sqlite3 in Phase 1; oanda/telegram libs added in Phase 2)
5. Anton and Tiberius are sister bots in the same swarm; their repos are at
   `~/swarm/anton/repo` and `~/swarm/tiberius/repo`. Anton's
   `web/src/pages/Guide.jsx` is the model for the Sulla Guide we'll write
   in Phase 5. Anton's `core/main.py` (preserved here as
   `core/_main_anton_reference.py`) is the source for the Phase 2 engine
   port.

---

## Session Protocol (Non-Negotiable)

1. WORKING_STATE.md is the first thing Claude reads — upload it at session start
2. Never suggest building something already built
3. Never re-open resolved bugs (see CLAUDE.md "Do Not Re-Suggest")
4. Phase-by-phase approach — propose, approve, execute, verify
5. Update WORKING_STATE.md before session ends
6. Commit and push before closing
