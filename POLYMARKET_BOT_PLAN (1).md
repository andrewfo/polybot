# Polymarket Signal-Based Trading Bot — Build Plan

> **Purpose**: Sectioned build plan for Claude Code. Use `/build-section N` to implement a section, `/verify-section N` to check acceptance criteria.
>
> **Goal**: Autonomous Polymarket trading bot for crypto binary markets. Signal-based trading with Kelly criterion sizing. Web dashboard (FastAPI + React) for monitoring. Paper trading by default.
>
> **Tech Stack**: Python 3.11+, py-clob-client, asyncio, SQLite, OpenRouter (tiered model routing), FastAPI + React (Vite).
>
> **LLM Cost Philosophy**: Cheap/free models for routine tasks. Frontier model (Claude Opus 4.6) for final probability estimation and trade decisions only. Target < $0.05 per trading cycle.

---

## Section 0: Project Scaffolding & Environment — COMPLETE

Core project structure, dependencies, config/settings.py (env-overridable), .env.example, LLM model routing config. See `config/settings.py` and `requirements.txt` for current state.

---

## Section 1: LLM Client with Tiered Model Routing — COMPLETE

`core/llm.py` — OpenRouter client with `cheap()`, `frontier()`, `call()`, `call_json()` methods. Task-based routing, cost tracking to `llm_costs` table, retry with backoff, rate limiting via semaphores. Cheap fallback model on primary cheap failure. Frontier failures raise (never fall back to cheap).

---

## Section 2: Wallet & Polymarket Client — COMPLETE

`core/wallet.py` — Balance checks (USDC, MATIC), gas monitoring, caching.
`core/client.py` — ClobClientWrapper for ORDER EXECUTION ONLY (place/cancel orders, get positions). No market reading methods.
`core/db.py` — SQLite with auto-creating tables (trades, positions, signals, bankroll, llm_costs, market_cache, signal_calibration, frontier_decisions, skipped_markets, parameter_overrides, parameter_change_snapshots, market_regimes, learning_reports).

---

## Section 3: Market Discovery & Filtering — COMPLETE

`strategy/market_filter.py` — Gamma API discovery (NOT CLOB), filtering pipeline (binary, liquidity band, time-to-resolution, price extremes, spread), LLM categorization (crypto-only gate), resolution param extraction, Kelly-adjusted edge ranking with Gaussian time scoring.

---

## Section 4A-4D: Signal Engine — COMPLETE

**4A**: `signals/base.py` — SignalResult dataclass + SignalProvider ABC.
**4B**: Replaced with `signals/prediction_markets.py` — Cross-platform consensus (Metaculus + Kalshi + Polymarket Gamma, keyword extraction + Jaccard matching, no LLM).
**4C**: `signals/resolution_crypto.py` — CoinGecko barrier/terminal probability models, multi-timescale volatility, Deribit IV, drift shrinkage. NO LLM — pure math.
**4D**: `signals/aggregator.py` — Dynamic-weighted merge (calibration-adjusted multipliers), log-odds averaging, pre-frontier divergence filter, single frontier model call with superforecaster prompt. Also `signals/web_search.py` (Perplexity Sonar), `signals/temporal.py` (date context), `signals/calibration.py` (Brier-score tracking, dynamic multipliers).

---

## Section 5: Kelly Criterion & Bet Sizing — COMPLETE

`strategy/kelly.py` — TradeDecision dataclass, sublinear confidence blending, fractional Kelly (0.25x), fee-adjusted odds, dynamic bankroll reserve. Integrated with depth analysis. `strategy/depth.py` — CLOB order book depth analysis, slippage estimation, bet size adjustment.

---

## Section 6: Order Execution & Position Management — COMPLETE

`strategy/executor.py` — PaperExecutor + TradeExecutor, risk guardrails (position count, trade rate, drawdown, daily loss), limit order placement, order monitoring, take-profit/stop-loss position management, AutoStopError for critical failures.

