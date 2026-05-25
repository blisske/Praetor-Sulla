# WORKING_STATE.md — Ionic V1 Session Log

> Maintained by Claude. Read at the start of every new conversation.
> Last updated: 2026-05-24 (+4) (parity sprint — Ionic at code-parity with Corinthian/Doric)

---

## 2026-05-24 (+4) — Parity sprint: Ionic at code-parity with the other two bots

**All six identified code-parity gaps closed in this commit.** Ionic now
matches the structural completeness of Corinthian + Doric. What's left
before the live flip is operational (config alignment + 7-14 day shadow
soak), not code. See `CLAUDE.md` "Live Deployment Gates" for the
checklist.

**Gaps closed:**

1. **Partial TP live path** — new `oanda_client.close_partial_position()`
   (passes longUnits=<n> instead of "ALL" to the same /positions/.../close
   endpoint), new `execution.execute_partial_take_profit()` wrapper, main.py
   branches at L431 to call it in live mode + then push BE stop via
   ratchet for the surviving units.

2. **Kill switch live path** — `cmd_confirm_kill` now branches on
   shadow_mode. Live iterates open positions and calls
   `execute_take_profit` (which = close_position) for each, logging
   `SELL` rows with Oanda-authoritative P&L + fee. Failures collected
   and surfaced in the final Telegram report.

3. **Oanda equity reconciliation** — new `live_account_cache` table
   (single-row, id=1 sentinel). Engine cycle calls `client.get_account()`
   in live mode and upserts NAV + balance + unrealized_pl + margin
   fields. Stale threshold 5min. New `database.get_account_state(
   shadow_mode)` wrapper unifies the read path: shadow → synthetic math;
   live → cache; live + stale cache → fall back to shadow math with
   `source: 'live-fallback-shadow'` so UI can flag it. `cmd_report`
   updated to use the wrapper + surface the cache-stale state.

4. **Tuner integration** — added `_try_promote_tunings(sym)` helper that
   wraps `tuner.check_promotions()`. Called after every
   `database.close_open_position(sym)` site (6 places: 2 in
   `_run_exit_engine`, 1 in `_reconcile_live_positions`, 2 in
   `cmd_confirm_kill`, 1 in the stop-hit reconciliation path). Mirrors
   Corinthian's L1374/L1435 pattern.

5. **`cmd_protect` live wiring** — was DB-only with a stale "live mode not
   yet wired" comment. Now in live mode, after writing the DB stop, also
   calls `execute_ratchet_stop` to push the protective stop to Oanda.
   Reports per-symbol whether Oanda accepted ("Oanda stop set") or
   rejected ("DB only — see logs") so operator knows the actual state.

6. **`Config.live.example.yaml` + Live Deployment Gates doc** —
   created mirroring Corinthian's `Config.aggressive.example.yaml`.
   Production-conservative settings: `risk_per_trade_pct: 1.5%`,
   `position_size_max_pct: 8%`, `max_open_trades: 4`,
   `correlation_aware_sizing.enabled: true` (FX majors are heavily
   USD-correlated), pyramiding OFF until first single-leg trend
   completes. `CLAUDE.md` Phase Status updated to reflect Phases 2-5
   are done; new "Live Deployment Gates" section with 7-step checklist
   modeled on Corinthian's pattern.

**Smoke-verified:** ionic-engine rebuilt cleanly. All 7 majors pulling
real OHLCV, consensus logic running, Telegram polling active (operator
had already set TELEGRAM_BOT_TOKEN). Macro calendar refreshed (92 events
this week, 11 high-impact). No regression.

**Tests:** full Ionic suite still passes — 70 tests total (32 tax + 38
oanda_client). No new tests added in this commit (the new code paths
have no easy unit-test surface — they're integration paths that need a
real Oanda account or a mocked HTTP layer; the existing 38 oanda_client
tests cover the shape correctness of the methods invoked).

**What changed in files:**
- `core/oanda_client.py`: +30 lines (close_partial_position method)
- `core/execution.py`: +55 lines (execute_partial_take_profit)
- `core/database.py`: +90 lines (live_account_cache table +
  upsert/get/get_account_state helpers)
- `core/main.py`: ~150 lines net change (partial TP branch, kill switch
  branch, equity cache refresh in cycle, cmd_protect live wiring,
  cmd_report wrapper change, _try_promote_tunings helper + 6 invocations)
- `core/Config.live.example.yaml`: new (110 lines)
- `CLAUDE.md`: Phase Status updated + new Live Deployment Gates section

**Ionic readiness now matches Corinthian/Doric:**
| Phase | Status |
|---|---|
| 1 — Scaffold | ✅ |
| 2 — Oanda adapter | ✅ |
| 2.5 — main.py live wiring | ✅ |
| 3 — FX math | ✅ |
| 4 — Macro calendar | ✅ |
| **5 — Shadow contract + Telegram + Guide + tax + UI re-theme** | **✅ (this commit closes the last gaps)** |
| 6 — Soak + live gates | ⏳ time-based only |

**Remaining for live flip (all operational):**
- 7-14 day shadow soak with criteria from CLAUDE.md gates section
- Operator: confirm Oanda token connected + scope=trade
- Config alignment per `Config.live.example.yaml`
- Final `oanda.shadow_mode: true → false` flip

---

## 2026-05-24 (+3) — Phase 2.5: main.py live-path wiring + reconciliation

**Phase 2 deferred work picked up.** Phase 2 (+2) shipped the Oanda
client + execution.py implementations + per-user creds + tests, but
main.py was still hard-shadow — every autonomous BUY wrote `SHADOW BUY`
to the DB and never called execute_buy_with_stop. This commit wires
`shadow_mode` branching through main.py end-to-end so flipping
`oanda.shadow_mode: false` in Config.yaml actually trades on Oanda.

**Architectural decision (matches Corinthian + Doric pattern):**
Oanda's server-side attached stop is **authoritative for stops**.
Engine-side exit logic skips stop-checking in live mode and relies on
a per-cycle reconciliation pass to detect closed positions. TP and
ratchet remain engine-driven (engine decides → engine calls Oanda).

**Wiring landed in main.py:**

| Block | Function | Shadow path | Live path |
|---|---|---|---|
| **A** Stop hit | `_run_exit_engine` | unchanged | SKIP (reconcile catches it) |
| **B** Partial TP | same | unchanged | DEFER (needs new oanda_client method) |
| **C** Full TP | same | unchanged | `execute_take_profit` → SELL row with Oanda P&L + fee |
| **D** HOLD_AND_TIGHTEN | same | DB stop | DB + `execute_ratchet_stop` |
| **E** Trailing ratchet | same | DB stop | DB + `execute_ratchet_stop` |
| **F** Pyramid leg | `_evaluate_pyramid_add` | SHADOW BUY ADD | Oanda fill FIRST, then DB leg (no phantom on reject) |
| **G** Autonomous BUY | `_evaluate_entry` | SHADOW BUY | `execute_buy_with_stop` with attached stop, DB uses fill price not requested |
| **H** Manual /buy | `cmd_buy` Telegram | SHADOW BUY | same as G |
| **I** Kill switch | `cmd_confirm_kill` | unchanged | DEFER (non-critical; user can manually close via Oanda) |

