# WORKING_STATE.md — Sulla V1 Session Log

> Maintained by Claude. Read at the start of every new conversation.
> Last updated: 2026-05-18 (Tuner trigger wired into engine loop — feature parity with Anton/Tiberius)

---

## Current System State

| Item | Value |
|---|---|
| Phase | **5 — FX Guide page rewritten (operator docs complete)** |
| Engine mode | Full 5-min cycle: indicator fetch → shadow exit engine → 4-layer consensus → shadow buy/sell against the $10K paper ledger. No Oanda orders (shadow-only by design). |
| Broker | Oanda v20 REST — client written, awaiting `OANDA_API_TOKEN` + `OANDA_ACCOUNT_ID` in `~/swarm/sulla/.env` |
| Universe | EUR/USD, GBP/USD, AUD/USD, NZD/USD, USD/JPY, USD/CHF, USD/CAD (7 majors, hot-reloaded from Config.yaml each cycle) |
| Timeframe | 1h |
| Shadow ledger | Empty (`/app/data/sulla.db` schema initialized, no trades yet) |
| Telegram | **Wired** — dedicated Sulla bot, full command set, autocomplete registered, trade-event notifications live |
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

---

## Phase 3 — Consensus + Shadow Trading + FX Math (2026-05-17)

Phase 2 wired Oanda for live data. Phase 3 turns the engine from "fetches
bars and logs them" into a real trader that runs the full Anton/Tiberius
2+1+1 consensus stack against the FX universe and writes shadow trades
to the synthetic $10K ledger. No Oanda order submission yet — shadow-only
by design until Phase 6 live gates clear.

### What landed

- **`core/fx_math.py` (NEW)** — FX-specific math the equity/crypto engines
  didn't need:
  - `pip_size(symbol)` returns 0.0001 / 0.01 (JPY) correctly
  - `pip_value_usd(symbol, price, units)` — USD-quote pairs are easy,
    USD-base pairs (USD/JPY etc.) divide through the rate, non-USD
    crosses raise NotImplementedError (out of scope for the 7 majors)
  - `calculate_units(equity, risk_pct, entry, stop, symbol, cap_pct)` —
    Oanda unit-based sizing with explicit pip math, cap respected via
    fx_math, returns whole units
  - `position_notional_usd(symbol, units, price)` — used by the synthetic
    ledger to debit/credit cash on entry/exit
  - `fp(price, symbol)` — magnitude-aware display formatter, gives 3
    decimals for JPY pairs (no more `158.77600`) and 5 for non-JPY

- **`core/execution.py` (REWRITE)** — Anton's Alpaca-heavy version was
  gutted. Phase 3 only needs `is_market_open()` (FX 24/5, Sun 17:00 ET →
  Fri 17:00 ET) and `calculate_position_units()`. Order-submission
  functions remain as `NotImplementedError` stubs so Phase 6 just fills
  in the bodies. The Anton reference engine is still preserved at
  `_main_anton_reference.py`.

- **`core/ai_brain.py` (REWRITE)** — same shape as Tiberius's May-12
  fix, FX-adapted:
  - `_parse_verdict()` returns the **answer body** (post-`</think>`) not
    the chain-of-thought, so Telegram messages and Live Log entries show
    the analyst's polished commentary rather than the model's internal
    scratchwork
  - Surfaces up to 3500 chars (Telegram 4096 cap minus prefix)
  - System prompt frames Gemma as a senior currency strategist, not an
    equity analyst
  - Brave Search query is FX-tuned: `"{base} {quote} forex central bank
    news"` — biased toward macro headlines rather than corporate news of
    similarly-named tickers

- **`core/strategy.py` (PATCH)** — the 4 paradigms (Trend Following,
  Mean Reversion, Volatility Breakout, Liquidity Sweep), supporting
  signals, and exit logic are asset-class-agnostic and ported as-is.
  Only change: replaced the equity 9:30–3:30 ET session guard with the
  FX 24/5 guard (closed Saturday + Sunday-before-17:00-ET +
  Friday-after-17:00-ET). Default symbol changed `SPY` → `EUR/USD`.