---

## Section 7: Monitoring & Notifications

### Context
The bot uses a web dashboard (FastAPI + React) for all monitoring. Notifications go to Python `logging` (writes to `data/bot.log`) and are visible in the web dashboard's Logs tab. No Telegram, no external notification services.

The web dashboard already exists (`web/server.py` + `frontend/`). This section adds the notification formatting layer and P&L tracking.

### Tasks
1. **`monitoring/notifications.py`** — Web-integrated notification system:
   ```python
   class Notifier:
       """Formats and logs notifications. Web dashboard reads from log buffer."""

       async def send(self, message: str, level: str = "info"):
           """level: 'info', 'warning', 'alert', 'critical'
           Logs via Python logging. Web dashboard polls log buffer via GET /api/logs."""

       async def send_trade(self, trade_decision: TradeDecision):
           """Format and log trade execution: market, side, size, price, edge, reasoning."""

       async def send_position_closed(self, position: dict, pnl: float):
           """Format and log position closure with P&L dollars and percent."""

       async def send_health_alert(self, issue: str):
           """Format and log health check failure with severity."""
   ```

   - All notifications use Python `logging` module
   - `web/server.py` already has a log buffer that the Logs tab polls — notifications feed into this automatically
   - Works standalone for testing (just logs, no web dependency)
   - No Telegram. No `python-telegram-bot`. No `scripts/setup_telegram.py`.

2. **`monitoring/pnl.py`** — P&L tracking:
   - `snapshot_bankroll()` — called each pipeline cycle:
     - Calculate: available cash + sum of position values at current market prices
     - Store in `bankroll` table (skip if last snapshot < 1 hour ago)
   - `get_daily_pnl()` → realized + unrealized P&L since midnight UTC
   - `get_weekly_pnl()` → last 7 days
   - `get_total_pnl()` → all-time
   - `get_metrics()` → dict with: win rate, avg win/loss size, profit factor, max drawdown, total LLM costs, net P&L after costs, ROI
   - `get_cost_breakdown()` → LLM costs by model tier, per day/month
   - All P&L data accessible via existing web API endpoints

### Acceptance Criteria
- Notifications appear in web dashboard Logs tab via log buffer
- All notifications written to `data/bot.log` via Python logging
- Works without web dashboard running (for testing)
- All notification types properly formatted (trade, position close, health alert)
- P&L snapshots are accurate and stored on each pipeline cycle
- Cost breakdown correctly separates cheap vs frontier vs sonar spending
- No external notification dependencies

---

## Section 8: Health Checks

### Context
Health checks run while the bot is active. Results display on the web dashboard's Dashboard tab (health panel already exists). Critical failures auto-stop the bot via `AutoStopError`.

### Tasks
1. **`monitoring/health.py`** — Health check system:
   - `run_health_checks()` — called by position monitor worker (every 30 min):
     - **Gamma API**: Can we reach `gamma-api.polymarket.com`? (simple market fetch)
     - **Wallet gas**: MATIC balance > 0.05? (warn at 0.1, critical at 0.05)
     - **Wallet funds**: USDC balance reasonable? (detect large unexpected changes)
     - **Stale orders**: Any orders PENDING > 30 minutes?
     - **OpenRouter**: Can we reach the API? (`/models` endpoint)
     - **CoinGecko**: Can we reach the API? (`/api/v3/ping`)
     - **Cost runaway**: Daily LLM cost < $20? (hard cap)
   - Each check returns: `{check_name, status: "ok"|"warning"|"critical", message}`
   - Results served via existing `GET /api/health` endpoint
   - On "warning" → log via Notifier
   - On "critical" → log via Notifier + raise `AutoStopError`

### Acceptance Criteria
- All health checks run without errors
- Warning and critical thresholds trigger appropriate responses
- Critical failures auto-stop the bot via AutoStopError
- Health results display on web dashboard Dashboard tab
- LLM cost hard cap prevents runaway spending