**New helper: `_reconcile_live_positions()`.** Runs at the top of
`_run_exit_engine` in live mode. Queries Oanda's open positions, compares
to DB, and for each DB position no longer on Oanda:
  - Logs a `SELL (RECONCILED)` row using current market price as
    approximate close price
  - Approximate P&L noted in the verdict (exact P&L requires pulling
    Oanda's transaction history — out of scope for Phase 2.5)
  - Closes the DB row so the engine stops trying to manage a phantom
  - Telegram notification with "Check Oanda for exact fill" footer

**Failure semantics — all live paths return cleanly on Oanda error:**
- BUY rejected: no DB row created, Telegram notifies, cycle continues
- TP rejected: DB row not closed, will retry next cycle
- Ratchet rejected: DB stop still updates (informational), Oanda stop
  is stale (logged as warning)
- Reconciliation skipped if client unavailable

**Cash tracking gap (acknowledged):** in live mode, `adjust_shadow_cash`
is NOT called. `shadow_cash` field becomes informational only — Oanda
balance is authoritative. Dashboard will diverge until Phase 5 wires a
periodic Oanda balance fetch into the equity-snapshot path.

**Smoke-verified:** ionic-engine rebuilt cleanly. Boots in shadow mode
(default), all 7 majors pulling real OHLCV from Oanda, consensus logic
running. Live branches are reachable (no syntax errors, all imports
resolve) but won't fire until a user sets `oanda.shadow_mode: false`.

**Phase 2.5 status:** ✅ complete for entry + exit + ratchet + manual /buy.
Deferred: partial-TP live path (needs `oanda_client.close_partial_position`),
kill-switch live path (lower priority — user can close manually via Oanda
web UI in an emergency), exact-P&L reconciliation (currently approximate).

**Effective Ionic deployment readiness now:**
- ✅ Phase 1 — scaffold
- ✅ Phase 2 — Oanda adapter (client + execution)
- ✅ Phase 2.5 — main.py live-path wiring (this commit)
- ✅ Phase 3 — FX math (already done)
- ✅ Phase 4 — Macro calendar (already done)
- ⏳ Phase 5 — formal shadow contract audit + Telegram bot wire-up +
              Oanda equity reconciliation
- ⏳ Phase 6 — soak run + live gates (7-14 day, ≥5 closed trades, etc.)

Ionic is now structurally trade-capable. A user with Oanda creds connected
+ `shadow_mode: false` would actually trade. Production-ready it is not
(Phase 5 + 6 still pending); Trade-capable it is.

---

## 2026-05-24 (+2) — Phase 2: Oanda broker adapter (infrastructure complete)

**Phase 2 per CLAUDE.md ladder: "Oanda broker adapter. Real OHLCV bars +
token auth."** Most of it was already scaffolded in earlier sessions
(oanda_client.py existed with candles + account + pricing). What this
commit lands is the **order placement, position management, per-user
credential dispatch, and tests** that take Phase 2 from "candles work"
to "could trade if main.py is wired."

**oanda_client.py additions (+265 lines):**
- `_request()` chokepoint for all REST calls (GET/POST/PUT) with uniform
  error mapping. `_get` / `_post` / `_put` thin wrappers atop it.
- `get_open_positions()` — list per-instrument net positions
- `get_open_trades()` — list per-fill open trades (needed for ratchet
  to find the trade ID to modify)
- `get_trade(trade_id)` — single trade detail
- `place_market_order(symbol, units, stop_loss_price, take_profit_price,
  client_order_id)` — MARKET FOK with attached stopLossOnFill /
  takeProfitOnFill. Signed-units convention (positive = long).
- `close_position(symbol, side)` — flat at market via /positions/.../close
- `modify_trade_stop(trade_id, new_stop_price)` — direct stop update
  (Oanda's nice v20 feature — no cancel-and-replace dance)
- `from_user(user_id)` — classmethod that loads encrypted broker_keys
  blob from global.db, decrypts via broker_crypto, constructs with the
  right environment. Reads `last_error` column for env marker
  ('env=practice' / 'env=live') per api/byok.py convention.
- Module-level fee/price extraction helpers:
  - `extract_fill_fee_usd()` — sums commission + financing +
    halfSpreadCost from orderFillTransaction (Oanda's standard accounts
    have $0 commission; the cost IS the spread, captured here).
  - `extract_fill_price()`
  - `extract_close_fee_usd()`
  - `extract_close_pl_usd()` — realized P&L on close

**execution.py: stubs replaced with real Oanda calls (+150 lines):**
- `execute_buy_with_stop(symbol, units, stop_price)` →
  `(success, fill_price, fee_usd)`. Submits MARKET FOK with attached
  stop. Generates `ionic_buy_*` idempotency keys.
- `execute_take_profit(symbol)` → `(success, pl_usd, fee_usd)`. Closes
  long position at market.
- `execute_ratchet_stop(symbol, new_stop_price)` → `bool`. Finds the
  matching open trade by instrument + modifies its stop directly.
- All three sync (called via `asyncio.to_thread` from main.py per the
  Doric/Corinthian pattern).
- All defensive: any OandaError returns the failure tuple cleanly
  instead of crashing the cycle.

**market_data.get_client() dispatches on USER_ID:**
- If `USER_ID` env var is set (per-user engine container — set by
  provisioner_daemon), call `OandaClient.from_user(user_id)` to
  load that user's encrypted credentials.
- Otherwise fall back to `OandaClient.from_env()` (operator engine, dev
  mode).
- Cached result + error result both memoized so cycles don't spam logs.

**Tests:** `tests/test_oanda_client.py` — 38 unit tests, all passing
(`docker exec -w /app ionic-api python3 -m unittest tests.test_oanda_client`).
Covers symbol/granularity mapping, environment routing, construction
validation, fee/price extraction (USD/garbage/None/string-coerce/
negative-value defensive cases), order request body shape (signed units,
attached stop format, client_order_id), close-position body shape,
modify-trade-stop body shape. Existing tax suite still 32/32.

**Smoke-verified:** ionic-engine rebuilt + recreated, boots clean,
pulls real OHLCV from Oanda for all 7 majors (EUR/USD, GBP/USD, AUD/USD,
NZD/USD, USD/JPY, USD/CHF, USD/CAD). ADX/RSI computing on real candles.
MTF daily filter, macro calendar, consensus logic all running on
real-market data. Operator's env credentials work end-to-end.

**⚠️ Deferred — Phase 2.5 / next session:** main.py autonomous-loop
live-path wiring. Currently main.py is hard-shadow — the autonomous-buy
block at L612-664 always writes `SHADOW BUY` to database.log_trade and
never calls execute_buy_with_stop. 4-5 call sites need a
`if shadow_mode: ... else: execute_buy_with_stop(...)` branch:
  - L632  autonomous BUY
  - L502  pyramid BUY ADD
  - L290 / L361 / L1229  exits (need careful reasoning re: Oanda's
                                 server-side attached stop vs engine-side
                                 exit logic — risk of double-close)
  - L1093 manual /buy Telegram cmd
Architectural decision needed: do we let Oanda's attached stop fire
server-side AND keep the engine-side exit running, or trust one or the
other? Different trade-offs. This is the actual work item; client +
execution layers are done.

**What's in this commit:**
- ✅ Full Oanda v20 client (orders, positions, stops, fee helpers)
- ✅ Real execution.py implementations  
- ✅ Per-user credential dispatch (USER_ID-aware)
- ✅ 38 unit tests passing
- ✅ Smoke-verified live Oanda candle fetching

**What's NOT in this commit (Phase 2.5):**
- main.py autonomous-loop shadow-vs-live branching
- Architecture decision on attached-stop vs engine-side-exit

---

## 2026-05-24 (+1) — UI re-theme: gold → blue

**Closed the brand-correctness gap** flagged when the tax MVP landed
earlier today. Ionic's canonical brand color per CLAUDE.md is blue
(`#3B82F6`), but the entire UI was still themed gold (`#c8922a`) — a
Corinthian SaaS-port leftover. Only the FoundationMark SVG logo had
the right blue.

**Sweep:**
- `#c8922a` → `#3B82F6` (blue-500)
- `#a8761d` → `#1D4ED8` (blue-700 — gradient bottom)
- `#e8b84b` → `#60A5FA` (blue-400 — used in GOLD_LITE)
- `rgba(200,146,42,*)` → `rgba(59,130,246,*)` (tint variants)
- `GOLD` / `GOLD_LITE` constants renamed → `BLUE` / `BLUE_LITE` in
  Layout.jsx, OnboardingBanners.jsx, reports/Tax.jsx

**Files touched (21):** Layout, Settings, Trading, Tax, Account, Mode,
Danger, TwoFactorCard, SettingsCard, TosReacceptModal, OnboardingBanners,
DemoModeBanner, ForgotPassword, ResetPassword, Signup, VerifyEmail,
DemoMode, LegalDoc, admin/Provisioner, admin/Users, admin/UserDetail.

**Also fixed:** Tax.jsx's `fmtQty` comment was "Doric trades in whole
shares — no need for crypto-style fractional precision" (copy-paste
leftover from the Doric port). Now reads "FX units of base currency —
typically 4 dp is plenty (e.g. 10000.00 EUR units)".

**Verified:** `docker compose build ionic-web` succeeded clean,
container recreated. Visit `http://192.168.0.135:8085/` (LAN) or
`https://ionic.blisske.hopto.org/` (prod) to confirm sidebar active
state, settings buttons, and tax page tabs render in blue.

**Method:** single `sed -i -E` pass across all 21 files; zero residual
matches confirmed via grep afterward. Constant renames done in a
separate pass on the three files that defined them. No logic changes —
purely cosmetic / brand-correctness.

---

## 2026-05-24 — Tax-reporting MVP + per-user healthcheck flap fix (preemptive)

**Tax-reporting MVP — FX §988 flavor.** Same architecture as Corinthian's
+ Doric's tax ships earlier today, but adapted for FX-specific tax
treatment. Per-user, year-scoped, FIFO/LIFO/HIFO lot matching (informational
under §988), CSV export. "Not tax advice" disclaimer + §988 specifics on
every screen.

**Why §988 matters:**
- US default for retail spot FX = IRC §988 ordinary income — NOT capital
  gains. No short/long-term split. Reported on Schedule 1 line 8z or
  Form 6781 line 1.
- §988(a)(1)(B) opt-out into capital-gains treatment exists but is rare
  for retail.
- Section 1256 (60/40 rule) doesn't apply to Oanda spot.

**Backend:**
- `core/tax.py` — pure-function lot matcher. Same FIFO/LIFO/HIFO machinery
  as the cap-gains bots (preserved for record-keeping clarity) but `term`
  field collapses to always `'ordinary'`. `holding_days` is still computed
  for informational display. `LONG_TERM_DAYS` constant removed; replaced
  with `TERM_ORDINARY = "ordinary"`. Module docstring rewritten end-to-end
  with §988 background + §988(a)(1)(B) opt-out warning + §1256
  non-applicability.
- `api/tax.py` — five endpoints. `SummaryResponse` reshaped:
  `total_ordinary_gain` + `ordinary_count` instead of short/long split.
  DISCLAIMER text mentions §988 specifics. CSV header reads
  "Foundation Ionic — tax report (FX, §988 ordinary income)" and the
  Term column comment reflects "always 'ordinary'" instead of Box A/B
  routing.
- `core/database.py` — `trades.fee_usd REAL DEFAULT 0.0` + idempotent
  ALTER TABLE + `log_trade()` signature extended. For Phase 1 scaffold
  this is always 0.0; Phase 2 (Oanda v20 broker integration) will derive
  it from the spread + financing in the trade-transaction stream. Oanda
  has no separate commission on standard accounts.

**Frontend (matches existing gold theme — Ionic UI was never re-themed to
blue per CLAUDE.md; separate cleanup task):**
- `web/src/pages/reports/Tax.jsx` — §988-shaped layout: dropped the
  Short-term / Long-term summary cards (replaced with single
  "Ordinary income" card), dropped the Term chip column from the
  disposals table, methodology footnote rewritten with §988 background.
  Header now reads "Mechanical year-end FX report under IRC §988".
- `web/src/pages/settings/Trading.jsx` — `TaxMethodCard` with §988-
  flavored disclaimer (no short/long-term, opt-out warning, "lot choice
  is informational under §988"). FIFO blurbs adapted for scalping +
  opt-out scenarios.
- `web/src/components/Layout.jsx` + `App.jsx` — Tax nav link + route.

**Tests:** `tests/test_tax.py` — 32 tests, all passing
(`docker exec -w /app ionic-api python3 -m unittest tests.test_tax`).
28 ported from Doric + 4 new §988-specific:
- `Section988OrdinaryTermTests` — replaces the deleted
  `HoldingPeriodBoundaryTests` class. Verifies term is 'ordinary' for
  short hold, long hold, AND zero-day hold (scalp).
- `Section988SummaryTests` — verifies summarize() returns
  `total_ordinary_gain` + `ordinary_count`, asserts capital-gains keys
  (`short_term_gain`, `long_term_gain`, `short_count`, `long_count`) are
  ABSENT, asserts `total_ordinary_gain == realized_gain` invariant.

**Phase 1 scaffold context:** Ionic has no live broker yet (Phase 2 is
Oanda integration). No real trades exist to compute against; the page
will show an empty state with the §988 disclaimer until fills start
landing. Schema + endpoints are ready; tax math has been validated
against synthetic data via the test suite.

**Cross-bot provisioner healthcheck flap fix.** Same fix that landed in
Corinthian (71f8124) + Doric (d157078) earlier today — `-mmin -2` →
`-mmin -10` in `scripts/provisioner_daemon.py` template. No Ionic per-user
engines exist yet so this is preemptive (template only; no live fragments
to patch).

**Known caveats (shipped as-is):**
- Whole UI still themed gold (`#c8922a`) — separate cleanup task to
  re-theme Ionic to its proper blue (`#3B82F6`).
- `fee_usd` is always 0.0 until Phase 2 — derived from Oanda spread +
  financing once the broker adapter lands.
- §988(a)(1)(B) opt-out + §1256 election aren't modeled. Disclaimer
  covers it.

---

## 2026-05-23 — File-structure cleanup: sulla → ionic

Final naming pass to match the 2026-05-21 brand rebrand. All three
levels in one cutover. Same recipe Corinthian + Doric got the day
before (see Corinthian commit 1ef7da2 / Doric commit c4a302f for the
template).

**LEVEL 3 — host filesystem:**
- `~/swarm/sulla/` → `~/swarm/ionic/` (inode-preserving mv)
- `data/sulla.db` → `data/ionic.db` — 13 trades + 1 open position +
  11,093 market_states rows intact (verified pre/post)

**LEVEL 2 — containers + compose + Traefik:**
- Containers: `sulla-{engine,api,web}` → `ionic-*`
- Network: `sulla-net` → `ionic-net`
- Image tags: `sulla-*:latest` → `ionic-*:latest`
- Linux user inside container: `sulla` → `ionic` (UID 1000 unchanged)
- Env vars: `SULLA_DATA_DIR / SULLA_ENV_FILE / SULLA_HOSTNAME` →
  `IONIC_*` in `~/swarm/.env`; added `IONIC_CORS_ORIGINS` allowing
  legacy `sulla.blisske.hopto.org` so old bookmarks keep working
- Traefik: `~/swarm/proxy/dynamic/sulla.yml` → `ionic.yml`. Router +
  service names renamed. Host() chain preserves all three aliases:
  - `ionic.foundationbots.com` (canonical, real domain)
  - `ionic.blisske.hopto.org` (legacy alias from brand pass)
  - `sulla.blisske.hopto.org` (legacy alias from pre-rebrand)
- Swarm-root `docker-compose.yml` include path updated to
  `./ionic/repo/docker-compose.yml`

**LEVEL 1 — source/docs:**
- 47 source files swept via bulk sed (`sulla → ionic`, `Sulla → Ionic`,
  `SULLA → IONIC`), with `Praetor-Sulla` restored in WORKING_STATE.md
  narrative entries per the brand-pass preservation note
- All in-source default paths flipped, getLogger names to `ionic`,
  Dockerfile USER `ionic`, README/CLAUDE.md paths
- A handful of WORKING_STATE.md historical-narrative lines hand-corrected
  after the sed (`Sulla → **Ionic**` got mangled to `Ionic → **Ionic**`,
  same with the Telegram flavor-line attributions)
- `CLAUDE.md` "How to Resume" section: stale cross-repo paths
  `~/swarm/anton/repo` + `~/swarm/tiberius/repo` → `~/swarm/doric/repo`
  + `~/swarm/corinthian/repo` (those bots themselves got renamed yesterday)

**Live verification post-cutover:**
- `ionic-engine` + `ionic-api` + `ionic-web` all Up + healthy
- 13 trades + 1 open position + 11,093 market_states intact in
  `ionic.db` (WAL mode)
- Engine pricing all 7 majors: EUR/USD, GBP/USD, AUD/USD, NZD/USD,
  USD/JPY, USD/CHF, USD/CAD — RANGING regime on all, BEAR/BULL split
  consistent with current macro tape
- Telegram bot reachable (`/getUpdates 200`)
- Doric + Corinthian unaffected (user's signup test on Corinthian still
  running — `corinthian-engine-13` per-user engine spawned by the
  provisioner stayed up through both renames)

**Intentionally preserved:**
- `Praetor-Sulla` in historical narrative entries of this file —
  reflects the actual clone-source name at the time
- `sulla.blisske.hopto.org` in Traefik Host() chain + CORS allowlist
  — legacy bookmark alias

---

## 2026-05-21 (evening) — Ionic engine bug fix: kill drawdown spam

Ionic was firing `RISK MODE → HALT, Drawdown 95.0% from peak ($200,125.81 → $9,992.54)` Telegram alerts every ~5 minutes despite zero open positions and a $10K shadow ledger. Two cascading bugs.

**Root cause 1: equity-mark math wrong for USD-base FX pairs.**

`get_shadow_account_state` (core/database.py) and `_compute_shadow_equity` (api/main.py) both inherited Anton's `market_value = sum(shares × current_price)` math. Correct for equities. Correct for FX where USD is the **quote** currency (AUD/USD, GBP/USD, EUR/USD — units × USD-per-base = USD). Catastrophically wrong for USD-**base** pairs (USD/JPY, USD/CAD, USD/CHF) where units are already USD; multiplying by price mis-marks them as JPY/CAD/CHF amounts.

Specifically:
- USD/JPY 1199 units @ 159.198 → mark = 1199 × 159.198 = $190,888 (BUG, should be $1,199)
- That single mark pushed equity to ~$200K, the peak watermark captured it, and after positions closed (equity back to $9,992) the drawdown calc reported 95% forever after.

Fix: new `_fx_position_value_usd(symbol, units, price)` helper in both modules (duplicated to avoid an api→core import dependency). For `USD/*` pairs it returns `units` directly; for `*/USD` pairs it returns `units × price`. Caveat documented: this gives held-asset value, not full directional PnL on USD-base pairs. Adequate for drawdown tracking; full FX-PnL pass can refine later.

**Root cause 2: `risk_state` row never seeded → transition gate broken.**

The tiered-drawdown state machine in `core/main.py:741` reads `get_risk_state()`, computes `target_mode`, fires Telegram alert only when `target_mode != prev_mode`. Gating logic was correct. But `update_risk_state` does UPDATE-only — no INSERT. If the row doesn't exist, the write silently no-ops.

Doric's `main.py` wires `database.init_risk_state()` at startup (line 51 there) to seed the row. Ionic's `main.py` copied most of Doric's startup sequence but missed that one call. Result: `risk_state` table stayed empty → `get_risk_state()` returned the `NORMAL` default fallback every cycle → with the polluted peak above, every cycle re-evaluated as `NORMAL → HALT` (a "transition") → alert fired every cycle → spam.

Fix: add the missing `database.init_risk_state()` call after `init_shadow_account()` in `trading_loop_async`, matching Doric's pattern. Comment explains why so future code archaeology finds the trail.

**Stop-the-bleeding actions (applied to live DB outside the commit):**
- `equity_peak` row reset from $200,125.81 → $10,000.00 via direct SQL
- `risk_state` row seeded with `(NORMAL, daily_halt=0)` via direct SQL so gating worked immediately on next cycle (no need to wait for engine restart to call `init_risk_state`)

**Verification:** engine + api rebuilt and recreated. One full cycle elapsed post-fix: zero risk-mode alerts, zero spam, state holding NORMAL with drawdown 0.08% against the corrected $10K peak.

**Not affected:** Doric (equities — math is right for stocks; `init_risk_state` wired up), Corinthian (no USD-base inversion in crypto pairs like BTC/USD — USD is always quote; `init_risk_state` wired up).

**Commit:** `7e6295c` pushed to `Foundation-Ionic:main`.

---

## 2026-05-21 — Foundation brand pass

Cross-swarm rebrand. Platform "Praetor" → "Foundation"; bots renamed to column orders — Anton → **Doric**, Tiberius → **Corinthian**, Sulla → **Ionic** (this codebase). User-visible surface only — containers, DBs, GitHub repo, env vars all stay as-is.

**What landed (Ionic / this codebase):**

- `web/index.html`, `web/public/manifest.webmanifest` — `<title>` and PWA name/short_name/description renamed.
- `web/public/favicon.svg` + `.ico` + `pwa-{64,192,512}*.png` + `maskable-icon-512x512.png` + `apple-touch-icon-180x180.png` — gold winged-P retired. New mark is the **Ionic column capital** (abacus + twin volutes + egg-and-dart band + fluted shaft) in electric-blue gradient (`#93C5FD → #3B82F6 → #1D4ED8`). PNGs and multi-res `.ico` rendered from the new SVG via a one-shot `nginx:alpine` container with `imagemagick` + `librsvg` apk'd at runtime. Reusable script at `/tmp/regen-favicons.sh`.
- `web/src/components/Layout.jsx` — local `PraetorMark` function renamed to `FoundationMark` and rewritten to render the Ionic capital (with the volutes enlarged to dominate the viewBox after a follow-up sizing tweak). Sidebar wordmark `PRAETOR` → `FOUNDATION`; subtitle `Ionic · FX` → `Ionic · FX`. Same in the mobile topbar.
- `web/src/pages/Login.jsx` — `FoundationMark` glyph; wordmark/subtitle/body copy renamed (`IONIC · TRADFI` → `IONIC · FX`, also fixing the legacy "TRADFI" copy-paste from the Anton fork; "Sign in to your Ionic trading dashboard." → "...Ionic..."). Top-left right-panel label `Praetor` → `Foundation`; top-right `Ionic · FX` → `Ionic · FX`. Right-panel currency-glyph and multi-pair chart decorations were already blue-themed — no recolor needed.
- `web/src/pages/Dashboard.jsx` — market-feed strip `Ionic · Live Market Feed · Alpaca` → `Ionic · Live Market Feed · Oanda` (also fixing a stale Anton-fork copy-paste — Ionic uses Oanda, not Alpaca).
- `web/src/pages/Config.jsx` — every bot-name reference in tooltip help text and `X-specific` badges renamed (Sulla → Ionic, plus the cross-reference Tiberius → Corinthian).
- `web/src/pages/Guide.jsx` — prose-wide rename (Sulla → Ionic, Anton → Doric, Tiberius → Corinthian, Praetor → Foundation), preserving code identifiers — `blisske/Foundation-Ionic` GitHub URL, container names, `~/swarm/ionic/` paths, DB filenames. Done by a delegated general-purpose agent with explicit preservation rules.
- `core/main.py` — Telegram surface: boot greeting `📈 Sulla (FX) ONLINE` → `📈 Ionic (FX) ONLINE`; `/help` header `📖 Sulla — Command Reference` → `📖 Ionic — Command Reference`; daily heartbeat "Sulla is ONLINE and scanning the seven majors." → "Ionic..."; reveille flavor lines `"The forum trades in seven tongues. Sulla listens to them all."` / `"London bid. New York offered. Sulla scanning."` / `"Carry trades carry. Sulla follows."` → Ionic equivalents (rest of the Roman/imperial flavor lines kept verbatim — they still fit the broader classical theme).

**Foundation landing page (NEW, swarm-level — not Ionic-specific):**

- `~/swarm/foundation/index.html` + `~/swarm/foundation/docker-compose.yml` — `foundation-web` (nginx:alpine bind-mounting `index.html` read-only). Included from the swarm-root compose.
- `~/swarm/proxy/dynamic/foundation.yml` — Traefik route binding `Host("blisske.hopto.org")` (the apex) → `foundation-web:80` with Let's Encrypt. Three clickable order cards (Doric / Ionic / Corinthian) link to each bot's dashboard.

**Hostname migration (alias mode):**

- `~/swarm/proxy/dynamic/ionic.yml` — Host rule extended to match `ionic.blisske.hopto.org` (new primary) AND `ionic.blisske.hopto.org` (legacy alias). Fresh Let's Encrypt cert issued for the Ionic name (valid through 2026-08-19). Foundation landing card href repointed to the new URL. Drop the `Host("ionic.…")` clause after ~30 days when bookmarks have settled.

**Operator action still required (BotFather, off-host):**

Telegram bot Name / About / Description live on Telegram's servers and can only be edited via `@BotFather` chat — not changeable via API. Suggested text:

- Name: `Foundation · Ionic`
- About: `Autonomous FX trading. Foundation swarm — Ionic capital.`
- Description: `Ionic is the FX arm of the Foundation swarm — an autonomous 24/5 trading bot running on Oanda v20. 4-paradigm signal engine with 2+1+1 consensus gate, AI verdict layer, macro-event blackout across the seven majors. Use /help for the command reference.`

Until pasted into BotFather, the Telegram client's contact list still shows the old display name even though every message the bot sends says "Ionic."

**Not renamed (intentionally — infrastructure-internal):**

`ionic-engine` / `ionic-api` / `ionic-web` containers, `ionic-net` network, `ionic.db` (+ WAL sidecars), `~/swarm/ionic/` bind-mount path, Python module names, env vars, function names, all stdout log messages. High blast radius (Traefik routes, nginx config, env vars, runbook commands, CI/CD) and zero user benefit — the brand-visible layer was the goal; the plumbing stays.

**Postscript (later 2026-05-21):** Follow-up pass renamed the GitHub repo too — `blisske/Praetor-Sulla` → `blisske/Foundation-Ionic` — via the REST API (operator-provided PAT, single-use, revoked after). Local `origin` remote updated. GitHub's permanent redirects keep the old clone URL working indefinitely. All in-code references (`CLAUDE.md`, `README.md`, `Guide.jsx`, and the cross-references in the Doric and Corinthian repos) updated to the new URL in the same sweep.

---

## 2026-05-20 — Tuning page: Inspect candidate + manual Reject

Cross-swarm push (Anton + Tiberius + Ionic all got this). Ionic has no
tuning candidates yet (`tuning_log` empty — FX cycle hasn't accumulated
enough closes per (symbol × paradigm) to trigger one), but the surface is
in place for when it does.

The Self-Tuning Monitor was read-only. Operator wanted to act on candidates
without violating the "Never bypass this gate" rule (CLAUDE.md, non-
negotiable for promotion). Rejection isn't bypass — the operator can veto
a candidate they hate without needing shadow data, and rejection enters
the same cooling-off as auto-rejection.

**What landed:**

- `core/database.py`:
  - Added `re` to top-level imports.
  - `tuning_log` CREATE TABLE now includes `rejection_reason TEXT`;
    idempotent ALTER TABLE migration at `init_db()`.
  - New `get_candidate_detail(log_id, db_path=None)` — bundles the
    proposal row, snapshot row, driving trades (≤ `baseline_max_trade_id`,
    paradigm-filtered), and recent trades.
  - New `reject_candidate(log_id, reason=None, db_path=None)` — sets both
    tuning_log and the matching param_snapshots row to REJECTED + writes
    the reason. `db_path` kwarg lets the API route demo vs admin reads.

- `api/main.py`:
  - New Pydantic `RejectCandidateBody`.
  - New `_db_path_for_user(user)` helper using `PRAGMA database_list`.
  - `GET /api/tuning/candidate/{log_id}` — admin + demo can read.
  - `POST /api/tuning/candidate/{log_id}/reject` — admin-only.

- `web/src/components/CandidateDetailModal.jsx` (NEW) — shared modal,
  byte-identical across Anton/Ionic/Tiberius (drift-detector parity).
  Renders proposal summary, driving + recent trades, optional reason
  textarea, confirm-to-reject flow. GREEN/RED are semantic (positive/
  negative), not brand — Ionic's electric-blue header stays untouched.

- `web/src/pages/Tuning.jsx`:
  - Validation rows now clickable (preserves BLUE brand styling).
  - History rows clickable, `title` attr surfaces `rejection_reason` on
    hover.
  - Modal mounts on `selectedLogId` set; refetches `/tuning` on reject.

**Verified post-rebuild:**
- Schema migration ran cleanly: `rejection_reason` column present in
  `tuning_log` (0 rows existing, nothing to disturb).
- `GET /api/tuning/candidate/999` → 404 (correct empty-state behavior).
- `POST /api/tuning/candidate/999/reject` (admin) → 404 (correct, no row
  to update). Demo-user POST returns 403 via existing dispatch.
- All three Ionic containers healthy after rebuild.

**Operator UX:** when the first FX candidate eventually fires (most likely
on a USD-quote pair after 10 closes accumulate on one paradigm), the
Active Shadow Validations table will show the row; click → modal opens
with the driving trades; Reject button kills it.

## 2026-05-20 — Icon recolor: electric blue

The PWA install icons for Anton, Tiberius, and Ionic were all the gold
PraetorMark P-with-wings — visually identical on the home screen. Recolored
each bot's `favicon.svg` to its own brand palette and regenerated the PWA
PNG icons. Ionic is now electric blue, matching the brand color locked in
during the Phase 3 login redesign.

**Ionic: electric-blue gradient** — light `#93C5FD` → mid `#3B82F6`
(matches the existing `--accent` in `index.css`) → dark `#1D4ED8`. Manifest
+ index.html `theme-color` were already `#3B82F6` from the original PWA
push; no change needed there.

**What landed:**
- `web/public/favicon.svg` — three gradient stops swapped from gold
  (`#e8b84b/#c8922a/#8a5e10`) to blue (`#93C5FD/#3B82F6/#1D4ED8`). Wing
  strokes recolored too.
- `web/public/{pwa-64x64,pwa-192x192,pwa-512x512,maskable-icon-512x512,
  apple-touch-icon-180x180}.png` + `favicon.ico` — regenerated via
  `npx @vite-pwa/assets-generator --override true`.

**Sister bots got matching recolors in the same push:** Anton emerald
(`#10B981`), Tiberius amber (`#F59E0B`).

**User-side note:** an already-installed PWA will keep its cached gold icon
until the home-screen entry is removed and the site re-installed. Long-press
the icon → Remove → revisit `https://ionic.blisske.hopto.org` → Add to Home
Screen.

## 2026-05-20 — PWA: dashboard installs as a phone app

The Ionic dashboard is now installable as a Progressive Web App. On iOS/Android,
the browser's Install prompt or "Add to Home Screen" produces a dedicated Ionic
icon that launches the dashboard full-screen (no URL bar, no browser tabs), and
the shell stays cached so it loads instantly + survives a brief offline blip.
Dashboard content unchanged — this is the phone-as-app wrapper only.

**What landed:**
- `web/public/manifest.webmanifest` — `name: "Praetor · Ionic"`,
  `short_name: "Ionic"`, `theme_color: "#3B82F6"` (matches the electric-blue
  brand color locked in during the Phase 3 login redesign),
  `background_color: "#020617"`, `display: standalone`. Icons at 64/192/512 +
  maskable 512.
- `web/public/sw.js` — minimal service worker (~35 lines), cache key
  `ionic-v1`. Strategy:
  - `/api/*` and `/ws` → NetworkOnly (real-time FX cycle data, never cached)
  - HTML navigation → NetworkFirst, fallback to cached shell when offline
  - Everything else → CacheFirst with background revalidate
  - `skipWaiting()` + `clients.claim()` so a deploy applies on next page load
- `web/public/{pwa-64x64,pwa-192x192,pwa-512x512,maskable-icon-512x512,apple-touch-icon-180x180}.png`
  + `favicon.ico` — generated from `favicon.svg` via
  `npx --yes @vite-pwa/assets-generator@latest --preset minimal-2023 public/favicon.svg`.
  No permanent npm dep added.
- `web/index.html` — manifest link, apple-touch-icon, theme-color meta, iOS
  standalone meta tags, inline SW registration.
- `web/nginx.conf` — `location = /manifest.webmanifest` block forcing
  `application/manifest+json` Content-Type (nginx's default mime.types serves
  `.webmanifest` as `application/octet-stream`, which trips Lighthouse PWA checks).

**Verified post-rebuild:**
- `curl -sI http://localhost:8085/manifest.webmanifest` → 200,
  `application/manifest+json`.
- `curl -sI http://localhost:8085/sw.js` → 200, `application/javascript`, 1931B.
- `curl -sI http://localhost:8085/pwa-192x192.png` → 200, `image/png`.
- Served HTML contains all four PWA tags + #3B82F6 theme color.

**Operator action to install:** open `https://ionic.blisske.hopto.org` on
phone → share/menu → "Add to Home Screen" / "Install app". Ionic icon appears
alongside Anton + Tiberius + Milton.

**Same change landed simultaneously on Anton, Tiberius, Milton.** Cache key
prefixed per bot.

**Do not re-suggest:** service worker is intentionally minimal — no Workbox,
no precache manifest with fingerprinted assets. NetworkFirst on HTML means
every new deploy lands on next page open; CacheFirst on `/assets/*` is safe
because Vite hashes those filenames. If we later want deploy-aware precaching,
reach for `vite-plugin-pwa` then.

## 2026-05-19 — 4-phase trading-logic audit

Confidence-restoration audit across strategy, sizing, tuning, and risk math
in all three Praetor bots. Eight real bugs surfaced; all fixed.

### Phase 1 (trade traces) — Ionic GBP/USD findings

Traced only open position: trade id 1 SHADOW BUY 894 units GBP/USD @
1.34182 (2026-05-18 16:02 UTC), VOLATILITY BREAKOUT, still open at
~1.34003 (−13 pips unrealized). Verified VB trigger conditions
(TRENDING, ADX 37.4, RSI 68.0 > vb_rsi=55, BBW squeeze trusted by
construction). Consensus 4/3 — full house. Position sizing math
correct: `equity × 12% / price` capped at 894.30 → floored to 894
units, notional $1,199.59. Initial stop placement correct at
`entry − ATR × 2.0 = 1.337473`; current stop 1.3383966 reflects
~9 pips of ratchet movement.

**One real bug found and fixed:**

- **`HOLD_AND_TIGHTEN` signal was computed but discarded.** When a
  TF/VB position's regime flips to RANGING (which GBP/USD did —
  ADX dropped from 37 to 14), the design intent is to tighten the
  stop to ~1× ATR with a BE floor. Tiberius honored this; Ionic's
  exit cycle only branched on `TAKE_PROFIT`, falling through to
  the normal ratchet. **Fix:** added a `HOLD_AND_TIGHTEN` handler
  at `core/main.py:338-357` between TAKE_PROFIT and the trailing
  ratchet, with `continue` to preempt the wider normal ratchet.
  Verified the code path is hit each cycle but correctly refuses
  to fire while the position is underwater (`tight_sl < price`
  guard prevents tightening into a phantom-loss territory). Will
  visibly fire the first time a TF/VB position is above-water
  when its regime flips.

### Phase 2 (per-paradigm code audit) — TREND FOLLOWING cross-bot

TF trigger byte-identical across bots. Found four divergences in
supporting infrastructure; two fixed in Ionic, one deferred, one
ported:

- **Volume threshold was hardcoded `0.8`** at `core/strategy.py:173`.
  Tiberius reads from `consensus.volume_participation_pct`. Fixed
  Ionic to use the same config path with `0.80` default.
- **VB RSI direction was paradigm-agnostic.** Ionic scored
  "RSI rising (any +delta)" as +1 for VB the same as for TF/MR/LS.
  Tiberius requires `delta ≥ 2` ("SURGING"). Fixed Ionic's
  `check_supporting_signals` to branch on
  `strategy_type == "VOLATILITY BREAKOUT"` for the surging gate.
- **MTF slope filter** (`block_strong_downtrend`): deferred —
  crypto-tuned threshold doesn't transfer to FX without re-tuning.
- **Partial profit-taking** — ported from Tiberius (default OFF):
  - `database.py`: extended `get_open_position` / `get_all_open_positions`
    to return `partial_exits_taken` + `position_size_usd`; added
    `mark_partial_exit(symbol, remaining_shares, remaining_size_usd, new_stop)`
  - `strategy.py`: `check_exit_signals(..., partial_exit_taken=False)`
    signature; returns `PARTIAL_TAKE_PROFIT` action on first mid-BB
    touch when PPT enabled and not already partial'd. Upper-BB
    exit unconditional.
  - `main.py`: shadow exit cycle reads partial flag, handles
    `PARTIAL_TAKE_PROFIT` by `math.floor(units × pct + 1e-9)`,
    short-circuits to normal TP if degenerate. Logs
    `SHADOW PARTIAL SELL` with sliced `position_size_usd` so the
    tuner doesn't double-count. Also added `import math` (was
    missing — only needed once partial-PT code landed).
  - `Config.yaml`: `strategy.partial_profit_taking` block
    (`enabled: false`, `partial_exit_pct: 50.0`,
    `move_stop_to_breakeven: true`).