- **`core/main.py` (REWRITE)** — Phase 2 placeholder cycle replaced with
  the full Phase 3 cycle:
  1. Touch heartbeat
  2. Check restart flag (`os._exit(0)`)
  3. Hot-reload Config.yaml
  4. **Shadow exit engine** — `_run_shadow_exit_engine()` iterates every
     open position, checks for stop hits + take-profits via
     `strategy.check_exit_signals`, executes trailing-stop ratchet on
     positive moves. SHADOW SELL writes flow through `database.log_trade`
     with the realized P&L USD in the `amount` column.
  5. Parallel `fetch_indicators()` for all `active_symbols`
  6. Log per-symbol regime/RSI/ADX (FX-precision via `fx_math.fp()`)
  7. Persist `market_states`
  8. **Consensus + shadow buy** — `_evaluate_entry()` per symbol:
     - Layer 1: `check_entry_signals()` — does any paradigm fire?
     - Layer 2: `check_supporting_signals()` — 2 of 3 confirm?
     - Score gate: ≥ `min_consensus_score` (default 3)
     - Layer 3: `ai_brain.get_ai_consensus()` — BULLISH / NEUTRAL /
       BEARISH (with `bearish_abort` veto)
     - If all clear: `execution.calculate_position_units()` for sizing,
       then SHADOW BUY logged + position recorded + stop set +
       synthetic cash debited
  9. Sleep with mid-sleep restart-flag wakeups every 30s

- **Brand color + login redesign** (folded into this session) — Sulla's
  visual identity locked to electric blue (`#3B82F6`). Login page has
  a distinct currency-glyph pattern overlay + blue accent line + FX-
  specific tagline. See preceding commit (56b807e) for the detail.

### What did NOT land in Phase 3

- **Telegram bot** — Phase 3b. Needs a Sulla BotFather token in `.env`,
  then porting the command handlers from Anton/Tiberius. Engine doesn't
  poll Telegram in Phase 3 so there's no token conflict.
- **`/api/account` + `/api/positions` Oanda integration** — Phase 6
  reads the real account from Oanda. Phase 3 dashboard reads from the
  synthetic ledger only.
- **Macro calendar blackout** — Phase 4. NFP / FOMC / CPI / ECB / BoJ
  event-window skipping.
- **Sulla-specific Guide page** — currently shows Anton's TradFi guide;
  Phase 5 rewrites for FX.

### Live verification

```
2026-05-17 14:26:13 === Sulla Phase 3 engine starting ===
2026-05-17 14:26:13 Oanda client ready: OandaClient(account='101-001-39349095-001', environment='practice', ...)
2026-05-17 14:26:14 --- CYCLE START | symbols: 7 | tf: 1h ---
2026-05-17 14:26:15 [EUR/USD] 1.16252 | TRENDING | ADX=42.1 | RSI=35.9 | Trend=BEAR
2026-05-17 14:26:15 [GBP/USD] 1.33242 | TRENDING | ADX=49.9 | RSI=31.5 | Trend=BEAR
2026-05-17 14:26:15 [AUD/USD] 0.71482 | TRENDING | ADX=37.1 | RSI=34.9 | Trend=BEAR
2026-05-17 14:26:15 [NZD/USD] 0.58391 | TRENDING | ADX=44.8 | RSI=29.4 | Trend=BEAR
2026-05-17 14:26:15 [USD/JPY] 158.776 | RANGING | ADX=14.2 | RSI=66.3 | Trend=BULL
2026-05-17 14:26:15 [USD/CHF] 0.78696 | RANGING | ADX=22.5 | RSI=63.2 | Trend=BULL
2026-05-17 14:26:15 [USD/CAD] 1.37495 | RANGING | ADX=25.5 | RSI=55.0 | Trend=BULL
```

USD/JPY shows `158.776` (3dp, fx_math.fp() picked up the JPY-pair convention).
Non-JPY pairs show 5dp. No entries fired this cycle — strong-dollar tape
means USD-base pairs are BEAR (TF needs BULL) and USD/JPY is RANGING but
with RSI 66 well above the MR threshold of 30. Engine is doing exactly
what it should: scanning, logging, nothing brewing.

