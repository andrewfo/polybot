# Polymarket Signal-Based Trading Bot

## Project Overview
Autonomous Polymarket trading bot focused exclusively on crypto markets. Signal-based trading with Kelly criterion sizing on mid-to-low liquidity binary markets. Runs 24/7, uses tiered LLM routing (cheap models for grunt work, frontier model for trade decisions).

## Tech Stack
- Python 3.11+, asyncio throughout (no sync blocking in async paths)
- py-clob-client for Polymarket CLOB API (order execution only)
- Gamma API for market discovery (no auth required)
- SQLite via sqlite-utils for state persistence (DB at `data/bot.db`)
- OpenRouter for all LLM calls (unified API, tiered model routing)
- aiohttp for async HTTP, web3/eth-account for wallet ops
- FastAPI + React (Vite) for localhost web dashboard
- Docker for deployment
- pytest + pytest-asyncio for testing

## Architecture
```
main.py                  → Entry point, --web flag for dashboard (main loop NOT YET IMPLEMENTED)
core/llm.py              → OpenRouter client, tiered routing (cheap vs frontier)
core/client.py           → Polymarket CLOB wrapper for ORDER EXECUTION ONLY (no market reading methods)
core/wallet.py           → Wallet balance checks, gas monitoring
core/db.py               → SQLite tables: trades, positions, signals, bankroll, llm_costs, market_cache, signal_calibration, frontier_decisions, skipped_markets
strategy/market_filter.py→ Gamma API discovery, filtering, LLM categorization (crypto-only gate), ranking
web/server.py            → FastAPI backend, REST endpoints, BotEngine stub, log buffer
frontend/                → React (Vite) dashboard: Dashboard, Markets, Analysis, Logs tabs + Chart.js charts
scripts/setup_wallet.py  → Wallet setup helper
scripts/dashboard.py     → Standalone web dashboard launcher
```

### Implemented Signal Engine (3 providers, crypto-focused)
```
signals/base.py              → SignalResult dataclass + SignalProvider ABC
signals/resolution_crypto.py → CoinGecko + barrier/terminal probability models (NO LLM)
signals/aggregator.py        → Dynamic-weighted signal merge → FRONTIER model final probability call
signals/temporal.py          → Date context injection, urgency tiers, frontier system prompt builder
signals/web_search.py        → Perplexity Sonar search-grounded LLM signal (universal, all categories)
signals/prediction_markets.py→ Cross-platform consensus (Metaculus + Kalshi + Polymarket Gamma, no auth, no LLM — keyword extraction + Jaccard matching)
signals/calibration.py       → Brier-score calibration, dynamic source multipliers, resolution tracking
strategy/depth.py            → CLOB order book depth analysis, slippage estimation, bet adjustment
```

### Order Execution (strategy/executor.py) — Section 6 COMPLETE
- `AutoStopError` exception for critical guardrail failures (drawdown, daily loss)
- 5 risk guardrail functions: `check_position_count()`, `check_trade_rate()`, `check_drawdown()`, `check_daily_loss()`, `check_all_guardrails()`
- `compute_limit_price(decision, market_data)`: BUY_YES uses `best_ask - SLIPPAGE_BUFFER`, BUY_NO uses `(1 - best_bid) - SLIPPAGE_BUFFER`, clamped `[0.01, 0.99]`
- `PaperExecutor`: instant fills, records trade + position with `paper=True`, `manage_positions()` fetches Gamma prices for PnL
- `TradeExecutor`: places limit orders via `ClobClientWrapper`, `monitor_orders()` detects fills and expires stale orders (>STALE_ORDER_MINUTES)
- **Take-profit / Stop-loss**: `manage_positions()` auto-closes positions at +TAKE_PROFIT_PCT (25%) or -STOP_LOSS_PCT (15%). PaperExecutor instant close; TradeExecutor places limit sell.
- Positions have `status` column: `open` / `closing` / `closed`. `close_position()` records exit_price + realized_pnl.
- Settings: `PAPER_TRADING` (default true), `STALE_ORDER_MINUTES` (default 15), `TAKE_PROFIT_PCT` (0.25), `STOP_LOSS_PCT` (0.15)
- DB migrations: `order_id`, `placed_at`, `market_question` columns on trades table; `status` column on positions table
- `get_recent_trade_count(hours)` helper for trade rate limiting

