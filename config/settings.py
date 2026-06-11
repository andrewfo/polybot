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
MIN_EDGE_THRESHOLD = float(os.getenv("MIN_EDGE_THRESHOLD", "0.04"))   # Only trade when edge > 4%
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.10"))       # Max 10% of bankroll per market
MIN_BANKROLL_RESERVE = float(os.getenv("MIN_BANKROLL_RESERVE", "10")) # Always keep $10 USDC untouched
TEST_BANKROLL = float(os.getenv("TEST_BANKROLL", "200"))             # Paper trading bankroll ($200)

# --- Market Filtering ---
MIN_MARKET_LIQUIDITY = float(os.getenv("MIN_MARKET_LIQUIDITY", "500"))
MAX_MARKET_LIQUIDITY = float(os.getenv("MAX_MARKET_LIQUIDITY", "500000"))
MIN_HOURS_TO_RESOLUTION = int(os.getenv("MIN_HOURS_TO_RESOLUTION", "24"))
MAX_DAYS_TO_RESOLUTION = int(os.getenv("MAX_DAYS_TO_RESOLUTION", "45"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.05"))
# Relative spread guard at entry time: refuse to buy when (ask-bid)/mid exceeds
# this. Without it, crossing the spread on a wide market books an instant
# unrealized loss that exceeds STOP_LOSS_PCT and stops us out on the first tick.
MAX_ENTRY_SPREAD_PCT = float(os.getenv("MAX_ENTRY_SPREAD_PCT", "0.15"))
MIN_24H_VOLUME = float(os.getenv("MIN_24H_VOLUME", "100"))
MARKET_CACHE_REFRESH_SECONDS = int(os.getenv("MARKET_CACHE_REFRESH_SECONDS", "1800"))  # 30 minutes

# --- Operational ---
DISCOVERY_INTERVAL_MINUTES = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "30"))      # 30 minutes
AGGREGATION_INTERVAL_MINUTES = int(os.getenv("AGGREGATION_INTERVAL_MINUTES", "30"))  # 30 minutes
POSITION_CHECK_INTERVAL_MINUTES = int(os.getenv("POSITION_CHECK_INTERVAL_MINUTES", "5"))
SLIPPAGE_BUFFER = float(os.getenv("SLIPPAGE_BUFFER", "0.02"))
POLYMARKET_FEE_RATE = float(os.getenv("POLYMARKET_FEE_RATE", "0.02"))  # 2% fee on net winnings
MIN_CONFIDENCE_BLEND = float(os.getenv("MIN_CONFIDENCE_BLEND", "0.20"))  # Floor for confidence blending

# --- Risk Guardrails ---
MAX_NEW_TRADES_PER_HOUR = int(os.getenv("MAX_NEW_TRADES_PER_HOUR", "8"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "12"))       # Cap total concurrent positions
MAX_CORRELATED_POSITIONS = int(os.getenv("MAX_CORRELATED_POSITIONS", "3"))  # Max positions per underlying asset (e.g., BTC)
MARKET_COOLDOWN_MINUTES = int(os.getenv("MARKET_COOLDOWN_MINUTES", "240"))  # Block re-entry on same market for 4h after close
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.30"))       # Stop trading if down 30%
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.15"))   # Stop for 24h if down 15% in a day

# --- Pre-Frontier Gate ---
# Skip the paid Sonar + frontier calls when the preliminary edge computed from
# the free signals (resolution_crypto, prediction_markets, onchain_flow) is
# below this. Set slightly below MIN_EDGE_THRESHOLD so borderline cases still
# reach the frontier model. Set to 0 to disable the gate.
PRE_FRONTIER_EDGE_THRESHOLD = float(os.getenv("PRE_FRONTIER_EDGE_THRESHOLD", "0.03"))