### Open Phase 3 follow-ups

1. **First entry** — wait for a setup. Most likely candidate based on the
   current tape: AUD/USD or NZD/USD hitting RSI 29-30 with ADX falling
   would fire Mean Reversion. Could happen this week.
2. **Verify the LLM gate fires correctly when something does brew** —
   Phase 3 hasn't actually exercised the Gemma 4 26B → answer-body
   rendering path in production yet. The Tiberius May-12 fix is ported
   verbatim so high confidence it's right, but until a real BULLISH
   verdict surfaces in a SHADOW BUY's enriched_verdict field we haven't
   end-to-end tested it on FX.
3. **Phase 3b: Telegram** — create a Sulla bot via @BotFather, add the
   token to `~/swarm/sulla/.env`, restart. Then port the command
   handlers from Anton.
4. **Phase 4: Macro calendar** — most useful before live mode. NFP /
   FOMC blackouts skip new entries N hours before high-impact events.

---

---

## Phase 3b — Telegram Bot + Trade Notifications (2026-05-17)

Dedicated Sulla Telegram bot wired alongside the Phase 3 trading loop. Both
run concurrently as async tasks. The user created a fresh bot via @BotFather
(separate token from Anton's and Tiberius's — Telegram only allows one
polling client per token, so each bot in the swarm needs its own).

### What landed

- **`main.py` restructured** — Phase 3's `main_async` (which directly ran
  the trading loop) split into:
  - `trading_loop_async()` — the autonomous cycle, now a background task
  - `main_async()` — bootstraps the Telegram app, registers all handlers,
    starts polling, spawns the trading loop as a concurrent task, and
    idles on a 2-second shutdown poll
  - Telegram is **optional** — if `TELEGRAM_BOT_TOKEN` / `TELEGRAM_USER_ID`
    are empty, the engine logs a warning and runs the trading loop headless
    (keeps the engine functional pre-BotFather setup or if the user
    deliberately wants no Telegram surface)

