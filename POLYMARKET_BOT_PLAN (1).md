# Polymarket Signal-Based Trading Bot — Build Plan

> **Purpose**: This document is a sectioned build plan for Claude Code. Feed one section at a time into a fresh project folder. Each section is self-contained with full context, acceptance criteria, and implementation notes. Do not assume anything is pre-existing.
>
> **Goal**: A TUI-driven Polymarket trading bot controlled via Start/Stop in the dashboard. Uses signal-based trading with Kelly criterion sizing on mid-to-low liquidity markets. Runs only when actively started by the user — no 24/7 daemon, no Docker required.
>
> **Tech Stack**: Python 3.11+, py-clob-client, asyncio, SQLite, OpenRouter (tiered model routing), Docker.
>
> **LLM Cost Philosophy**: Use free/cheap models for routine tasks (summarization, classification, parsing). Use frontier models (Claude Opus 4.6) for high-stakes decisions (final probability estimation, trade/no-trade calls). The goal is to keep average cost per trading cycle under $0.05 while maximizing accuracy on the decisions that directly affect P&L.

---

## Section 0: Project Scaffolding & Environment

### Context
Set up the entire project from scratch in an empty folder. This includes the repo structure, all dependencies, configuration, secrets handling, and the LLM routing layer.

### Tasks
1. Initialize the project structure:
   ```
   polymarket-bot/
   ├── config/
   │   ├── settings.py          # All configurable params (Kelly fraction, min edge, polling intervals, model config)
   │   └── .env.example         # Template for every required secret
   ├── core/
   │   ├── __init__.py
   │   ├── client.py            # Polymarket CLOB client wrapper
   │   ├── wallet.py            # Wallet setup, auth, balance checking
   │   ├── db.py                # SQLite state management
   │   └── llm.py               # OpenRouter LLM client with tiered model routing
   ├── signals/
   │   ├── __init__.py
   │   ├── base.py              # Abstract signal provider interface
   │   ├── resolution_crypto.py # Crypto resolution source watcher (CoinGecko + log-normal model)
   │   └── aggregator.py        # Combines signals into probability estimates, calls frontier model for final estimate
   ├── strategy/
   │   ├── __init__.py
   │   ├── kelly.py             # Kelly criterion bet sizing
   │   ├── market_filter.py     # Filters for mid-to-low liquidity targets
   │   └── executor.py          # Order placement, monitoring, position management
   ├── monitoring/
   │   ├── __init__.py
   │   ├── pnl.py               # P&L tracking and reporting
   │   ├── health.py            # Health checks and alerting
   │   └── notifications.py     # Notification system (Telegram setup from scratch, or stdout fallback)
   ├── scripts/
   │   ├── setup_wallet.py      # One-time wallet setup helper
   │   ├── setup_telegram.py    # One-time Telegram bot setup helper
   │   ├── backtest.py          # Historical backtesting (future)
   │   └── dry_run.py           # Paper trading mode
   ├── tests/
   │   ├── __init__.py
   │   ├── test_kelly.py        # Unit tests for Kelly sizing
   │   ├── test_market_filter.py
   │   ├── test_signals.py
   │   └── test_resolution_crypto.py # Mock CoinGecko responses, verify log-normal math + signal output
   ├── main.py                  # Entry point / orchestrator loop
   ├── requirements.txt
   ├── Dockerfile
   ├── docker-compose.yml
   └── README.md
   ```

2. Create `requirements.txt`:
   ```
   py-clob-client
   python-dotenv
   aiohttp
   aiofiles
   web3
   eth-account
   requests
   feedparser
   beautifulsoup4
   lxml
   schedule
   sqlite-utils
   python-telegram-bot
   pytest
   ```

3. Create `config/settings.py` — every value must be overridable via environment variable:
   ```python
   import os

   # --- LLM Model Routing ---
   # Cheap model: used for summarization, article parsing, classification, simple extraction
   # Should cost < $0.10 per million input tokens or be free
   CHEAP_MODEL = os.getenv("CHEAP_MODEL", "google/gemini-2.0-flash-lite-001")

   # Frontier model: used for final probability estimation and trade/no-trade decisions
   # This is where accuracy matters most — worth paying for
   FRONTIER_MODEL = os.getenv("FRONTIER_MODEL", "anthropic/claude-opus-4-6")

   # OpenRouter config
   OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

   # --- Trading Parameters ---
   KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))           # Quarter Kelly (conservative)
   MIN_EDGE_THRESHOLD = float(os.getenv("MIN_EDGE_THRESHOLD", "0.05"))   # Only trade when edge > 5%
   MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.10"))       # Max 10% of bankroll per market
   MIN_BANKROLL_RESERVE = float(os.getenv("MIN_BANKROLL_RESERVE", "20")) # Always keep $20 USDC untouched

   # --- Market Filtering ---
   MIN_MARKET_LIQUIDITY = float(os.getenv("MIN_MARKET_LIQUIDITY", "500"))
   MAX_MARKET_LIQUIDITY = float(os.getenv("MAX_MARKET_LIQUIDITY", "50000"))
   MIN_HOURS_TO_RESOLUTION = int(os.getenv("MIN_HOURS_TO_RESOLUTION", "24"))
   MAX_DAYS_TO_RESOLUTION = int(os.getenv("MAX_DAYS_TO_RESOLUTION", "90"))

   # --- Operational ---
   POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
   SIGNAL_REFRESH_SECONDS = int(os.getenv("SIGNAL_REFRESH_SECONDS", "600"))
   ORDER_TYPE = "limit"
   SLIPPAGE_BUFFER = float(os.getenv("SLIPPAGE_BUFFER", "0.02"))

   # --- Risk Guardrails ---
   MAX_SIMULTANEOUS_POSITIONS = int(os.getenv("MAX_SIMULTANEOUS_POSITIONS", "5"))
   MAX_NEW_TRADES_PER_HOUR = int(os.getenv("MAX_NEW_TRADES_PER_HOUR", "3"))
   MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.30"))       # Stop trading if down 30%
   MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.15"))   # Stop for 24h if down 15% in a day

   # --- Resolution Source Monitoring ---
   RESOLUTION_SIGNAL_WEIGHT = float(os.getenv("RESOLUTION_SIGNAL_WEIGHT", "2.0"))
   # FRED_API_KEY removed — economics provider removed, crypto-only focus

   # --- Notifications ---
   NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").lower() == "true"
   TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
   ```

