"""On-chain flow signal provider.

Queries multiple free APIs to compute a directional capital-flow pressure
score from -1.0 (capital leaving crypto) to +1.0 (capital entering crypto).
Applies that pressure as a tilt on a market-specific baseline probability
computed from the target distance, time to expiry, and 14-day realized vol
(falls back to a flat 0.5 anchor when no target/price data is available).

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
import math
import statistics
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from core import coingecko_throttle, db, fetch_with_retry
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
COINGECKO_COIN_CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    "?vs_currency=usd&days=14&interval=daily"
)

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Confidence tiers by data availability — base values boosted by source count
_CONFIDENCE_TIERS: dict[str, float] = {
    "bitcoin": 0.40,
    "ethereum": 0.38,
}
_DEFAULT_CONFIDENCE = 0.30

# Source weights — retained for backward compat with raw_data emission;
# the composite blend below uses an agreement-weighted simple mean instead.
_SOURCE_WEIGHTS: dict[str, float] = {
    "stablecoin_flow": 0.25,
    "tvl_trend": 0.20,
    "fear_greed": 0.15,
    "global_market": 0.15,
    "coin_specific": 0.25,
}

# Maximum probability adjustment from flow signal (±18 pp with strong multi-source agreement)
MAX_ADJUSTMENT = 0.18

# tanh shaping constant — controls how quickly mid-range pressure reaches the cap.
# Normalized so pressure=±1.0 maps exactly to ±MAX_ADJUSTMENT.
_TANH_K = 2.0
_TANH_NORM = math.tanh(_TANH_K)


def _pressure_to_adjustment(pressure: float) -> float:
    """Convert pressure score [-1, +1] to probability adjustment.

    Uses a tanh-shaped mapping so weak pressure stays weak but moderate
    pressure reaches a meaningful fraction of the cap. Normalized so that
    pressure=±1.0 returns exactly ±MAX_ADJUSTMENT.
    """
    clamped = max(-1.0, min(1.0, pressure))
    return MAX_ADJUSTMENT * math.tanh(_TANH_K * clamped) / _TANH_NORM


def _baseline_probability(
    current_price: float,
    target_price: float,
    daily_vol: float,
    days_remaining: float,
    resolution_type: str,
    target_direction: str,
) -> float | None:
    """Crude market-specific baseline P(YES) from target distance and vol.

    Driftless normal approximation on log returns using the 14-day realized
    daily vol the coin fetcher already computed. Deliberately rougher than
    resolution_crypto's calibrated model (shorter vol window, no drift, no
    EWM/IV blending) — this is an independent anchor so the flow tilt is
    applied to THIS market's odds instead of a flat 0.5, which made every
    market get the same ~0.48 prediction (Brier 0.25 over 81 resolved).

    Returns None when inputs can't support an estimate (caller falls back).
    """
    if (
        current_price <= 0
        or target_price <= 0
        or daily_vol <= 0
        or days_remaining <= 0
    ):
        return None

    z = math.log(target_price / current_price) / (daily_vol * math.sqrt(days_remaining))
    phi = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))  # P(terminal price < target)

    if resolution_type == "terminal":
        p = (1.0 - phi) if target_direction != "below" else phi
    else:
        # Barrier (touch anytime): reflection principle ≈ doubles the
        # terminal tail probability on the target's side of spot.
        if target_price > current_price:
            p = 2.0 * (1.0 - phi)
        elif target_price < current_price:
            p = 2.0 * phi
        else:
            p = 1.0

    return max(0.02, min(0.98, p))


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

    # Real stablecoin supply variance is ~±0.5% weekly / ±1.5% monthly.
    # Original ±2%/±5% normalizers made this source contribute ~0 in practice.
    weekly_pressure = max(-1.0, min(1.0, weekly_change_pct / 0.005))
    monthly_pressure = max(-1.0, min(1.0, monthly_change_pct / 0.015))
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
        await coingecko_throttle()
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


async def _fetch_coin_specific(
    session: aiohttp.ClientSession,
    coin_id: str,
) -> tuple[float, dict[str, Any]] | None:
    """CoinGecko per-coin market chart — 7d price + volume momentum.

    Breaks the all-markets-identical-signal problem by giving each coin
    its own pressure component. Price weighted 0.7, volume weighted 0.3.
    """
    if not coin_id:
        return None

    url = COINGECKO_COIN_CHART_URL.format(coin_id=coin_id)

    async def _attempt() -> dict[str, Any]:
        await coingecko_throttle()
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label=f"CoinGecko coin {coin_id}")
    if not data:
        return None

    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    if len(prices) < 8 or len(volumes) < 8:
        return None

    # Last 7 daily points vs the prior 7
    recent_price = prices[-1][1]
    week_ago_price = prices[-8][1]
    if week_ago_price <= 0:
        return None
    price_change_7d = (recent_price - week_ago_price) / week_ago_price

    recent_vol = statistics.fmean([v[1] for v in volumes[-7:]])
    prior_vol = statistics.fmean([v[1] for v in volumes[-14:-7]])
    if prior_vol <= 0:
        return None
    vol_change_7d = (recent_vol - prior_vol) / prior_vol

    # ±10% weekly price = full pressure; ±50% volume change = full pressure
    price_pressure = max(-1.0, min(1.0, price_change_7d / 0.10))
    vol_pressure = max(-1.0, min(1.0, vol_change_7d / 0.50))
    pressure = price_pressure * 0.7 + vol_pressure * 0.3
    pressure = max(-1.0, min(1.0, pressure))

    # Realized daily vol from the same series — feeds the market-aware
    # baseline so each market's probability reflects its target distance.
    daily_vol = 0.0
    try:
        log_returns = [
            math.log(prices[i][1] / prices[i - 1][1])
            for i in range(1, len(prices))
            if prices[i][1] > 0 and prices[i - 1][1] > 0
        ]
        if len(log_returns) >= 5:
            daily_vol = statistics.stdev(log_returns)
    except (ValueError, ZeroDivisionError):
        pass

    metrics = {
        "source": "coingecko_coin",
        "pressure": pressure,
        "coin_id": coin_id,
        "price_change_7d_pct": round(price_change_7d * 100, 2),
        "volume_change_7d_pct": round(vol_change_7d * 100, 2),
        "current_price": recent_price,
        "daily_vol": daily_vol,
    }
    return pressure, metrics


# ---------------------------------------------------------------------------
# Composite flow data
# ---------------------------------------------------------------------------

_flow_fetch_lock = asyncio.Lock()

_GLOBAL_SOURCES_KEY = "__global_sources__"
_GLOBAL_SOURCE_NAMES = ["stablecoin_flow", "tvl_trend", "fear_greed", "global_market"]


async def _fetch_global_sources(
    session: aiohttp.ClientSession,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """Fetch the four coin-independent sources, cached once for all coins.

    These were previously re-fetched per coin_id, multiplying CoinGecko
    /global calls by the number of distinct coins in a cycle.
    """
    cached = _flow_cache.get(_GLOBAL_SOURCES_KEY)
    if cached is not None:
        cached_data, cached_at = cached
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            logger.debug("Global flow sources cache hit")
            return cached_data["pressures"], cached_data["metrics"]

    results = await asyncio.gather(
        _fetch_stablecoin_flow(session),
        _fetch_tvl_trend(session),
        _fetch_fear_greed(session),
        _fetch_coingecko_global(session),
        return_exceptions=True,
    )

    pressures: dict[str, float] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for name, result in zip(_GLOBAL_SOURCE_NAMES, results):
        if isinstance(result, Exception):
            logger.warning("Flow source %s failed: %s", name, result)
            continue
        if result is None:
            continue
        pressure, source_metrics = result
        pressures[name] = pressure
        metrics[name] = source_metrics

    # Don't cache a total blackout — retry next cycle instead of staying
    # blind for the full TTL.
    if pressures:
        _flow_cache[_GLOBAL_SOURCES_KEY] = (
            {"pressures": pressures, "metrics": metrics}, time.monotonic(),
        )
    return pressures, metrics


async def _fetch_flow_data(
    coin_id: str,
) -> tuple[float, dict[str, Any]]:
    """Fetch and compute composite flow pressure from all sources.

    Returns (pressure_score, raw_data_dict).
    Global market sources are cached once across all coins; only the
    per-coin chart is fetched per coin_id. A lock prevents concurrent
    cache misses from stampeding rate-limited APIs (especially CoinGecko
    free tier).
    """
    cache_key = f"composite::{coin_id}"

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

        # Global sources are shared across coins; only the coin chart is per-coin
        async with aiohttp.ClientSession() as session:
            global_pressures, global_metrics = await _fetch_global_sources(session)
            try:
                coin_result = await _fetch_coin_specific(session, coin_id)
            except Exception as exc:
                logger.warning("Flow source coin_specific failed: %s", exc)
                coin_result = None

        source_pressures: dict[str, float] = dict(global_pressures)
        source_metrics: dict[str, dict[str, Any]] = dict(global_metrics)
        if coin_result is not None:
            pressure, metrics = coin_result
            source_pressures["coin_specific"] = pressure
            source_metrics["coin_specific"] = metrics

        if not source_pressures:
            empty_metrics = {
                "data_source": "none",
                "asset": coin_id,
                "pressure_score": 0.0,
                "sources_available": 0,
                "error": "no_data_available",
            }
            return 0.0, empty_metrics

        # Agreement-weighted simple mean. The prior fixed-weight blend let weak
        # sources (stablecoin baseline ~0) drag strong sources (fear_greed,
        # tvl) toward zero. Replace with: simple mean × agreement multiplier
        # so coherent signals are amplified and split signals attenuated.
        ps = list(source_pressures.values())
        raw_mean = statistics.fmean(ps)

        signs = [1 if p > 0.05 else (-1 if p < -0.05 else 0) for p in ps]
        non_neutral = [s for s in signs if s != 0]
        agreement = abs(sum(non_neutral)) / len(non_neutral) if non_neutral else 0.0

        # 0.7x when sources split, 1.4x when fully aligned
        composite_pressure = raw_mean * (0.7 + 0.7 * agreement)
        composite_pressure = max(-1.0, min(1.0, composite_pressure))

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
            "coin_price_change_7d_pct": source_metrics.get("coin_specific", {}).get("price_change_7d_pct"),
            "coin_volume_change_7d_pct": source_metrics.get("coin_specific", {}).get("volume_change_7d_pct"),
            "coin_current_price": source_metrics.get("coin_specific", {}).get("current_price"),
            "coin_daily_vol": source_metrics.get("coin_specific", {}).get("daily_vol"),
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
    6. Tilt the market-specific baseline (target distance / expiry / vol)
       by the flow adjustment (±18pp max); flat 0.5 anchor as fallback
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

        resolution_keywords = kwargs.get("resolution_keywords") or {}

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

        # More sources = more confidence (up to +40% boost at 5 sources)
        source_bonus = (sources_available - 1) * 0.08  # +8% per extra source
        # High agreement = more confidence (up to +15% boost)
        agreement_bonus = agreement * 0.15
        raw_conf = base_confidence + source_bonus + agreement_bonus

        data_points = raw_metrics.get("stablecoins_tracked", 0) + sources_available

        # Convert pressure to probability adjustment
        adjustment = _pressure_to_adjustment(pressure)

        # Market-aware baseline: anchor on THIS market's odds (target distance,
        # time to expiry, realized vol) instead of a flat 0.5. The flat anchor
        # made every market get the same ~0.48 prediction regardless of
        # question, direction, or deadline — pure noise per market.
        target_direction = resolution_keywords.get("target_direction", "above")
        resolution_type = resolution_keywords.get("resolution_type", "barrier")
        target_value = resolution_keywords.get("target_value")
        current_price = raw_metrics.get("coin_current_price") or 0.0
        daily_vol = raw_metrics.get("coin_daily_vol") or 0.0

        days_remaining = 0.0
        if market_end_date:
            try:
                from datetime import datetime, timezone
                end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                days_remaining = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
            except (ValueError, TypeError):
                pass

        baseline = None
        if target_value is not None:
            try:
                baseline = _baseline_probability(
                    current_price=float(current_price),
                    target_price=float(target_value),
                    daily_vol=float(daily_vol),
                    days_remaining=days_remaining,
                    resolution_type=resolution_type,
                    target_direction=target_direction,
                )
            except (ValueError, TypeError):
                baseline = None

        strength = min(1.0, abs(pressure) * 2.0)
        if baseline is not None:
            # Tilt the market-specific baseline by flow pressure. Positive
            # pressure (capital entering crypto) favors upside events.
            if resolution_type == "terminal":
                upside_event = target_direction != "below"
            else:
                upside_event = float(target_value) > float(current_price)
            probability = baseline + (adjustment if upside_event else -adjustment)
            # The baseline carries real information even at zero pressure —
            # only mild attenuation for weak flow.
            confidence = min(0.80, raw_conf * (0.75 + 0.25 * strength))
        else:
            # Fallback (no target/price/vol data, event markets): legacy
            # flat-anchor behavior. Attenuate hard when pressure is weak —
            # without a baseline, a near-zero tilt carries no information.
            probability = 0.5 + (adjustment if target_direction != "below" else -adjustment)
            confidence = min(0.80, raw_conf * (0.5 + 0.5 * strength))

        probability = max(0.02, min(0.98, probability))

        # Build reasoning
        weekly_chg = raw_metrics.get("weekly_change_pct", 0.0)
        monthly_chg = raw_metrics.get("monthly_change_pct", 0.0)
        supply = raw_metrics.get("total_stablecoin_supply", 0)
        fg_value = raw_metrics.get("fear_greed_value")
        fg_label = raw_metrics.get("fear_greed_label", "")
        tvl_chg = raw_metrics.get("tvl_weekly_change_pct")
        mcap_chg = raw_metrics.get("market_cap_change_24h_pct")

        if baseline is not None:
            anchor_desc = (
                f"baseline={baseline:.3f} (target ${float(target_value):,.0f} vs "
                f"${float(current_price):,.0f}, {resolution_type}, {days_remaining:.1f}d, "
                f"vol={daily_vol:.1%}/d)"
            )
        else:
            anchor_desc = "baseline=0.500 (no market-specific data)"
        parts = [
            f"Composite flow ({sources_available} sources, agreement={agreement:.0%}): "
            f"pressure={pressure:+.2f}, {anchor_desc}, "
            f"adjustment={adjustment:+.3f} -> P={probability:.3f}.",
        ]
        if supply:
            parts.append(f"Stablecoins: ${supply/1e9:.1f}B (weekly={weekly_chg:+.1f}%, monthly={monthly_chg:+.1f}%).")
        if fg_value is not None:
            parts.append(f"Fear&Greed: {fg_value}/100 ({fg_label}).")
        if tvl_chg is not None:
            parts.append(f"DeFi TVL: weekly={tvl_chg:+.1f}%.")
        if mcap_chg is not None:
            parts.append(f"Global mcap: 24h={mcap_chg:+.1f}%.")
        coin_price = raw_metrics.get("coin_price_change_7d_pct")
        coin_vol = raw_metrics.get("coin_volume_change_7d_pct")
        if coin_price is not None:
            parts.append(f"{coin_id} 7d: price={coin_price:+.1f}%, vol={coin_vol:+.1f}%.")

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