---

## Section 9: Pipeline Integration (Main Loop)

### Context
`main.py` is the entry point. With `--web` flag it launches the FastAPI server. The `BotEngine` class in `web/server.py` manages bot lifecycle. This section wires the full trading pipeline into BotEngine's 3 worker loops.

The worker architecture already exists as stubs in `web/server.py`. This section implements the actual pipeline logic in each worker.

### Tasks
1. **Wire BotEngine workers** in `web/server.py`:

   **Worker 1: Discovery Loop** (every `DISCOVERY_INTERVAL_MINUTES`, default 2h):
   ```
   1. Run check_and_record_resolutions() — update calibration data
   2. Run update_skipped_resolutions() — track skipped market outcomes
   3. Refresh calibration multipliers via get_multiplier_dict()
   4. discover_markets() from Gamma API
   5. filter_markets() → categorize → extract_resolution_params → rank
   6. Cache ranked results for aggregation worker
   7. Log: markets found, filtered count, top candidates
   ```

   **Worker 2: Aggregation Loop** (every `AGGREGATION_INTERVAL_MINUTES`, default 2h):
   ```
   1. Read from discovery cache, apply conditionId dedup
   2. For each candidate (up to BATCH_SIZE=40):
      a. Skip if existing position in this market
      b. Collect signals from all 3 providers
      c. Aggregate signals → frontier model final probability
      d. Calculate Kelly sizing
      e. Run depth analysis
      f. Check all risk guardrails
      g. If should_trade → execute via PaperExecutor or TradeExecutor
      h. Record frontier decision + calibration prediction
   3. Run learning cycle (monitoring/learning.py)
   4. Log: markets analyzed, trades placed, LLM cost this batch
   ```

   **Worker 3: Position Monitor Loop** (every `POSITION_CHECK_INTERVAL_MINUTES`, default 30min):
   ```
   1. Run executor.monitor_orders() — check fills, expire stale orders
   2. Run executor.manage_positions() — P&L update, take-profit, stop-loss
   3. Run health checks (monitoring/health.py)
   4. Snapshot bankroll if > 1 hour since last
   5. Log: positions updated, orders filled/expired
   ```

   All workers use cancellation-safe sleep (5s chunks checking `_bot_running`).

2. **Startup sequence** (BotEngine.start()):
   ```
   1. Validate env vars (OPENROUTER_API_KEY, PRIVATE_KEY at minimum)
   2. Initialize SQLite database (auto-create tables)
   3. Verify OpenRouter connectivity
   4. Check wallet balances, warn if low
   5. Load existing state (open positions, pending orders)
   6. Start all 3 worker loops
   7. Log startup summary
   ```

3. **Shutdown** (BotEngine.stop()):
   - Cancel all workers gracefully
   - Do NOT cancel open orders on Polymarket
   - Save state snapshot to SQLite
   - Log stop summary

4. **Error handling**:
   - Each worker wraps its cycle in try/except
   - Log full traceback, increment failure counter
   - 3 consecutive failures in any worker → auto-stop bot
   - AutoStopError from guardrails → stop bot, log reason

5. **Web API integration**:
   - `POST /api/commands/start` → BotEngine.start()
   - `POST /api/commands/stop` → BotEngine.stop()
   - Dashboard tab shows current worker phase (filtering/aggregating/monitoring/waiting)
   - Analysis tab shows current batch with per-market status (waiting/processing/done/skipped/error)

### Acceptance Criteria
- Start/Stop via web dashboard controls the full pipeline
- All 3 workers run independently on their own intervals
- Full pipeline: discover → filter → signals → Kelly → depth → execute → monitor
- State persists in SQLite across start/stop cycles
- 3 consecutive failures auto-stop the bot
- Paper trading is the default (PAPER_TRADING=true)
- Web dashboard reflects live bot state (phase, batch progress)
- Error handling never crashes the server

---

