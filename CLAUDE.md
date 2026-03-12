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
- Textual for TUI dashboard
- Docker for deployment
- pytest + pytest-asyncio for testing

## Architecture
```
main.py                  → Entry point, --tui flag for dashboard (main loop NOT YET IMPLEMENTED)
core/llm.py              → OpenRouter client, tiered routing (cheap vs frontier)
core/client.py           → Polymarket CLOB wrapper for ORDER EXECUTION ONLY (no market reading methods)
core/wallet.py           → Wallet balance checks, gas monitoring
core/db.py               → SQLite tables: trades, positions, signals, bankroll, llm_costs, market_cache
strategy/market_filter.py→ Gamma API discovery, filtering, LLM categorization (crypto-only gate), ranking
tui/app.py               → Textual TUI dashboard with 7 tabs (Home, Markets, In Progress, Costs, Signals, Bets, Logs), navy/grey/white theme
tui/widgets/             → StatusPanel, MarketsPanel, PipelinePanel (In Progress), CostsPanel, SignalsPanel, BetsPanel, LogPanel, CommandBar
scripts/setup_wallet.py  → Wallet setup helper
scripts/dashboard.py     → Standalone dashboard launcher
```

### Implemented Signal Engine (3 providers, crypto-focused)
```
signals/base.py              → SignalResult dataclass + SignalProvider ABC
signals/resolution_crypto.py → CoinGecko + barrier/terminal probability models (NO LLM)
signals/aggregator.py        → Weighted signal merge → FRONTIER model final probability call
signals/temporal.py          → Date context injection, urgency tiers, frontier system prompt builder
signals/web_search.py        → Perplexity Sonar search-grounded LLM signal (universal, all categories)
signals/prediction_markets.py→ Cross-platform consensus (Metaculus + Kalshi + PredictIt, no auth)
```

### Not Yet Implemented (build plan sections 6-11)
```
strategy/executor.py     → Order placement, fill monitoring, position management (+ PaperExecutor)
monitoring/pnl.py        → P&L tracking, bankroll snapshots, performance metrics
monitoring/health.py     → Health checks while bot is running (TUI-driven)
monitoring/notifications.py → TUI log panel + Python logging (no Telegram)
```

### Kelly Criterion (strategy/kelly.py) — Section 5 COMPLETE
- `TradeDecision` dataclass with full audit trail (15 fields incl. `effective_prob`)
- `calculate_kelly()` confidence-blends estimate toward market price, then computes fractional Kelly (0.25x) with fee-adjusted odds
- Confidence blending: `effective_prob = confidence * estimated_prob + (1 - confidence) * market_price`
- Fee adjustment: Polymarket's 2% profit fee reduces effective odds (POLYMARKET_FEE_RATE setting)
- Integrated into pipeline: every successful aggregation runs Kelly sizing
- Results shown in TUI "Bets" tab with table + detail view
- Safety checks: edge threshold, positive Kelly, min bet $1, max position 10%, bankroll reserve $20, existing exposure

## Market Discovery — Gamma API
- Use Gamma API (`https://gamma-api.polymarket.com/markets`) for all market discovery — NOT the CLOB API
- No auth required for Gamma read endpoints
- Fetch with `?active=true&closed=false&order=volume24hr&ascending=false&limit=200`
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
3. Time to resolution: `MIN_HOURS_TO_RESOLUTION` (24h) to `MAX_DAYS_TO_RESOLUTION` (90d)
4. Near-certain price: drop if any outcome price <= 0.02 or >= 0.98
5. Spread: drop if spread > `MAX_SPREAD` (0.10) — Gamma data only
6. Skip markets with existing positions
7. Sort survivors by `volume_24hr` descending

## Signal Aggregator (signals/aggregator.py)
- Collects signals from 3 providers (resolution_crypto, web_search, prediction_markets)
- Filters out signals with confidence=0 or probability=None
- If 0 usable signals → returns None (skip market)
- Computes weighted preliminary estimate using source multipliers:
  - `resolution_crypto`: 2.0x (direct CoinGecko data, barrier/terminal model — NO LLM)
  - `prediction_markets`: 1.8x (cross-platform market consensus)
  - `web_search`: 1.5x (Perplexity Sonar search-grounded)
  - Weight = `signal.confidence * source_multiplier`
