"""Market discovery, filtering, LLM categorization, and ranking.

Discovers markets from Polymarket, filters by liquidity/spread/time/volume,
categorizes via cheap LLM, extracts resolution params for econ/crypto markets,
and ranks candidates by desirability score.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import aiohttp

from config.settings import (
    MAX_DAYS_TO_RESOLUTION,
    MAX_MARKET_LIQUIDITY,
    MAX_SPREAD,
    MARKET_CACHE_REFRESH_SECONDS,
    MIN_24H_VOLUME,
    MIN_HOURS_TO_RESOLUTION,
    MIN_MARKET_LIQUIDITY,
)
from core import db
from core.llm import LLMClient

# Gamma API returns richer market data (liquidity, volume, spread, etc.)
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
GAMMA_FETCH_LIMIT = 200  # per page
GAMMA_MAX_PAGES = 5      # up to 1000 markets total

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({"crypto", "other"})

# Keyword-based crypto classification (saves ~20 LLM calls per cycle)
CRYPTO_KEYWORDS = frozenset({
    # Major coins
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "cardano", "ada",
    "ripple", "xrp", "dogecoin", "doge", "polkadot", "dot", "avalanche", "avax",
    "chainlink", "link", "polygon", "matic", "litecoin", "ltc", "uniswap", "uni",
    "cosmos", "atom", "near", "aptos", "apt", "arbitrum", "arb", "optimism",
    "sui", "sei", "celestia", "tia", "jupiter", "jup", "bonk", "pepe",
    "shiba", "shib", "toncoin", "ton", "tron", "trx", "stellar", "xlm",
    "hedera", "hbar", "filecoin", "fil", "render", "rndr", "injective", "inj",
    "stacks", "stx", "kaspa", "kas", "mantle", "mnt", "beam", "wormhole",
    # Generic crypto terms
    "crypto", "cryptocurrency", "blockchain", "defi", "nft", "token",
    "altcoin", "memecoin", "stablecoin", "usdc", "usdt", "tether", "dai",
    "mining", "halving", "ethereum etf", "bitcoin etf", "crypto etf",
    "binance", "coinbase", "coingecko", "dex", "cex",
})

# Multi-word patterns checked as substrings (lowercased)
CRYPTO_BIGRAMS = [
    "bitcoin etf", "ethereum etf", "crypto etf", "spot etf",
    "btc price", "eth price", "crypto market", "market cap",
    "hash rate", "gas fee", "smart contract", "layer 2",
    "proof of stake", "proof of work",
]


def _is_crypto_keyword_match(question: str) -> bool:
    """Check if market question matches crypto keywords.

    Uses word-set intersection for single keywords and substring
    matching for multi-word patterns.
    """
    q_lower = question.lower()
    q_words = set(q_lower.split())

    # Single keyword match
    if q_words & CRYPTO_KEYWORDS:
        return True

    # Bigram / substring match
    for bigram in CRYPTO_BIGRAMS:
        if bigram in q_lower:
            return True

    return False


async def discover_markets(max_pages: int = 0) -> list[dict[str, Any]]:
    """Fetch active markets from Gamma API, caching in SQLite market_cache table.

    Uses the Gamma API which returns rich metadata (liquidity, volume, spread,
    token IDs, end dates) — unlike the CLOB API which omits these fields.

    Cache refreshes every MARKET_CACHE_REFRESH_SECONDS (default 30 min).

    Args:
        max_pages: Maximum pages to fetch. 0 = use GAMMA_MAX_PAGES default.
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
                # Validate cache has Gamma-format data (has liquidity field)
                sample = database.execute(
                    "SELECT data FROM market_cache LIMIT 1"
                ).fetchone()
                if sample:
                    try:
                        sample_data = json.loads(sample[0])
                        if "liquidity" not in sample_data:
                            logger.info("Cache contains old CLOB-format data, refreshing...")
                            raise ValueError("stale cache format")
                    except (json.JSONDecodeError, ValueError):
                        raise ValueError("stale cache")

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

    # Fetch from Gamma API (rich metadata: liquidity, volume, spread, etc.)
    pages = max_pages if max_pages > 0 else GAMMA_MAX_PAGES
    all_markets: list[dict[str, Any]] = []
    offset = 0

    logger.info("Fetching markets from Gamma API (limit=%d per page, max_pages=%d)...", GAMMA_FETCH_LIMIT, pages)

    seen_ids: set[str] = set()

    async with aiohttp.ClientSession() as session:
        # Primary fetch: sorted by volume (most liquid)
        for page in range(pages):
            url = (
                f"{GAMMA_API_BASE}/markets"
                f"?active=true&closed=false"
                f"&limit={GAMMA_FETCH_LIMIT}&offset={offset}"
                f"&order=volume24hr&ascending=false"
            )
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.error("Gamma API returned HTTP %d on page %d", resp.status, page)
                        break
                    page_markets = await resp.json()
                    if not page_markets:
                        break
                    for pm in page_markets:
                        cid = pm.get("conditionId", "")
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            all_markets.append(pm)
                    offset += GAMMA_FETCH_LIMIT
                    if len(page_markets) < GAMMA_FETCH_LIMIT:
                        break  # Last page
            except Exception as e:
                logger.error("Gamma API fetch failed on page %d: %s", page, e)
                break

        # Secondary fetch: recently created markets (more likely mispriced)
        # New markets haven't had time for full price discovery
        try:
            url = (
                f"{GAMMA_API_BASE}/markets"
                f"?active=true&closed=false"
                f"&limit={GAMMA_FETCH_LIMIT}"
                f"&order=startDate&ascending=false"
            )
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    new_markets = await resp.json()
                    new_count = 0
                    for pm in (new_markets or []):
                        cid = pm.get("conditionId", "")
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            all_markets.append(pm)
                            new_count += 1
                    if new_count:
                        logger.info("Added %d recently-created markets from secondary fetch", new_count)
        except Exception as e:
            logger.warning("Secondary Gamma fetch (new markets) failed: %s", e)

    logger.info("Discovered %d unique markets from Gamma API", len(all_markets))

    # Normalize Gamma field names to match expected format
    markets = []
    for m in all_markets:
        market = _normalize_gamma_market(m)
        if market:
            markets.append(market)

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


