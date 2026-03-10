# Polymarket Signal-Based Trading Bot

Autonomous trading bot for Polymarket prediction markets. Uses signal-based trading with Kelly criterion sizing on mid-to-low liquidity binary markets. Runs 24/7 with tiered LLM routing (cheap models for grunt work, frontier model for trade decisions).

## Prerequisites

- **Python 3.11+**
- **Docker** (optional, for production deployment)
- **Polymarket account** with USDC funded on Polygon
- **OpenRouter account** with API credits ($5-10 to start)
- **Polygon wallet** private key linked to your Polymarket account

## Quick Start

### 1. Clone and install

```bash
git clone <your-repo-url>
cd polymarket-bot
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in your API keys and wallet private key
```

Required secrets:
- `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE` — from Polymarket API dashboard
- `PRIVATE_KEY` — your Polygon wallet private key
- `OPENROUTER_API_KEY` — from [OpenRouter](https://openrouter.ai/)

### 3. Run setup scripts

```bash
python scripts/setup_wallet.py    # Verify wallet and balances
python scripts/setup_telegram.py  # (Optional) Set up Telegram notifications
```

### 4. Paper trade first

```bash
python scripts/dry_run.py
```

This runs the full bot logic without placing real orders. Monitor the output to verify signals, probability estimates, and sizing look reasonable.

### 5. Go live

```bash
python main.py
```

Or with Docker:

```bash
docker-compose up -d
docker-compose logs -f
```

## Architecture

```
polymarket-bot/
├── config/
│   └── settings.py          # All configurable params (env-overridable)
├── core/
│   ├── client.py            # Polymarket CLOB wrapper with retry + rate limiting
│   ├── wallet.py            # Wallet balance checks, gas monitoring
│   ├── db.py                # SQLite state persistence (data/bot.db)
│   └── llm.py               # OpenRouter client, tiered routing (cheap vs frontier)
├── signals/
│   ├── base.py              # SignalResult dataclass + SignalProvider ABC
│   ├── news.py              # Google News RSS + Reddit → cheap LLM summarization
│   ├── polling.py           # Structured data (polls, FRED, CoinGecko) → cheap LLM
│   └── aggregator.py        # Weighted signal merge → frontier model final probability
├── strategy/
│   ├── kelly.py             # Kelly criterion sizing with safety caps
│   ├── market_filter.py     # Discovery, filtering, LLM categorization, ranking
│   └── executor.py          # Order placement, fill monitoring, position management
├── monitoring/
│   ├── pnl.py               # P&L tracking, bankroll snapshots, performance metrics
│   ├── health.py            # Automated health checks every 5 min
│   └── notifications.py     # Telegram or stdout notifications
├── scripts/
│   ├── setup_wallet.py      # One-time wallet setup helper
│   ├── setup_telegram.py    # One-time Telegram bot setup helper
│   ├── backtest.py          # Historical backtesting
│   └── dry_run.py           # Paper trading mode
├── tests/                   # pytest test suite
├── main.py                  # Entry point / orchestrator loop
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## LLM Cost Breakdown

The bot uses a tiered LLM approach via OpenRouter:

| Task | Model | Cost |
|------|-------|------|
| Article summarization | Gemini 2.0 Flash (free) | $0/day |
| Market classification | Gemini 2.0 Flash (free) | $0/day |
| Data extraction | Gemini 2.0 Flash (free) | $0/day |
| Final probability estimation | Claude Opus 4.6 | ~$1-5/day |
| Trade/no-trade decisions | Claude Opus 4.6 | (included above) |

**Target**: < $0.05 average cost per trading cycle. Total daily cost typically $1-5 depending on market volume.

## Configuration

All parameters in `config/settings.py` can be overridden via environment variables. Key settings:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `KELLY_FRACTION` | 0.25 | Quarter Kelly (conservative sizing) |
| `MIN_EDGE_THRESHOLD` | 0.05 | Minimum 5% edge to trade |
| `MAX_POSITION_PCT` | 0.10 | Max 10% of bankroll per market |
| `MAX_SIMULTANEOUS_POSITIONS` | 5 | Position limit |
| `MAX_DRAWDOWN_PCT` | 0.30 | Stop trading if down 30% |
| `POLL_INTERVAL_SECONDS` | 300 | Main loop interval (5 min) |

## Risk Warnings

- **This bot trades real money.** Start with paper trading (`dry_run.py`) and small bankrolls.
- **Prediction markets are risky.** Past performance does not guarantee future results.
- **Never invest more than you can afford to lose.** The bot has risk guardrails (max drawdown, daily loss limits, position limits) but they are not foolproof.
- **Keep your private key secure.** Never share it, never commit it to git. The `.env` file is gitignored.
- **Monitor actively** when first deploying. Review trades, P&L, and LLM costs daily until you're confident in the bot's behavior.