- Makes single FRONTIER MODEL call with superforecaster prompt
- Frontier model sees both terminal and barrier probabilities + multi-timescale vol data
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

### TUI Commands (command bar via ':' key)
- `aggregate [question] [market_price]` — Full aggregation pipeline (signals + frontier model)
- `signal-test [question]` — Run individual signal providers without aggregation
- `categorize <question>` — Categorize a market question via cheap LLM
- `llm-test <prompt>` — Send a prompt to the cheap model
- `refresh` — Re-run health checks and market fetch

### TUI Keybindings
- `1-7` — Switch tabs (Home, Markets, In Progress, Costs, Signals, Bets, Logs)
- `s` — Start/Stop bot
- `a` — Run aggregate on default test question
- `r` — Refresh all
- `:` — Toggle command bar

### Pipeline Loop (when bot is running)
When the bot is started via `s` key, it runs a continuous loop:
1. **Filter**: Discover → filter → categorize → extract → rank markets
2. **Aggregate**: Take top 20 filtered markets, run full signal aggregation on each
3. **Dedup**: Skip markets already aggregated in previous cycles (tracked by conditionId)
4. **Repeat**: After top 20 are done, discard remaining and re-filter
5. If all top markets are already processed, clear history and re-filter
6. The "In Progress" tab shows the current batch with per-market status (waiting/processing/done/skipped/error)
7. The Home tab shows the current bot process phase (filtering/aggregating/waiting)

## Critical Design Rules

### LLM Routing — Never Violate These
- Cheap model (`google/gemini-2.0-flash-lite-001`): summarization, classification, extraction, search query generation, initial probability estimates
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
- SQLite tables auto-create on first import of `core/db.py`
- Paper trades stored in same tables with `paper=True` column
- All timestamps ISO 8601 UTC

### TUI Behavior
- Theme: navy (#0a1628, #0d1f3c), grey (#8899aa, #667788), white (#e0e8f0), blue accent (#4488cc)
- Bot Stop MUST cancel ALL worker groups (pipeline-loop, pipeline, health-loop, health-check, markets, costs) — no background tasks should survive a stop
- Bot Start restarts health-loop and pipeline-loop, clears aggregated IDs
- Health checks (wallet, RPC, OpenRouter) always run
- "In Progress" tab (formerly Filter) shows current batch of markets being aggregated with live status per market

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
python main.py --tui             # TUI dashboard (current primary interface)
python main.py                   # Live trading (NOT YET IMPLEMENTED)

# Test
pytest tests/ -v
pytest tests/test_aggregator.py -v     # Signal aggregator tests
pytest tests/test_market_filter.py -v  # Market filter tests
pytest tests/test_llm.py -v            # LLM client tests
pytest tests/test_db.py -v             # Database tests

# Docker
docker-compose up -d               # Production
docker-compose logs -f              # Tail logs
```

## Build Sequence
This project is built section by section from `POLYMARKET_BOT_PLAN (1).md`. Each section is self-contained. Build in order: 0 → 1 → 2 → 3 → 4A → 4B → 4C → 4D → 5 → 6 → 7 → 8 → 9 → 10 → 11. Do not skip ahead. Run tests after each section before proceeding.

**Current progress:** Sections 0-5 complete (core infra, LLM, wallet, DB, market filtering, TUI, full signal engine with aggregator, Kelly criterion). Section 6+ (executor, monitoring, main loop) not yet implemented.

## File Naming
- All Python files use snake_case
- Config in `config/`, core infra in `core/`, signal providers in `signals/`, trading logic in `strategy/`, ops in `monitoring/`, one-off helpers in `scripts/`, TUI in `tui/`
- Database file lives at `data/bot.db` (auto-create `data/` directory)
