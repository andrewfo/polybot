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
POSITION_CHECK_INTERVAL_MINUTES = int(os.getenv("POSITION_CHECK_INTERVAL_MINUTES", "10"))
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

# --- Signal Weights (defaults, overridden by calibration when enough data) ---
RESOLUTION_SIGNAL_WEIGHT = float(os.getenv("RESOLUTION_SIGNAL_WEIGHT", "1.3"))
PREDICTION_MARKETS_SIGNAL_WEIGHT = float(os.getenv("PREDICTION_MARKETS_SIGNAL_WEIGHT", "1.8"))
WEB_SEARCH_SIGNAL_WEIGHT = float(os.getenv("WEB_SEARCH_SIGNAL_WEIGHT", "1.5"))
ONCHAIN_FLOW_SIGNAL_WEIGHT = float(os.getenv("ONCHAIN_FLOW_SIGNAL_WEIGHT", "1.3"))
MIN_FRONTIER_CONFIDENCE = float(os.getenv("MIN_FRONTIER_CONFIDENCE", "0.35"))

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

# --- Execution ---
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
STALE_ORDER_MINUTES = int(os.getenv("STALE_ORDER_MINUTES", "15"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.12"))    # Close position at +12% profit
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.10"))        # Close position at -10% loss

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
