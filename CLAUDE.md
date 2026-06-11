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
- pytest + pytest-asyncio for testing

## Architecture Map
```
main.py                       → Entry point, --web flag for dashboard
config/settings.py            → All config (env-overridable), get_effective_param() for learning overrides
core/llm.py                   → OpenRouter client, tiered routing (cheap/frontier/sonar)
core/client.py                → Polymarket CLOB wrapper — ORDER EXECUTION ONLY
core/wallet.py                → Wallet balance, gas monitoring
core/db.py                    → SQLite (14 tables, auto-create on import)
strategy/market_filter.py     → Gamma API discovery + filtering + ranking
strategy/kelly.py             → Kelly criterion sizing, TradeDecision dataclass
strategy/depth.py             → CLOB order book depth/slippage analysis
strategy/executor.py          → Paper + live execution, positions, risk guardrails
signals/base.py               → SignalResult dataclass + SignalProvider ABC
signals/resolution_crypto.py  → CoinGecko barrier/terminal probability (NO LLM)
signals/aggregator.py         → Weighted signal merge → frontier model final call
signals/temporal.py           → Date context, urgency tiers for frontier prompt
signals/web_search.py         → Perplexity Sonar search-grounded signal
signals/prediction_markets.py → Cross-platform consensus (Manifold/Kalshi/Gamma)
signals/onchain_flow.py       → Multi-source capital flow signal: DeFi Llama stablecoins+TVL, Fear&Greed, CoinGecko global (NO LLM)
signals/calibration.py        → Brier-score calibration, dynamic source multipliers
monitoring/learning.py        → Continuous learning engine, auto-apply recommendations
web/server.py                 → FastAPI backend, BotEngine, REST endpoints
frontend/                     → React (Vite) dashboard: Dashboard, Markets, Analysis, Learning, Database, Logs
```

## Build Sequence & Progress
Built section by section from `POLYMARKET_BOT_PLAN (1).md`. Build in order, run tests after each section.

**Sections 0-9: COMPLETE** — Core infra, LLM, wallet, DB, market filtering, signal engine (4 providers + aggregator), Kelly criterion, order execution, web dashboard (7 tabs incl. Trades), continuous learning, monitoring/notifications, health checks, pipeline integration (3 workers). **485 tests passing**, zero TODOs/FIXMEs. 2026-06-11 ops fixes: cheap-model slugs refreshed after OpenRouter delisting (gemini-2.5-flash-lite / glm-4.5-air), None-guard on `resolution_keywords` in signal providers, global CoinGecko throttle (`core.coingecko_throttle`, 6s spacing) + 429-aware backoff + shared global-source cache in onchain_flow. Recent (Phases 1-4 of `docs/PROFITABILITY_FIX_PLAN.md`): pre-frontier edge gate (`PRE_FRONTIER_EDGE_THRESHOLD`); `web_search` benched + `onchain_flow` weight 0 with calibration earn-back; `onchain_flow` rebuilt market-aware; `RESOLUTION_SIGNAL_WEIGHT` 1.3→2.5; learning state reset — `LEARNING_DATA_CUTOFF` (2026-05-22T20:30Z) excludes pre-fix optimistic-pricing rows from all learning + calibration queries, poisoned KELLY_FRACTION override deactivated, rows tagged `data_regime` via `scripts/reset_learning_state.py`; Phase 4 measurement gate — `GET /api/paper/summary` reports post-cutoff net-of-LLM-cost PnL, profit concentration, per-signal Brier, frontier-vs-market Brier, and go/no-go criteria (`PAPER_RUN_*` settings). **7-day honest-pricing paper run pending** before live.

**Next:** Section 10 remainder (live readiness gate on BotEngine.start, requirements.txt cleanup — paper summary endpoint done) → 11 (docs & dead code). Fix-plan Phase 5 (hold-to-resolution, wider TP) can ship during the Phase 4 measurement run.

Available skills: `/build-section N`, `/verify-section N`, `/status`, `/test-module <name>`, `/audit-llm`, `/check-pipeline`, `/check-consistency`, `/add-signal <name>`, `/improve-signal <name>`, `/tune-prompts`, `/sync-frontend`, `/update-context`.

## Critical Rules — Do Not Violate

### LLM Routing
- **Cheap** (`google/gemini-2.5-flash-lite`): summarization, extraction, classification fallback
- **Cheap fallback** (`z-ai/glm-4.5-air`): auto-fallback if primary cheap fails
- **Sonar** (`perplexity/sonar`): search-grounded web signal. Falls back to cheap on failure.
- **Frontier** (`anthropic/claude-opus-4-6`): final probability estimation and trade decisions ONLY
- If frontier fails: **ALERT AND SKIP**. Never silently fall back to cheap for frontier tasks.
- Every LLM call must be logged to `llm_costs` table before returning

### Gamma API vs CLOB — Common Mistake
- **Gamma API** (`gamma-api.polymarket.com/markets`): ALL market discovery. No auth required.
- **CLOB client** (`py-clob-client`): ORDER EXECUTION ONLY. No market reading methods.
- `discover_markets()` and `filter_markets()` must NEVER take or instantiate ClobClientWrapper
- ClobClientWrapper init causes auth HTTP calls — never instantiate in filter pipeline
- Spread data from Gamma only — CLOB spread calls cause 400 errors
- If Gamma `spread` is null, compute from `bestAsk - bestBid`

### Async Patterns
- All I/O functions must be `async def`
- Use `aiohttp.ClientSession` with context managers (not new session per request)
- Rate limiting via `asyncio.Semaphore`: 10/s CLOB, 20/min cheap LLM, 5/min frontier
- Never use `schedule` library — async main loop + `asyncio.sleep` only

### Error Handling
- All external API calls: 3 retries with exponential backoff
- Main loop body in try/except — log traceback, increment failure counter
- 3 consecutive failures → pause trading + critical alert

### Code Standards
- Type hints on all signatures and returns
- Dataclasses for structured data — not raw dicts
- No stubs, no TODOs, no placeholders — every function fully implemented
- Use exact LLM prompt templates from the build plan
- Python `logging` module, not print statements
- All timestamps ISO 8601 UTC

### Web UI
- Theme: navy (#0a1628, #0d1f3c), grey (#8899aa, #667788), white (#e0e8f0), accent (#4488cc)
- Backend: FastAPI :8080, Frontend: Vite :5173 (dev), proxies /api → :8080
- Polling: Dashboard 30s, Analysis 15s, Logs 5s

## Key Commands
```bash
# Run
python main.py --web             # Web dashboard (API on :8080)
cd frontend && npm run dev       # React dev server (HMR on :5173)

# Test
pytest tests/ -v
pytest tests/test_<module>.py -v # Individual test files

# Frontend
cd frontend && npm install && npm run dev    # Dev
cd frontend && npm run build                 # Production → dist/
```

## File Naming
- Python: snake_case. Config in `config/`, core in `core/`, signals in `signals/`, strategy in `strategy/`, monitoring in `monitoring/`, scripts in `scripts/`, web in `web/`, frontend in `frontend/`
- DB at `data/bot.db` (auto-creates `data/` dir)