### Audit Trail (core/db.py)
- `frontier_decisions` table: market_id, estimated_prob, effective_prob, market_price, edge, kelly_fraction, bet_size_usd, confidence, should_trade, skip_reason, timestamp
- `skipped_markets` table: market_id, skip_reason, market_price_at_skip, estimated_prob, confidence, timestamp, resolution_outcome
- `record_frontier_decision()` called after every Kelly computation
- `record_skipped_market()` called on every skip (aggregation or Kelly)

### Not Yet Implemented (build plan sections 7-11)
```
monitoring/pnl.py        → P&L tracking, bankroll snapshots, performance metrics
monitoring/health.py     → Health checks while bot is running (web UI-driven)
monitoring/notifications.py → Web log panel + Python logging (no Telegram)
```

### Kelly Criterion (strategy/kelly.py) — Section 5 COMPLETE
- `TradeDecision` dataclass with full audit trail (18 fields incl. `effective_prob`, depth fields)
- `calculate_kelly()` confidence-blends estimate toward market price, then computes fractional Kelly (0.25x) with fee-adjusted odds
- **Sublinear confidence blending**: `blend_weight = max(confidence ** 0.75, MIN_CONFIDENCE_BLEND)` — preserves more edge at low confidence than linear blending. conf=0.25→0.35, conf=0.50→0.59, conf=0.80→0.85
- `MIN_CONFIDENCE_BLEND` default: 0.15 (was 0.50), `MIN_EDGE_THRESHOLD` default: 0.02 (was 0.03)
- Fee adjustment: Polymarket's 2% profit fee reduces effective odds (POLYMARKET_FEE_RATE setting)
- **Dynamic bankroll reserve**: `max(MIN_BANKROLL_RESERVE, bankroll * 0.05)` — scales with portfolio size
- Integrated into pipeline: every successful aggregation runs Kelly sizing + depth analysis
- Results shown in web UI Analysis tab with table + detail view
- Safety checks: edge threshold, positive Kelly, min bet $1, max position 10%, dynamic reserve, existing exposure

### Order Book Depth Analysis (strategy/depth.py)
- Fetches CLOB order book via public HTTP endpoint (no auth, no ClobClientWrapper)
- `analyze_depth()`: walks ask levels to compute average fill price, slippage, max fillable
- Skips trade if total book depth < `MIN_DEPTH_USD` ($200)
- Reduces bet size if slippage > `MAX_ACCEPTABLE_SLIPPAGE` (3%) using binary search
- `DepthAnalysis` dataclass with full audit trail (token_id, slippage, adjusted_bet, skip_reason)
- Integrated post-Kelly: runs after Kelly sizing, before trade execution
- `DEPTH_ANALYSIS_ENABLED` setting to toggle (default: true)

### Signal Calibration (signals/calibration.py)
- Tracks signal provider predictions vs actual market resolutions in `signal_calibration` DB table
- `record_prediction()`: called after each aggregation for every usable signal, uses `conditionId` as `market_id` (not question text)
- `record_resolution()`: updates predictions when markets resolve (via Gamma API check)
- `check_and_record_resolutions()`: called at start of each pipeline cycle to check for newly resolved markets
- `get_dynamic_multipliers()`: computes time-decay-weighted Brier score per provider, scales weights relative to average
- Time decay: `weight = exp(-age_days / 45)` — recent predictions weighted more heavily
- Ratio = avg_brier / provider_brier (better providers get higher multipliers)
- Multipliers clamped to [0.5x, 2.0x] of default to prevent wild swings
- Falls back to defaults when < `MIN_CALIBRATION_SAMPLES` (20) resolved predictions per provider
- Aggregator refreshes multipliers at start of each aggregation cycle