4. Create `.env.example` — document every single secret and what it's for:
   ```
   # === REQUIRED: Polymarket ===
   # Get these from: https://docs.polymarket.com/ (API credentials section)
   # You need to: 1) Create a Polymarket account, 2) Fund with USDC on Polygon, 3) Generate API keys
   POLYMARKET_API_KEY=
   POLYMARKET_API_SECRET=
   POLYMARKET_API_PASSPHRASE=

   # === REQUIRED: Wallet ===
   # Your Polygon wallet private key (the one linked to your Polymarket account)
   # NEVER share this. NEVER commit this.
   PRIVATE_KEY=

   # === REQUIRED: Polygon RPC ===
   # Free public endpoint works but is rate-limited. For better reliability, get a free key from Alchemy or Infura.
   POLYGON_RPC_URL=https://polygon-rpc.com

   # === REQUIRED: OpenRouter ===
   # Sign up at https://openrouter.ai/ and add credits ($5-10 to start)
   # This is used for LLM calls (signal analysis, probability estimation)
   OPENROUTER_API_KEY=

   # === OPTIONAL: Telegram Notifications ===
   # To set up: run `python scripts/setup_telegram.py` and follow the instructions
   # If not set, notifications will print to stdout instead
   TELEGRAM_ENABLED=false
   TELEGRAM_BOT_TOKEN=
   TELEGRAM_CHAT_ID=

   # === OPTIONAL: Model Overrides ===
   # Cheap model for routine tasks (default: Gemini Flash Lite)
   # CHEAP_MODEL=google/gemini-2.0-flash-lite-001
   # Frontier model for high-stakes decisions (default: Claude Opus 4.6)
   # FRONTIER_MODEL=anthropic/claude-opus-4-6
   ```

5. Create `README.md` with full setup instructions:
   - Prerequisites: Python 3.11+, Docker (optional), a Polymarket account with USDC on Polygon
   - Step-by-step: clone → create .env → pip install → run setup scripts → paper trade → go live
   - Architecture overview
   - Cost breakdown
   - Risk warnings

### Acceptance Criteria
- Running `pip install -r requirements.txt` in a fresh venv succeeds with zero errors
- All directories and `__init__.py` files exist
- `config/settings.py` is importable and every value can be overridden via env vars
- `.env.example` documents every required and optional variable with setup instructions
- `README.md` gives a complete newcomer enough info to get running

---

## Section 1: LLM Client with Tiered Model Routing

### Context
The bot uses LLMs for two very different jobs:
1. **Cheap tasks** (summarizing articles, classifying market topics, extracting structured data from text) — these happen frequently and don't need to be perfect. Use a free or near-free model.
2. **Expensive tasks** (estimating the true probability of an event, deciding whether the edge is real enough to trade on) — these happen less often but directly determine P&L. Use the best model available.

All LLM calls go through OpenRouter, which provides a unified API for hundreds of models. The routing logic decides which model to use based on the task type.

### Tasks
1. **`core/llm.py`** — OpenRouter client with tiered routing:
   ```python
   class LLMClient:
       """
       Tiered LLM client via OpenRouter.

       Usage:
           llm = LLMClient(api_key="...")

           # Cheap call for article summarization
           summary = await llm.cheap("Summarize this article: ...")

           # Frontier call for probability estimation
           estimate = await llm.frontier("Given the following evidence, estimate the probability...")

           # Auto-route based on task type
           result = await llm.call(prompt, task_type="summarize")  # routes to cheap
           result = await llm.call(prompt, task_type="estimate_probability")  # routes to frontier
       """
   ```

   - Methods:
     - `cheap(prompt, system=None)` → calls CHEAP_MODEL, returns text response
     - `frontier(prompt, system=None)` → calls FRONTIER_MODEL, returns text response
     - `call(prompt, task_type, system=None)` → auto-routes based on task_type
     - `call_json(prompt, task_type, system=None)` → same but parses response as JSON (with retry on parse failure)

   - Task type routing map:
     ```python
     TASK_ROUTING = {
         # Cheap model tasks
         "summarize": "cheap",
         "classify": "cheap",
         "extract": "cheap",
         "parse": "cheap",
         "search_queries": "cheap",

         # Frontier model tasks
         "estimate_probability": "frontier",
         "trade_decision": "frontier",
         "analyze_edge": "frontier",
         "evaluate_confidence": "frontier",
     }
     ```

   - Cost tracking:
     - Log every LLM call: model used, input tokens, output tokens, estimated cost
     - Store in SQLite `llm_costs` table (timestamp, model, task_type, input_tokens, output_tokens, cost_usd)
     - Method `get_daily_cost()` → returns total spend today
     - Method `get_monthly_cost()` → returns total spend this month

   - Error handling:
     - Retry on 429 (rate limit) with exponential backoff
     - Retry on 500/502/503 with 3 attempts
     - If frontier model fails, DO NOT fall back to cheap model for frontier tasks — alert and skip
     - If cheap model fails, try one alternative free model before alerting
     - Timeout: 30 seconds for cheap, 120 seconds for frontier

   - Rate limiting:
     - Max 20 cheap calls per minute
     - Max 5 frontier calls per minute
     - Queue and wait if limits would be exceeded

### Model Selection Notes for Claude Code
When implementing, use these OpenRouter model strings:
- Cheap: `"google/gemini-2.0-flash-exp:free"` (free tier, good enough for summarization)
- Frontier: `"anthropic/claude-opus-4-6"` (best reasoning, worth the cost for trade decisions)
- Fallback cheap: `"meta-llama/llama-3.1-8b-instruct:free"` (backup if Gemini is down)

The frontier model will be called roughly 10-50 times per day (once per candidate market that passes filtering). At Claude Opus 4.6 pricing via OpenRouter, this should be roughly $1-5/day depending on volume. The cheap model handles hundreds of calls per day at $0.

### Acceptance Criteria
- `LLMClient` can make calls to both cheap and frontier models via OpenRouter
- Task routing correctly maps task types to model tiers
- Cost tracking logs every call and can report daily/monthly totals
- Retry logic handles rate limits and server errors gracefully
- Frontier tasks never silently fall back to cheap models
- Unit test: mock OpenRouter responses and verify routing, retries, and cost tracking

---

## Section 2: Wallet & Polymarket Client

### Context
The bot needs to authenticate with Polymarket's CLOB (Central Limit Order Book) API and interact with its order book. Polymarket runs on Polygon and uses a proxy wallet system. The `py-clob-client` SDK handles most of the protocol details, but we need a clean wrapper with error handling.

This section also includes a one-time setup script for users who haven't created a Polymarket wallet yet.

### Tasks
1. **`scripts/setup_wallet.py`** — Interactive wallet setup helper:
   - Walks the user through:
     - Checking if they have a Polygon wallet (if not, explains how to create one)
     - Checking USDC balance on Polygon
     - Checking MATIC balance for gas
     - Verifying Polymarket API credentials work
     - Testing that the proxy wallet is registered
   - Prints clear instructions for any missing steps
   - This is a one-time script, not part of the bot runtime

2. **`core/wallet.py`** — Wallet management:
   - Load private key from env (`PRIVATE_KEY`)
   - Derive Polygon address from private key
   - `get_usdc_balance()` → USDC balance on Polygon (using web3.py + USDC contract address)
   - `get_matic_balance()` → MATIC balance for gas
   - `get_polymarket_balance()` → USDC available in Polymarket proxy wallet
   - `has_sufficient_gas()` → bool, True if MATIC > 0.1 (enough for ~100 transactions)
   - All balance checks should cache for 60 seconds (avoid spamming RPC)

