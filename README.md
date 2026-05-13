# Polymarket Signal-Based Trading Bot

Autonomous trading bot for Polymarket crypto binary markets. Signal-based trading with Kelly criterion sizing on mid-to-low liquidity markets. Web dashboard for monitoring. Paper trading by default.

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for frontend dev server)
- **Polymarket account** with USDC funded on Polygon
- **OpenRouter account** with API credits ($5-10 to start)
- **Polygon wallet** private key linked to your Polymarket account

## Quick Start

```bash
# 1. Install
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. Configure
cp .env.example .env
# Edit .env — fill in: POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
# POLYMARKET_API_PASSPHRASE, PRIVATE_KEY, OPENROUTER_API_KEY

# 3. Verify wallet
python scripts/setup_wallet.py

# 4. Run (paper trading mode — default)
python main.py --web              # API server on :8080
cd frontend && npm run dev        # Dashboard on :5173 (separate terminal)
```

Open `http://localhost:5173` to view the dashboard. Press Start to begin paper trading.

## Architecture

```
config/settings.py            — All config (env-overridable), get_effective_param() for learning overrides
core/
  llm.py                      — OpenRouter client: cheap(), frontier(), sonar tiers
  client.py                   — Polymarket CLOB wrapper (ORDER EXECUTION ONLY)
  wallet.py                   — Wallet balance, gas monitoring
  db.py                       — SQLite (14 auto-created tables)
signals/
  base.py                     — SignalResult dataclass + SignalProvider ABC
  resolution_crypto.py        — CoinGecko barrier/terminal probability (math only, NO LLM)
  web_search.py               — Perplexity Sonar search-grounded signal
  prediction_markets.py       — Cross-platform consensus (Metaculus + Kalshi + Gamma)
  aggregator.py               — Weighted merge → frontier model final probability
  calibration.py              — Brier-score tracking, dynamic source multipliers
  temporal.py                 — Date context, urgency tiers for frontier prompt
strategy/
  market_filter.py            — Gamma API discovery, filtering, crypto-only gate, ranking
  kelly.py                    — Kelly criterion sizing, TradeDecision dataclass
  depth.py                    — Order book depth/slippage analysis
  executor.py                 — PaperExecutor + TradeExecutor, risk guardrails
monitoring/
  learning.py                 — Continuous learning: bias analysis, auto-apply recommendations
web/
  server.py                   — FastAPI backend, BotEngine, REST endpoints
frontend/                     — React (Vite) dashboard
```

### Trading Pipeline

The bot runs 3 independent async worker loops:

1. **Discovery** (every 2h) — Fetch markets from Gamma API, filter, categorize, rank candidates
2. **Aggregation** (every 2h) — For each candidate: collect signals, aggregate (frontier call), Kelly sizing, execute
3. **Position Monitor** (every 30min) — Check order fills, manage positions (take-profit/stop-loss), health checks

### Signal Providers

| Provider | Source | LLM Tier | Weight |
|----------|--------|----------|--------|
| resolution_crypto | CoinGecko barrier/terminal model | None (math) | 2.0x |
| prediction_markets | Metaculus + Kalshi + Gamma | None | 1.8x |
| web_search | Perplexity Sonar | Sonar | 1.5x |

Signals are merged with dynamic calibration-adjusted weights, then a single **frontier model** (Claude Opus 4.6) call produces the final probability estimate.

## LLM Cost Breakdown

| Task | Model | Cost |
|------|-------|------|
| Market classification, extraction | Gemini 2.0 Flash Lite (cheap) | ~$0 |
| Web search signal | Perplexity Sonar | ~$0.01/call |
| Final probability estimation | Claude Opus 4.6 (frontier) | ~$0.015/call |
| Signal math (crypto, prediction markets) | None | $0 |

Target: < $0.05 per trading cycle. Typical daily cost: $1-5 depending on market volume.

## Configuration