# --- Signal Weights (defaults, overridden by calibration when enough data) ---
# May 2026 calibration (241 resolved samples): resolution_crypto Brier 0.048,
# web_search 0.251, onchain_flow 0.253 (0.25 = always predicting 50%). The two
# noise signals are benched at weight 0; they earn weight back via the
# calibration earn-back path below once their rolling Brier improves.
RESOLUTION_SIGNAL_WEIGHT = float(os.getenv("RESOLUTION_SIGNAL_WEIGHT", "2.5"))
PREDICTION_MARKETS_SIGNAL_WEIGHT = float(os.getenv("PREDICTION_MARKETS_SIGNAL_WEIGHT", "1.8"))
WEB_SEARCH_SIGNAL_WEIGHT = float(os.getenv("WEB_SEARCH_SIGNAL_WEIGHT", "0.0"))
ONCHAIN_FLOW_SIGNAL_WEIGHT = float(os.getenv("ONCHAIN_FLOW_SIGNAL_WEIGHT", "0.0"))
MIN_FRONTIER_CONFIDENCE = float(os.getenv("MIN_FRONTIER_CONFIDENCE", "0.35"))

# When false, the Sonar-backed web_search provider is not constructed at all —
# no Sonar spend. Re-enable together with a non-zero WEB_SEARCH_SIGNAL_WEIGHT
# (or let the earn-back path restore its weight) to bring it back.
ENABLE_WEB_SEARCH_SIGNAL = os.getenv("ENABLE_WEB_SEARCH_SIGNAL", "false").lower() == "true"

# Earn-back path for benched signals (default weight 0): a benched source
# regains aggregation weight once its rolling Brier beats this threshold over
# at least this many resolved samples (free signals keep running and logging
# calibration predictions even at weight 0).
BENCHED_EARN_BACK_BRIER = float(os.getenv("BENCHED_EARN_BACK_BRIER", "0.20"))
BENCHED_EARN_BACK_MIN_SAMPLES = int(os.getenv("BENCHED_EARN_BACK_MIN_SAMPLES", "30"))

# Perplexity Sonar model via OpenRouter (search-grounded LLM)
SONAR_MODEL = os.getenv("SONAR_MODEL", "perplexity/sonar")
SONAR_RATE_LIMIT = int(os.getenv("SONAR_RATE_LIMIT", "20"))

# --- Frontier Divergence Guardrails ---
# Skip trade if frontier estimate diverges too far from market price
MAX_DIVERGENCE_LOW_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_LOW_CONFIDENCE", "0.40"))   # max divergence when confidence < 0.7
MAX_DIVERGENCE_ANY_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_ANY_CONFIDENCE", "0.50"))   # max divergence regardless of confidence
DIVERGENCE_CONFIDENCE_THRESHOLD = float(os.getenv("DIVERGENCE_CONFIDENCE_THRESHOLD", "0.7"))

# --- Event Market Thresholds ---
# Event markets (crypto events, not price targets) use LLM signals instead of math models
MIN_FRONTIER_CONFIDENCE_EVENT = float(os.getenv("MIN_FRONTIER_CONFIDENCE_EVENT", "0.25"))
MAX_DIVERGENCE_LOW_CONFIDENCE_EVENT = float(os.getenv("MAX_DIVERGENCE_LOW_CONFIDENCE_EVENT", "0.45"))
EVENT_MARKET_BASELINE_EDGE = float(os.getenv("EVENT_MARKET_BASELINE_EDGE", "0.05"))

# --- Order Book Depth Analysis ---
MAX_ACCEPTABLE_SLIPPAGE = float(os.getenv("MAX_ACCEPTABLE_SLIPPAGE", "0.03"))  # 3% max slippage
MIN_DEPTH_USD = float(os.getenv("MIN_DEPTH_USD", "200"))  # Skip if total book depth < $50
DEPTH_ANALYSIS_ENABLED = os.getenv("DEPTH_ANALYSIS_ENABLED", "true").lower() == "true"

# --- Gas Cost Analysis ---
# Round-trip gas budget (entry + exit) for a single trade cycle on Polygon
GAS_UNITS_PER_TRADE_CYCLE = int(os.getenv("GAS_UNITS_PER_TRADE_CYCLE", "500000"))
# Expected value must clear this multiple of round-trip gas cost.
# Kelly now subtracts gas from the edge directly, so this is a belt-and-suspenders
# sanity check for cases where depth adjustment shrinks the bet after Kelly.
MIN_EV_GAS_RATIO = float(os.getenv("MIN_EV_GAS_RATIO", "1.5"))
# Fallbacks used when live fetches fail
GAS_PRICE_FALLBACK_GWEI = float(os.getenv("GAS_PRICE_FALLBACK_GWEI", "50.0"))
MATIC_USD_FALLBACK = float(os.getenv("MATIC_USD_FALLBACK", "0.50"))
GAS_ANALYSIS_ENABLED = os.getenv("GAS_ANALYSIS_ENABLED", "true").lower() == "true"
# Minimum bet in USD — bets below this are skipped regardless of Kelly output
MIN_BET_USD = float(os.getenv("MIN_BET_USD", "5.0"))
# Gas may consume at most this fraction of a bet; floor rises when gas spikes
MAX_GAS_DRAG_PCT = float(os.getenv("MAX_GAS_DRAG_PCT", "0.01"))