## Market Discovery — Gamma API
- Use Gamma API (`https://gamma-api.polymarket.com/markets`) for all market discovery — NOT the CLOB API
- No auth required for Gamma read endpoints
- Primary fetch: `?active=true&closed=false&order=volume24hr&ascending=false&limit=200` (by volume)
- Secondary fetch: `?active=true&closed=false&order=startDate&ascending=false&limit=200` (newest, more likely mispriced)
- Key fields: `conditionId`, `liquidityNum`, `volume24hr`, `spread`, `bestBid`, `bestAsk`, `outcomePrices[]`, `endDate`, `clobTokenIds[]`, `outcomes[]`
- `liquidity`/`liquidityNum` values on Polymarket range from $500 to $5M+ — filter bands must account for this
- Spread data comes from Gamma directly — do NOT fall back to CLOB API for spread (causes 400 errors)
- If Gamma `spread` is null, compute from `bestAsk - bestBid`
- CLOB client (`py-clob-client`) is for order execution only — NO market reading methods on ClobClientWrapper
- `discover_markets()` and `filter_markets()` do NOT take a CLOB client parameter — they use Gamma API exclusively
- The filter pipeline must NEVER instantiate ClobClientWrapper (it causes auth HTTP calls to clob.polymarket.com on init)

### Market Filter Pipeline (strategy/market_filter.py)
1. Binary only (2 tokens)
2. Liquidity band: `MIN_MARKET_LIQUIDITY` ($500) to `MAX_MARKET_LIQUIDITY` ($500k)
3. Time to resolution: `MIN_HOURS_TO_RESOLUTION` (72h) to `MAX_DAYS_TO_RESOLUTION` (30d)
4. Near-certain price: drop if any outcome price <= 0.02 or >= 0.98
5. Spread: drop if spread > `MAX_SPREAD` (0.05) — Gamma data only
6. Skip markets with existing positions
7. Pre-screen with CoinGecko math: compute barrier/terminal probability, compare to market price, attach `_model_edge`
8. Rank by Kelly-adjusted edge (model_edge × kelly_leverage), continuous Gaussian time score (peak at 5d), then total score

## Signal Aggregator (signals/aggregator.py)
- Collects signals from 3 providers (resolution_crypto, web_search, prediction_markets)
- Filters out signals with confidence=0 or probability=None
- If 0 usable signals → returns None (skip market)
- Computes weighted preliminary estimate using source multipliers:
  - `resolution_crypto`: 2.0x (direct CoinGecko data, barrier/terminal model — NO LLM)
  - `prediction_markets`: 1.8x (cross-platform market consensus)
  - `web_search`: 1.5x (Perplexity Sonar search-grounded)
  - Weight = `signal.confidence * source_multiplier`
- Log-odds averaging enabled by default (`USE_LOG_ODDS_AVERAGING` setting, default True) — more calibrated at extremes
- Pre-computes `signals_agreement` from stdev of signal probabilities (<0.05 agree, <0.15 mixed, else disagree)
- `aggregate()` accepts `condition_id` parameter for calibration tracking
- **Pre-frontier divergence filter**: skips frontier call when preliminary estimate diverges >0.35 from market AND signals disagree (saves ~$0.015/call)
- Makes single FRONTIER MODEL call with superforecaster prompt
- Frontier model sees both terminal and barrier probabilities + multi-timescale vol data + pre-computed agreement
- If frontier confidence < 0.25 → skip market (returns None)
- Frontier failure RAISES — never falls back to cheap model
- All signals logged to `signals` SQLite table with full audit trail
- `AggregatedSignal` dataclass holds final result with all metadata