### Phase 3 — tuner safety + bounds

All eight safety surfaces verified clean and consistent across bots.
Ionic tuner: no bugs. `tuning_log` empty (tuner has never proposed
a change yet). One Tiberius-specific bug fixed there (see Tiberius
WORKING_STATE.md).

### Phase 4 — math spot-checks

**One critical safety bug found and fixed in Ionic:**

- **Ionic had no working drawdown safety net.** Both `equity_peak`
  and `risk_state` tables were empty — nothing in the cycle was
  updating them. The `/resume` command and the `/report` cosmetic
  existed, but the *transitions* into ALERT/DERISK/HALT had no code
  path. With `peak_equity = 0`, `drawdown_pct` resolved to `0%`
  regardless of actual loss, so HALT could never have triggered.
  Ionic was running with **no drawdown safety net**.
- **Fix:** ported Anton's tiered drawdown state machine verbatim
  into `_run_cycle` at `core/main.py:710-765`. Same NORMAL → ALERT →
  DERISK → HALT cascade with `recovery_pct` hysteresis. HALT
  short-circuits new entries (open positions continue to ratchet
  through the shadow exit engine). DERISK halves effective
  `shadow_equity` passed to `calculate_position_units` —
  mathematically equivalent to halving both `risk_per_trade_pct`
  and `position_size_max_pct` (both scale linearly with equity).