3. **`core/client.py`** — CLOB client wrapper:
   - Initialize `ClobClient` from py-clob-client using env credentials
   - Methods (all with retry logic — 3 retries, exponential backoff):
     - `get_markets()` → list of all active markets with metadata (question, end_date, tokens, liquidity)
     - `get_market(condition_id)` → single market details
     - `get_orderbook(token_id)` → current order book (bids/asks with depths)
     - `get_midpoint(token_id)` → midpoint price as float (implied probability)
     - `get_best_bid(token_id)` → highest bid price
     - `get_best_ask(token_id)` → lowest ask price
     - `get_spread(token_id)` → ask - bid
     - `place_limit_order(token_id, side, price, size)` → place order, return order ID
     - `cancel_order(order_id)` → cancel an open order
     - `get_open_orders()` → all open orders with details
     - `get_positions()` → current token positions
   - Rate limiting: max 10 requests/second to CLOB API (use asyncio semaphore)
   - Logging: every API call logged with method, params, response status, latency
   - On authentication failure: clear error message pointing to `.env` setup

4. **`core/db.py`** — SQLite state persistence:
   - Database file: `data/bot.db` (auto-create `data/` directory)
   - Tables (auto-created on first import):
     ```
     trades:
       id TEXT PRIMARY KEY
       market_id TEXT
       token_id TEXT
       side TEXT (BUY/SELL)
       price REAL
       size REAL
       timestamp TEXT (ISO 8601)
       status TEXT (PENDING/FILLED/CANCELLED/EXPIRED)
       fill_price REAL (NULL until filled)
       pnl REAL (NULL until position closed)

     positions:
       token_id TEXT PRIMARY KEY
       market_id TEXT
       market_question TEXT
       side TEXT
       avg_entry REAL
       size REAL
       current_price REAL
       unrealized_pnl REAL
       opened_at TEXT
       last_updated TEXT

     signals:
       id INTEGER PRIMARY KEY AUTOINCREMENT
       market_id TEXT
       signal_source TEXT
       probability REAL
       confidence REAL
       reasoning TEXT
       model_used TEXT
       timestamp TEXT

     bankroll:
       timestamp TEXT PRIMARY KEY
       total_value REAL
       available_cash REAL
       unrealized_pnl REAL
       realized_pnl_today REAL
       realized_pnl_total REAL

     llm_costs:
       id INTEGER PRIMARY KEY AUTOINCREMENT
       timestamp TEXT
       model TEXT
       task_type TEXT
       input_tokens INTEGER
       output_tokens INTEGER
       cost_usd REAL

     market_cache:
       condition_id TEXT PRIMARY KEY
       data TEXT (JSON blob)
       fetched_at TEXT
       category TEXT
     ```
   - Helper methods:
     - `record_trade(...)`, `update_trade_status(...)`, `get_open_trades()`
     - `upsert_position(...)`, `close_position(...)`, `get_open_positions()`
     - `record_signal(...)`, `get_latest_signals(market_id)`
     - `snapshot_bankroll(...)`, `get_daily_pnl()`, `get_total_pnl()`
     - `record_llm_cost(...)`, `get_daily_llm_cost()`, `get_monthly_llm_cost()`

### Acceptance Criteria
- `setup_wallet.py` runs interactively and validates all credentials
- Client can authenticate and fetch the market list from Polymarket
- Can check all wallet balances (USDC, MATIC, Polymarket proxy)
- Can place and cancel a limit order (verified in paper trading mode)
- All API calls are logged, retried on failure, and rate-limited
- SQLite DB auto-creates all tables on first run
- All DB helper methods work (write basic tests)

---

## Section 3: Market Discovery & Filtering

### Context
The bot's edge lives in mid-to-low liquidity markets where fewer sophisticated participants are pricing things. We need to automatically discover and filter markets that match our criteria, and categorize them so the signal engine knows which signal sources to apply.