All parameters in `config/settings.py` are overridable via environment variables:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PAPER_TRADING` | `true` | Paper mode (must set `false` for live) |
| `KELLY_FRACTION` | `0.25` | Quarter Kelly (conservative sizing) |
| `MIN_EDGE_THRESHOLD` | `0.02` | Minimum 2% edge to trade |
| `MAX_POSITION_PCT` | `0.10` | Max 10% of bankroll per market |
| `MAX_SIMULTANEOUS_POSITIONS` | `5` | Position limit |
| `MAX_DRAWDOWN_PCT` | `0.30` | Auto-stop if down 30% |
| `MAX_DAILY_LOSS_PCT` | `0.15` | Auto-stop if down 15% in a day |
| `TAKE_PROFIT_PCT` | `0.12` | Close position at +12% |
| `STOP_LOSS_PCT` | `0.10` | Close position at -10% |
| `DISCOVERY_INTERVAL_MINUTES` | `120` | Market discovery frequency |
| `AGGREGATION_INTERVAL_MINUTES` | `120` | Signal aggregation frequency |

The learning engine can auto-adjust `KELLY_FRACTION`, `MIN_EDGE_THRESHOLD`, `TAKE_PROFIT_PCT`, `STOP_LOSS_PCT` based on performance data. Overrides are stored in the database and read via `get_effective_param()`.

## Development

### Commands

```bash
python main.py --web                    # Start API server (:8080)
cd frontend && npm run dev              # Start frontend dev server (:5173, proxies /api → :8080)
cd frontend && npm run build            # Production build → frontend/dist/

pytest tests/ -v                        # Run all tests
pytest tests/test_<module>.py -v        # Run specific module tests
```

### Claude Code Skills

These slash commands are available when developing with Claude Code:

| Skill | Description |
|-------|-------------|
| `/build-section N` | Implement build plan section N |
| `/verify-section N` | Check acceptance criteria for section N |
| `/status` | Project status report (build progress, tests, issues) |
| `/test-module <name>` | Run and debug tests for a specific module |
| `/audit-llm` | Audit all LLM calls for routing compliance |
| `/check-health` | Health check all external dependencies |
| `/run-pipeline` | Dry-run the full trading pipeline |
| `/add-signal <name>` | Scaffold a new signal provider |

### Project Structure

- **Build plan**: `POLYMARKET_BOT_PLAN (1).md` — sectioned implementation spec
- **Context file**: `CLAUDE.md` — rules and constraints for Claude Code
- **Hooks**: `.claude/hooks/` — auto-run tests on edit, block secret exposure
- **Database**: `data/bot.db` (SQLite, auto-created)
- **Logs**: `data/bot.log`

### Build Progress

Sections 0-6 complete (core infra, LLM routing, wallet, DB, market filtering, signal engine, Kelly criterion, order execution, web dashboard, learning engine). Next: Section 7 (monitoring/notifications).

See `POLYMARKET_BOT_PLAN (1).md` for full section details.

## Web Dashboard

Four tabs:

1. **Dashboard** — Bot status, health checks, wallet balance, LLM costs, open positions
2. **Markets** — Gamma API market browser with sort/filter
3. **Analysis** — Signal analysis results with Chart.js visualizations (probability bars, vol comparison, Kelly breakdown)
4. **Logs** — Real-time log viewer with level filtering

Theme: navy/grey/white (#0a1628, #0d1f3c, #8899aa, #e0e8f0, accent #4488cc)

## Risk Warnings

- **Paper trade first.** `PAPER_TRADING=true` is the default. Run for 7+ days and 30+ trades before going live.
- **Start small.** $100-200 bankroll maximum to start.
- **Fractional Kelly is mandatory.** Full Kelly guarantees ruin with imperfect estimates.
- **Monitor LLM costs.** If frontier spending outpaces profits, reduce trade frequency.
- **Keep your private key secure.** Never share it, never commit it. The `.env` file is gitignored.
- **Regulatory.** Understand Polymarket's ToS and your local laws regarding prediction markets.
