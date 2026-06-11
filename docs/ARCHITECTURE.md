# Architecture

System map for the Polymarket signal-based trading bot. Autonomous, crypto-markets-only,
signal-driven with Kelly sizing, running 24/7 with tiered LLM routing. For the why behind
recent design changes (cost gates, benched signals, data-regime cutoff), see
`PROFITABILITY_FIX_PLAN.md`.

---

## 1. Bird's-eye view

```
                        EXTERNAL SERVICES
 ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────────────────┐
 │ Gamma API  │ │ CoinGecko│ │ DeFi Llama│ │ OpenRouter               │
 │ (markets,  │ │ (prices, │ │ Fear&Greed│ │  cheap: gemini-flash-lite│
 │  discovery,│ │  vol)    │ │ CG global │ │  sonar: perplexity/sonar │
 │  no auth)  │ │          │ │           │ │  frontier: claude-opus   │
 └─────┬──────┘ └────┬─────┘ └─────┬─────┘ └────────────┬─────────────┘
       │             │             │                    │
       │   ┌─────────┴─────────────┴────────────────────┴──────┐
       │   │ Manifold / Kalshi (consensus)   Polymarket CLOB   │
       │   │                                 (orders ONLY)     │
       │   └────────────────────────┬──────────────────────────┘
       ▼                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       BotEngine (web/server.py)                     │
│                                                                     │
│  ┌────────────────┐   ┌─────────────────┐   ┌───────────────────┐  │
│  │ _discovery_loop│──▶│_aggregation_loop│──▶│ _position_loop    │  │
│  │ every 30 min   │   │ every 30 min    │   │ every 5 min       │  │
│  │ market_filter  │   │ signals/* +     │   │ executor TP/SL,   │  │
│  │ (Gamma)        │   │ kelly + executor│   │ stale orders      │  │
│  └────────────────┘   └─────────────────┘   └───────────────────┘  │
│                                                                     │
│            3 consecutive failures ⇒ pause + critical alert         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ reads/writes
                               ▼
                 ┌──────────────────────────────┐
                 │  SQLite  data/bot.db         │
                 │  (core/db.py, WAL mode)      │
                 └──────────────┬───────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
  ┌──────────────────┐ ┌─────────────────┐ ┌────────────────────┐
  │ Learning engine  │ │ Calibration     │ │ FastAPI :8080      │
  │ monitoring/      │ │ signals/        │ │  └ React (Vite)    │
  │ learning.py      │ │ calibration.py  │ │    dashboard :5173 │
  └──────────────────┘ └─────────────────┘ └────────────────────┘
```

Hard boundary worth repeating: **Gamma API does all market reading; the CLOB client
(`core/client.py`) does order execution only** and is never instantiated in the
discovery/filter pipeline.

---

## 2. Trading pipeline (one market's journey)

The aggregation loop pulls up to 20 candidates per cycle from the discovery cache
(deduped by `conditionId`; re-analyzed only if price moved >5%, the interval elapsed,
or expiry is <7 days; markets with open positions are skipped) and runs each through
this funnel. The ordering is deliberate: **free math first, paid LLMs last.**

```
 Gamma discovery (strategy/market_filter.py)
   crypto category, liquidity $500–$500k, 24h–45d to resolution,
   spread ≤ 5%, volume ≥ $100 ─▶ ranked candidates ─▶ market_cache
        │
        ▼
 ┌─ signals/aggregator.py ─────────────────────────────────────────────┐
 │                                                                     │
 │  STEP 1 — FREE signals (parallel, no LLM)                           │
 │   ├ resolution_crypto    CoinGecko barrier/terminal math            │
 │   │                      (Brier 0.048 — the edge source, weight 2.5)│
 │   ├ prediction_markets   Manifold/Kalshi/Gamma consensus (1.8)      │
 │   └ onchain_flow         flow tilt on vol/expiry baseline (weight 0,│
 │                          earn-back via calibration)                 │
 │        │                                                            │
 │        ▼                                                            │
 │  STEP 2 — PRE-FRONTIER EDGE GATE (the cost gate)                    │
 │   prelim_prob = weighted merge of free signals                      │
 │   |prelim_prob − market_price| < PRE_FRONTIER_EDGE_THRESHOLD (3%)?  │
 │        │                                                            │
 │        ├── yes ─▶ SKIP. Record in frontier_decisions                │
 │        │          (skip_reason = pre-frontier gate), free signals   │
 │        │          still logged to calibration. $0 spent.            │
 │        ▼ no                                                         │
 │  STEP 3 — PAID signals                                              │
 │   ├ web_search (Sonar)  — benched: ENABLE_WEB_SEARCH_SIGNAL=false,  │
 │   │                       not constructed, weight 0                 │
 │   └ frontier (Opus)     — final probability + confidence, given     │
 │                           temporal context (signals/temporal.py).   │
 │                           Failure ⇒ ALERT AND SKIP, never fall      │
 │                           back to cheap.                            │
 └────────┬────────────────────────────────────────────────────────────┘
          ▼
 strategy/kelly.py — TradeDecision
   blend frontier prob toward market price by confidence, subtract
   fees + gas + slippage, edge ≥ MIN_EDGE_THRESHOLD (4%)?
   divergence guardrails, quarter-Kelly (0.25) × bankroll,
   cap MAX_POSITION_PCT (10%), floor MIN_BET_USD ($5)
          │
          ▼
 strategy/depth.py — CLOB order book check
   slippage ≤ 3%, depth ≥ $200, shrink bet to available depth,
   depth-aware limit price
          │
          ▼
 strategy/executor.py — PaperExecutor | TradeExecutor
   risk guardrails: ≤8 trades/h, ≤12 open positions, ≤3 per underlying,
   4h re-entry cooldown, max-entry-spread 15%, drawdown (30%) and
   daily-loss (15%) circuit breakers
          │
          ▼
   trades + positions tables   (REALISTIC_PRICING: entries vs fresh
                                bid/ask, exits valued at the bid)
```

