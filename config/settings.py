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
MIN_EDGE_THRESHOLD = float(os.getenv("MIN_EDGE_THRESHOLD", "0.02"))   # Only trade when edge > 2%
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.10"))       # Max 10% of bankroll per market
MIN_BANKROLL_RESERVE = float(os.getenv("MIN_BANKROLL_RESERVE", "20")) # Always keep $20 USDC untouched
TEST_BANKROLL = float(os.getenv("TEST_BANKROLL", "1000"))            # Placeholder bankroll for testing ($1000)

# --- Market Filtering ---
MIN_MARKET_LIQUIDITY = float(os.getenv("MIN_MARKET_LIQUIDITY", "500"))
MAX_MARKET_LIQUIDITY = float(os.getenv("MAX_MARKET_LIQUIDITY", "500000"))
MIN_HOURS_TO_RESOLUTION = int(os.getenv("MIN_HOURS_TO_RESOLUTION", "72"))
MAX_DAYS_TO_RESOLUTION = int(os.getenv("MAX_DAYS_TO_RESOLUTION", "30"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.05"))
MIN_24H_VOLUME = float(os.getenv("MIN_24H_VOLUME", "500"))
MARKET_CACHE_REFRESH_SECONDS = int(os.getenv("MARKET_CACHE_REFRESH_SECONDS", "1800"))  # 30 minutes

# --- Operational ---
DISCOVERY_INTERVAL_MINUTES = int(os.getenv("DISCOVERY_INTERVAL_MINUTES", "120"))     # 2 hours
AGGREGATION_INTERVAL_MINUTES = int(os.getenv("AGGREGATION_INTERVAL_MINUTES", "120")) # 2 hours
POSITION_CHECK_INTERVAL_MINUTES = int(os.getenv("POSITION_CHECK_INTERVAL_MINUTES", "30"))
SLIPPAGE_BUFFER = float(os.getenv("SLIPPAGE_BUFFER", "0.02"))
POLYMARKET_FEE_RATE = float(os.getenv("POLYMARKET_FEE_RATE", "0.02"))  # 2% fee on net winnings
MIN_CONFIDENCE_BLEND = float(os.getenv("MIN_CONFIDENCE_BLEND", "0.15"))  # Floor for confidence blending

# --- Risk Guardrails ---
MAX_SIMULTANEOUS_POSITIONS = int(os.getenv("MAX_SIMULTANEOUS_POSITIONS", "5"))
MAX_NEW_TRADES_PER_HOUR = int(os.getenv("MAX_NEW_TRADES_PER_HOUR", "50"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.30"))       # Stop trading if down 30%
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.15"))   # Stop for 24h if down 15% in a day

# --- Signal Weights (defaults, overridden by calibration when enough data) ---
RESOLUTION_SIGNAL_WEIGHT = float(os.getenv("RESOLUTION_SIGNAL_WEIGHT", "1.3"))
PREDICTION_MARKETS_SIGNAL_WEIGHT = float(os.getenv("PREDICTION_MARKETS_SIGNAL_WEIGHT", "1.8"))
WEB_SEARCH_SIGNAL_WEIGHT = float(os.getenv("WEB_SEARCH_SIGNAL_WEIGHT", "1.5"))
ONCHAIN_FLOW_SIGNAL_WEIGHT = float(os.getenv("ONCHAIN_FLOW_SIGNAL_WEIGHT", "1.3"))
MIN_FRONTIER_CONFIDENCE = float(os.getenv("MIN_FRONTIER_CONFIDENCE", "0.35"))

# --- New Signal Source API Keys ---
# Metaculus API token (required — their API returns 403 without auth)
# Get one at https://www.metaculus.com/aib/  (free tier available)
METACULUS_API_TOKEN = os.getenv("METACULUS_API_TOKEN", "")

# Glassnode API key (required for on-chain exchange flow data)
# Free tier at https://studio.glassnode.com — covers BTC/ETH exchange flows
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")

# Perplexity Sonar model via OpenRouter (search-grounded LLM)
SONAR_MODEL = os.getenv("SONAR_MODEL", "perplexity/sonar")
SONAR_RATE_LIMIT = int(os.getenv("SONAR_RATE_LIMIT", "20"))

# --- Frontier Divergence Guardrails ---
# Skip trade if frontier estimate diverges too far from market price
MAX_DIVERGENCE_LOW_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_LOW_CONFIDENCE", "0.40"))   # max divergence when confidence < 0.7
MAX_DIVERGENCE_ANY_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_ANY_CONFIDENCE", "0.50"))   # max divergence regardless of confidence
DIVERGENCE_CONFIDENCE_THRESHOLD = float(os.getenv("DIVERGENCE_CONFIDENCE_THRESHOLD", "0.7"))

# --- Order Book Depth Analysis ---
MAX_ACCEPTABLE_SLIPPAGE = float(os.getenv("MAX_ACCEPTABLE_SLIPPAGE", "0.03"))  # 3% max slippage
MIN_DEPTH_USD = float(os.getenv("MIN_DEPTH_USD", "200"))  # Skip if total book depth < $50
DEPTH_ANALYSIS_ENABLED = os.getenv("DEPTH_ANALYSIS_ENABLED", "true").lower() == "true"

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

# --- Web UI ---
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


def get_effective_param(name: str, default: float) -> float:
    """Return DB override if active, else the module-level default."""
    from core.db import get_active_overrides
    overrides = get_active_overrides()
    return overrides.get(name, default)
