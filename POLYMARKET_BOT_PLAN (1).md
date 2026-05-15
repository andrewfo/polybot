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

## Section 7: Monitoring & Notifications — COMPLETE

`monitoring/notifications.py` — Notifier class with `send()`, `send_trade()`, `send_position_closed()`, `send_health_alert()`. All notifications via Python logging → web dashboard Logs tab. No Telegram, no external services.
`monitoring/pnl.py` — Bankroll snapshots (hourly), daily/weekly/total P&L, win rate, profit factor, max drawdown, cost breakdown by model tier.

---

## Section 8: Health Checks — COMPLETE

`monitoring/health.py` — `run_health_checks()` called by position monitor worker. Checks: Gamma API, wallet gas, wallet funds, stale orders, OpenRouter, CoinGecko, cost runaway ($20/day hard cap). Returns `{check_name, status, message}`. Critical failures auto-stop bot via `AutoStopError`. Results displayed on Dashboard tab.
`monitoring/learning.py` — Continuous learning engine with auto-apply recommendations, impact tracking, time-decay weighting, regime awareness. Learning tab in frontend.

---

## Section 9: Pipeline Integration (Main Loop) — COMPLETE

`main.py` entry point with `--web` flag launches FastAPI server. `BotEngine` in `web/server.py` manages 3 independent workers (discovery, aggregation, position monitor) on configurable intervals. Headless main loop for non-web mode. 3 consecutive failures auto-stop bot. Frontend: 6 tabs (Dashboard, Markets, Analysis, Learning, Database, Logs). Database explorer with paginated table viewer, categorized sidebar. Cross-file consistency audit (12 fixes applied).

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
