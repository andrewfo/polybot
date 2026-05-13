"""On-chain flow signal provider.

Queries CryptoQuant's free-tier API for exchange net flow and whale
transaction data. Computes a directional pressure score from -1.0
(strong sell pressure) to +1.0 (strong buy pressure) based on z-scored
7-day net flow relative to the 30-day rolling average. Converts that
pressure into a probability adjustment relative to the math model's
baseline.

Confidence scales with data availability: high for BTC/ETH (deep
on-chain data), moderate for top-50 alts, zero for tokens without
coverage — causing graceful fallback to the remaining signals.

No LLM calls — pure data pipeline.
"""

import logging
import math
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from core import db
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: coin_id -> (flow_data, timestamp)
_flow_cache: dict[str, tuple[dict[str, Any], float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

CRYPTOQUANT_BASE_URL = "https://api.cryptoquant.com/v1"
BLOCKCHAIN_COM_BASE_URL = "https://api.blockchain.info"

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# CoinGecko ID -> CryptoQuant asset symbol mapping
# CryptoQuant free tier covers BTC and ETH; pro tier adds more.
_COINGECKO_TO_CRYPTOQUANT: dict[str, str] = {
    "bitcoin": "btc",
    "ethereum": "eth",
}

# Confidence tiers by data availability
_CONFIDENCE_TIERS: dict[str, float] = {
    "btc": 0.55,   # Deep on-chain data
    "eth": 0.50,   # Good on-chain data
}
_DEFAULT_CONFIDENCE = 0.0  # No coverage -> zero confidence -> graceful skip

# Maximum probability adjustment from flow signal (±8 percentage points)
MAX_ADJUSTMENT = 0.08


def _z_score(value: float, mean: float, std: float) -> float:
    """Compute z-score, returning 0.0 if std is too small."""
    if std < 1e-9:
        return 0.0
    return (value - mean) / std


def _pressure_to_adjustment(pressure: float) -> float:
    """Convert pressure score [-1, +1] to probability adjustment.

    A +1.0 pressure score adjusts by at most +MAX_ADJUSTMENT toward
    the target outcome. A -1.0 score adjusts by at most -MAX_ADJUSTMENT.
    Uses a linear mapping capped at ±MAX_ADJUSTMENT.
    """
    clamped = max(-1.0, min(1.0, pressure))
    return clamped * MAX_ADJUSTMENT


async def _fetch_cryptoquant_exchange_flow(
    session: aiohttp.ClientSession,
    asset: str,
    window: str = "day",
    limit: int = 30,
) -> list[dict[str, Any]] | None:
    """Fetch exchange net flow data from CryptoQuant free API.

    Returns list of daily flow records with 'netflow' (positive = inflow
    to exchanges = sell pressure, negative = outflow = accumulation).
    """
    url = f"{CRYPTOQUANT_BASE_URL}/btc/exchange-flows/netflow"
    if asset == "eth":
        url = f"{CRYPTOQUANT_BASE_URL}/eth/exchange-flows/netflow"

    params = {"window": window, "limit": str(limit)}
    try:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 403:
                logger.debug("CryptoQuant returned 403 for %s (may need API key)", asset)
                return None
            if resp.status != 200:
                logger.warning("CryptoQuant netflow returned %d for %s", resp.status, asset)
                return None
            data = await resp.json()
        # CryptoQuant response: {"status": {...}, "result": {"data": [...]}}
        result = data.get("result", {})
        return result.get("data", [])
    except Exception as e:
        logger.warning("Error fetching CryptoQuant netflow for %s: %s", asset, e)
        return None


async def _fetch_cryptoquant_whale_count(
    session: aiohttp.ClientSession,
    asset: str,
    limit: int = 7,
) -> list[dict[str, Any]] | None:
    """Fetch whale transaction count (transfers > $1M) from CryptoQuant."""
    url = f"{CRYPTOQUANT_BASE_URL}/{asset}/network-data/transactions-count-over-1m"
    params = {"window": "day", "limit": str(limit)}
    try:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.debug("CryptoQuant whale count returned %d for %s", resp.status, asset)
                return None
            data = await resp.json()
        result = data.get("result", {})
        return result.get("data", [])
    except Exception as e:
        logger.debug("Error fetching CryptoQuant whale count for %s: %s", asset, e)
        return None


async def _fetch_blockchain_com_btc_flow(
    session: aiohttp.ClientSession,
) -> dict[str, Any] | None:
    """Fallback: fetch BTC mempool and exchange stats from Blockchain.com.

    Free endpoint, no auth required. Used as fallback when CryptoQuant
    is unavailable.
    """
    try:
        async with session.get(
            f"{BLOCKCHAIN_COM_BASE_URL}/stats",
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("Blockchain.com stats fetch failed: %s", e)
        return None


def _compute_pressure_from_netflow(
    flow_data: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Compute directional pressure score from exchange net flow data.

    Z-scores the 7-day average net flow against the 30-day rolling average.
    Positive net flow = coins moving TO exchanges = sell pressure → negative score.
    Negative net flow = coins moving OFF exchanges = accumulation → positive score.

    Returns (pressure_score, raw_metrics).
    """
    if not flow_data or len(flow_data) < 7:
        return 0.0, {"error": "insufficient_data", "records": len(flow_data) if flow_data else 0}

    # Extract netflow values (handle various CryptoQuant response formats)
    values: list[float] = []
    for record in flow_data:
        nf = record.get("netflow") or record.get("value") or record.get("net_flow")
        if nf is not None:
            try:
                values.append(float(nf))
            except (ValueError, TypeError):
                continue

    if len(values) < 7:
        return 0.0, {"error": "insufficient_numeric_data", "parsed": len(values)}

    # 30-day stats (or all available data)
    all_values = values
    mean_30d = sum(all_values) / len(all_values)
    variance_30d = sum((v - mean_30d) ** 2 for v in all_values) / max(len(all_values) - 1, 1)
    std_30d = math.sqrt(variance_30d)

    # 7-day average
    recent_7d = values[-7:]
    mean_7d = sum(recent_7d) / len(recent_7d)

    # Z-score: how unusual is recent flow compared to the 30-day baseline
    z = _z_score(mean_7d, mean_30d, std_30d)

    # Invert: positive netflow (inflow to exchanges) = sell pressure = negative score
    # Clamp z-score to [-3, +3] then normalize to [-1, +1]
    z_clamped = max(-3.0, min(3.0, z))
    pressure = -(z_clamped / 3.0)  # Invert and normalize

    raw_metrics = {
        "mean_7d_netflow": mean_7d,
        "mean_30d_netflow": mean_30d,
        "std_30d_netflow": std_30d,
        "z_score": z,
        "pressure_score": pressure,
        "net_flow_direction": "outflow (accumulation)" if mean_7d < 0 else "inflow (sell pressure)",
        "data_points_30d": len(all_values),
        "data_points_7d": len(recent_7d),
    }

    return pressure, raw_metrics


def _compute_whale_metric(
    whale_data: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Extract whale transaction count summary."""
    if not whale_data:
        return {"whale_tx_count": None, "whale_data_available": False}

    counts: list[float] = []
    for record in whale_data:
        c = record.get("transactions_count_over_1m") or record.get("value") or record.get("count")
        if c is not None:
            try:
                counts.append(float(c))
            except (ValueError, TypeError):
                continue

    if not counts:
        return {"whale_tx_count": None, "whale_data_available": False}

    recent = counts[-1] if counts else 0
    avg = sum(counts) / len(counts) if counts else 0

    return {
        "whale_tx_count": int(recent),
        "whale_tx_avg_7d": round(avg, 1),
        "whale_trend": "elevated" if recent > avg * 1.2 else "normal" if recent > avg * 0.8 else "subdued",
        "whale_data_available": True,
    }


async def _fetch_flow_data(
    asset: str,
) -> tuple[float, dict[str, Any]]:
    """Fetch and compute flow pressure for an asset.

    Tries CryptoQuant first, falls back to Blockchain.com for BTC.
    Returns (pressure_score, raw_data_dict).
    """
    # Check cache
    now = time.monotonic()
    cached = _flow_cache.get(asset)
    if cached is not None:
        cached_data, cached_at = cached
        if now - cached_at < CACHE_TTL_SECONDS:
            logger.debug("Flow cache hit for %s", asset)
            return cached_data.get("pressure_score", 0.0), cached_data

    async with aiohttp.ClientSession() as session:
        # Try CryptoQuant
        flow_records = await _fetch_cryptoquant_exchange_flow(session, asset)
        whale_data = await _fetch_cryptoquant_whale_count(session, asset)

        if flow_records and len(flow_records) >= 7:
            pressure, metrics = _compute_pressure_from_netflow(flow_records)
            whale_metrics = _compute_whale_metric(whale_data)
            metrics.update(whale_metrics)
            metrics["data_source"] = "cryptoquant"
            metrics["asset"] = asset
            metrics["pressure_score"] = pressure

            _flow_cache[asset] = (metrics, now)
            return pressure, metrics

        # Fallback: Blockchain.com (BTC only)
        if asset == "btc":
            bc_stats = await _fetch_blockchain_com_btc_flow(session)
            if bc_stats:
                # Use mempool size and hash rate as rough proxy signals
                # Not as good as exchange flow but provides some signal
                n_tx = bc_stats.get("n_tx", 0)
                mempool_size = bc_stats.get("mempool_size", 0)
                metrics = {
                    "data_source": "blockchain_com_fallback",
                    "asset": asset,
                    "mempool_tx_count": n_tx,
                    "mempool_size": mempool_size,
                    "pressure_score": 0.0,  # Can't reliably compute pressure from mempool alone
                    "note": "Fallback data source — limited directional signal",
                    "whale_tx_count": None,
                    "whale_data_available": False,
                }
                _flow_cache[asset] = (metrics, now)
                return 0.0, metrics

    # No data available
    metrics = {
        "data_source": "none",
        "asset": asset,
        "pressure_score": 0.0,
        "error": "no_data_available",
        "whale_tx_count": None,
        "whale_data_available": False,
    }
    return 0.0, metrics


class OnchainFlowProvider(SignalProvider):
    """On-chain exchange flow signal provider.

    Pipeline:
    1. If category != crypto -> return confidence=0 immediately
    2. Resolve CryptoQuant asset symbol from resolution_keywords
    3. Fetch exchange net flow (30d) and whale transaction counts (7d)
    4. Z-score 7-day net flow against 30-day rolling average
    5. Convert to directional pressure score [-1, +1]
    6. Apply conservative probability adjustment (±8pp max)
    7. Return with raw flow data for frontier model

    No LLM calls — pure data pipeline.
    """

    name: str = "onchain_flow"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: Any = None,  # Accepted for interface consistency but unused
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
        # Gate: skip non-crypto categories
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

        # Resolve CryptoQuant asset symbol
        coin_id = resolution_keywords.get("coin_id", "")
        if not coin_id:
            # Try extracting from market question via the ticker whitelist
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

        # Map CoinGecko ID to CryptoQuant asset
        cq_asset = _COINGECKO_TO_CRYPTOQUANT.get(coin_id)
        if not cq_asset:
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning=f"No on-chain flow coverage for {coin_id} (BTC/ETH only on free tier)",
                model_used="none",
                data_points=0,
                raw_data={"coin_id": coin_id, "coverage": "none"},
            )

        self._emit(market_question, "onchain", f"fetching {cq_asset} exchange flow data")

        try:
            pressure, raw_metrics = await _fetch_flow_data(cq_asset)
        except Exception as e:
            logger.error("On-chain flow fetch failed for %s: %s", cq_asset, e)
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
        base_confidence = _CONFIDENCE_TIERS.get(cq_asset, _DEFAULT_CONFIDENCE)

        # Reduce confidence if using fallback data source
        data_source = raw_metrics.get("data_source", "none")
        if data_source == "blockchain_com_fallback":
            base_confidence = max(base_confidence * 0.4, 0.15)
        elif data_source == "none":
            base_confidence = 0.0

        # If no meaningful data, return with zero confidence (graceful skip)
        if base_confidence == 0.0 or raw_metrics.get("error"):
            return SignalResult(
                source="onchain_flow",
                probability=None,
                confidence=0.0,
                reasoning=f"Insufficient on-chain data for {cq_asset}: {raw_metrics.get('error', 'no data')}",
                model_used="none",
                data_points=0,
                raw_data=raw_metrics,
            )

        data_points = raw_metrics.get("data_points_30d", 0)

        # Scale confidence with data availability
        if data_points >= 25:
            base_confidence = min(base_confidence + 0.05, 0.60)
        elif data_points < 14:
            base_confidence = max(base_confidence - 0.10, 0.20)

        # Convert pressure to probability adjustment
        adjustment = _pressure_to_adjustment(pressure)

        # Apply adjustment to a 0.5 baseline (neutral).
        # The frontier model will weigh this against the math model and other signals.
        probability = 0.5 + adjustment

        # Determine the market direction context for reasoning
        target_direction = resolution_keywords.get("target_direction", "above")
        if target_direction == "below":
            # If market asks "will price drop below X", accumulation (positive pressure)
            # means less likely, so invert the adjustment
            probability = 0.5 - adjustment

        probability = max(0.02, min(0.98, probability))

        # Build reasoning
        flow_dir = raw_metrics.get("net_flow_direction", "unknown")
        z = raw_metrics.get("z_score", 0.0)
        whale_info = ""
        if raw_metrics.get("whale_data_available"):
            whale_count = raw_metrics.get("whale_tx_count", "?")
            whale_trend = raw_metrics.get("whale_trend", "?")
            whale_info = f" Whale txs (>$1M): {whale_count} ({whale_trend})."

        reasoning = (
            f"On-chain flow ({data_source}): pressure={pressure:+.2f} "
            f"[z={z:+.2f}, {flow_dir}]. "
            f"Adjustment: {adjustment:+.3f} → P={probability:.3f}.{whale_info} "
            f"Based on {data_points} days of exchange flow data for {cq_asset.upper()}."
        )

        self._emit(market_question, "done", f"pressure={pressure:+.2f}")

        result = SignalResult(
            source="onchain_flow",
            probability=probability,
            confidence=base_confidence,
            reasoning=reasoning,
            model_used="none",  # No LLM used — pure data
            data_points=data_points,
            raw_data=raw_metrics,
        )

        # Log to DB
        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source="onchain_flow",
                probability=probability,
                confidence=base_confidence,
                reasoning=reasoning[:1000],
                model_used="none",
            )
        except Exception as e:
            logger.warning("Failed to log onchain_flow signal to DB: %s", e)

        return result


def clear_flow_cache() -> None:
    """Clear the in-memory flow data cache."""
    _flow_cache.clear()