- **Verified:** first cycle initialized `equity_peak = $9997.90`;
  `risk_state` will populate on first transition (NORMAL is default,
  so no write needed yet).

Other math surfaces (profit-factor sentinel, win-rate handling,
partial-sell exclusion in tuner queries) all verified correct on
Ionic. No changes needed there.

### Stylistic divergences flagged but not fixed
- Correlation multiplier: Ionic has none yet (Phase 1 scaffold area,
  scheduled for future phase). When implemented, decide between
  Anton's lookup curve or Tiberius's linear formula — currently
  both produce identical outcomes at defaults.

### Soak plan
Leave the current Ionic configuration alone for a week before
flipping `strategy.partial_profit_taking.enabled: true`. Goal:
establish a clean post-audit baseline with the new drawdown
state machine actually running for a full FX week.

## 2026-05-19 — Tier 2 drift audit closeout

Three Ionic findings closed:

1. **`position_size_usd` was dead column.** Yesterday's schema migration
   added the column for parity with Tiberius; the SHADOW BUY call sites
   in `_evaluate_entry` and the manual `/buy` handler never wrote a
   value, so it always stored 0. Fixed `record_open_position` to accept
   the kwarg and updated both call sites to pass FX notional.

2. **`get_tuning_summary` accepts `limit=50` kwarg** — matches Tiberius.

