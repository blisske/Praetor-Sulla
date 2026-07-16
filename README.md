# Foundation · Ionic

Autonomous **long-only spot FX** trading bot. Third instance of the Foundation
swarm, alongside [Doric](https://github.com/blisske/Foundation-Doric) (TradFi
equities) and [Corinthian](https://github.com/blisske/Foundation-Corinthian) (crypto).

## Status

**Deployed; shadow-soaking (Oanda).** Containers boot, dashboard loads, restart
flow works end-to-end. The engine runs the 4-paradigm signal engine against the
FX majors through the Oanda v20 broker adapter, with shadow trades and
backtests logged. Live-capital deployment gates remain the next milestone.

## Universe

Initial 7 majors:

- EUR/USD · USD/JPY · GBP/USD · USD/CHF
- AUD/USD · USD/CAD · NZD/USD

Defined as `strategy.active_symbols` in `Config.yaml`, hot-reloaded each cycle.

## Stack

- Python 3.12 (async architecture)
- Oanda v20 REST API (practice account, then live)
- FastAPI dashboard backend on `127.0.0.1:8002`
- React + Vite + Tailwind frontend served by nginx at `:8085` (LAN debug)
  and `ionic.blisske.hopto.org` (public via swarm Traefik + Let's Encrypt)
- SQLite (WAL mode), bind-mounted from `~/swarm/ionic/data/`
- AI sentiment via Gemma 4 26B on LM Studio (`host.docker.internal:1234`)
- News context via Brave Search API
- Telegram bot for remote command + alerts

## Quick start

```bash
# Build + bring up (run from ~/swarm/, NEVER from ~/swarm/ionic/repo/)
cd ~/swarm && docker compose up -d --build ionic-engine ionic-api ionic-web

# Logs
docker compose logs ionic-engine --tail 50

# Trigger clean restart (also works from the dashboard's Restart button
# or /restart Telegram command in Phase 2+)
docker exec ionic-api touch /app/data/.restart_engine
```

## Architecture

Ionic shares the Foundation stack with Doric and Corinthian — same 4-paradigm signal
engine (Trend Following, Mean Reversion, Volatility Breakout, Liquidity Sweep),
same 2+1+1 consensus (paradigm signal + supporting indicators + AI verdict +
score gate), same self-tuner (10 closes → SHADOW_PENDING → 10 more →
PROMOTED or REJECTED), same shadow-mode contract.

FX-specific differences:

| Concern | Corinthian (crypto) | Doric (equities) | Ionic (FX) |
|---|---|---|---|
| Broker | Binance.US (CCXT) | Alpaca | Oanda v20 |
| Hours | 24/7 | 9:30–4 ET, session-aware | 24/5 (Sun 17:00 ET → Fri 17:00 ET) |
| Timeframe | 1h | 30min | 1h |
| Sizing | USD notional | Whole shares | Units of base currency |
| Pip math | n/a | n/a | 0.0001 (0.01 for JPY pairs) |
| Calendar blackout | none | Earnings (yfinance) | Macro events (NFP / FOMC / ECB / CPI) |
| Force-exit | none | 3:50 PM ET | Optional Friday-afternoon flatten |
| Leverage | None (spot) | None (cash account) | None (deliberately unleveraged) |

## Phase plan

1. ✅ **Phase 1** — Repo + infra scaffold. Containers boot, dashboard loads.
2. ⏳ **Phase 2** — Oanda broker adapter. Real OHLCV bars in the dashboard.
3. ⏳ **Phase 3** — FX-specific math (pip values, JPY pair handling, unit-based sizing).
4. ⏳ **Phase 4** — Macro calendar blackout (NFP / FOMC / etc.).
5. ⏳ **Phase 5** — Shadow contract + Telegram cmds + Guide page.
6. ⏳ **Phase 6** — Soak in shadow. 30+ closed trades, one tuning cycle, then live gates.

## Sibling repos

- [Foundation-Doric](https://github.com/blisske/Foundation-Doric) — TradFi equities (Alpaca)
- [Foundation-Corinthian](https://github.com/blisske/Foundation-Corinthian) — crypto (Binance.US / Corinthian)