### Crypto Probability Model (signals/resolution_crypto.py)
- **Barrier model** (`barrier_probability()`): P(price touches target anytime before expiry) — used for "Will X reach Y?" markets (most crypto markets)
- **Terminal model** (`log_normal_probability()`): P(price above/below target at expiry) — used for "Will X be above Y on date Z?" markets
- Resolution type (barrier/terminal) extracted by `extract_resolution_params()` in market_filter.py, defaults to "barrier" for crypto
- **Volatility estimation** (`VolEstimate` dataclass): time-weighted with Bessel's correction, EWM (λ=0.94), 7-day short-term, all computed from actual timestamp intervals (not assuming daily)
- **Vol selection**: Deribit IV preferred (forward-looking), blended with short-term realized for <14d markets; falls back to EWM → historical → 80% default
- **Drift shrinkage**: Bayesian shrinkage toward zero based on t-statistic significance (prevents noisy 90d momentum from dominating)
- **Confidence scoring**: penalizes vol regime instability, extreme probabilities, heavily-shrunk drift; boosts for Deribit IV availability
- **Ticker whitelist**: `TICKER_TO_COINGECKO` maps 50 common crypto tickers/names → CoinGecko IDs; checked before LLM fallback to eliminate hallucination risk

### Web UI Tabs (4 tabs)
1. **Dashboard**: Bot status, connections (health checks), wallet, LLM costs (doughnut chart), open positions table
2. **Markets**: Gamma API market browser with sort/limit controls, click row for detail panel
3. **Analysis**: Horizontal split — market list (40%) + unified detail view (60%) with Chart.js charts (probability bars, vol comparison, price chart, Kelly breakdown, signal weights)
4. **Logs**: Log viewer with level filtering, auto-scroll, polls every 5s

