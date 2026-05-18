"""On-chain flow signal provider.

Queries multiple free APIs to compute a directional capital-flow pressure
score from -1.0 (capital leaving crypto) to +1.0 (capital entering crypto).
Converts that pressure into a probability adjustment relative to the math
model's baseline.

Data sources (all free, no auth required):
1. DeFi Llama stablecoin supply — weekly/monthly stablecoin mint/burn
2. DeFi Llama TVL — total value locked trend across DeFi protocols
3. Alternative.me Fear & Greed Index — composite crypto sentiment (0-100)
4. CoinGecko global market data — market cap & volume change trends

Confidence scales with how many sources return data and whether they agree.
For tokens without crypto category the provider returns confidence=0,
causing graceful fallback to the remaining signals.

No LLM calls — pure data pipeline.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from core import db, fetch_with_retry
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: key -> (data, timestamp)
_flow_cache: dict[str, tuple[dict[str, Any], float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

# API endpoints (all free, no auth)
DEFILLAMA_STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
DEFILLAMA_TVL_URL = "https://api.llama.fi/v2/historicalChainTvl"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=2"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Confidence tiers by data availability — base values boosted by source count
_CONFIDENCE_TIERS: dict[str, float] = {
    "bitcoin": 0.40,
    "ethereum": 0.38,
}
_DEFAULT_CONFIDENCE = 0.30

# Source weights in the composite pressure blend
_SOURCE_WEIGHTS: dict[str, float] = {
    "stablecoin_flow": 0.35,  # Stablecoin mint/burn is the strongest signal
    "tvl_trend": 0.25,        # DeFi TVL shows capital commitment
    "fear_greed": 0.20,       # Sentiment composite — useful but noisy
    "global_market": 0.20,    # CoinGecko market cap/volume trends
}

# Maximum probability adjustment from flow signal (±10 pp with multi-source)
MAX_ADJUSTMENT = 0.10


def _pressure_to_adjustment(pressure: float) -> float:
    """Convert pressure score [-1, +1] to probability adjustment.

    A +1.0 pressure score adjusts by at most +MAX_ADJUSTMENT toward
    the target outcome. Uses a linear mapping capped at ±MAX_ADJUSTMENT.
    """
    clamped = max(-1.0, min(1.0, pressure))
    return clamped * MAX_ADJUSTMENT


# ---------------------------------------------------------------------------
# Data source fetchers — each returns (pressure, metrics_dict) or None
# ---------------------------------------------------------------------------

async def _fetch_stablecoin_flow(
    session: aiohttp.ClientSession,
) -> tuple[float, dict[str, Any]] | None:
    """DeFi Llama stablecoin supply flow — weekly/monthly mint/burn."""

    async def _attempt() -> dict[str, Any]:
        async with session.get(
            DEFILLAMA_STABLECOINS_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label="DeFi Llama stablecoins")
    if data is None:
        return None

    assets = data.get("peggedAssets", [])
    if not assets:
        return None

    total_now = 0.0
    total_prev_week = 0.0
    total_prev_month = 0.0
    counted = 0

    for asset in assets[:10]:
        circ = asset.get("circulating", {}).get("peggedUSD", 0)
        prev_week = asset.get("circulatingPrevWeek", {}).get("peggedUSD", 0)
        prev_month = asset.get("circulatingPrevMonth", {}).get("peggedUSD", 0)

        if circ and prev_week:
            total_now += circ
            total_prev_week += prev_week
            total_prev_month += prev_month if prev_month else prev_week
            counted += 1

    if counted == 0 or total_prev_week == 0:
        return None

    weekly_change_pct = (total_now - total_prev_week) / total_prev_week
    monthly_change_pct = (total_now - total_prev_month) / total_prev_month if total_prev_month > 0 else 0.0

    # +2% weekly = strong bullish, -2% = strong bearish
    weekly_pressure = max(-1.0, min(1.0, weekly_change_pct / 0.02))
    monthly_pressure = max(-1.0, min(1.0, monthly_change_pct / 0.05))
    pressure = weekly_pressure * 0.7 + monthly_pressure * 0.3
    pressure = max(-1.0, min(1.0, pressure))

    metrics = {
        "source": "defillama_stablecoins",
        "pressure": pressure,
        "total_supply": total_now,
        "weekly_change_pct": round(weekly_change_pct * 100, 3),
        "monthly_change_pct": round(monthly_change_pct * 100, 3),
        "stablecoins_tracked": counted,
    }
    return pressure, metrics


async def _fetch_tvl_trend(
    session: aiohttp.ClientSession,
) -> tuple[float, dict[str, Any]] | None:
    """DeFi Llama historical TVL — compare recent vs prior period."""

    async def _attempt() -> list[dict[str, Any]]:
        async with session.get(
            DEFILLAMA_TVL_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label="DeFi Llama TVL")
    if not data or len(data) < 14:
        return None

    # Each entry: {"date": unix_ts, "tvl": float}
    # Compare last 7 days avg to prior 7 days avg
    recent_7 = [d["tvl"] for d in data[-7:] if "tvl" in d]
    prior_7 = [d["tvl"] for d in data[-14:-7] if "tvl" in d]

    if not recent_7 or not prior_7:
        return None

    avg_recent = sum(recent_7) / len(recent_7)
    avg_prior = sum(prior_7) / len(prior_7)

    if avg_prior == 0:
        return None

    weekly_tvl_change = (avg_recent - avg_prior) / avg_prior

    # ±5% weekly TVL change = full pressure
    pressure = max(-1.0, min(1.0, weekly_tvl_change / 0.05))

    metrics = {
        "source": "defillama_tvl",
        "pressure": pressure,
        "current_tvl": avg_recent,
        "weekly_tvl_change_pct": round(weekly_tvl_change * 100, 3),
    }
    return pressure, metrics


async def _fetch_fear_greed(
    session: aiohttp.ClientSession,
) -> tuple[float, dict[str, Any]] | None:
    """Alternative.me Crypto Fear & Greed Index (0=extreme fear, 100=extreme greed)."""

    async def _attempt() -> dict[str, Any]:
        async with session.get(
            FEAR_GREED_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label="Fear & Greed Index")
    if not data:
        return None

    entries = data.get("data", [])
    if not entries:
        return None

    value = int(entries[0].get("value", 50))
    label = entries[0].get("value_classification", "Neutral")

    # Map 0-100 to pressure [-1, +1]: 50 = neutral, 0 = -1, 100 = +1
    pressure = (value - 50) / 50.0
    pressure = max(-1.0, min(1.0, pressure))

    # Previous day for trend
    prev_value = int(entries[1]["value"]) if len(entries) > 1 else value
    trend = value - prev_value  # positive = sentiment improving

    metrics = {
        "source": "fear_greed_index",
        "pressure": pressure,
        "value": value,
        "label": label,
        "previous_value": prev_value,
        "daily_trend": trend,
    }
    return pressure, metrics


async def _fetch_coingecko_global(
    session: aiohttp.ClientSession,
) -> tuple[float, dict[str, Any]] | None:
    """CoinGecko global market data — market cap and volume change %."""

    async def _attempt() -> dict[str, Any]:
        async with session.get(
            COINGECKO_GLOBAL_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label="CoinGecko global")
    if not data:
        return None

    gdata = data.get("data", {})
    if not gdata:
        return None

    mcap_change_24h = gdata.get("market_cap_change_percentage_24h_usd", 0.0)
    total_mcap = gdata.get("total_market_cap", {}).get("usd", 0)
    total_vol = gdata.get("total_volume", {}).get("usd", 0)
    btc_dominance = gdata.get("market_cap_percentage", {}).get("btc", 0)

    # ±5% daily mcap change = full pressure
    pressure = max(-1.0, min(1.0, mcap_change_24h / 5.0))

    metrics = {
        "source": "coingecko_global",
        "pressure": pressure,
        "market_cap_change_24h_pct": round(mcap_change_24h, 2),
        "total_market_cap": total_mcap,
        "total_volume_24h": total_vol,
        "btc_dominance": round(btc_dominance, 1),
    }
    return pressure, metrics


# ---------------------------------------------------------------------------
# Composite flow data
# ---------------------------------------------------------------------------

_flow_fetch_lock = asyncio.Lock()


async def _fetch_flow_data(
    coin_id: str,
) -> tuple[float, dict[str, Any]]:
    """Fetch and compute composite flow pressure from all sources.

    Returns (pressure_score, raw_data_dict).
    All four sources return global market data (not coin-specific), so we
    cache under a single key and use a lock to prevent concurrent fetches
    from stampeding rate-limited APIs (especially CoinGecko free tier).
    """
    cache_key = "composite_global"

    now = time.monotonic()
    cached = _flow_cache.get(cache_key)
    if cached is not None:
        cached_data, cached_at = cached
        if now - cached_at < CACHE_TTL_SECONDS:
            logger.debug("Flow cache hit for %s", coin_id)
            return cached_data.get("pressure_score", 0.0), cached_data

    # Lock prevents concurrent cache misses from hitting APIs in parallel
    async with _flow_fetch_lock:
        # Re-check cache after acquiring lock (another coroutine may have filled it)
        cached = _flow_cache.get(cache_key)
        if cached is not None:
            cached_data, cached_at = cached
            if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
                logger.debug("Flow cache hit (post-lock) for %s", coin_id)
                return cached_data.get("pressure_score", 0.0), cached_data

        # Fetch all sources concurrently
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                _fetch_stablecoin_flow(session),
                _fetch_tvl_trend(session),
                _fetch_fear_greed(session),
                _fetch_coingecko_global(session),
                return_exceptions=True,
            )

        source_pressures: dict[str, float] = {}
        source_metrics: dict[str, dict[str, Any]] = {}
        source_names = ["stablecoin_flow", "tvl_trend", "fear_greed", "global_market"]

        for name, result in zip(source_names, results):
            if isinstance(result, Exception):
                logger.warning("Flow source %s failed: %s", name, result)
                continue
            if result is None:
                continue
            pressure, metrics = result
            source_pressures[name] = pressure
            source_metrics[name] = metrics

        if not source_pressures:
            empty_metrics = {
                "data_source": "none",
                "asset": coin_id,
                "pressure_score": 0.0,
                "sources_available": 0,
                "error": "no_data_available",
            }
            return 0.0, empty_metrics

        # Weighted composite pressure
        total_weight = 0.0
        weighted_sum = 0.0
        for name, p in source_pressures.items():
            w = _SOURCE_WEIGHTS.get(name, 0.2)
            weighted_sum += p * w
            total_weight += w

        composite_pressure = weighted_sum / total_weight if total_weight > 0 else 0.0
        composite_pressure = max(-1.0, min(1.0, composite_pressure))

        # Agreement metric: how much do sources agree in direction?
        # If all sources point the same way, agreement = 1.0
        signs = [1 if p > 0.05 else (-1 if p < -0.05 else 0) for p in source_pressures.values()]
        non_neutral = [s for s in signs if s != 0]
        if non_neutral:
            agreement = abs(sum(non_neutral)) / len(non_neutral)
        else:
            agreement = 0.0

        # Build composite raw_data
        stablecoin = source_metrics.get("stablecoin_flow", {})
        composite_metrics = {
            "data_source": "composite",
            "asset": coin_id,
            "pressure_score": composite_pressure,
            "sources_available": len(source_pressures),
            "source_agreement": round(agreement, 2),
            "source_pressures": {k: round(v, 3) for k, v in source_pressures.items()},
            # Preserve top-level fields for backward compat with aggregator formatter
            "total_stablecoin_supply": stablecoin.get("total_supply", 0),
            "weekly_change_pct": stablecoin.get("weekly_change_pct", 0.0),
            "monthly_change_pct": stablecoin.get("monthly_change_pct", 0.0),
            "stablecoins_tracked": stablecoin.get("stablecoins_tracked", 0),
            # New source details
            "fear_greed_value": source_metrics.get("fear_greed", {}).get("value"),
            "fear_greed_label": source_metrics.get("fear_greed", {}).get("label"),
            "tvl_weekly_change_pct": source_metrics.get("tvl_trend", {}).get("weekly_tvl_change_pct"),
            "current_tvl": source_metrics.get("tvl_trend", {}).get("current_tvl"),
            "market_cap_change_24h_pct": source_metrics.get("global_market", {}).get("market_cap_change_24h_pct"),
            "btc_dominance": source_metrics.get("global_market", {}).get("btc_dominance"),
            "total_market_cap": source_metrics.get("global_market", {}).get("total_market_cap"),
        }

        _flow_cache[cache_key] = (composite_metrics, time.monotonic())
        return composite_pressure, composite_metrics


class OnchainFlowProvider(SignalProvider):
    """On-chain flow signal provider using multiple free data sources.

    Pipeline:
    1. If category != crypto -> return confidence=0 immediately
    2. Resolve coin_id from resolution_keywords or question text
    3. Fetch data concurrently from 4 sources:
       - DeFi Llama stablecoin supply (mint/burn trends)
       - DeFi Llama TVL (total value locked trends)
       - Alternative.me Fear & Greed Index
       - CoinGecko global market data
    4. Blend source pressures into composite score [-1, +1]
    5. Scale confidence by source count and agreement
    6. Apply probability adjustment (±10pp max)
    7. Return with rich raw data for frontier model

    No LLM calls — pure data pipeline.
    """

    name: str = "onchain_flow"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: Any = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._on_progress = on_progress

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        if self._on_progress:
            try:
                self._on_progress(question, stage, detail)
            except Exception:
                pass

    async def get_signal(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        **kwargs: Any,
    ) -> SignalResult:
        """Produce an on-chain flow signal for a crypto market."""
        if market_category.lower() != "crypto":
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' is not crypto",
                model_used="none",
                data_points=0,
            )

        resolution_keywords = kwargs.get("resolution_keywords", {})

        coin_id = resolution_keywords.get("coin_id", "")
        if not coin_id:
            from signals.resolution_crypto import TICKER_TO_COINGECKO
            q_lower = market_question.lower()
            for ticker, cg_id in TICKER_TO_COINGECKO.items():
                if ticker in q_lower.split() or ticker in q_lower:
                    coin_id = cg_id
                    break

        if not coin_id:
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning="Could not determine coin for on-chain flow analysis",
                model_used="none",
                data_points=0,
            )

        self._emit(market_question, "onchain", f"fetching multi-source flow data for {coin_id}")

        try:
            pressure, raw_metrics = await _fetch_flow_data(coin_id)
        except Exception as e:
            logger.error("On-chain flow fetch failed for %s: %s", coin_id, e)
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning=f"Flow data fetch failed: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        # Determine base confidence from asset tier
        base_confidence = _CONFIDENCE_TIERS.get(coin_id, _DEFAULT_CONFIDENCE)

        data_source = raw_metrics.get("data_source", "none")
        if data_source == "none":
            base_confidence = 0.0

        if base_confidence == 0.0 or raw_metrics.get("error"):
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning=f"Insufficient on-chain data for {coin_id}: {raw_metrics.get('error', 'no data')}",
                model_used="none",
                data_points=0,
                raw_data=raw_metrics,
            )

        # Scale confidence by number of sources and their agreement
        sources_available = raw_metrics.get("sources_available", 1)
        agreement = raw_metrics.get("source_agreement", 0.0)

        # More sources = more confidence (up to +40% boost at 4 sources)
        source_bonus = (sources_available - 1) * 0.10  # +10% per extra source
        # High agreement = more confidence (up to +15% boost)
        agreement_bonus = agreement * 0.15
        confidence = min(0.65, base_confidence + source_bonus + agreement_bonus)

        data_points = raw_metrics.get("stablecoins_tracked", 0) + sources_available

        # Convert pressure to probability adjustment
        adjustment = _pressure_to_adjustment(pressure)

        probability = 0.5 + adjustment

        target_direction = resolution_keywords.get("target_direction", "above")
        if target_direction == "below":
            probability = 0.5 - adjustment

        probability = max(0.02, min(0.98, probability))

        # Build reasoning
        weekly_chg = raw_metrics.get("weekly_change_pct", 0.0)
        monthly_chg = raw_metrics.get("monthly_change_pct", 0.0)
        supply = raw_metrics.get("total_stablecoin_supply", 0)
        fg_value = raw_metrics.get("fear_greed_value")
        fg_label = raw_metrics.get("fear_greed_label", "")
        tvl_chg = raw_metrics.get("tvl_weekly_change_pct")
        mcap_chg = raw_metrics.get("market_cap_change_24h_pct")

        parts = [
            f"Composite flow ({sources_available} sources, agreement={agreement:.0%}): "
            f"pressure={pressure:+.2f}, adjustment={adjustment:+.3f} -> P={probability:.3f}.",
        ]
        if supply:
            parts.append(f"Stablecoins: ${supply/1e9:.1f}B (weekly={weekly_chg:+.1f}%, monthly={monthly_chg:+.1f}%).")
        if fg_value is not None:
            parts.append(f"Fear&Greed: {fg_value}/100 ({fg_label}).")
        if tvl_chg is not None:
            parts.append(f"DeFi TVL: weekly={tvl_chg:+.1f}%.")
        if mcap_chg is not None:
            parts.append(f"Global mcap: 24h={mcap_chg:+.1f}%.")

        reasoning = " ".join(parts)

        self._emit(market_question, "done", f"pressure={pressure:+.2f} ({sources_available} sources)")

        result = SignalResult(
            source="onchain_flow",
            probability=probability,
            confidence=confidence,
            reasoning=reasoning,
            model_used="none",
            data_points=data_points,
            raw_data=raw_metrics,
        )

        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source="onchain_flow",
                probability=probability,
                confidence=confidence,
                reasoning=reasoning[:1000],
                model_used="none",
            )
        except Exception as e:
            logger.warning("Failed to log onchain_flow signal to DB: %s", e)

        return result


def clear_flow_cache() -> None:
    """Clear the in-memory flow data cache."""
    _flow_cache.clear()