Every decision — trade or skip — leaves a row in `frontier_decisions` /
`skipped_markets`, and every signal prediction lands in `signal_calibration`, so the
feedback loops below always have data even when no money moves.

---

## 3. Position management

```
 _position_loop (every 5 min)
   │
   ├ refresh quotes for open positions (bid side when REALISTIC_PRICING)
   │
   ├ take-profit:  +12% (TAKE_PROFIT_PCT)        ┐ close_position():
   ├ stop-loss:    −10% (STOP_LOSS_PCT), but     ├▶ positions.status='closed',
   │   never inside STOP_LOSS_MIN_TICKS (3 ticks │  realized PnL written back
   │   × $0.01) of entry — protects low-priced   │  to the matching trade row
   │   markets from tick-noise stops             ┘
   ├ resolution:   market resolved ⇒ close at 0/1
   │
   └ stale orders: unfilled PENDING > 15 min ⇒ cancel
```

---

## 4. Feedback loops (calibration + learning)

Two loops run off the recorded outcomes. Both ignore everything before
`LEARNING_DATA_CUTOFF` (2026-05-22T20:30Z) — data from the optimistic-pricing era is
tagged `data_regime='pre_fix'` and never informs weights or parameters again.

```
                    market resolves (Gamma closed=true)
                                  │
                                  ▼
              signal_calibration.actual_outcome backfilled
                  │                              │
   ┌──────────────▼───────────────┐ ┌────────────▼─────────────────────┐
   │ CALIBRATION (per signal)     │ │ LEARNING (per parameter)         │
   │ signals/calibration.py       │ │ monitoring/learning.py           │
   │                              │ │                                  │
   │ rolling time-decayed Brier   │ │ frontier bias · skip retrospect  │
   │ ⇒ dynamic weight multipliers │ │ edge realization · cost ROI      │
   │ (0.5×–2×) once ≥20 samples   │ │ ⇒ recommendations, auto-applied  │
   │                              │ │   to parameter_overrides when    │
   │ benched signals (weight 0)   │ │   conf ≥0.7, n ≥30, ≤10%/cycle,  │
   │ earn back at Brier <0.20     │ │   hard floors/ceilings           │
   │ over ≥30 resolved samples    │ │ ⇒ auto-revert if post-change     │
   │                              │ │   metrics degrade >20%           │
   └──────────────┬───────────────┘ └────────────┬─────────────────────┘
                  ▼                              ▼
       aggregator signal weights      get_effective_param() reads
       next cycle                     overrides at decision time
```

The go/no-go layer on top: `GET /api/paper/summary` evaluates the post-cutoff window
against the live-readiness criteria (≥7 days, ≥100 closed trades, net-of-LLM-cost
PnL > 0, top market <25% of profit, frontier Brier ≤ market-price Brier on ≥30
resolved markets).

---

## 5. LLM routing (core/llm.py)

```
 task                          model                          on failure
 ──────────────────────────    ───────────────────────────    ─────────────────────
 summarize/extract/classify    google/gemini-2.5-flash-lite   z-ai/glm-4.5-air
 web search signal (benched)   perplexity/sonar               fall back to cheap
 final probability + decision  anthropic/claude-opus-4-6      ALERT AND SKIP — never
                                                              silently downgrade
```

All calls go through OpenRouter, are rate-limited (20/min cheap, 5/min frontier), and
are logged to `llm_costs` **before** returning — cost accounting is not optional, and
`get_paper_balance` / `get_paper_summary` subtract it from PnL.

---

## 6. Database (core/db.py — SQLite, auto-created, WAL)

```
 trading state            decision audit            feedback / meta
 ──────────────           ──────────────            ──────────────
 trades                   frontier_decisions        signal_calibration
 positions                skipped_markets           signal_multiplier_history
 bankroll                 signals                   learning_reports
 market_cache             llm_costs                 parameter_overrides
                                                    parameter_change_snapshots
                                                    market_regimes
```

Conventions: `condition_id` keys markets everywhere; timestamps are ISO-8601 UTC;
`trades.pnl` (not `positions.realized_pnl`, which is reset on re-entry) is the
authoritative realized-PnL record; `data_regime` tags pre-/post-fix rows on the four
learning-relevant tables.

---

## 7. Web layer

```
 main.py --web
   └ FastAPI :8080 (web/server.py) — owns BotEngine + the 3 worker loops
       ├ REST: /api/health /api/markets /api/analysis /api/trades
       │       /api/pnl /api/costs /api/paper-balance /api/paper/summary
       │       /api/learning/* /api/bot/{start,stop,pause,resume} /api/db/*
       ├ WebSocket: live phase/cycle/analysis broadcast
       └ React (Vite :5173, proxies /api) — tabs: Dashboard · Markets ·
         Analysis · Trades · Learning · Database · Logs
         (polling: dashboard 30s, analysis 15s, logs 5s)
```

The engine is embedded in the web process — starting the dashboard *is* starting the
bot's workers; `main.py` without `--web` runs the same loops headless.