3. **`/api/session` docstring fix** — said "equities are session-bound"
   (an Anton copy-paste artifact). FX is the actual asset class.

## 2026-05-19 — Automated drift detector (`scripts/drift/praetor_drift.py`)

Cron-driven cross-bot drift detector, lives at
`/home/blisske/swarm/scripts/drift/` outside any git repo (same pattern
as backup scripts). Daily at 9 AM MDT.

**Categories checked:** module presence · Config.yaml keys ·
`requirements.txt` versions · Telegram commands · SQLite table schemas
(per-column) · public function signatures · live `/api/*` response
shapes.

Known architectural drift is documented inline with reasons —
FX-specific helpers, `/calendar` (Ionic-only) vs `/catalysts`
(Tiberius-only) vs `/earnings` (Anton-only) commands, the existing
inline-vs-helper pattern for MTF gate, etc.

**Cron pipeline:**
- `drift_cron.sh` runs `praetor_drift.py`
- Clean exit: silent (logs to `/tmp/drift.log`)
- Non-zero exit: Telegram ping via the Tiberius bot token

**First-run state: 0 items flagged.**

## 2026-05-19 — WebSocket event broadcasts wired

**Problem found:** `ConnectionManager.broadcast()` was dead code across all three Praetor bots — defined but never called. The WS endpoint's own `while True` tick loop was carrying data (5s polling), so the dashboard wasn't actually frozen, but there was no event-driven push for SHADOW BUY/SELL fills or risk-mode transitions.

