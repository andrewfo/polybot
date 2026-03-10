# Polymarket Signal-Based Trading Bot

## Project Overview
Autonomous Polymarket trading bot. Signal-based trading with Kelly criterion sizing on mid-to-low liquidity binary markets. Runs 24/7, uses tiered LLM routing (cheap models for grunt work, frontier model for trade decisions).

## Tech Stack
- Python 3.11+, asyncio throughout (no sync blocking in async paths)
- py-clob-client for Polymarket CLOB API
- SQLite via sqlite-utils for state persistence (DB at `data/bot.db`)
- OpenRouter for all LLM calls (unified API, tiered model routing)
- aiohttp for async HTTP, web3/eth-account for wallet ops
- Docker for deployment
- pytest for testing

## Architecture
```
main.py                  → Entry point, main async loop
core/llm.py              → OpenRouter client, tiered routing (cheap vs frontier)
core/client.py           → Polymarket CLOB wrapper with retry + rate limiting
core/wallet.py           → Wallet balance checks, gas monitoring
core/db.py               → SQLite tables: trades, positions, signals, bankroll, llm_costs, market_cache
signals/base.py          → SignalResult dataclass + SignalProvider ABC
signals/news.py          → Google News RSS + Reddit scraping → cheap LLM summarization
signals/polling.py       → Structured data (polls, FRED, CoinGecko) → cheap LLM interpretation
signals/aggregator.py    → Weighted signal merge → FRONTIER model final probability call
strategy/kelly.py        → Kelly criterion sizing with safety caps
strategy/market_filter.py→ Discovery, filtering, LLM categorization, ranking
strategy/executor.py     → Order placement, fill monitoring, position management
monitoring/pnl.py        → P&L tracking, bankroll snapshots, performance metrics
monitoring/health.py     → Automated health checks every 5 min
monitoring/notifications.py → Telegram (optional) or stdout notifications + command handler
```

## Critical Design Rules

### LLM Routing — Never Violate These
- Cheap model (`z-ai/glm-4.5-air`): summarization, classification, extraction, search query generation, initial probability estimates
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
python scripts/dry_run.py          # Paper trading (START HERE)
python main.py                     # Live trading

# Test
pytest tests/ -v
pytest tests/test_kelly.py -v      # Kelly sizing tests

# Docker
docker-compose up -d               # Production
docker-compose logs -f              # Tail logs
```

## Build Sequence
This project is built section by section from `POLYMARKET_BOT_PLAN.md`. Each section is self-contained. Build in order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Do not skip ahead. Run tests after each section before proceeding.

## File Naming
- All Python files use snake_case
- Config in `config/`, core infra in `core/`, signal providers in `signals/`, trading logic in `strategy/`, ops in `monitoring/`, one-off helpers in `scripts/`
- Database file lives at `data/bot.db` (auto-create `data/` directory)