def _normalize_gamma_market(gamma: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Gamma API market data to the format expected by filters.

    Maps Gamma field names (camelCase) to our expected field names and builds
    a `tokens` list matching the format other code expects.
    """
    condition_id = gamma.get("conditionId", "")
    if not condition_id:
        return None

    # Build tokens list from Gamma's separate arrays
    # Gamma API returns these as JSON strings, not Python lists — parse them
    clob_ids = gamma.get("clobTokenIds", [])
    outcomes = gamma.get("outcomes", [])
    outcome_prices = gamma.get("outcomePrices", [])
    if isinstance(clob_ids, str):
        try:
            clob_ids = json.loads(clob_ids)
        except (json.JSONDecodeError, TypeError):
            clob_ids = []
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except (json.JSONDecodeError, TypeError):
            outcome_prices = []

    tokens = []
    for i, token_id in enumerate(clob_ids):
        token: dict[str, Any] = {"token_id": token_id}
        if i < len(outcomes):
            token["outcome"] = outcomes[i]
        if i < len(outcome_prices):
            try:
                token["price"] = str(outcome_prices[i])
            except (TypeError, ValueError):
                pass
        tokens.append(token)

    return {
        "condition_id": condition_id,
        "question": gamma.get("question", ""),
        "tokens": tokens,
        "liquidity": gamma.get("liquidityNum", gamma.get("liquidity", 0)),
        "volume": gamma.get("volumeNum", gamma.get("volume", 0)),
        "volume24hr": gamma.get("volume24hr", 0),
        "endDate": gamma.get("endDate", gamma.get("endDateIso", "")),
        "startDate": gamma.get("startDate", ""),
        "spread": gamma.get("spread", None),
        "bestBid": gamma.get("bestBid", None),
        "bestAsk": gamma.get("bestAsk", None),
        "slug": gamma.get("slug", ""),
        "description": gamma.get("description", ""),
        "negRisk": gamma.get("negRisk", False),
        # Preserve raw Gamma data for reference
        "_gamma_id": gamma.get("id", ""),
    }


async def filter_markets(
    markets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply filtering pipeline to narrow down candidate markets.

    Filters in order:
    1. Binary only (YES/NO markets)
    2. Liquidity band (MIN_MARKET_LIQUIDITY to MAX_MARKET_LIQUIDITY)
    3. Time to resolution (MIN_HOURS_TO_RESOLUTION to MAX_DAYS_TO_RESOLUTION)
    4. Near-certain filter: drop if best outcome price <= 0.02 or >= 0.98
    5. Spread filter: drop if spread > MAX_SPREAD (Gamma data only, no CLOB fallback)
    6. Minimum 24h volume filter (MIN_24H_VOLUME)
    7. Not already at max position
    8. Sort survivors by volume_24hr descending
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

    # 4. Near-certain price filter: drop if any outcome price <= 0.02 or >= 0.98
    filtered = []
    for m in markets:
        prices = _get_outcome_prices(m)
        if prices and all(0.02 < p < 0.98 for p in prices):
            filtered.append(m)
        elif not prices:
            # No price data available — keep the market (don't penalize missing data)
            filtered.append(m)
    _log_filter_step("near_certain", len(markets), len(filtered))
    markets = filtered

    # 5. Spread filter: use Gamma spread or compute from bestAsk - bestBid
    filtered = []
    for m in markets:
        spread = _get_spread(m)
        if spread is not None and spread <= MAX_SPREAD:
            m["_spread"] = spread
            filtered.append(m)
        elif spread is None:
            # No spread data — keep market, don't penalize missing data
            m["_spread"] = None
            filtered.append(m)
    _log_filter_step("spread", len(markets), len(filtered))
    markets = filtered

    # 6. Minimum 24h volume
    filtered = []
    for m in markets:
        volume = _get_volume_24h(m)
        if volume >= MIN_24H_VOLUME:
            filtered.append(m)
    _log_filter_step("min_24h_volume", len(markets), len(filtered))
    markets = filtered

    # 7. Not already at max position
    open_positions = db.get_open_positions()
    position_market_ids = {p["market_id"] for p in open_positions if p.get("size", 0) > 0}
    filtered = []
    for m in markets:
        condition_id = m.get("condition_id", "")
        if condition_id not in position_market_ids:
            filtered.append(m)
    _log_filter_step("max_position", len(markets), len(filtered))

    # 8. Sort by volume_24hr descending
    filtered.sort(key=lambda m: _get_volume_24h(m), reverse=True)

    logger.info(
        "Filter pipeline complete: %d → %d markets",
        initial_count, len(filtered),
    )
    return filtered


async def categorize_market(market: dict[str, Any], llm: LLMClient) -> str:
    """Classify a market question into a category.

    Tries deterministic keyword matching first (free, instant).
    Falls back to cheap LLM only if no keyword match is found.

    Returns one of: crypto, other.
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

    # Try keyword matching first (saves LLM call)
    if _is_crypto_keyword_match(question):
        category = "crypto"
        logger.debug("Keyword-classified '%s' → crypto", question[:80])
    else:
        # LLM fallback for non-obvious cases
        prompt = (
            'Classify this prediction market question into exactly one category.\n'
            f'Question: "{question}"\n'
            'Categories: crypto, politics, sports, science, economics, other\n'
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


async def batch_categorize_markets(
    markets: list[dict[str, Any]], llm: LLMClient, batch_size: int = 20
) -> None:
    """Categorize multiple markets, using keyword matching first then LLM fallback.

    Tries deterministic keyword matching for each market first.
    Only sends remaining unclassified markets to the LLM in batches.
    Modifies markets in-place, setting '_category' on each.
    """
    # Split into cached, keyword-matched, and needs-LLM
    uncached: list[dict[str, Any]] = []
    keyword_matched = 0
    for m in markets:
        condition_id = m.get("condition_id", "")
        if condition_id:
            cached = db.get_cached_market(condition_id)
            if cached and cached.get("category"):
                m["_category"] = cached["category"]
                continue

        # Try keyword matching before adding to uncached
        question = m.get("question", "")
        if question and _is_crypto_keyword_match(question):
            m["_category"] = "crypto"
            keyword_matched += 1
            # Cache the keyword classification
            if condition_id:
                db.cache_market(condition_id=condition_id, data=m, category="crypto")
            continue

        uncached.append(m)

    logger.info(
        "Batch categorize: %d markets (%d cached, %d keyword-matched, %d need LLM)",
        len(markets), len(markets) - len(uncached) - keyword_matched,
        keyword_matched, len(uncached),
    )

    # Process remaining in batches via LLM
    for batch_start in range(0, len(uncached), batch_size):
        batch = uncached[batch_start:batch_start + batch_size]

        # Build numbered list for the LLM
        lines = []
        for i, m in enumerate(batch, 1):
            lines.append(f'{i}. "{m.get("question", "unknown")}"')
        numbered_list = "\n".join(lines)

        prompt = (
            'Classify each prediction market question into exactly one category.\n'
            f'Categories: crypto, other\n\n'
            f'{numbered_list}\n\n'
            'Respond with ONLY a numbered list of categories, one per line, like:\n'
            '1. politics\n'
            '2. crypto\n'
            'No other text.'
        )

        try:
            response = await llm.call(prompt, task_type="classify")
            # Parse numbered response lines
            categories = _parse_batch_categories(response, len(batch))
        except Exception as e:
            logger.error("Batch categorize failed: %s", e)
            categories = ["other"] * len(batch)

        # Assign categories and cache
        for m, category in zip(batch, categories):
            m["_category"] = category
            condition_id = m.get("condition_id", "")
            if condition_id:
                db.cache_market(
                    condition_id=condition_id,
                    data=m,
                    category=category,
                )

        logger.info(
            "Batch categorized %d-%d of %d markets",
            batch_start + 1, batch_start + len(batch), len(uncached),
        )


def _parse_batch_categories(response: str, expected_count: int) -> list[str]:
    """Parse a numbered list of categories from a batch LLM response.

    Expects lines like '1. politics' or '1. politics\n2. crypto'.
    Returns a list of validated category strings.
    """
    import re

    categories: list[str] = []
    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Match patterns like "1. politics" or "1: politics" or just "politics"
        match = re.match(r'^\d+[\.\):\-]\s*(.+)$', line)
        if match:
            cat = match.group(1).strip().lower().replace(" ", "_")
        else:
            cat = line.strip().lower().replace(" ", "_")

        if cat in VALID_CATEGORIES:
            categories.append(cat)
        else:
            categories.append("other")

    # Pad or truncate to expected count
    while len(categories) < expected_count:
        categories.append("other")
    return categories[:expected_count]


async def extract_resolution_params(
    market_question: str,
    category: str,
    llm: LLMClient,
    condition_id: str = "",
) -> dict[str, Any] | None:
    """Extract structured resolution parameters for crypto markets.

    Only runs for 'crypto' category — returns None for all others.
    Results are cached in the market_cache data blob.
    """
    if category != "crypto":
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
        'Extract the key resolution parameters from this crypto market question.\n'
        'Identify: coin/token name, target price or metric, '
        'direction (above/below), target date.\n'
        '\n'
        'Also identify:\n'
        '- Any specific resolution methodology (e.g., specific exchange, TWAP, snapshot time)\n'
        '- Resolution type: "barrier" if the market resolves YES when price reaches/touches '
        'the target at ANY point before the deadline (e.g., "Will BTC hit $100k?", '
        '"Will ETH reach $5000 by June?"). Use "terminal" if the market resolves based on '
        'the price AT the specific deadline/expiry date (e.g., "Will BTC be above $100k '
        'on Dec 31?", "closing price on March 15"). Most crypto markets are "barrier" type.\n'
        '\n'
        'Respond as JSON only:\n'
        '{"indicator_type": "price", "metric_name": "...", "target_value": null, '
        '"target_direction": "above"|"below"|"other", '
        '"target_date": "YYYY-MM-DD or null", "coin_id": "coingecko_id or null", '
        '"resolution_source": "specific exchange/source mentioned or null", '
        '"resolution_type": "barrier"|"terminal"}'
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


def validate_resolution_params(market: dict[str, Any]) -> tuple[bool, str]:
    """Check if extracted resolution params are usable by our math models.

    Our barrier/terminal probability models require:
    - A valid CoinGecko coin_id
    - A numeric target_value (price target)
    - A direction of "above" or "below"

    Markets about crypto events (regulations, exchange actions, protocol
    upgrades, adoption metrics, etc.) don't have these and can't be modeled.

    Returns:
        (is_valid, reason) — True if we can compute probability, else False + reason.
    """
    params = market.get("_resolution_params")
    if not params:
        return False, "no resolution params extracted"

    coin_id = params.get("coin_id")
    if not coin_id:
        return False, "no coin_id — cannot fetch price data"

    target_value = params.get("target_value")
    if target_value is None:
        return False, "no target_value — no price target to model"

    # Validate target_value is numeric
    try:
        tv = float(target_value)
        if tv <= 0:
            return False, f"target_value={tv} is not a positive price"
    except (TypeError, ValueError):
        return False, f"target_value={target_value!r} is not numeric"

    direction = params.get("target_direction", "")
    if direction not in ("above", "below"):
        return False, f"target_direction={direction!r} — must be 'above' or 'below'"

    resolution_type = params.get("resolution_type", "")
    if resolution_type not in ("barrier", "terminal"):
        return False, f"resolution_type={resolution_type!r} — must be 'barrier' or 'terminal'"

    return True, "ok"


def filter_computable_markets(
    markets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove crypto markets that our math models cannot handle.

    This is the critical gate: only markets with a valid coin_id,
    numeric target_value, and above/below direction pass through.
    Markets about crypto events, regulations, adoption, etc. are dropped.
    """
    valid = []
    rejected = 0
    for m in markets:
        ok, reason = validate_resolution_params(m)
        if ok:
            valid.append(m)
        else:
            rejected += 1
            logger.debug(
                "Rejected '%s': %s",
                m.get("question", "")[:80], reason,
            )

    logger.info(
        "Computability filter: %d → %d markets (%d rejected as non-computable)",
        len(markets), len(valid), rejected,
    )
    return valid


async def pre_screen_crypto_edge(
    markets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pre-screen crypto markets with quick CoinGecko math to find edge potential.

    For each crypto market that has resolution params (coin_id, target_price),
    fetches CoinGecko data and computes the barrier/terminal model probability.
    Compares to market price to estimate edge BEFORE running the expensive
    frontier model. Markets with larger model-vs-market divergence are more
    likely to have real edges worth investigating.

    Attaches _model_edge (absolute divergence) to each market.
    Markets without computable edge get _model_edge = 0.
    """
    import aiohttp
    from signals.resolution_crypto import (
        _compute_volatility,
        _fetch_coingecko_chart,
        _fetch_coingecko_price,
        _fetch_deribit_iv,
        _select_volatility,
        _shrink_drift,
        barrier_probability,
        log_normal_probability,
        _coin_data_cache,
        COIN_DATA_CACHE_TTL,
    )
    import time as _time

    screened = 0
    edge_found = 0

    async with aiohttp.ClientSession() as session:
        for m in markets:
            m["_model_edge"] = 0.0
            m["_model_prob"] = None

            params = m.get("_resolution_params")
            if not params:
                continue

            coin_id = params.get("coin_id")
            target_value = params.get("target_value")
            target_direction = params.get("target_direction", "above")
            resolution_type = params.get("resolution_type", "barrier")

            if not coin_id or target_value is None:
                # Crypto market without clean price target — our math can't model this
                # Don't give baseline edge; these will be filtered out
                continue

            try:
                target_price = float(target_value)
            except (TypeError, ValueError):
                continue

            # Get market price
            market_price = 0.50
            for tok in m.get("tokens", []):
                if tok.get("outcome", "").upper() == "YES":
                    try:
                        market_price = float(tok.get("price", 0.50))
                    except (TypeError, ValueError):
                        pass
                    break

            # Use coin-level cache (shared with resolution_crypto provider)
            now = _time.monotonic()
            cached = _coin_data_cache.get(coin_id)
            if cached is not None:
                price_data, chart_data, deribit_iv, cached_at = cached
                if now - cached_at >= COIN_DATA_CACHE_TTL:
                    cached = None

            if cached is None:
                price_data = await _fetch_coingecko_price(session, coin_id)
                chart_data = await _fetch_coingecko_chart(session, coin_id, days=90)
                deribit_iv = await _fetch_deribit_iv(session, coin_id)
                _coin_data_cache[coin_id] = (price_data, chart_data, deribit_iv, now)
            else:
                price_data, chart_data, deribit_iv, _ = cached

            if not price_data:
                continue

            current_price = price_data.get("usd", 0.0)
            if current_price <= 0:
                continue

            # Compute vol
            vol_est = None
            if chart_data and len(chart_data) >= 2:
                vol_est = _compute_volatility(chart_data)

            if vol_est is None or vol_est.annual_vol <= 0:
                continue

            # Days remaining
            end_date = _parse_end_date(m)
            if not end_date:
                continue
            days_remaining = max(0, (end_date - datetime.now(timezone.utc)).total_seconds() / 86400)

            annual_vol, _ = _select_volatility(vol_est, deribit_iv, days_remaining)
            shrunk_drift = None
            if vol_est.realized_drift is not None:
                shrunk_drift = _shrink_drift(vol_est.realized_drift, vol_est.drift_stderr)

            # Compute model probability
            if resolution_type == "terminal":
                model_prob = log_normal_probability(
                    current_price, target_price, annual_vol,
                    days_remaining, target_direction, shrunk_drift,
                )
            else:
                model_prob = barrier_probability(
                    current_price, target_price, annual_vol,
                    days_remaining, target_direction, shrunk_drift,
                )

            edge = abs(model_prob - market_price)
            m["_model_edge"] = edge
            m["_model_prob"] = model_prob

            screened += 1
            if edge >= 0.03:
                edge_found += 1

            logger.debug(
                "Pre-screen %s: model=%.3f mkt=%.3f edge=%.3f",
                m.get("question", "")[:50], model_prob, market_price, edge,
            )

    logger.info(
        "Pre-screened %d crypto markets: %d with edge >= 3%%",
        screened, edge_found,
    )
    return markets


def rank_candidates(filtered_markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and rank markets by edge potential and desirability.

    Primary sort: _model_edge (pre-screened mathematical edge vs market price).
    Markets with higher model-vs-market divergence are ranked first because
    they're more likely to have real tradeable edges.

    Secondary scoring (tiebreakers):
    - Resolution in 1-4 weeks: +3 points
    - Resolution in 4-8 weeks: +1 point
    - Liquidity $1k-$10k: +2 points (mid-liquidity = more likely mispriced)
    - Liquidity $500-$1k: +1 point
    - Market price in tradeable range (0.15-0.85): +2 points
    - 24h volume > $500: +1 point
    - Ultra-high volume (> $50k/day): -1 point (efficiently priced)

    Returns sorted list (highest edge first, then score) with _score attached.
    """
    now = datetime.now(timezone.utc)
    scored: list[dict[str, Any]] = []

    for market in filtered_markets:
        score = 0

        # Time to resolution scoring — strongly prefer shorter markets
        time_score = 0
        end_date = _parse_end_date(market)
        if end_date:
            days_to_resolution = (end_date - now).total_seconds() / 86400
            if 3 <= days_to_resolution <= 7:
                time_score = 5
            elif 7 < days_to_resolution <= 14:
                time_score = 4
            elif 14 < days_to_resolution <= 21:
                time_score = 2
            elif 21 < days_to_resolution <= 30:
                time_score = 1
        score += time_score
        market["_time_score"] = time_score

        # Liquidity scoring — mid-liquidity markets are more likely mispriced
        liquidity = _get_liquidity(market)
        if 1000 <= liquidity <= 10000:
            score += 2
        elif 500 <= liquidity < 1000:
            score += 1

        # Price range scoring — markets near 0.50 have more room for edge
        prices = _get_outcome_prices(market)
        if prices:
            yes_price = prices[0]
            if 0.15 <= yes_price <= 0.85:
                score += 2

        # Volume scoring
        volume = _get_volume_24h(market)
        if volume > 500:
            score += 1
        # Ultra-high volume markets are efficiently priced — less edge opportunity
        if volume > 50000:
            score -= 1

        market["_score"] = score
        scored.append(market)

    # Sort: time_score first, then model_edge, then total score
    scored.sort(
        key=lambda m: (m.get("_time_score", 0), m.get("_model_edge", 0.0), m.get("_score", 0)),
        reverse=True,
    )

    logger.info(
        "Ranked %d candidates (top edge=%.3f/score=%d, bottom edge=%.3f/score=%d)",
        len(scored),
        scored[0].get("_model_edge", 0) if scored else 0,
        scored[0].get("_score", 0) if scored else 0,
        scored[-1].get("_model_edge", 0) if scored else 0,
        scored[-1].get("_score", 0) if scored else 0,
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


def _get_outcome_prices(market: dict[str, Any]) -> list[float]:
    """Extract outcome prices from token data.

    Returns list of floats for each outcome's price, or empty list if unavailable.
    """
    tokens = market.get("tokens", [])
    prices: list[float] = []
    for token in tokens:
        price = token.get("price")
        if price is not None:
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                continue
    return prices


def _get_spread(market: dict[str, Any]) -> float | None:
    """Extract spread from Gamma API data.

    Uses the spread field directly if available, otherwise computes from
    bestAsk - bestBid. Returns None if no spread data is available.
    Does NOT fall back to CLOB API (avoids 400 errors).
    """
    # Try Gamma spread field first
    gamma_spread = market.get("spread")
    if gamma_spread is not None:
        try:
            return float(gamma_spread)
        except (TypeError, ValueError):
            pass

    # Compute from bestAsk - bestBid
    best_ask = market.get("bestAsk")
    best_bid = market.get("bestBid")
    if best_ask is not None and best_bid is not None:
        try:
            return float(best_ask) - float(best_bid)
        except (TypeError, ValueError):
            pass

    return None


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