## Section 10: Paper Trading Validation

### Context
Paper trading is already implemented in `strategy/executor.py` (PaperExecutor). This section adds validation tooling and the live-trading readiness gate.

### Tasks
1. **Paper trading validation endpoint** — `GET /api/paper/summary`:
   - Total paper trades placed
   - Win rate on closed paper positions
   - Average edge realized vs predicted
   - Total simulated P&L
   - LLM cost during paper period
   - Days of paper trading
   - Recommendation: ready for live? (requires 7+ days, 30+ trades, win rate > 52%)

2. **Live readiness gate** — when user sets `PAPER_TRADING=false`:
   - On BotEngine.start(), check paper trading stats
   - If insufficient paper history (< 7 days or < 30 trades), log warning but allow start
   - Log prominent "LIVE TRADING MODE" banner

3. **Cleanup requirements.txt** — remove unused dependencies:
   - Remove `python-telegram-bot` (no Telegram)
   - Remove `schedule` (banned, using asyncio)
   - Remove `feedparser` (no RSS news signal)
   - Remove `beautifulsoup4` and `lxml` (no scraping)
   - Verify all remaining deps are actually imported

### Acceptance Criteria
- Paper trading summary endpoint returns accurate statistics
- Live readiness check runs on start with PAPER_TRADING=false
- requirements.txt contains only used dependencies
- Paper trades clearly distinguished from live in database

---

## Section 11: Documentation & Polish

### Tasks
1. **Update README.md** — accurate setup, architecture, and usage instructions reflecting web dashboard (not TUI)
2. **Verify all settings in config/settings.py** have correct defaults and are documented
3. **Remove dead code** — any references to TUI, Telegram, Docker, news.py, polling.py, schedule
4. **Run full test suite** — all tests pass
5. **Cost audit** — verify LLM routing compliance with `/audit-llm`

### Acceptance Criteria
- README matches actual project state
- No dead code or unused imports
- All tests pass
- LLM routing is compliant (no frontier tasks on cheap, no silent fallbacks)

---

## Appendix A: API & Resource Reference

| Resource | URL | Auth |
|----------|-----|------|
| Gamma API (market discovery) | `gamma-api.polymarket.com/markets` | No |
| Polymarket CLOB API | `clob.polymarket.com` | Yes |
| py-clob-client SDK | github.com/Polymarket/py-clob-client | -- |
| OpenRouter API | `openrouter.ai/api/v1` | Yes |
| CoinGecko API | `api.coingecko.com/api/v3/` | No |
| Deribit API (options IV) | `www.deribit.com/api/v2/` | No |
| Perplexity Sonar | Via OpenRouter | Yes |
| Metaculus API | `www.metaculus.com/api/` | Yes |

## Appendix B: LLM Task Routing

| Task | Model Tier | Cost/Call | When |
|------|-----------|-----------|------|
| Market classification | Cheap | ~$0 | Once per new market |
| Resolution param extraction | Cheap | ~$0 | Once per crypto market |
| CoinGecko coin ID mapping | Cheap | ~$0 | Fallback only (whitelist handles most) |
| Web search signal | Sonar | ~$0.01 | Per market per aggregation |
| **Final probability estimation** | **Frontier** | **~$0.015** | **Per candidate market** |
| All other signal providers | None | $0 | Math-only (no LLM) |

## Appendix C: Risk Warnings

- **Start small**: $100-200 bankroll maximum to start. Scale only after 100+ trades with proven profitability.
- **Fractional Kelly is mandatory**: Full Kelly guarantees ruin with imperfect estimates. Quarter Kelly (0.25x) is the recommended max.
- **Paper trade first**: Run with `PAPER_TRADING=true` (the default) for at least 7 days. No exceptions.
- **Monitor LLM costs**: If frontier spending outpaces profits, reduce trade frequency.
- **Regulatory**: Understand Polymarket's terms of service and your local laws regarding prediction markets.