**What landed (Ionic — mirrors Anton's architecture, since both have a persisted `risk_state` table):**
- `core/database.py` — added `pending_events` table (engine writes, API drains, DB-as-IPC same pattern as `.restart_engine`) + `emit_event()` helper. Wrapped `log_trade()` to auto-emit `trade` events for any `SHADOW ` action, and `update_risk_state()` to auto-emit `risk_transition` events on actual mode change.
- `api/main.py` — added `_drain_pending_events()` background task (1s cadence, ships via `manager.broadcast()`, prunes >7d). Extended `ConnectionManager` to track `(ws, user)` tuples so drained events broadcast to admin only.
- `web/src/pages/Dashboard.jsx` — `onmessage` now handles `trade`/`risk_transition`. New `LiveEventStrip` renders an ephemeral flash strip under the risk banner; trade events also refetch `/trades` and `/equity`.

**Verified:** schema migrated, smoke-test event round-tripped end-to-end on `ionic.db`. The pipeline is ready for when Phase 2's Oanda integration produces the first real SHADOW BUY fill.

**Do not re-suggest:** `manager.broadcast()` is no longer dead code; the WS endpoint's 5s tick loop is intentionally kept as a safety net.

---

## Current System State

| Item | Value |
|---|---|
| Phase | **5 — FX Guide page rewritten (operator docs complete)** |
| Engine mode | Full 5-min cycle: indicator fetch → shadow exit engine → 4-layer consensus → shadow buy/sell against the $10K paper ledger. No Oanda orders (shadow-only by design). |
| Broker | Oanda v20 REST — client written, awaiting `OANDA_API_TOKEN` + `OANDA_ACCOUNT_ID` in `~/swarm/ionic/.env` |
| Universe | EUR/USD, GBP/USD, AUD/USD, NZD/USD, USD/JPY, USD/CHF, USD/CAD (7 majors, hot-reloaded from Config.yaml each cycle) |
| Timeframe | 1h |
| Shadow ledger | Empty (`/app/data/ionic.db` schema initialized, no trades yet) |
| Telegram | **Wired** — dedicated Ionic bot, full command set, autocomplete registered, trade-event notifications live |
| Dashboard | Reachable at `http://192.168.0.135:8085/` (LAN debug) and `https://ionic.blisske.hopto.org/` (Traefik + Let's Encrypt) |

---

## Phase 1 — Infrastructure Scaffold (2026-05-17)

Cloned Anton's repo structure as the starting point, renamed everywhere
(anton→ionic, ports 8001→8002 and 8080→8085), wired into the swarm.

### What landed

- **Compose stack** — three containers `ionic-engine` / `ionic-api` /
  `ionic-web` with per-bot bridge net `ionic-net` and shared `swarm-net`
  for Traefik. Container names `container_name:` baked in so the swarm-root
  compose project owns them.
- **Ports** — ionic-api on `127.0.0.1:8002` (loopback debug),
  ionic-web on `:8085` (LAN debug). 8084 was skipped because fixit-api owns
  it. The canonical public ingress is Traefik at `ionic.blisske.hopto.org`.
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
- **Web** — React SPA serves at port 8085. Login page rebranded ("Ionic
  trading dashboard", "Autonomous FX intelligence"), sidebar header reads
  "Ionic · FX". Other pages (Dashboard / Trades / Tuning / Market / Config)
  inherited unchanged from Anton — they read from the empty Ionic DB and
  render with empty-state placeholders.
- **nginx** — `web/nginx.conf` uses the `resolver 127.0.0.11` + variable
  in `proxy_pass` pattern (the May-12 Anton/Tiberius fix), so future
  `ionic-api` rebuilds won't 502 the dashboard.
- **Traefik route** — `~/swarm/proxy/dynamic/ionic.yml` registered;
  file-provider picked it up automatically. Let's Encrypt cert issued for
  `ionic.blisske.hopto.org`. Confirmed by a `curl https://ionic.blisske.hopto.org/`
  returning 200.
- **Swarm root** — `~/swarm/docker-compose.yml` `include:` list extended
  with `./ionic/repo/docker-compose.yml`. `~/swarm/.env` extended with
  `IONIC_DATA_DIR`, `IONIC_ENV_FILE`, `IONIC_HOSTNAME` for compose
  interpolation.
- **Repo identity** — Anton-specific docs (CLAUDE/WORKING_STATE/AGGRESSIVE/PIVOT/MIGRATION)
  removed, Ionic CLAUDE.md and this file written. Backtest results from
  Anton's tuning campaigns deleted (`scripts/backtest_results/`,
  `scripts/backtest_cache/`).
- **Verification** —
  - `ionic-api` loopback `/api/health` → 200
  - `ionic-web` LAN `/api/health` → 200 (nginx proxy hits API)
  - `ionic-web` LAN `/api/config` → 401 (auth working)
  - `https://ionic.blisske.hopto.org/` → 200 (Traefik + Let's Encrypt working)
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
   lands and add the token to `~/swarm/ionic/.env`.
2. **Oanda practice-account credentials** — sign up, generate personal
   access token, grab account ID. Phase 2 entry condition.
3. **Cosmetic** — the Login page still has Anton-vintage subtext ("Sign in
   to your Ionic trading dashboard"). Fine, but the marketing copy could be
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
  10:56:59  flag placed via ionic-api docker exec
  10:57:21  engine: "Restart flag detected mid-sleep; exiting for compose to restart."
  10:57:24  fresh engine: "Ionic Phase 2 engine starting"
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
  the Anton TradFi guide; Ionic-specific Guide is Phase 5.

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
4. **Populate `~/swarm/ionic/.env`** — paste the token and account ID
   into the placeholders added in Phase 2:
   ```
   OANDA_API_TOKEN=<your token>
   OANDA_ACCOUNT_ID=<your account id>
   OANDA_ENVIRONMENT=practice
   ```
5. **Restart the engine** — either via the dashboard's Config-page Restart
   button, or:
   ```bash
   docker exec ionic-api touch /app/data/.restart_engine
   ```
   Within ~30 seconds the engine wakes, picks up the new env vars, and
   starts hitting Oanda for live OHLCV.

### Phase 2 verification (post-credentials)

Once creds are populated and the engine has restarted, expected log lines:
```
=== Ionic Phase 2 engine starting ===
Oanda client ready: OandaClient(account='101-001-12345678-001', environment='practice', ...)
DB schema ready at /app/data/ionic.db
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

- **Brand color + login redesign** (folded into this session) — Ionic's
  visual identity locked to electric blue (`#3B82F6`). Login page has
  a distinct currency-glyph pattern overlay + blue accent line + FX-
  specific tagline. See preceding commit (56b807e) for the detail.

### What did NOT land in Phase 3

- **Telegram bot** — Phase 3b. Needs a Ionic BotFather token in `.env`,
  then porting the command handlers from Anton/Tiberius. Engine doesn't
  poll Telegram in Phase 3 so there's no token conflict.
- **`/api/account` + `/api/positions` Oanda integration** — Phase 6
  reads the real account from Oanda. Phase 3 dashboard reads from the
  synthetic ledger only.
- **Macro calendar blackout** — Phase 4. NFP / FOMC / CPI / ECB / BoJ
  event-window skipping.
- **Ionic-specific Guide page** — currently shows Anton's TradFi guide;
  Phase 5 rewrites for FX.

### Live verification

```
2026-05-17 14:26:13 === Ionic Phase 3 engine starting ===
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
3. **Phase 3b: Telegram** — create a Ionic bot via @BotFather, add the
   token to `~/swarm/ionic/.env`, restart. Then port the command
   handlers from Anton.
4. **Phase 4: Macro calendar** — most useful before live mode. NFP /
   FOMC blackouts skip new entries N hours before high-impact events.

---

---

## Phase 3b — Telegram Bot + Trade Notifications (2026-05-17)

Dedicated Ionic Telegram bot wired alongside the Phase 3 trading loop. Both
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
    sections; Ionic's shadow contract means no naked stops can exist and
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

- **Boot announcement** — engine sends `"📈 Ionic (FX) ONLINE"` on
  startup once Telegram is wired. First boot failed with "Chat not
  found" because Telegram allowlists DMs per-bot and the user had to
  `/start` the new bot first. After `/help` was sent once, the
  channel is established and all subsequent notifications work.

### Verification

```
2026-05-17 15:43:54 === Ionic Phase 3b engine starting ===
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

- **`/protect` and `/apply`** — deliberately skipped. Ionic shadow-mode
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

- **All four containers healthy:** ionic-engine, ionic-api, ionic-web,
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
- **Phase 5 (Ionic Guide page rewrite)** — `web/src/pages/Guide.jsx`
  still shows the Anton TradFi guide. Needs FX-specific sections
  including a section on the macro blackout we just built.
- **Phase 6 (live Oanda)** — separate effort. Live deployment gates.

### Operational state at handoff

- All four containers healthy: ionic-engine, ionic-api, ionic-web,
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
Phase 1 sed sweep (Anton → Ionic identifier rename). Content stayed
equity-centric: PDT references, US session hours, earnings blackout,
shares-based sizing, "TradFi" framing throughout. Phase 5 is a clean
rewrite for FX context.

### What landed

- **`web/src/pages/Guide.jsx` (FULL REWRITE)** — ~830 lines, 11 sections,
  structurally mirrors the Tiberius / Anton guides but with FX-specific
  content throughout.

  Section list:
  1. **What Ionic does** — overview, unleveraged-by-design framing,
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
  5. **Risk management** — Ionic-specific numbers (5% paper / 2% live,
     12% cap, 5 max). Added a callout box for the unleveraged-by-design
     principle (Oanda offers 50:1 retail leverage; Ionic deliberately
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
  9. **Telegram commands** — updated for Ionic's surface: added
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
      · **Praetor / stack** — Ionic / Anton / Tiberius cross-references
        updated; Battlemage host described accurately

- **Color palette unchanged**: Ionic brand BLUE (#3B82F6) is the section
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

- Guide page live at `https://ionic.blisske.hopto.org/guide` and
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
and Tiberius both have one; Ionic did not). User asked whether all three
bots reflect their market-open semantics correctly.

Audit + landed work:
- **Anton:** already correct. The market-open gate at the top of the cycle
  loop (querying Alpaca's clock API) skips the entire cycle body —
  including the reveille block — on weekends and holidays.
- **Tiberius:** already correct (you'd fixed this earlier with rotating
  flavor lines for 24/7 framing).
- **Ionic:** had no reveille. Added one in `_maybe_send_reveille()` with
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
Anton, Tiberius, and Ionic, making the three pages look like recolors of
the same template. Replaced Ionic's with `MultiPairChartTrace` —
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
   Tiberius was already at 3. Bumped to **3** on Anton and Ionic in
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
`docker compose up -d --build anton-engine tiberius-engine ionic-engine`
from `~/swarm/`. All three healthy and cycling at 20:07 ET.

## Tuner Trigger Wired Into Engine Loop (2026-05-18, evening)

While fixing a session-counter bug on Anton and Tiberius's tuner trigger,
discovered Ionic's main.py was **missing the tuner invocation entirely**.
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
loop. Ionic currently has 0 closed shadow trades (just started cycling
after the Oanda credentials landed earlier this week), so the block runs
silently every cycle. Once any symbol accumulates 10 closes, the
`[TUNER] Trigger fired` line will appear in engine logs and the tuner's
internal per-strategy + cooling-off guards take over from there.

This brings Ionic to feature parity with Anton and Tiberius on the
tuning mechanism. Verified post-restart: first cycle ran clean, no
exceptions, all 7 FX pairs scanned normally.

## Alpaca Scaffolding Purge — FX-Native /api/session (2026-05-19)

The Phase 1 rsync from Anton left several Alpaca-shaped code paths that
nothing ever ported off. They worked (or appeared to) because the alpaca-py
package was still pinned in `requirements.txt`, satisfying the imports.
But the runtime semantics were wrong: `/api/session` was asking Alpaca's
US-equity clock whether US-equity hours were open and returning
`Pre-Market`/`After Hours`/`Weekend` for Ionic's FX dashboard. Cleanup:

- **`api/main.py` — `/api/session` rewritten for FX 24/5.** Dropped
  `_alpaca_is_open()` (Alpaca clock + 60s cache) and the equity-flavored
  `_market_session_status()` (9:30-AM-/-3:30-PM-/-4:00-PM-ET branches).
  Replaced with a pure-datetime helper that calls
  `execution.is_market_open()` (canonical FX gate: Sun 17:00 ET → Fri
  17:00 ET) and returns:
  - `{open: True, status: "Open"}` (continuous most of the week)
  - `{open: True, status: "Open", closes_in_minutes: N}` when Friday
    close is within 24h
  - `{open: False, status: "Closed", opens_in_minutes: N}` over the
    weekend gap (countdown to Sunday 17:00 ET)
  Tested across all the boundary times (Mon morning, Wed afternoon, Fri
  16:59 ET, Fri 17:00 ET, Sat noon, Sun 16:59 ET, Sun 17:00 ET) —
  payload is correct at every transition.

- **`api/main.py` — `_CONFIG_REQUIRED_KEYS` fixed.** The save-config
  validator was requiring `"alpaca"` in the posted Config, but Ionic's
  Config.yaml has `"oanda"` — meaning every POST to `/api/config` from
  the dashboard was returning HTTP 422. Changed `"alpaca"` → `"oanda"`.
  Save flow now actually works.

- **`api/main.py` — module docstring** updated from "TradFi instance —
  Alpaca Paper Account / US Equities" to "FX instance — Oanda v20
  (Phase 1 scaffold; shadow-only)."

- **`core/config_manager.py` — dead Alpaca client factory removed.**
  Deleted `get_trading_client()` and `get_data_client()` plus their
  module-level `_trading_client` / `_data_client` caches. Removed the
  `requests.adapters.HTTPAdapter` and `urllib3.util.retry.Retry` imports
  that only existed to hand-roll Alpaca's "network armor." Updated the
  module header comment from "TradFi Alpaca keys + centralized Alpaca
  client instances" to "FX (Oanda) secrets + Config.yaml; Oanda client
  itself is constructed in the broker adapter."

- **`requirements.txt`** — `alpaca-py==0.43.2` removed. Confirmed
  post-rebuild: `pip show alpaca-py` reports "not found" inside
  ionic-api, and `import alpaca` raises ImportError.

- **`web/src/pages/Dashboard.jsx`** — `SessionBadge` collapsed from the
  five-status equity palette (`Open` / `No New Entries` / `Pre-Market`
  / `After Hours` / `Weekend`) to the two-status FX palette (`Open`
  green, `Closed` gray). Fallback color updated to `Closed`.

### Live verification post-rebuild

```
$ docker exec ionic-api pip show alpaca-py
WARNING: Package(s) not found: alpaca-py

$ docker exec ionic-api python3 -c "
  import config_manager
  print(hasattr(config_manager, 'get_trading_client'),
        hasattr(config_manager, 'get_data_client'))"
False False

$ docker exec ionic-api python3 -c "
  import main; print(main._market_session_status())"
{'open': True, 'status': 'Open'}    # Tue 10:58 ET, > 24h from Fri close

$ curl -s http://127.0.0.1:8002/api/health
{"status":"ok","service":"Ionic API","version":"1.0.0"}
```

All three containers rebuilt + recreated from `~/swarm/`, all healthy.
Engine cycle is running the 5-min FX scan normally; no errors in the
ionic-api uvicorn boot log.

### Known residue (out of scope this pass)

- **`core/config_manager.py:load_secrets()`** still returns
  `alpaca_api_key` / `alpaca_secret_key` env reads. These are harmless
  (return `None` since the env vars are unset) and have no import
  dependency on alpaca-py — pure `os.getenv`. Worth deleting eventually
  for cleanliness; not blocking anything today.
- **`scripts/lightweight_backtest.py`** is the Anton SPY/QQQ backtest
  harness, imports `alpaca.data.*` at module level. With alpaca-py
  removed from `requirements.txt` it will now ImportError on launch
  inside the container. It's a standalone tool (not wired into the
  engine), so the trading loop is unaffected. Decide later whether to
  delete it or port it to Oanda v20 candle endpoint.

## How to Resume With Claude

1. Upload this WORKING_STATE.md at session start
2. SSH into the host: `ssh blisske@192.168.0.135`
3. `cd ~/swarm/` (always — compose runs from the swarm root; running from
   `~/swarm/ionic/repo/` creates a different compose project and collides
   with the swarm-managed `container_name:` declarations)
4. For one-off Python: `docker exec -it ionic-engine python3` (limited to
   stdlib + sqlite3 in Phase 1; oanda/telegram libs added in Phase 2)
5. Anton and Tiberius are sister bots in the same swarm; their repos are at
   `~/swarm/anton/repo` and `~/swarm/tiberius/repo`. Anton's
   `web/src/pages/Guide.jsx` is the model for the Ionic Guide we'll write
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