### Web UI Architecture
- **Backend**: FastAPI on :8080 (`web/server.py`), REST endpoints proxying to existing backend functions
- **Frontend**: React (Vite) on :5173 (dev), Chart.js via react-chartjs-2 for visualization
- **Theme**: navy/grey/white palette (#0a1628, #0d1f3c, #8899aa, #e0e8f0, accent #4488cc)
- **Dev workflow**: Vite dev server on :5173 (HMR) proxies /api → :8080. Production: `npm run build` → FastAPI serves `dist/`
- **BotEngine stub**: `web/server.py` has BotEngine class — start/stop not yet wired to workers
- **Command stubs**: POST /api/commands/* return 501 — will wire with workers later
- **WebSocket**: /ws endpoint stubbed for future push updates (currently polling)

### Pipeline Workers (3 decoupled loops when bot is running)
When wired, the BotEngine will launch 3 independent workers:

**Worker 1: Discovery Loop** (every `DISCOVERY_INTERVAL_MINUTES`, default 2h, group: `discovery-loop`)
- Runs calibration check + full filter pipeline (discover → filter → categorize → extract → pre-screen → rank)
- Caches ranked results in `_filtered_market_cache` for the aggregation worker
- Caches results for API endpoint

**Worker 2: Aggregation Loop** (every `AGGREGATION_INTERVAL_MINUTES`, default 4h, group: `aggregation-loop`)
- Reads from `_filtered_market_cache`, applies conditionId dedup
- Runs `_aggregate_batch()` on top candidates (up to BATCH_SIZE=40)
- Stores results in BotEngine.analysis_entries for API endpoint

**Worker 3: Position Monitor Loop** (every `POSITION_CHECK_INTERVAL_MINUTES`, default 30min, group: `position-loop`)
- Runs `executor.monitor_orders()` + `executor.manage_positions()`
- Uses PaperExecutor or TradeExecutor based on PAPER_TRADING setting

All workers use cancellation-safe sleep (5s chunks checking `_bot_running`).
No race conditions: all workers are coroutines in the same event loop.
- The Analysis tab shows the current batch with per-market status (waiting/processing/done/skipped/error)
- The Dashboard tab shows the current bot process phase (filtering/aggregating/monitoring/waiting)

## Critical Design Rules

### LLM Routing — Never Violate These
- Cheap model (`google/gemini-2.0-flash-lite-001`): summarization, extraction, initial probability estimates, classification fallback (keyword matching handles most categorization)
- Fallback cheap model (`z-ai/glm-4.5-air:free`): used automatically if primary cheap model fails
- Sonar model (`perplexity/sonar`): search-grounded web search signal (via OpenRouter, ~$1/M tokens). Falls back to cheap on failure.
- Frontier model (`anthropic/claude-opus-4-6`): final probability estimation, trade/no-trade decisions only
- If frontier model fails: ALERT AND SKIP. Never silently fall back to cheap model for frontier tasks.
- Every LLM call must be logged to the `llm_costs` SQLite table before returning

### Async Patterns
- All I/O-bound functions must be `async def`
- Use `aiohttp.ClientSession` with proper context managers (do not create a new session per request)
- Rate limiting via `asyncio.Semaphore`: 10 req/s for CLOB API, 20/min cheap LLM, 5/min frontier LLM
- Never use the `schedule` library — all timing is handled by the async main loop + `asyncio.sleep`

### Error Handling
- All external API calls: 3 retries with exponential backoff
- Wrap main loop body in try/except — log full traceback, send notification, increment failure counter
- 3 consecutive loop failures → pause trading + critical alert
- On authentication failure: clear error pointing to `.env` setup

### Data Integrity
- All config values in `config/settings.py` must be overridable via environment variables
- SQLite tables auto-create on first import of `core/db.py` (9 tables: trades, positions, signals, bankroll, llm_costs, market_cache, signal_calibration, frontier_decisions, skipped_markets)
- Paper trades stored in same tables with `paper=True` column
- All timestamps ISO 8601 UTC

### Web UI Behavior
- Theme: navy (#0a1628, #0d1f3c), grey (#8899aa, #667788), white (#e0e8f0), blue accent (#4488cc)
- Health checks (wallet, RPC, OpenRouter) via GET /api/health
- Analysis tab shows current batch of markets being aggregated with live status per market
- All detail views are inline in the Analysis tab's right pane
- Auto-refresh polling: Dashboard 30s, Analysis 15s, Logs 5s

### Code Standards
- Type hints on all function signatures and return types
- Dataclasses for structured data (SignalResult, TradeDecision, etc.) — not raw dicts
- No stubs, no TODOs, no placeholders. Every function must be fully implemented.
- Use the exact LLM prompt templates from the build plan. Do not modify prompt wording.
- Logging via Python `logging` module, not print statements

## Key Commands
```bash
# Setup
pip install -r requirements.txt
cp .env.example .env  # then fill in secrets

# Run
python main.py --web             # Web dashboard (API on :8080)
cd frontend && npm run dev       # React dev server (HMR on :5173)
python main.py                   # Live trading (NOT YET IMPLEMENTED)

# Test
pytest tests/ -v
pytest tests/test_aggregator.py -v     # Signal aggregator tests
pytest tests/test_market_filter.py -v  # Market filter tests
pytest tests/test_llm.py -v            # LLM client tests
pytest tests/test_db.py -v             # Database tests

# Frontend
cd frontend && npm install       # Install React dependencies
cd frontend && npm run build     # Production build → frontend/dist/
cd frontend && npm run dev       # Dev server with HMR

# Docker
docker-compose up -d               # Production
docker-compose logs -f              # Tail logs
```

## Build Sequence
This project is built section by section from `POLYMARKET_BOT_PLAN (1).md`. Each section is self-contained. Build in order: 0 → 1 → 2 → 3 → 4A → 4B → 4C → 4D → 5 → 6 → 7 → 8 → 9 → 10 → 11. Do not skip ahead. Run tests after each section before proceeding.

**Current progress:** Sections 0-6 complete (core infra, LLM, wallet, DB, market filtering, web UI, full signal engine with aggregator, Kelly criterion, order execution). TUI replaced with React + FastAPI web dashboard. Section 7+ (monitoring, main loop) not yet implemented.

## File Naming
- All Python files use snake_case
- Config in `config/`, core infra in `core/`, signal providers in `signals/`, trading logic in `strategy/`, ops in `monitoring/`, one-off helpers in `scripts/`, web backend in `web/`, React frontend in `frontend/`
- Database file lives at `data/bot.db` (auto-create `data/` directory)