# --- Aggregation ---
USE_LOG_ODDS_AVERAGING = os.getenv("USE_LOG_ODDS_AVERAGING", "true").lower() == "true"

# --- Signal Calibration ---
MIN_CALIBRATION_SAMPLES = int(os.getenv("MIN_CALIBRATION_SAMPLES", "20"))  # Min resolved predictions to use dynamic weights
CALIBRATION_LOOKBACK_DAYS = int(os.getenv("CALIBRATION_LOOKBACK_DAYS", "90"))  # Rolling window for Brier scores

# --- Learning Data Regime ---
# Rows timestamped before this cutoff were produced under the optimistic paper
# pricing engine (entries at limit price, exits at mid; realistic-pricing fixes
# landed 2026-05-22) and include pre-upsert calibration churn duplicates. Win
# rates, edge efficiency, Brier scores, and parameter recommendations computed
# from them are unreliable, so the learning engine and dynamic signal
# calibration exclude them. scripts/reset_learning_state.py tags those rows
# with data_regime='pre_fix' for audit.
LEARNING_DATA_CUTOFF = os.getenv("LEARNING_DATA_CUTOFF", "2026-05-22T20:30:00+00:00")

# --- Paper Run Validation Gate ---
# Go/no-go thresholds for the honest-pricing paper run, evaluated by
# GET /api/paper/summary over post-cutoff data only. Live trading should not
# be enabled until every criterion passes.
PAPER_RUN_MIN_DAYS = float(os.getenv("PAPER_RUN_MIN_DAYS", "7"))
PAPER_RUN_MIN_CLOSED_TRADES = int(os.getenv("PAPER_RUN_MIN_CLOSED_TRADES", "100"))
PAPER_RUN_MAX_PROFIT_CONCENTRATION = float(os.getenv("PAPER_RUN_MAX_PROFIT_CONCENTRATION", "0.25"))
PAPER_RUN_MIN_BRIER_SAMPLES = int(os.getenv("PAPER_RUN_MIN_BRIER_SAMPLES", "30"))

# --- Execution ---
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
STALE_ORDER_MINUTES = int(os.getenv("STALE_ORDER_MINUTES", "15"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.12"))    # Close position at +12% profit
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.10"))        # Close position at -10% loss
# Tick-aware stop-loss floor. On a $0.08 market a 1-cent tick is a 12.5% swing,
# so a flat 10% stop fires on the first quote update. Require N ticks of move
# against entry before stopping; widens the effective stop on low-priced markets
# while leaving mid/high-priced behavior unchanged.
STOP_LOSS_TICK_SIZE = float(os.getenv("STOP_LOSS_TICK_SIZE", "0.01"))
STOP_LOSS_MIN_TICKS = int(os.getenv("STOP_LOSS_MIN_TICKS", "3"))
# When true, both paper and live trading model realistic spread cost: Kelly
# sees a freshly fetched bid/ask (not a 30-min-old discovery snapshot), and
# TP/SL fires against the bid (what a real sell would realize). Disable to
# reproduce the legacy mid-based accounting for backtests/A-B comparisons.
# The env var keeps the PAPER_ prefix for backwards compatibility.
REALISTIC_PRICING = os.getenv(
    "REALISTIC_PRICING",
    os.getenv("PAPER_REALISTIC_PRICING", "true"),
).lower() == "true"

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Web UI ---
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


def get_effective_param(name: str, default: float) -> float:
    """Return DB override if active, else the module-level default."""
    from core.db import get_active_overrides
    overrides = get_active_overrides()
    return overrides.get(name, default)
