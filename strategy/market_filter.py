"""Market discovery, filtering, LLM categorization, and ranking.

Discovers markets from Polymarket, filters by liquidity/spread/time/volume,
categorizes via cheap LLM, extracts resolution params for econ/crypto markets,
and ranks candidates by desirability score.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from config.settings import (
    MAX_DAYS_TO_RESOLUTION,
    MAX_MARKET_LIQUIDITY,
    MAX_POSITION_PCT,
    MAX_SPREAD,
    MARKET_CACHE_REFRESH_SECONDS,
    MIN_24H_VOLUME,
    MIN_HOURS_TO_RESOLUTION,
    MIN_MARKET_LIQUIDITY,
)
from core import db
from core.client import ClobClientWrapper
from core.llm import LLMClient

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({
    "politics", "crypto", "sports", "science_tech",
    "entertainment", "economics", "other",
})


async def discover_markets(client: ClobClientWrapper) -> list[dict[str, Any]]:
    """Fetch all active markets, caching in SQLite market_cache table.

    Cache refreshes every MARKET_CACHE_REFRESH_SECONDS (default 30 min).
    Returns the full list with metadata.
    """
    # Check if we have a recent enough cache
    database = db.get_db()
    try:
        rows = list(database.execute(
            "SELECT fetched_at FROM market_cache ORDER BY fetched_at DESC LIMIT 1"
        ).fetchall())
        if rows:
            last_fetch = datetime.fromisoformat(rows[0][0])
            age = (datetime.now(timezone.utc) - last_fetch).total_seconds()
            if age < MARKET_CACHE_REFRESH_SECONDS:
                logger.info(
                    "Using cached markets (age=%.0fs, refresh=%ds)",
                    age, MARKET_CACHE_REFRESH_SECONDS,
                )
                cached = list(database["market_cache"].rows)
                markets = []
                for row in cached:
                    try:
                        market_data = json.loads(row["data"])
                        market_data["_category"] = row.get("category", "")
                        markets.append(market_data)
                    except (json.JSONDecodeError, KeyError):
                        continue
                return markets
    except Exception:
        pass  # Table empty or doesn't exist yet — fetch fresh

    # Fetch fresh from Polymarket
    logger.info("Fetching fresh market list from Polymarket CLOB API...")
    markets = await client.get_markets()
    logger.info("Discovered %d markets from API", len(markets))

    # Cache each market
    for market in markets:
        condition_id = market.get("condition_id", "")
        if not condition_id:
            continue

        # Preserve existing category if already classified
        existing = db.get_cached_market(condition_id)
        existing_category = existing.get("category", "") if existing else ""

        db.cache_market(
            condition_id=condition_id,
            data=market,
            category=existing_category,
        )

    logger.info("Cached %d markets in market_cache table", len(markets))
    return markets


async def filter_markets(
    markets: list[dict[str, Any]],
    client: ClobClientWrapper,
) -> list[dict[str, Any]]:
    """Apply filtering pipeline to narrow down candidate markets.

    Filters in order:
    1. Binary only (YES/NO markets)
    2. Liquidity band
    3. Time to resolution
    4. Spread < MAX_SPREAD
    5. 24h volume >= MIN_24H_VOLUME
    6. Not already at max position
    """
    initial_count = len(markets)
    logger.info("Starting filter pipeline with %d markets", initial_count)

    # 1. Binary only — must have exactly 2 tokens
    filtered = []
    for m in markets:
        tokens = m.get("tokens", [])
        if len(tokens) == 2:
            filtered.append(m)
    _log_filter_step("binary_only", initial_count, len(filtered))
    markets = filtered

    # 2. Liquidity band
    filtered = []
    for m in markets:
        liquidity = _get_liquidity(m)
        if MIN_MARKET_LIQUIDITY <= liquidity <= MAX_MARKET_LIQUIDITY:
            filtered.append(m)
    _log_filter_step("liquidity_band", len(markets), len(filtered))
    markets = filtered

    # 3. Time to resolution
    now = datetime.now(timezone.utc)
    min_resolution = now + timedelta(hours=MIN_HOURS_TO_RESOLUTION)
    max_resolution = now + timedelta(days=MAX_DAYS_TO_RESOLUTION)
    filtered = []
    for m in markets:
        end_date = _parse_end_date(m)
        if end_date and min_resolution <= end_date <= max_resolution:
            filtered.append(m)
    _log_filter_step("time_to_resolution", len(markets), len(filtered))
    markets = filtered

    # 4. Spread < MAX_SPREAD
    filtered = []
    for m in markets:
        tokens = m.get("tokens", [])
        if tokens:
            token_id = tokens[0].get("token_id", "")
            if token_id:
                try:
                    spread = await client.get_spread(token_id)
                    if spread < MAX_SPREAD:
                        m["_spread"] = spread
                        filtered.append(m)
                except Exception as e:
                    logger.debug("Failed to get spread for %s: %s", token_id, e)
    _log_filter_step("spread", len(markets), len(filtered))
    markets = filtered

    # 5. 24h volume
    filtered = []
    for m in markets:
        volume = _get_volume_24h(m)
        if volume >= MIN_24H_VOLUME:
            filtered.append(m)
    _log_filter_step("volume_24h", len(markets), len(filtered))
    markets = filtered

    # 6. Not already at max position
    open_positions = db.get_open_positions()
    position_market_ids = {p["market_id"] for p in open_positions if p.get("size", 0) > 0}
    # Count positions at max — simplified check: skip markets we already have positions in
    # A more precise check would compare position size vs bankroll * MAX_POSITION_PCT
    filtered = []
    for m in markets:
        condition_id = m.get("condition_id", "")
        if condition_id not in position_market_ids:
            filtered.append(m)
    _log_filter_step("max_position", len(markets), len(filtered))

    logger.info(
        "Filter pipeline complete: %d → %d markets",
        initial_count, len(filtered),
    )
    return filtered


async def categorize_market(market: dict[str, Any], llm: LLMClient) -> str:
    """Classify a market question into a category using the cheap LLM.

    Returns one of: politics, crypto, sports, science_tech, entertainment, economics, other.
    Caches the result in market_cache to avoid re-classification.
    """
    condition_id = market.get("condition_id", "")
    question = market.get("question", "")

    # Check cache first
    if condition_id:
        cached = db.get_cached_market(condition_id)
        if cached and cached.get("category"):
            return cached["category"]

    if not question:
        return "other"

    prompt = (
        'Classify this prediction market question into exactly one category.\n'
        f'Question: "{question}"\n'
        'Categories: politics, crypto, sports, science_tech, entertainment, economics, other\n'
        'Respond with only the category name, nothing else.'
    )

    try:
        response = await llm.call(prompt, task_type="classify")
        category = response.strip().lower().replace(" ", "_")

        # Validate — fall back to "other" if LLM returns unexpected value
        if category not in VALID_CATEGORIES:
            logger.warning(
                "LLM returned invalid category '%s' for '%s', defaulting to 'other'",
                category, question[:80],
            )
            category = "other"
    except Exception as e:
        logger.error("Failed to categorize market '%s': %s", question[:80], e)
        category = "other"

    # Cache the category
    if condition_id:
        db.cache_market(
            condition_id=condition_id,
            data=market,
            category=category,
        )

    logger.debug("Categorized '%s' → %s", question[:80], category)
    return category


async def extract_resolution_params(
    market_question: str,
    category: str,
    llm: LLMClient,
    condition_id: str = "",
) -> dict[str, Any] | None:
    """Extract structured resolution parameters for economics/crypto markets.

    Only runs for 'economics' and 'crypto' categories — returns None for all others.
    Results are cached in the market_cache data blob.
    """
    if category not in ("economics", "crypto"):
        return None

    # Check cache for existing resolution params
    if condition_id:
        cached = db.get_cached_market(condition_id)
        if cached and cached.get("data"):
            data = cached["data"] if isinstance(cached["data"], dict) else {}
            if data.get("_resolution_params"):
                return data["_resolution_params"]

    prompt = (
        f'Market question: "{market_question}"\n'
        f'Category: {category}\n'
        '\n'
        'Extract the key resolution parameters from this market question.\n'
        'For economics markets, identify: indicator type (rate, inflation, employment, gdp, other), '
        'specific metric if known, target value or direction, target date.\n'
        'For crypto markets, identify: coin/token name, target price or metric, '
        'direction (above/below), target date.\n'
        '\n'
        'Also identify any specific resolution methodology mentioned '
        '(e.g., specific exchange, TWAP, specific data source, snapshot time).\n'
        '\n'
        'Respond as JSON only:\n'
        '{"indicator_type": "...", "metric_name": "...", "target_value": null, '
        '"target_direction": "above"|"below"|"cut"|"hike"|"other", '
        '"target_date": "YYYY-MM-DD or null", "coin_id": "coingecko_id or null", '
        '"resolution_source": "specific exchange/source mentioned or null"}'
    )

    try:
        result = await llm.call_json(prompt, task_type="extract")
        if not isinstance(result, dict):
            logger.warning("Resolution param extraction returned non-dict: %s", type(result))
            return None

        params: dict[str, Any] = result

        # Cache in market_cache data blob
        if condition_id:
            cached = db.get_cached_market(condition_id)
            if cached and isinstance(cached.get("data"), dict):
                market_data = cached["data"]
            else:
                market_data = {"question": market_question}
            market_data["_resolution_params"] = params
            db.cache_market(
                condition_id=condition_id,
                data=market_data,
                category=category,
            )

        logger.debug(
            "Extracted resolution params for '%s': %s",
            market_question[:80], json.dumps(params, default=str),
        )
        return params

    except Exception as e:
        logger.error(
            "Failed to extract resolution params for '%s': %s",
            market_question[:80], e,
        )
        return None


def rank_candidates(filtered_markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and rank markets by desirability.

    Scoring:
    - Resolution in 1-4 weeks: +3 points
    - Resolution in 4-8 weeks: +1 point
    - Liquidity $1k-$10k: +2 points
    - Liquidity $500-$1k: +1 point
    - Category is economics or crypto: +2 points
    - Category is politics: +1 point
    - 24h volume > $500: +1 point

    Returns sorted list (highest score first) with _score attached.
    """
    now = datetime.now(timezone.utc)
    scored: list[dict[str, Any]] = []

    for market in filtered_markets:
        score = 0

        # Time to resolution scoring
        end_date = _parse_end_date(market)
        if end_date:
            days_to_resolution = (end_date - now).total_seconds() / 86400
            if 7 <= days_to_resolution <= 28:
                score += 3
            elif 28 < days_to_resolution <= 56:
                score += 1

        # Liquidity scoring
        liquidity = _get_liquidity(market)
        if 1000 <= liquidity <= 10000:
            score += 2
        elif 500 <= liquidity < 1000:
            score += 1

        # Category scoring
        category = market.get("_category", "")
        if category in ("economics", "crypto"):
            score += 2
        elif category == "politics":
            score += 1

        # Volume scoring
        volume = _get_volume_24h(market)
        if volume > 500:
            score += 1

        market["_score"] = score
        scored.append(market)

    scored.sort(key=lambda m: m["_score"], reverse=True)

    logger.info(
        "Ranked %d candidates (top score=%d, bottom score=%d)",
        len(scored),
        scored[0]["_score"] if scored else 0,
        scored[-1]["_score"] if scored else 0,
    )
    return scored


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_liquidity(market: dict[str, Any]) -> float:
    """Extract liquidity value from market data."""
    for key in ("liquidity", "volume", "totalLiquidity"):
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def _get_volume_24h(market: dict[str, Any]) -> float:
    """Extract 24-hour volume from market data."""
    for key in ("volume24hr", "volume_24h", "volume24h", "dailyVolume"):
        val = market.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def _parse_end_date(market: dict[str, Any]) -> datetime | None:
    """Parse the market end/resolution date."""
    for key in ("end_date_iso", "end_date", "endDate", "resolution_date"):
        val = market.get(key)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue
    return None


def _log_filter_step(step_name: str, before: int, after: int) -> None:
    """Log how many markets were eliminated in a filter step."""
    eliminated = before - after
    logger.info(
        "Filter [%s]: %d → %d (eliminated %d)",
        step_name, before, after, eliminated,
    )
