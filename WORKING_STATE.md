# WORKING_STATE.md — Sulla V1 Session Log

> Maintained by Claude. Read at the start of every new conversation.
> Last updated: 2026-05-17 (Phase 2 — Oanda v20 adapter + real cycle loop landed; awaiting credentials)

---

## Current System State

| Item | Value |
|---|---|
| Phase | **2 — Oanda adapter wired; awaiting credentials** |
| Engine mode | Live cycle loop (5 min interval). Fetches OHLCV + indicators per symbol when credentials are present; idles gracefully when they're not. No trading logic yet. |
| Broker | Oanda v20 REST — client written, awaiting `OANDA_API_TOKEN` + `OANDA_ACCOUNT_ID` in `~/swarm/sulla/.env` |
| Universe | EUR/USD, GBP/USD, AUD/USD, NZD/USD, USD/JPY, USD/CHF, USD/CAD (7 majors, hot-reloaded from Config.yaml each cycle) |
| Timeframe | 1h |
| Shadow ledger | Empty (`/app/data/sulla.db` schema initialized, no trades yet) |
| Telegram | Off (Phase 2 engine doesn't import any Telegram code so no token conflict with Anton/Tiberius) |
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

### Phase 1 follow-ups — closed/superseded

These are tracked in Phase 2 below where they got addressed.

### Original Phase 1 follow-ups (for history)

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

---

## Phase 2 — Oanda Adapter + Real Cycle Loop (2026-05-17)

Phase 1 was infrastructure scaffolding. Phase 2 wires the trading engine to
real Oanda market data and runs the per-symbol indicator pipeline on a fixed
cycle. Still no trading — the consensus layer + execution wire-up is Phase 3.

### What landed

- **`core/oanda_client.py`** — Minimal v20 REST client. Uses raw `requests`
  (no `oandapyV20` SDK dep). Implements:
  - `get_account()` — balance / NAV / margin
  - `get_candles(symbol, granularity, count, price)` — OHLCV bars, drops the
    in-progress incomplete bar so indicators don't see partial volume
  - `get_pricing(symbols)` — current bid/ask + spread for one or more pairs
  - Auth via `Authorization: Bearer {token}` header
  - Symbol convention transparently bridges `EUR/USD` (internal) ↔ `EUR_USD`
    (Oanda's notation)
  - Granularity map (`1h` → `H1`, `30m` → `M30`, etc.)
  - `practice` vs `live` base URL selection from `OANDA_ENVIRONMENT`
  - Specific error messages for 401 (bad token / wrong environment) and 404
    (wrong account ID / untradeable instrument)
  - Graceful `OandaMissingCredentials` exception when token/account are empty
    — caller decides whether to crash or idle

- **`core/market_data.py`** — Rewritten against Oanda. **Crucial:** the
  `fetch_indicators()` return shape is byte-for-byte compatible with
  Anton/Tiberius (same dict keys, same value types), so `strategy.py`,
  `ai_brain.py`, `tuner.py` and the dashboard endpoints all work unchanged
  when we wire them up in Phase 3+.
  - Lazy client init: first `fetch_indicators()` call attempts client
    construction, caches success or failure. No-creds case logs once and
    returns `None` thereafter without spamming.
  - Indicator math is identical to Anton/Tiberius (EMA, RSI, ADX, ATR,
    Bollinger via `pandas_ta`).
  - FX-appropriate staleness guard — 4× the bar interval during weekdays,
    75 hours on weekends (handles the Fri 17:00 ET → Sun 17:00 ET FX
    market close).

- **`core/main.py`** — Replaced the Phase 1 placeholder with a real cycle
  loop. Per cycle:
  1. Touch heartbeat
  2. Check restart flag (`os._exit(0)` if set — May-12 fix preserved)
  3. Hot-reload Config.yaml
  4. Fetch indicators for every `active_symbols` entry in parallel (one
     Oanda candle request per symbol; ~1–2 seconds for 7 majors)
  5. Log per-symbol `regime / ADX / RSI / trend`
  6. Persist snapshot to `market_states` table (powers `/api/market` on
     the dashboard)
  7. Sleep `update_interval_min` minutes, checking the restart flag every
     30 seconds during sleep so the web Restart button feels responsive

- **`Config.yaml`** — Full rewrite for FX:
  - Renamed `alpaca:` section → `oanda:` (informational; engine reads creds
    from env directly)
  - `strategy.timeframe`: `30m` → `1h`
  - `strategy.active_symbols`: 13 equities → 7 FX majors (EUR/USD, GBP/USD,
    AUD/USD, NZD/USD, USD/JPY, USD/CHF, USD/CAD)
  - Removed equity-specific keys: `earnings_blackout_days`, `eod_exit_hour`,
    `eod_exit_minute`, `symbol_overrides` for SPY/NVDA/etc.
  - Replaced equity sector buckets in `correlation_aware_sizing.sectors`
    with FX-appropriate ones (USD_short / USD_long / Risk_on / Risk_off)
  - Phase 4 placeholder comments left for `macro_blackout` + `friday_close`

- **`.env`** — Added empty placeholders for `OANDA_API_TOKEN`,
  `OANDA_ACCOUNT_ID`, `OANDA_ENVIRONMENT=practice` with inline comments
  pointing at the Oanda signup page. Engine reads them on next restart.

- **Restart-flow verified end-to-end** under Phase 2:
  ```
  10:56:59  flag placed via sulla-api docker exec
  10:57:21  engine: "Restart flag detected mid-sleep; exiting for compose to restart."
  10:57:24  fresh engine: "Sulla Phase 2 engine starting"
  ```
  Mid-sleep detection works (engine wakes every 30s during the 5-min cycle
  sleep), so the dashboard's Restart button doesn't have to wait a full
  cycle for the engine to notice.

### What did NOT land in Phase 2

- No consensus layer. `strategy.py`, `ai_brain.py`, `tuner.py` are still
  the Anton-shape files inherited from the rsync. Phase 3 ports them to FX.
- No Telegram. `set_my_commands`, `/indicators`, `/report`, `/pnl`, etc.
  not wired up (the engine doesn't even import the telegram library).
- No FX-specific math. Pip values, JPY pair quirks, unit-based position
  sizing — all Phase 3.
- No macro calendar. The `macro_blackout` config block doesn't exist yet;
  NFP / FOMC / CPI / ECB / BoJ event-window skipping is Phase 4.
- No Guide page rewrite. The current `web/src/pages/Guide.jsx` is still
  the Anton TradFi guide; Sulla-specific Guide is Phase 5.

### What the user needs to do to activate Phase 2

The engine is fully wired but idle. To turn it on:

1. **Sign up at Oanda.** https://www.oanda.com/ — pick the practice
   account (free, unlimited, no funding). US residents can also create
   it through oanda.com/us-en/.
2. **Generate a personal access token.** From the dashboard:
   *My Account → Manage API Access → Generate*. Copy the token (it
   shows only once).
3. **Note the practice account ID.** Shown on the same Manage API Access
   page; format is e.g. `101-001-12345678-001`.
4. **Populate `~/swarm/sulla/.env`** — paste the token and account ID
   into the placeholders added in Phase 2:
   ```
   OANDA_API_TOKEN=<your token>
   OANDA_ACCOUNT_ID=<your account id>
   OANDA_ENVIRONMENT=practice
   ```
5. **Restart the engine** — either via the dashboard's Config-page Restart
   button, or:
   ```bash
   docker exec sulla-api touch /app/data/.restart_engine
   ```
   Within ~30 seconds the engine wakes, picks up the new env vars, and
   starts hitting Oanda for live OHLCV.

### Phase 2 verification (post-credentials)

Once creds are populated and the engine has restarted, expected log lines:
```
=== Sulla Phase 2 engine starting ===
Oanda client ready: OandaClient(account='101-001-12345678-001', environment='practice', ...)
DB schema ready at /app/data/sulla.db
--- CYCLE START | symbols: 7 | tf: 1h ---
[EUR/USD] $1.08234 | TRENDING | ADX=33.2 | RSI=58.1 | Trend=BULL
[GBP/USD] $1.26715 | RANGING | ADX=21.7 | RSI=49.3 | Trend=BEAR
[USD/JPY] $156.421 | TRENDING | ADX=29.8 | RSI=62.5 | Trend=BULL
... (4 more)
```

The dashboard `/market` page should also start showing live readouts once
the first cycle completes.

### Open Phase 2 follow-ups

1. **Oanda credentials** (above) — gates everything else.
2. **Account-status surface in the dashboard** — Phase 3 prerequisite.
   Anton/Tiberius's `/report` Telegram command queries account balance +
   open positions; equivalent needs `client.get_account()` wiring.
3. **JPY pair display precision** — currently rendered with 5 decimals
   like the others; Phase 3 swaps in the magnitude-aware `_fp()` helper
   from Tiberius.

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
