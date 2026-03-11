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
MAX_MARKET_LIQUIDITY = float(os.getenv("MAX_MARKET_LIQUIDITY", "500000"))
MIN_HOURS_TO_RESOLUTION = int(os.getenv("MIN_HOURS_TO_RESOLUTION", "24"))
MAX_DAYS_TO_RESOLUTION = int(os.getenv("MAX_DAYS_TO_RESOLUTION", "90"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.10"))
MIN_24H_VOLUME = float(os.getenv("MIN_24H_VOLUME", "100"))
MARKET_CACHE_REFRESH_SECONDS = int(os.getenv("MARKET_CACHE_REFRESH_SECONDS", "1800"))  # 30 minutes

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
FRED_API_KEY = os.getenv("FRED_API_KEY", "DEMO_KEY")

# --- Frontier Divergence Guardrails ---
# Skip trade if frontier estimate diverges too far from market price
MAX_DIVERGENCE_LOW_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_LOW_CONFIDENCE", "0.30"))   # max divergence when confidence < 0.7
MAX_DIVERGENCE_ANY_CONFIDENCE = float(os.getenv("MAX_DIVERGENCE_ANY_CONFIDENCE", "0.40"))   # max divergence regardless of confidence
DIVERGENCE_CONFIDENCE_THRESHOLD = float(os.getenv("DIVERGENCE_CONFIDENCE_THRESHOLD", "0.7"))

# --- Notifications ---
NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").lower() == "true"
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