- **Command handlers** ported from Anton (FX-adapted):
  - `/help` — full command reference, FX-tuned (no `/protect` or `/apply`
    sections; Sulla's shadow contract means no naked stops can exist and
    there's no ratchet-proposal flow)
  - `/indicators` — regime / RSI / ADX / trend across all 7 majors, FX
    precision via `fx_math.fp()` (USD/JPY at 3dp, others at 5dp), one
    bullet-list block per pair, "Brewing" call-out when a setup is detected
  - `/report` — Account / Open Positions / Active Defense sections.
    Reads from `database.get_shadow_account_state()`. Surfaces equity,
    cash, total P&L from initial capital, drawdown %, risk mode + daily
    halt status. Open positions show units + entry + strategy. Defense
    section flags any naked stops.
  - `/pnl` — same shape as Anton's: By Pair / Summary / Recent Tuning
    Activity. Each row shows trades / WR / PF / Avg% / dollar P&L.
    Summary has Net P&L with direction emoji + tuning eligibility line.
    Recent Tuning Activity surfaces the most-recent 5 entries from
    `database.get_tuning_summary()`.
  - `/buy PAIR USD` — manual buy bypassing consensus. Accepts pair in
    any case/separator (`/buy EURUSD 1000`, `/buy eur/usd 1000`,
    `/buy EUR_USD 1000` all work). Converts USD → units via
    `fx_math` (USD-quote pairs: `units = USD/price`; USD-base pairs:
    `units = USD`). Records SHADOW BUY in DB, debits cash, sets ATR stop.
  - `/kill` → `/confirm_kill` — two-step emergency liquidation with a
    60-second window. Closes every open shadow position at current
    market price, credits the synthetic ledger, logs `KILL SWITCH:` in
    the trades verdict. Refuses if /confirm_kill comes after the window
    expires.
  - `/resume` — clears drawdown halt. Re-checks current DD vs
    `drawdown_halt_pct`; refuses if still over threshold so a fat-finger
    can't bypass the safety. On clear: sets risk_mode → NORMAL,
    daily_halt → 0.
  - `/restart` — touches `.restart_engine`. Engine catches it within
    30 seconds (mid-sleep flag check) and `os._exit(0)`'s so compose
    respawns.
  - `handle_text` — plain text matching `[A-Z]{6}` or `[A-Z]{3}/[A-Z]{3}`
    triggers an ad-hoc AI sentiment query for that pair. Returns the
    Gemma 4 26B verdict + analyst commentary (using the May-12 fixed
    answer-body rendering).

- **`set_my_commands` registration** — typing `/` in Telegram surfaces
  the 9-command autocomplete menu with one-line descriptions. Wired
  best-effort at startup; logged-but-non-fatal on failure.

- **Trade-event notifications** — the trading loop now sends Telegram
  messages on:
  - `SHADOW BUY` — full consensus chain + sentiment + AI verdict body
    (mirrors Anton/Tiberius's enriched message format)
  - `SHADOW STOP HIT` — direction emoji (🟢/🔴/⚪), strategy, % + $ P&L,
    entry→stop price
  - `SHADOW TAKE PROFIT` — direction emoji, strategy, % + $ P&L,
    entry→exit price
  - `BEARISH VETO` — when AI vetoes an otherwise-passing consensus,
    surfaces the analyst's reasoning so the user understands what was
    almost taken
  Notifications wired via a `_notify()` helper that no-ops when `_bot`
  is None — keeps the trading loop callable even without Telegram.

- **Boot announcement** — engine sends `"📈 Sulla (FX) ONLINE"` on
  startup once Telegram is wired. First boot failed with "Chat not
  found" because Telegram allowlists DMs per-bot and the user had to
  `/start` the new bot first. After `/help` was sent once, the
  channel is established and all subsequent notifications work.

### Verification

```
2026-05-17 15:43:54 === Sulla Phase 3b engine starting ===
2026-05-17 15:43:57 HTTP getMe → 200 OK
2026-05-17 15:43:57 Application started
2026-05-17 15:43:57 setMyCommands → 200 OK
2026-05-17 15:43:57 sendMessage → 400 Bad Request (boot announcement, fixed by /start)
2026-05-17 15:43:57 Oanda client ready: account='101-001-39349095-001', environment='practice'
2026-05-17 15:43:57 --- CYCLE START | symbols: 7 | tf: 1h ---
```

User confirmed `/help` returned the full FX-tuned command reference.
Bot is bidirectional and trade notifications will fire on the next
SHADOW BUY / SHADOW SELL the engine produces.

### What did NOT land in Phase 3b

- **`/protect` and `/apply`** — deliberately skipped. Sulla shadow-mode
  positions always have an in-DB stop set on entry so no naked-stop
  scenario can exist. Phase 6 (live Oanda) might bring `/protect` back
  if Oanda's order-rejection edge cases create naked-position windows;
  decide then.
- **Phase 4 (macro calendar blackout)** — separate effort. NFP / FOMC /
  CPI / ECB / BoJ event-window skipping.
- **Phase 5 (Guide page rewrite)** — `web/src/pages/Guide.jsx` is
  still the Anton TradFi guide; needs FX-specific Section 7 (the macro-
  blackout calendar, once Phase 4 lands), updated Telegram command
  table, FX-specific glossary.

### Operational state at handoff

- **All four containers healthy:** sulla-engine, sulla-api, sulla-web,
  swarm-proxy
- **Telegram bot live:** receives commands, sends notifications, full
  command-menu autocomplete
- **Oanda client live:** fetching 7 majors every 5 minutes
- **No open shadow positions** (engine is still scanning; the current
  strong-dollar / RANGING-USD/JPY tape isn't firing any of the four
  paradigms)
- **First entry**, when it comes, will trigger the full notification
  chain end-to-end

---

---

## Phase 4 — Macro Calendar Blackout (2026-05-17)

The FX equivalent of Anton's earnings blackout. Blocks new entries on any
pair whose base OR quote currency has a high-impact macro event in the
configured window. Single-event movers (NFP, FOMC, CPI, rate decisions)
routinely produce stop-hunting volatility that blows through ATR-based
stops; we let the print land + dust settle, then resume scanning.

### What landed

- **`core/macro_calendar.py` (NEW)** — wraps the ForexFactory weekly JSON
  feed (`https://nfs.faireconomy.media/ff_calendar_thisweek.json`). Free,
  no auth, hand-curated impact ratings, ~8 years of stable schema. Module
  contract:
  - `get_blackout_status(symbol, macro_cfg, now=None)` → `(bool, event)`
    used by the trading loop's entry path
  - `upcoming_events(symbols, macro_cfg, look_ahead_hours)` → list, used
    by `/calendar`
  - 30-minute in-memory cache so refresh isn't on the hot path
  - Fail-open on fetch error: serves stale cache if any, else returns
    no-blackout (trading continues — better to miss a blackout than to
    silently halt the engine when ForexFactory hiccups)
  - Stamps last-fetch-error into `cache_status()` so `/calendar` can
    surface "calendar is broken" to the operator

- **`Config.yaml`** — new `macro_blackout` section:
  ```yaml
  macro_blackout:
    enabled: true
    minutes_before: 60     # block N min before each high-impact event
    minutes_after:  120    # ... and after (let volatility settle)
    importance_min: High   # "Low" | "Medium" | "High"
  ```
  Defaults are tuned to ~3 hr total window around each high-impact print.
  "High" is the right filter — wider thresholds (Medium) block roughly
  half the trading week.

- **`main.py` — entry path patched**. `_evaluate_entry()` now runs the
  blackout check BEFORE the paradigm evaluation, so we don't even waste
  a paradigm-signal computation on a blacked-out pair. Logs the
  triggering event when it fires:
  ```
  [USD/JPY] MACRO BLACKOUT: USD FOMC Meeting Minutes @ 2026-05-20T14:00:00-04:00 (High) — skipping entry
  ```

- **`/calendar` Telegram command** — shows upcoming events for the
  current watchlist currencies, grouped by day, color-coded by impact
  (🔴 High, 🟡 Medium, ⚪ Low). Optional `<hours>` arg overrides the
  default 48h horizon (`/calendar 168` = full week). Footer shows the
  current blackout window (`60 min before, 120 min after`).

- **`/report` — gains "Active Macro Blackout" section** that only
  appears when at least one watchlist pair is currently in a blackout
  window. Surfaces the triggering event so the operator knows why /pnl
  is quiet despite a juicy setup on screen.

- **`/help` updated** with `/calendar` description and the optional
  hours argument.

- **`set_my_commands`** extended with `/calendar` so the slash-autocomplete
  menu includes it.

### Live verification (Sat May 17 2026)

Calendar parsed 114 events for the week, 8 high-impact. The next 7 days:
```
Tue May 19 06:00 UTC | GBP | High | Claimant Count Change
Tue May 19 12:30 UTC | CAD | High | CPI m/m
Wed May 20 06:00 UTC | GBP | High | CPI y/y
Wed May 20 18:00 UTC | USD | High | FOMC Meeting Minutes
Thu May 21 01:30 UTC | AUD | High | Employment Change
Thu May 21 01:30 UTC | AUD | High | Unemployment Rate
Thu May 21 08:30 UTC | GBP | High | Flash Manufacturing PMI
Thu May 21 08:30 UTC | GBP | High | Flash Services PMI
```

Saturday → all pairs are clear. Tuesday will be the first real test:
GBP/USD blackout 5-8 UTC (Claimant Count) and USD/CAD blackout
11:30-14:30 UTC (CPI m/m). Wed 17:00-20:00 UTC is the big one — FOMC
Minutes blocks ALL USD-leg pairs (6 of 7 majors) for three hours.

### What did NOT land in Phase 4

- **Force-exit before macro events** — Anton force-exits positions
  before earnings to dodge the gap risk. The FX equivalent would be
  "close all USD positions 30 min before NFP." Phase 4 only blocks
  new entries, doesn't close open ones. Decision deferred — defer until
  we've watched a real event window play out on a live shadow position
  and seen whether the stop holds vs needs proactive flatten.
- **Per-event-importance override** — currently all "High" events get
  the same window. Could imagine wanting a longer window for FOMC
  (3 hrs?) vs CPI (1.5 hrs). Skip until we have evidence the
  one-size-fits-all setting is wrong.
- **Phase 5 (Sulla Guide page rewrite)** — `web/src/pages/Guide.jsx`
  still shows the Anton TradFi guide. Needs FX-specific sections
  including a section on the macro blackout we just built.
- **Phase 6 (live Oanda)** — separate effort. Live deployment gates.

### Operational state at handoff

- All four containers healthy: sulla-engine, sulla-api, sulla-web,
  swarm-proxy
- Telegram bot bidirectional; `/calendar` + `/help` confirm Phase 4
  surface live
- Macro calendar cache populated (114 events, 8 high-impact)
- All 7 majors clear right now (weekend)
- Tuesday May 19 will be the first real-world fire of the blackout
  logic on GBP/USD and USD/CAD pairs

---

---

## Phase 5 — FX Guide Page Rewrite (2026-05-17)

The dashboard's Guide tab was inherited from Anton's TradFi guide via the
Phase 1 sed sweep (Anton → Sulla identifier rename). Content stayed
equity-centric: PDT references, US session hours, earnings blackout,
shares-based sizing, "TradFi" framing throughout. Phase 5 is a clean
rewrite for FX context.

### What landed

- **`web/src/pages/Guide.jsx` (FULL REWRITE)** — ~830 lines, 11 sections,
  structurally mirrors the Tiberius / Anton guides but with FX-specific
  content throughout.

  Section list:
  1. **What Sulla does** — overview, unleveraged-by-design framing,
     five pillars (added "Macro-event blackout" + "Pip-aware sizing" to
     Anton's three)
  2. **The seven majors** (NEW) — per-pair characterization card grid
     (EUR/USD "fiber", GBP/USD "cable", USD/JPY "ninja", USD/CHF
     "swissy", AUD/USD "aussie", USD/CAD "loonie", NZD/USD "kiwi") with
     each pair's typical behavior + sensitivity
  3. **The four trading paradigms** — same TF/MR/VB/LS architecture, FX-
     specific entry examples (currency trends last longer than equity
     trends; cable's UK-news gaps; carry-trade behavior of USD/JPY)
  4. **The 2+1+1 consensus** — same layers; FX-tuned "why this matters"
     callout (FX is the most algo-saturated market on Earth)
  5. **Risk management** — Sulla-specific numbers (5% paper / 2% live,
     12% cap, 5 max). Added a callout box for the unleveraged-by-design
     principle (Oanda offers 50:1 retail leverage; Sulla deliberately
     ignores it). Removed Anton's PDT/cash-account section + EOD force-
     exit + earnings blackout.
  6. **Self-tuning** — same lifecycle; recalibrated patience math
     (7 pairs × 4 paradigms = 28 slots, multi-month sweep)
  7. **Reading the dashboard** — Anton's panels carried over; added a
     note on JPY-pair display (3dp vs 5dp); pointed at /calendar for
     macro-blackout visibility (dashboard banner is planned for Phase 5+)
  8. **Macro-event blackout** (NEW Phase 4 content) — what it does, data
     source (ForexFactory), refresh cadence, trigger filter, window
     defaults, per-currency pair selection. Side-by-side grid of
     "Headline events per currency" + "What it does NOT do (yet)" so
     the operator knows force-exit isn't included.
  9. **Telegram commands** — updated for Sulla's surface: added
     /calendar row, dropped /protect (no naked stops in shadow mode)
     and /apply (no ratchet-proposal flow), changed argument names
     (`PAIR USD` not `ASSET USD`)
  10. **Playbook** — 8 FX-flavored scenarios (drawdown tiers, manual
      buy via `/buy EUR/USD`, macro event approach, watchlist add/drop,
      shadow→Oanda-practice flip). Drops Anton's EOD scenarios + cash-
      account scenarios.
  11. **Glossary** (FULL REWRITE) — five groups:
      · **FX mechanics** (NEW): pip, pipette, lot, base/quote, bid/ask/
        spread, USD-quote vs USD-base pair, weekend gap
      · **Technical indicators** — same as Anton, lightly reworded
      · **Macro events** (NEW): NFP, FOMC, CPI, ECB, BoJ, ForexFactory
        feed
      · **System concepts** — same architecture terms; added macro
        blackout entry
      · **Operational states** — shadow vs live; live mode flagged as
        "planned" because Phase 6 (Oanda order submission) isn't built
      · **Praetor / stack** — Sulla / Anton / Tiberius cross-references
        updated; Battlemage host described accurately

- **Color palette unchanged**: Sulla brand BLUE (#3B82F6) is the section
  accent; semantic colors preserved (GREEN MR, AMBER LS, RED halt, CYAN
  VB — the renamed-from-BLUE indicator color from Phase 1).

- **TOC sidebar** updated with the new 11-entry structure; sticky on
  large screens.

### Verification

Bundle inspected post-build. All FX-distinctive strings present:
```
"seven majors":      1
"Macro-event blackout": 1
"ForexFactory":      1
"fiber":             1
"ninja":             1
"swissy":            1
"Aussie":            1
"loonie":            1
"kiwi":              1
"unleveraged":       1
"pipette":           1
```

No Anton-era content surviving (no "TradFi", "PDT", "EOD force-exit",
"earnings blackout", "9:30 AM" references in Guide content).

### What did NOT land

- **Dashboard macro-blackout banner** — the Guide notes that the
  /report Telegram command surfaces active blackouts but the web
  Market page doesn't. That's a small UI add (planned for a Phase 5+
  iteration); for now `/calendar` is the canonical view.

### Operational state at handoff

- Guide page live at `https://sulla.blisske.hopto.org/guide` and
  `http://192.168.0.135:8085/guide`
- All four containers healthy
- Trading loop + Telegram bot + macro blackout all running concurrently
- Tuesday May 19 still queued as the first real-world macro-blackout
  fire (GBP/USD around Claimant Count 06:00 UTC, USD/CAD around CPI
  12:30 UTC)

---

---

## Daily Reveille + Boot Suppression (2026-05-17, late)

The Phase 3 main.py rewrite didn't include a daily morning greeting (Anton
and Tiberius both have one; Sulla did not). User asked whether all three
bots reflect their market-open semantics correctly.

Audit + landed work:
- **Anton:** already correct. The market-open gate at the top of the cycle
  loop (querying Alpaca's clock API) skips the entire cycle body —
  including the reveille block — on weekends and holidays.
- **Tiberius:** already correct (you'd fixed this earlier with rotating
  flavor lines for 24/7 framing).
- **Sulla:** had no reveille. Added one in `_maybe_send_reveille()` with
  21 rotating FX-themed lines (Roman/imperial, FX-native "London bid New
  York offered", wry one-liners). Fires once per calendar day after 07:30
  MT IF `execution.is_market_open()` is True (FX 24/5 gate correctly
  handles all-Saturday + Sunday-before-17:00-ET + Friday-after-17:00-ET).

Also fixed (across all three bots): the boot greeting and the daily
reveille were firing back-to-back on any restart during session hours
(config save, manual /restart, container respawn). Now the bootstrap
stamps `_last_reveille_day` to today's date after the boot greeting
succeeds, so the cycle's reveille check sees "already sent today" and
skips. Verified on this deploy — single `sendMessage` at engine start
for each bot.

## Login: multi-pair chart trace (2026-05-17, late)

The login page's gold `DramaticCandles` motif was shared verbatim across
Anton, Tiberius, and Sulla, making the three pages look like recolors of
the same template. Replaced Sulla's with `MultiPairChartTrace` —
seven smooth Bezier curves in graduated blues, one per major. Two hero
lines (EUR/USD in cyan, USD/JPY in electric blue) have a Gaussian-blur
glow + pulsing endpoint dot suggesting live data; the other five recede
into the background in graduated blue depth. Each path is hand-tuned to
look like distinct price-action shapes rather than uniform sine waves.

The currency-glyph pattern (€ £ ¥ $ AUD NZD CHF CAD) from the earlier
rebrand stays as the layer underneath the chart trace.

## BEARISH VETO triage (2026-05-17, evening)

Telegram saw a burst of three BEARISH VETO messages on USD/JPY VB within
~17 minutes — each message also chopped mid-word at the 500-char hard
slice. Three stacked issues:

1. **Prefilter too permissive.** `consensus.min_consensus_score: 2`
   contradicted the inline comment ("primary(1) + 2 of 3 supporting
   signals minimum"), which should be 3. Anton had the same drift;
   Tiberius was already at 3. Bumped to **3** on Anton and Sulla in
   both `data/Config.yaml` (live, hot-reloaded) and `repo/core/Config.yaml`
   (image baseline). This tightens the gate so weak setups (USD/JPY VB
   with ADX 13.6, RSI 68.8, volume 0.4× — a momentum trap) never reach
   the LLM in the first place.
2. **Veto-notification spam.** Same setup re-firing every cycle issued
   one Telegram message per cycle. Added a 60-min cooldown per
   `(symbol, paradigm)` tuple in `core/main.py` via module-level
   `_veto_last_notified_at` dict + `_should_notify_veto()` /
   `_mark_veto_notified()` helpers. **The LLM call still runs and the
   veto still logs to stdout each cycle — only the Telegram notification
   is debounced.** Dict is in-memory and resets on restart (acceptable —
   you'll see the first veto after every restart anyway).
3. **Mid-word truncation.** Removed the `[:500]` hard slice in the veto
   notification path. The full `verdict_body` now flows through. The
   `ai_brain` layer already caps verdicts at 3500 chars, which fits
   inside Telegram's 4096-char message limit with the header prefix.

Tiberius got the same `_veto_last_notified_at` cooldown + truncation
removal. Anton's veto path only logs to DB (no Telegram notification by
design) so it received only the Config bump.

Deployed all three engines via
`docker compose up -d --build anton-engine tiberius-engine sulla-engine`
from `~/swarm/`. All three healthy and cycling at 20:07 ET.

## Tuner Trigger Wired Into Engine Loop (2026-05-18, evening)

While fixing a session-counter bug on Anton and Tiberius's tuner trigger,
discovered Sulla's main.py was **missing the tuner invocation entirely**.
`tuner.py` existed (with the full `run_tuning_cycle()` implementation),
the `tuning_log` table existed, the config had a `tuning:` section, and
`/pnl` even claimed symbols were "tuning cycle eligible" once they hit 10
closes — but no code path called `tuner.run_tuning_cycle()`. The
mechanism was scaffolded and never wired.

Fix: imported `tuner` at the top of `core/main.py` and added a
DB-backed trigger block in `trading_loop_async()` right after
`_run_cycle()` returns. Same pattern landing the same day on Tiberius:

    _min_t = config.get('tuning', {}).get('min_trades_to_tune', 10)
    _db_counts = {}
    for _t in database.get_closed_trades():
        _db_counts[_t['symbol']] = _db_counts.get(_t['symbol'], 0) + 1
    _ready = [s for s, cnt in _db_counts.items() if cnt >= _min_t]
    if _ready:
        logger.info(f"[TUNER] Trigger fired for: {_ready}")
        tuner.run_tuning_cycle(_ready)

Wrapped in try/except so any tuner-side bug doesn't take down the trading
loop. Sulla currently has 0 closed shadow trades (just started cycling
after the Oanda credentials landed earlier this week), so the block runs
silently every cycle. Once any symbol accumulates 10 closes, the
`[TUNER] Trigger fired` line will appear in engine logs and the tuner's
internal per-strategy + cooling-off guards take over from there.

This brings Sulla to feature parity with Anton and Tiberius on the
tuning mechanism. Verified post-restart: first cycle ran clean, no
exceptions, all 7 FX pairs scanned normally.

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