### Tasks
1. **`strategy/market_filter.py`** — Market filtering pipeline:
   - `discover_markets()`:
     - Fetch all active markets from Polymarket via client
     - Cache in SQLite `market_cache` table (refresh every 30 minutes)
     - Return full list with metadata

   - `filter_markets(markets)`:
     - Apply these filters in order:
       1. **Binary only**: Must be YES/NO markets (skip multi-outcome for v1)
       2. **Liquidity band**: Between `MIN_MARKET_LIQUIDITY` and `MAX_MARKET_LIQUIDITY`
       3. **Time to resolution**: Between `MIN_HOURS_TO_RESOLUTION` and `MAX_DAYS_TO_RESOLUTION`
       4. **Spread**: Order book spread < $0.10 (wider = too illiquid to trade reliably)
       5. **Volume**: At least $100 in trailing 24h volume (signs of active market)
       6. **Not already maxed**: Skip markets where we already hold MAX_POSITION_PCT
     - Log how many markets are eliminated at each filter step

   - `categorize_market(market)`:
     - Use the CHEAP LLM model to classify the market question into one of:
       - `politics` (elections, legislation, government)
       - `crypto` (token prices, blockchain events, protocol governance)
       - `other` (everything non-crypto)
     - Cache category in `market_cache` table (don't re-classify)
     - **Category gate**: only `crypto` markets pass through; all others are dropped
     - Prompt template:
       ```
       Classify this prediction market question into exactly one category.
       Question: "{market_question}"
       Categories: crypto, other
       Respond with only the category name, nothing else.
       ```

   - `extract_resolution_params(market_question, category)`:
     - Only runs for `crypto` category — skip all others
     - Uses CHEAP model to extract structured resolution metadata from the question
     - Cached in `market_cache` data blob (no schema change needed — store as JSON in existing blob column)
     - Prompt template:
       ```
       Market question: "{question}"
       Category: {category}

       Extract the key resolution parameters from this crypto market question.
       Identify: coin/token name, target price or metric, direction (above/below), target date.

       Also identify any specific resolution methodology mentioned (e.g., specific exchange, TWAP, specific data source, snapshot time).

       Respond as JSON only:
       {"indicator_type": "price", "metric_name": "...", "target_value": null, "target_direction": "above"|"below"|"other", "target_date": "YYYY-MM-DD or null", "coin_id": "coingecko_id or null", "resolution_source": "specific exchange/source mentioned or null"}
       ```

   - `rank_candidates(filtered_markets)`:
     - Score each market by desirability:
       - Resolution in 1-4 weeks: +3 points (sweet spot for signal accuracy)
       - Resolution in 4-8 weeks: +1 point
       - Liquidity $1k-$10k: +2 points (enough to trade, not too efficient)
       - Liquidity $500-$1k: +1 point
       - Category is `crypto`: +2 points (dedicated resolution source monitoring available)
       - 24h volume > $500: +1 point (active interest)
     - Return sorted by score descending
     - Target: 10-50 candidate markets per cycle

### Acceptance Criteria
- Filter pipeline runs end-to-end and returns a manageable candidate list
- Each filter step is logged with elimination count
- Markets are categorized using the cheap LLM (only crypto passes category gate)
- Resolution params are extracted and cached for crypto markets
- Ranking produces a sensible ordering (crypto markets get +2 due to resolution source monitoring)
- Market cache prevents redundant API calls and LLM classifications
- All settings are configurable in `settings.py`

---

## Section 4A: Signal Base + News Signal

### Context
This is the first part of the signal engine. We define the shared dataclass and abstract base, then build the news/sentiment signal provider — the most broadly applicable signal source. This part has zero external API key requirements.

The architecture across all of Section 4: cheap models do the grunt work (fetching, parsing, summarizing), frontier model makes the final call (in 4D).

### Dependencies
- Sections 0-3 complete (core/llm.py, core/db.py, strategy/market_filter.py)
- `feedparser` package (add to requirements.txt)

### Tasks
1. **`signals/base.py`** — Abstract signal interface:
   ```python
   from dataclasses import dataclass
   from typing import Optional

   @dataclass
   class SignalResult:
       source: str                    # e.g., "news", "polling"
       probability: Optional[float]   # 0-1, or None if insufficient data
       confidence: float              # 0-1, how confident this signal is
       reasoning: str                 # Human-readable explanation
       model_used: str                # Which LLM model produced this
       data_points: int               # How many articles/polls/etc. were analyzed
       raw_data: dict                 # Raw inputs for debugging

   class SignalProvider:
       """Abstract base class for all signal sources."""
       name: str = "base"

       async def get_signal(self, market_question: str, market_category: str, market_end_date: str, **kwargs) -> SignalResult:
           """
           kwargs may include:
           - resolution_keywords: dict from extract_resolution_params() for crypto markets
           """
           raise NotImplementedError
   ```

2. **`signals/news.py`** — News and sentiment signal:
   - **Data collection** (no API keys needed for these):
     - Google News RSS: `https://news.google.com/rss/search?q={query}` — parse with feedparser
     - Reddit search: `https://www.reddit.com/search.json?q={query}&sort=relevance&t=week`
     - Add User-Agent header to avoid blocks
   - **Pipeline**:
     1. Use CHEAP model to generate 2-3 search queries from the market question:
        ```
        Given this prediction market question: "{question}"
        Generate 2-3 short search queries (3-6 words each) that would find relevant recent news.
        Return as JSON array of strings, nothing else.
        ```
     2. Fetch articles/posts from RSS and Reddit for each query (last 7 days)
     3. Deduplicate by title similarity (simple string matching, >80% overlap = duplicate)
     4. Use CHEAP model to summarize each article into 2-3 sentences with sentiment toward YES/NO:
        ```
        Market question: "{question}"
        Article title: "{title}"
        Article snippet: "{snippet}"
        Summarize in 2 sentences. State whether this evidence supports YES or NO for the market question, or is neutral.
        Respond as JSON: {"summary": "...", "direction": "YES"|"NO"|"NEUTRAL"}
        ```
     5. Compile all summaries into a single evidence brief
     6. Use CHEAP model to make an initial probability estimate based on the evidence:
        ```
        Market question: "{question}"
        Evidence summaries:
        {compiled_summaries}
        Based on this evidence, estimate the probability of YES (0.0 to 1.0).
        Respond as JSON: {"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}
        If there is insufficient evidence, set probability to null and confidence to 0.
        ```
   - Cache signal results per market for 30 minutes
   - If fewer than 2 relevant articles found → return confidence = 0, probability = None

3. **`signals/__init__.py`** — Export `SignalResult`, `SignalProvider`, `NewsSignalProvider`

4. **`tests/test_news_signal.py`** — Unit tests:
   - Mock Google News RSS and Reddit responses
   - Mock cheap LLM calls (search queries, summarization, probability estimate)
   - Test: sufficient articles → returns probability and confidence
   - Test: fewer than 2 articles → returns confidence=0, probability=None
   - Test: LLM failure during summarization → graceful degradation
   - Test: deduplication removes near-identical titles
   - Test: cache returns same result within 30 minutes

### Acceptance Criteria
- `SignalResult` dataclass and `SignalProvider` ABC are defined and importable
- News signal fetches articles from Google News RSS and Reddit without API keys
- All LLM calls use cheap tier only
- Cache prevents redundant fetches within 30 minutes
- All tests pass with mocked external dependencies
- Signal results are logged to `signals` SQLite table

---

## Section 4B: Polling Signal

### Context
The polling/structured data signal for politics and general categories. Handles RSS and scraping of polling data sources. Economics and crypto categories are skipped here — they get dedicated resolution providers in 4C.

### Dependencies
- Section 4A complete (signals/base.py with SignalResult + SignalProvider)
- `beautifulsoup4` package (add to requirements.txt if not already present)

### Tasks
1. **`signals/polling.py`** — Structured data signal (politics and general categories):
   - **Scope**: This provider handles `politics` and other general categories. Skip `crypto` category (return confidence=0, probability=None) — handled by dedicated resolution source provider (`resolution_crypto.py`).
   - **Data sources** (all free, no API keys):
     - FiveThirtyEight / Silver Bulletin: RSS feeds for polling averages
     - RealClearPolitics: Scrape polling average tables with BeautifulSoup
   - **Pipeline**:
     1. If category is `crypto` → return confidence=0, probability=None immediately
     2. Based on market category, select relevant data source
     3. Fetch and parse structured data
     4. Use CHEAP model to interpret data in context of market question:
        ```
        Market question: "{question}"
        Relevant data:
        {structured_data}
        Based on this data, estimate the probability of YES (0.0 to 1.0).
        Respond as JSON: {"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}
        ```
   - This signal is most valuable for `politics` category
   - For categories without structured data sources → return confidence = 0, probability = None

2. **`tests/test_polling_signal.py`** — Unit tests:
   - Mock RSS and HTML responses from polling sources
   - Mock cheap LLM calls
   - Test: politics category → returns probability from polling data
   - Test: non-crypto category → immediately returns confidence=0, probability=None
   - Test: crypto category → immediately returns confidence=0, probability=None
   - Test: no structured data available → returns confidence=0, probability=None
   - Test: fetch failure → graceful degradation

### Acceptance Criteria
- Polling provider correctly skips crypto category (returns confidence=0)
- Politics markets produce probability estimates from polling data
- Categories with no data source return confidence=0 gracefully
- All LLM calls use cheap tier only
- All tests pass with mocked external dependencies

---

## Section 4C: Resolution Source Signals (Economics + Crypto)

### Context
This is the highest-value signal provider — it fetches data directly from the source that would be used to resolve crypto markets (CoinGecko). The crypto provider includes a log-normal price model for a mathematically grounded baseline before LLM adjustment.

### Dependencies
- Section 4A complete (signals/base.py with SignalResult + SignalProvider)
- Economics provider (resolution_econ.py) removed — bot is now crypto-only

### Tasks
1. **`signals/resolution_crypto.py`** — Crypto resolution source watcher:
   - `CryptoResolutionProvider(SignalProvider)` with `name = "resolution_crypto"`
   - **Data sources** (all free, no API key):

     | Source | Endpoint | Data |
     |--------|----------|------|
     | CoinGecko price | `https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd&include_24hr_change=true` | Current price, 24h change |
     | CoinGecko history | `https://api.coingecko.com/api/v3/coins/{id}/market_chart?vs_currency=usd&days=30` | 30-day price history |

   - **Pipeline**:
     1. If category != `crypto` → return confidence=0, probability=None
     2. Use `resolution_keywords["coin_id"]` from kwargs for CoinGecko ID; if missing, use cheap LLM to map coin name → CoinGecko ID (one-time, cached in market_cache)
     3. Fetch current price + 30-day history
     4. Calculate derived metrics: distance from target (%), daily volatility (std dev of log returns), days to resolution
     5. **Log-normal price model probability (no LLM needed):**
        - Compute annualized volatility from 30-day daily log returns
        - Use geometric Brownian motion to estimate P(price reaches target by resolution date):
          ```python
          import math

          def norm_cdf(x: float) -> float:
              """Normal CDF via math.erf — no scipy dependency needed."""
              return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

          log_ratio = math.log(target_price / current_price)
          drift = -0.5 * annual_vol**2  # risk-neutral drift
          time_years = days_remaining / 365.0
          z = (log_ratio - drift * time_years) / (annual_vol * math.sqrt(time_years))
          model_prob = 1.0 - norm_cdf(z)  # P(price >= target)
          # Flip for "below" direction: model_prob = norm_cdf(z)
          ```
        - This gives a mathematically grounded baseline probability at zero LLM cost
     6. **CHEAP model adjusts for trend/sentiment** — the LLM gets the model output as an anchor:
        ```
        Market question: "{question}"
        Resolution date: {end_date}

        Current market data from CoinGecko:
        - Current price: ${current_price}
        - 24h change: {change_24h}%
        - 30-day trend: {trend_description}
        - Distance from target: {distance}%
        - 30-day annualized volatility: {annual_vol}%
        - Days until resolution: {days_remaining}

        Log-normal price model estimate: {model_prob:.2f} probability of YES
        (Based on current price, volatility, and time remaining assuming random walk)

        Adjust this probability based on the trend data and any momentum factors.
        The model estimate is mathematically derived — only adjust if trend/context warrants it.
        Adjustments should typically be small (±0.05-0.15).

        Respond as JSON: {"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}
        ```
     7. Return SignalResult with source="resolution_crypto". Store both `model_prob` and LLM-adjusted prob in `raw_data` dict for audit.
   - Cache results per market for 15 minutes (crypto moves fast)
   - **No scipy dependency**: Use the `norm_cdf` helper via `math.erf` as shown above

2. **`tests/test_resolution_crypto.py`** — Unit tests:
   - Mock CoinGecko price + history responses
   - Mock cheap LLM adjustment calls
   - Test: crypto category → fetches price data, computes log-normal model, returns SignalResult
   - Test: non-crypto category → immediately returns confidence=0
   - Test: log-normal model math with known inputs produces correct outputs (no LLM mock needed)
   - Test: missing coin_id → cheap LLM maps name to CoinGecko ID
   - Test: CoinGecko API failure → graceful degradation

### Acceptance Criteria
- Crypto provider computes log-normal model probability, then uses cheap LLM to adjust for trend
- Log-normal model unit tests: known inputs produce correct mathematical outputs (no LLM needed to verify)
- Non-matching categories immediately return confidence=0 (no wasted API calls)
- All LLM calls use cheap tier only
- Both providers cache results to avoid redundant API calls
- All tests pass with mocked external dependencies
- **No scipy dependency**: `norm_cdf` uses `math.erf` only

---

## Section 4D: Signal Aggregator (Frontier Model)

### Context
This is the most important module in the entire bot. The aggregator collects signals from all providers (4A-4C), computes a weighted preliminary estimate, then makes the single FRONTIER MODEL call that determines our final probability. This is the only place the expensive frontier model is used in the signal pipeline.

### Dependencies
- Sections 4A, 4B, 4C complete (all signal providers)
- Frontier model configured in config/settings.py (already exists)

### Tasks
1. **`signals/aggregator.py`** — Signal aggregation with frontier model final call:
   - **Step 1**: Collect signals from all providers for a given market
   - **Step 2**: Filter out signals with confidence = 0 or probability = None
   - **Step 3**: If 0 usable signals → skip this market (return None)
   - **Step 4**: If 1+ usable signals → weighted average as a preliminary estimate, with source-based weight multipliers:
     ```python
     SIGNAL_WEIGHT_MULTIPLIERS = {
         "resolution_crypto": 2.0, # Direct resolution source — data from CoinGecko
         "prediction_markets": 1.8, # Cross-platform market consensus
         "web_search": 1.5,        # Search-grounded LLM (Perplexity Sonar)
     }
     # Use RESOLUTION_SIGNAL_WEIGHT from settings.py for resolution_* multipliers

     effective_weight = signal.confidence * SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
     preliminary_prob = sum(s.probability * ew(s) for s in signals) / sum(ew(s) for s in signals)
     ```
   - **Step 5**: FRONTIER MODEL CALL — this is where accuracy matters most:
     ```
     You are a superforecaster analyzing a prediction market. Your job is to estimate the true probability of an event as accurately as possible.

     Market question: "{question}"
     Market category: {category}
     Current market price (implied probability): {market_price}
     Market resolution date: {end_date}

     Signal analysis from multiple sources:
     {for each signal:}
     - Source: {signal.source} {"(DIRECT RESOLUTION SOURCE)" if signal.source.startswith("resolution_") else ""}
       Estimate: {signal.probability}
       Confidence: {signal.confidence}
       Reasoning: {signal.reasoning}
       Data points analyzed: {signal.data_points}

     Preliminary weighted estimate: {preliminary_prob}

     Instructions:
     1. Critically evaluate each signal source. Are any likely biased or unreliable?
     2. Signals marked as "DIRECT RESOLUTION SOURCE" come from the actual data providers (CoinGecko) whose data would be used to resolve this market. Weight these more heavily than news or sentiment signals.
     3. IMPORTANT: Check whether the market's resolution criteria specifies a particular data source, exchange, timestamp methodology, or TWAP that might differ from the signal data provided. If the resolution source differs from our data source (e.g., market resolves on Binance spot price but our data is from CoinGecko aggregated price), adjust your confidence downward accordingly.
     4. Consider base rates for this type of event.
     5. Consider what information the market might have that our signals don't.
     6. Provide your final probability estimate.
     7. Rate your overall confidence (0-1) in this estimate.
     8. Explain your reasoning in 2-3 sentences.

     IMPORTANT: Be calibrated. If you're unsure, your probability should be closer to the market price, not further from it. Only diverge significantly from the market when evidence is strong.

     Respond as JSON only:
     {
       "final_probability": 0.XX,
       "confidence": 0.XX,
       "reasoning": "...",
       "signals_agreement": "agree"|"mixed"|"disagree",
       "market_efficiency_assessment": "underpriced"|"overpriced"|"fair"
     }
     ```
   - **Step 6**: Parse frontier model response. If confidence < 0.4 → skip market.
   - **Step 7**: Return final aggregated result with full audit trail.
   - Store everything in `signals` table for later analysis.

2. **`tests/test_aggregator.py`** — Unit tests:
   - Mock all signal providers to return known SignalResult values
   - Mock frontier LLM call
   - Test: multiple signals → weighted average computed correctly
   - Test: resolution source signals get 2x weight multiplier
   - Test: 0 usable signals → returns None (skip market)
   - Test: frontier model confidence < 0.4 → skip market
   - Test: frontier model failure → raises, does NOT fall back to cheap
   - Test: full pipeline with mixed signal confidences
   - Test: all results stored in signals SQLite table

### Acceptance Criteria
- Aggregator produces a final probability estimate with reasoning
- Resolution source signals are weighted 2x in the aggregator
- Frontier prompt includes resolution source labels and resolution criteria mismatch warning
- Markets with insufficient data (0 usable signals) are correctly skipped
- Low-confidence frontier results (< 0.4) are correctly skipped
- All signal results are logged to SQLite `signals` table with full audit trail
- Cost per market analysis averages ~$0.02-0.05 (mostly from one frontier call)
- Frontier model failure raises — NEVER silently falls back to cheap
- All tests pass with mocked LLM and signal providers

---

## Section 5: Kelly Criterion & Bet Sizing

### Context
Kelly criterion converts our probability edge into optimal bet sizes. We use fractional Kelly for safety. This module takes the signal engine's output and determines exactly how much to bet and in which direction.

### Tasks
1. **`strategy/kelly.py`** — Kelly calculation engine:
   ```python
   @dataclass
   class TradeDecision:
       market_id: str
       token_id: str
       market_question: str
       side: str                    # "BUY_YES" or "BUY_NO"
       estimated_prob: float        # Our probability estimate
       market_price: float          # Current market implied probability
       edge: float                  # estimated_prob - market_price (or inverse)
       full_kelly_fraction: float   # What full Kelly says
       adjusted_fraction: float     # After applying KELLY_FRACTION multiplier
       bet_size_usd: float          # Dollar amount
       expected_value: float        # Expected profit per dollar risked
       confidence: float            # From signal aggregator
       should_trade: bool           # Final yes/no decision
       skip_reason: str             # If should_trade is False, why
   ```

   - `calculate_kelly(estimated_prob, market_price, available_bankroll)`:
     - Determine side:
       - If `estimated_prob > market_price` → BUY YES at `market_price`
         - `b = (1 - market_price) / market_price`
         - `p = estimated_prob`, `q = 1 - estimated_prob`
         - `edge = estimated_prob - market_price`
       - If `estimated_prob < market_price` → BUY NO at `(1 - market_price)`
         - `b = market_price / (1 - market_price)`
         - `p = 1 - estimated_prob`, `q = estimated_prob`
         - `edge = market_price - estimated_prob`
     - `full_kelly_f = (b * p - q) / b`
     - `adjusted_f = full_kelly_f * KELLY_FRACTION`
     - `bet_size = available_bankroll * adjusted_f`

   - Safety checks (applied in order):
     1. If `edge < MIN_EDGE_THRESHOLD` → skip, reason: "edge below threshold"
     2. If `full_kelly_f <= 0` → skip, reason: "no positive edge"
     3. If `bet_size < 1.0` → skip, reason: "bet too small (< $1)"
     4. If `bet_size > available_bankroll * MAX_POSITION_PCT` → cap to MAX_POSITION_PCT
     5. If `available_bankroll - bet_size < MIN_BANKROLL_RESERVE` → reduce to maintain reserve
     6. If existing position in this market → subtract existing exposure from max allowed

   - `expected_value = edge * bet_size` (simplified EV)

2. **Write comprehensive unit tests** in `tests/test_kelly.py`:
   - Test: positive edge BUY YES (market at 0.40, estimate 0.55)
   - Test: positive edge BUY NO (market at 0.70, estimate 0.50)
   - Test: zero edge (market = estimate) → should not trade
   - Test: negative edge → should not trade
   - Test: edge below MIN_EDGE_THRESHOLD → should not trade
   - Test: bet capped by MAX_POSITION_PCT
   - Test: bet reduced to maintain MIN_BANKROLL_RESERVE
   - Test: existing position reduces available sizing
   - Test: very small bankroll → bet_size < $1 → skip

### Acceptance Criteria
- Kelly formula produces correct results for all test cases
- All safety checks fire correctly
- TradeDecision dataclass contains full audit info for every decision
- Unit tests pass with 100% coverage of the kelly module
- No trade is ever placed that violates safety constraints

---

## Section 6: Order Execution & Position Management

### Context
Convert Kelly-sized trade decisions into actual Polymarket orders. Always use limit orders to avoid slippage. Handle the full order lifecycle: place, monitor, cancel stale orders, track positions, enforce risk guardrails. All execution happens while the bot is running (user pressed Start in TUI). When the user presses Stop, the executor finishes its current cycle and halts — open orders and positions persist in SQLite and are picked up on next Start.

### Tasks
1. **`strategy/executor.py`** — Order execution engine:

   - `execute_trade(trade_decision: TradeDecision)`:
     - Calculate limit price:
       - For BUY YES: `best_ask - SLIPPAGE_BUFFER` (try to fill below ask)
       - For BUY NO: `best_ask_no - SLIPPAGE_BUFFER` (buying the NO token)
     - Clamp price to valid range (0.01 to 0.99)
     - Calculate size in shares: `bet_size_usd / price`
     - Place limit order via client
     - Record in `trades` table with status "PENDING"
     - Log full details: market question, side, price, size, reasoning
     - Return order ID

   - `monitor_orders()` — called each pipeline cycle while bot is running:
     - Fetch all open orders from Polymarket
     - For each PENDING trade in our DB:
       - If filled → update status "FILLED", update `positions` table, log
       - If partially filled → update fill amount, keep monitoring
       - If open > 15 minutes and not filled:
         - Cancel the order
         - Re-check market price
         - If edge still exists → re-place at updated price
         - If edge gone → mark as "EXPIRED", move on
       - If cancelled externally → update status "CANCELLED"

   - `manage_positions()` — called each pipeline cycle while bot is running:
     - For each open position:
       - Fetch current market price
       - Update `unrealized_pnl` in positions table
       - **Profit taking**: If position is up and market price > 0.92 (for YES) or < 0.08 (for NO):
         - The market is likely to resolve in our favor
         - Consider selling early if we can lock in > 80% of max profit
         - Use CHEAP model to quickly assess: "Is this market likely to resolve? Price is at {price}."
       - **Loss evaluation**: If position is down > 20% from entry:
         - Re-run signal check for this market
         - If signal still supports our direction → hold (don't panic sell)
         - If signal has flipped → close position at loss
       - **Near resolution**: If market resolves in < 24h → hold, let it resolve
       - Update all P&L figures

   - **Risk guardrails** — checked before every new trade:
     - `check_position_count()`: Open positions < MAX_SIMULTANEOUS_POSITIONS
     - `check_trade_rate()`: New trades this hour < MAX_NEW_TRADES_PER_HOUR
     - `check_drawdown()`: Total unrealized + realized loss < MAX_DRAWDOWN_PCT of starting bankroll
     - `check_daily_loss()`: Today's realized loss < MAX_DAILY_LOSS_PCT of bankroll
     - If ANY guardrail fails → block the trade, log the reason, log to TUI
     - If drawdown guardrail fires → auto-stop the bot (equivalent to pressing Stop)
     - If daily loss guardrail fires → auto-stop the bot, log reason

### Acceptance Criteria
- Limit orders are placed with correct slippage buffer
- Order monitoring correctly handles fills, partial fills, stale orders, external cancellations
- Position management updates P&L and handles profit-taking / loss evaluation
- All risk guardrails are enforced and logged
- Guardrail triggers auto-stop the bot via TUI
- No trade ever bypasses the guardrail checks
- Open orders/positions survive Stop and are resumed on next Start

---

## Section 7: TUI Notifications & Event Log

### Context
The bot is controlled entirely through the TUI. All notifications go to the TUI's Logs tab and are also written to the Python `logging` module (which writes to `data/bot.log`). No Telegram, no remote monitoring — the user is watching the dashboard when the bot is running.

### Tasks
1. **`monitoring/notifications.py`** — TUI-integrated notification system:
   ```python
   class Notifier:
       """Sends notifications to TUI log panel and Python logging."""

       def __init__(self, app=None):
           """app is the Textual App instance (optional — works without TUI for testing)."""

       async def send(self, message: str, level: str = "info"):
           """level: 'info', 'warning', 'alert', 'critical'
           Logs via Python logging and posts to TUI log panel if app is set."""

       async def send_trade(self, trade_decision):
           """Format and log trade execution notification."""

       async def send_position_closed(self, position, pnl):
           """Format and log position closure with P&L."""

       async def send_health_alert(self, issue):
           """Format and log health check failure."""
   ```

   - All notifications go to Python `logging` (which writes to `data/bot.log` and TUI LogPanel)
   - If a Textual app reference is set, also post messages to the TUI via `app.post_message()`
   - Works without TUI for testing (just logs to Python logging)
   - Message formatting:
     - Trade executed: market question, side, size, price, edge, abbreviated reasoning
     - Position closed: market question, entry vs exit, P&L dollars and percent
     - Health alert: what failed, severity, recommended action
   - No Telegram dependency. No `python-telegram-bot` in requirements.
   - No `scripts/setup_telegram.py` needed.

### Acceptance Criteria
- Notifications appear in TUI Logs tab in real time
- All notifications also written to `data/bot.log` via Python logging
- Works without TUI app reference (for unit testing)
- All notification types are properly formatted and readable
- No external notification dependencies (no Telegram, no email)

---

## Section 8: Monitoring & Health Checks

### Context
Health checks and P&L tracking run while the bot is active (Start pressed in TUI). When the bot is stopped, no monitoring runs. Health check results display on the TUI Home tab. P&L data is always available in SQLite for review even when the bot is stopped.

### Tasks
1. **`monitoring/pnl.py`** — P&L tracking:
   - `snapshot_bankroll()` — called each pipeline cycle while bot is running:
     - Calculate: available cash + sum of all position values at current market prices
     - Store in `bankroll` table (skip if last snapshot was < 1 hour ago)
   - `get_daily_pnl()` → realized + unrealized P&L since midnight UTC
   - `get_weekly_pnl()` → same but last 7 days
   - `get_total_pnl()` → all-time
   - `get_metrics()` → dict with:
     - Win rate (trades closed profitably / total closed trades)
     - Average win size (dollars)
     - Average loss size (dollars)
     - Profit factor (gross wins / gross losses)
     - Max drawdown (largest peak-to-trough decline in bankroll)
     - Total LLM costs
     - Net P&L after costs (this is the number that matters)
     - ROI on initial bankroll
   - `get_cost_breakdown()` → LLM costs by model tier, per day/month

2. **`monitoring/health.py`** — Health checks (run while bot is active):
   - `run_health_checks()` — called by the TUI health-loop worker (already exists, runs every 5 min while bot is running):
     - **API connectivity**: Can we reach Polymarket CLOB API? (simple market list fetch)
     - **Wallet gas**: MATIC balance > 0.05? (warn at 0.1, critical at 0.05)
     - **Wallet funds**: USDC balance matches expected? (detect unauthorized transfers)
     - **Stale orders**: Any orders PENDING > 30 minutes? (may indicate API issue)
     - **LLM availability**: Can we reach OpenRouter? (simple /models endpoint call)
     - **CoinGecko API connectivity**: Can we reach CoinGecko? (simple `/api/v3/ping` endpoint)
     - **Cost runaway**: Daily LLM cost < $20? (hard cap to prevent billing surprises)
   - Each check returns: `{check_name, status: "ok"|"warning"|"critical", message}`
   - Results displayed on TUI Home tab (StatusPanel already shows health)
   - On "warning" → log via Notifier
   - On "critical" → log via Notifier + auto-stop the bot
   - Store health check history in SQLite for debugging

### Acceptance Criteria
- P&L snapshots are accurate and stored while bot is running
- All health checks run without errors while bot is active
- Warning and critical thresholds trigger appropriate responses
- Critical health failures auto-stop the bot
- Cost breakdown correctly separates cheap vs frontier model spending
- Health check history is queryable for debugging
- LLM cost hard cap prevents runaway spending
- No monitoring runs when bot is stopped

---

## Section 9: TUI-Driven Pipeline Integration

### Context
The TUI is the only way to run the bot. `main.py --tui` launches the dashboard (already implemented). The Start/Stop button on the Home tab controls the trading pipeline. This section wires the full trading pipeline (signals → Kelly → executor → position management) into the existing TUI worker system.

The TUI already has worker groups for `pipeline-loop`, `pipeline`, `health-loop`, `health-check`, `markets`, and `costs`. The Start button starts workers, the Stop button cancels all of them. This section extends the pipeline worker to include the full trading flow (not just signal aggregation).

### Tasks
1. **Extend `tui/app.py` pipeline worker** — wire full trading flow:
   - **On Start** (user presses `s` or clicks Start):
     1. Validate required env vars are present (show error in TUI if missing)
     2. Initialize SQLite database (auto-create tables and `data/` directory)
     3. Initialize LLM client, verify OpenRouter connectivity
     4. Initialize Polymarket client, verify API credentials
     5. Check wallet balances (USDC, MATIC), warn in TUI if low
     6. Load existing state from SQLite (open positions, pending orders)
     7. Reconcile local state with Polymarket (cancel orphaned orders, sync positions)
     8. Start health-loop and pipeline-loop workers (already implemented)
     9. Log startup summary to Logs tab

   - **Pipeline cycle** (each iteration of pipeline-loop, every POLL_INTERVAL_SECONDS):
     ```
     1. Refresh market list from Gamma API (re-fetch if stale)
     2. Filter and rank candidate markets
     3. For each candidate (up to MAX_NEW_TRADES_PER_HOUR remaining):
        a. Skip if we already hold max position in this market
        a2. Call `extract_resolution_params()` (cached, crypto-only) and pass the resulting `resolution_keywords` dict through to signal providers via kwargs
        b. Fetch/refresh signals for this market (pass resolution_keywords to providers)
        c. Aggregate signals → get final probability estimate (frontier model call)
        d. Calculate Kelly sizing
        e. Check all risk guardrails
        f. If should_trade → queue the order
     4. Execute queued orders (with rate limiting)
     5. Monitor existing orders (check fills, cancel stale)
     6. Manage open positions (P&L update, profit-taking, loss evaluation)
     7. Snapshot bankroll if it's been > 1 hour since last snapshot
     8. Log cycle summary: markets scanned, trades made, P&L, LLM cost this cycle
     9. Sleep until next cycle
     ```

   - **On Stop** (user presses `s` or clicks Stop):
     - Cancel all worker groups (already implemented)
     - Do NOT cancel open orders on Polymarket (they persist — user may restart soon)
     - Save current state snapshot to SQLite
     - Log stop summary to Logs tab

   - **Error handling** (within pipeline worker):
     - Wrap entire pipeline cycle in try/except
     - On any exception: log full traceback to Logs tab, increment failure counter
     - On 3 consecutive cycle failures: auto-stop the bot, show critical alert in TUI
     - Never crash the TUI — errors are caught and displayed

   - **On TUI exit** (Ctrl+C or quit):
     - Stop bot if running (cancel workers)
     - Save state to SQLite
     - Exit cleanly

2. **Paper trading mode** — controlled via settings, not a separate script:
   - Add `PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"` to `config/settings.py`
   - Default is `true` (paper mode) — user must explicitly set `PAPER_MODE=false` in `.env` to go live
   - When `PAPER_MODE=true`:
     - Replace executor with `PaperExecutor` that simulates order placement
     - Simulates fills: if real market price touches limit price within 15 minutes, mark as filled
     - Tracks simulated positions using real market prices
     - All other components run for real (signals, LLM calls, Kelly, filtering)
     - TUI title bar shows `[PAPER]` indicator
     - Paper trades stored in SQLite with `paper=True` column
   - When `PAPER_MODE=false`:
     - Real order execution via CLOB client
     - TUI title bar shows `[LIVE]` indicator

### Acceptance Criteria
- Start/Stop in TUI controls the entire trading pipeline
- Full pipeline cycle runs: filter → signals → Kelly → execute → monitor → manage positions
- State persists in SQLite across Start/Stop cycles
- 3 consecutive failures auto-stop the bot with visible error in TUI
- Paper mode is the default — real trading requires explicit opt-in
- TUI never crashes from pipeline errors
- All components integrate correctly end-to-end

---

## Section 10: Paper Executor & Live Readiness

### Context
**CRITICAL: Do not trade real money until paper trading is validated.** Paper mode is the default (`PAPER_MODE=true` in settings). The user runs the same TUI — Start/Stop works identically — but order execution is simulated. This section implements the `PaperExecutor` and the live readiness checklist.

### Tasks
1. **`strategy/executor.py`** — Add `PaperExecutor` alongside the real executor:
   - `PaperExecutor` implements the same interface as the real executor:
     - `place_order()` → simulates order placement, returns fake order ID, logs to TUI
     - Simulates fills: if real market price touches limit price within 15 minutes, mark as filled
     - Tracks simulated positions using real market prices from Gamma API
     - Calculates simulated P&L identically to live mode
   - All other components run for real (signals, LLM calls, Kelly, filtering)
   - Paper trades stored in SQLite with `paper=True` column

2. **TUI live readiness check** — shown when user sets `PAPER_MODE=false`:
   - On Start with `PAPER_MODE=false`, show a confirmation dialog in TUI:
     ```
     ⚠ LIVE TRADING MODE ⚠
     Real money will be used. Checklist:
     - Paper traded for 7+ days?
     - 30+ simulated trades?
     - Win rate > 52%?
     - Kelly sizing reasonable?
     - LLM costs within budget?

     Press Enter to confirm, Escape to cancel.
     ```
   - If user confirms → proceed with real executor
   - If user cancels → stay stopped

### Acceptance Criteria
- Paper trading is functionally identical to live except order execution
- Simulated fills are realistic (based on actual market price movement)
- Simulated P&L is tracked and fully reportable
- Paper trades are clearly distinguished from live trades in the database
- All LLM calls happen for real (so cost estimates are accurate)
- Live mode requires explicit confirmation in TUI

---

## Section 11: Polish & Documentation

### Context
The bot runs locally via `python main.py --tui`. No Docker, no VPS, no 24/7 daemon. This section covers final polish, documentation, and cost estimation.

### Tasks
1. **Remove Docker files** — delete `Dockerfile` and `docker-compose.yml` if they exist. The bot is a local TUI application.

2. **Clean up requirements.txt** — remove `python-telegram-bot` and `schedule` (neither is used). Verify all remaining dependencies are actually imported somewhere.

3. **Update README.md**:
   - **How to run**: `python main.py --tui` — that's it
   - **Usage**: Press `s` to Start/Stop the bot. Use tabs 1-6 to navigate. Use `:` for command bar.
   - **Paper vs Live**: Default is paper mode. Set `PAPER_MODE=false` in `.env` to go live.
   - **Document expected costs**:
     ```
     COST BREAKDOWN (estimated, per session):
     ├── LLM — Cheap model ... ~$0 (free tier via OpenRouter)
     ├── LLM — Frontier model  ~$0.03-0.05 per market analyzed
     ├── Polygon gas ......... ~$0.01 per trade
     ├── News/data APIs ...... $0 (RSS feeds + free APIs)
     └── Typical session ...... $0.50-5.00 depending on markets analyzed

     Costs only accrue while the bot is running (Start pressed).
     Monitor spending in real time on the Costs tab.
     ```
   - **Architecture overview** — brief description of signal pipeline
   - **Risk warnings** — same as Appendix C

### Acceptance Criteria
- README accurately describes TUI-driven usage (no mention of 24/7, Docker, or Telegram)
- Requirements.txt contains only actually-used dependencies
- Cost estimates reflect session-based usage, not 24/7 operation
- No Docker files in the project

---

## Appendix A: API & Resource Reference

| Resource | URL | Auth Required |
|----------|-----|--------------|
| Polymarket CLOB API | https://docs.polymarket.com/ | Yes (API key) |
| py-clob-client SDK | https://github.com/Polymarket/py-clob-client | — |
| OpenRouter API | https://openrouter.ai/docs | Yes (API key) |
| OpenRouter models | https://openrouter.ai/models | — |
| Polygon RPC | https://polygon-rpc.com | No |
| Google News RSS | https://news.google.com/rss/search?q={query} | No |
| Reddit search | https://www.reddit.com/search.json?q={query}&sort=relevance&t=week | No |
| CoinGecko API | https://api.coingecko.com/api/v3/ | No |
| ~~FRED API~~ | ~~removed — crypto-only focus~~ | ~~N/A~~ |
| ~~Telegram Bot API~~ | ~~removed — TUI-only~~ | — |

## Appendix B: LLM Task Routing Reference

| Task | Model Tier | Approx Cost/Call | Frequency |
|------|-----------|-----------------|-----------|
| Market classification | Cheap | ~$0 | Once per new market |
| Search query generation | Cheap | ~$0 | Per market per signal refresh |
| Article summarization | Cheap | ~$0 | Per article (5-15 per market) |
| Initial probability est. | Cheap | ~$0 | Per signal source per market |
| Polling data interpretation | Cheap | ~$0 | Per market (political only) |
| **Final probability est.** | **Frontier** | **~$0.03-0.05** | **Per candidate market** |
| Profit-taking assessment | Cheap | ~$0 | Per position check |

## Appendix C: Risk Warnings

- **Start small**: $100-200 bankroll maximum to start. Scale only after 100+ trades with proven profitability.
- **Fractional Kelly is mandatory**: Full Kelly guarantees ruin with imperfect probability estimates. Quarter Kelly (0.25) is the recommended max.
- **Your model will be wrong**: The goal is to be right more often than the crowd, not every time. A 55% win rate with proper sizing is profitable.
- **Liquidity risk**: Never take a position larger than 5% of a market's daily volume.
- **LLM costs are real**: Monitor daily. If frontier model spending outpaces profits, reduce trade frequency or lower POLL_INTERVAL.
- **Paper trade first**: Run with `PAPER_MODE=true` (the default) for at least 7 days before real money. No exceptions.
- **Regulatory**: Understand Polymarket's terms of service and your local laws regarding prediction markets.
