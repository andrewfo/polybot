"""Dynamic signal source calibration based on historical accuracy.

Tracks each signal provider's predicted probabilities vs actual market
resolutions. Computes rolling Brier scores and converts them into
dynamic weight multipliers for the aggregator.

Replaces the fixed SIGNAL_WEIGHT_MULTIPLIERS when sufficient history
exists (>= MIN_CALIBRATION_SAMPLES resolved predictions per provider).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config.settings import (
    CALIBRATION_LOOKBACK_DAYS,
    MIN_CALIBRATION_SAMPLES,
)
from core import db

logger = logging.getLogger(__name__)

# Default multipliers (used when insufficient calibration data)
DEFAULT_MULTIPLIERS: dict[str, float] = {
    "resolution_crypto": 2.0,
    "prediction_markets": 1.8,
    "web_search": 1.5,
}

# Brier score of a random guesser (always predicting 0.5)
BASELINE_BRIER = 0.25

# Gamma API for checking market resolutions
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets"


@dataclass
class ProviderCalibration:
    """Calibration statistics for a single signal provider."""

    source: str
    brier_score: float        # Mean Brier score (lower = better)
    sample_count: int         # Number of resolved predictions
    multiplier: float         # Dynamic weight multiplier
    is_default: bool          # True if using default (insufficient data)


def record_prediction(
    market_id: str,
    signal_source: str,
    predicted_probability: float,
    market_question: str = "",
) -> None:
    """Record a signal provider's prediction for later calibration.

    Called after each signal is produced during aggregation.
    """
    try:
        d = db.get_db()
        d["signal_calibration"].insert({
            "market_id": market_id,
            "signal_source": signal_source,
            "predicted_probability": predicted_probability,
            "actual_outcome": None,
            "market_question": market_question[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resolved_at": None,
        })
    except Exception as e:
        logger.warning("Failed to record calibration prediction: %s", e)


def record_resolution(market_id: str, actual_outcome: float) -> None:
    """Update all predictions for a market with its actual resolution.

    Parameters
    ----------
    market_id : str
        The condition_id of the resolved market.
    actual_outcome : float
        1.0 if YES resolved, 0.0 if NO resolved.
    """
    try:
        d = db.get_db()
        now = datetime.now(timezone.utc).isoformat()
        d.execute(
            "UPDATE signal_calibration SET actual_outcome = ?, resolved_at = ? "
            "WHERE market_id = ? AND actual_outcome IS NULL",
            [actual_outcome, now, market_id],
        )
    except Exception as e:
        logger.warning("Failed to record calibration resolution: %s", e)


def get_provider_brier_scores() -> dict[str, tuple[float, int]]:
    """Compute Brier score per signal source from resolved predictions.

    Returns dict of source -> (brier_score, sample_count).
    Only considers predictions within CALIBRATION_LOOKBACK_DAYS.
    """
    try:
        d = db.get_db()
        rows = list(d.execute(
            "SELECT signal_source, predicted_probability, actual_outcome "
            "FROM signal_calibration "
            "WHERE actual_outcome IS NOT NULL "
            "AND resolved_at >= datetime('now', ?)",
            [f"-{CALIBRATION_LOOKBACK_DAYS} days"],
        ).fetchall())
    except Exception as e:
        logger.warning("Failed to query calibration data: %s", e)
        return {}

    if not rows:
        return {}

    # Group by source
    scores: dict[str, list[float]] = {}
    for row in rows:
        source = row[0]
        predicted = float(row[1])
        actual = float(row[2])
        brier = (predicted - actual) ** 2
        scores.setdefault(source, []).append(brier)

    result: dict[str, tuple[float, int]] = {}
    for source, brier_list in scores.items():
        mean_brier = sum(brier_list) / len(brier_list)
        result[source] = (mean_brier, len(brier_list))

    return result


def get_dynamic_multipliers() -> dict[str, ProviderCalibration]:
    """Compute dynamic weight multipliers from calibration data.

    For providers with sufficient history (>= MIN_CALIBRATION_SAMPLES):
    - Compute mean Brier score
    - Scale multiplier relative to average performance:
      ratio = avg_brier / provider_brier (better = higher ratio)
      dynamic_multiplier = default_multiplier * ratio
    - Clamp between 0.5x and 2.0x of the default multiplier

    For providers with insufficient history, use defaults.
    """
    brier_data = get_provider_brier_scores()
    result: dict[str, ProviderCalibration] = {}

    # Collect providers with sufficient samples
    sufficient: dict[str, tuple[float, int]] = {}
    for source, (brier, count) in brier_data.items():
        if count >= MIN_CALIBRATION_SAMPLES and source in DEFAULT_MULTIPLIERS:
            sufficient[source] = (brier, count)

    # Compute average Brier across providers with sufficient data
    if len(sufficient) >= 2:
        avg_brier = sum(b for b, _ in sufficient.values()) / len(sufficient)
    else:
        avg_brier = BASELINE_BRIER

    for source, default_mult in DEFAULT_MULTIPLIERS.items():
        if source in sufficient:
            provider_brier, count = sufficient[source]

            # Avoid division by zero
            if provider_brier < 0.001:
                ratio = 2.0  # Near-perfect → max boost
            else:
                ratio = avg_brier / provider_brier

            # Clamp ratio to [0.5, 2.0]
            ratio = max(0.5, min(2.0, ratio))
            dynamic_mult = default_mult * ratio

            result[source] = ProviderCalibration(
                source=source,
                brier_score=provider_brier,
                sample_count=count,
                multiplier=dynamic_mult,
                is_default=False,
            )
            logger.info(
                "Calibration %s: brier=%.3f samples=%d ratio=%.2f mult=%.2f (default=%.1f)",
                source, provider_brier, count, ratio, dynamic_mult, default_mult,
            )
        else:
            count = brier_data.get(source, (0.0, 0))[1]
            result[source] = ProviderCalibration(
                source=source,
                brier_score=BASELINE_BRIER,
                sample_count=count,
                multiplier=default_mult,
                is_default=True,
            )

    return result


def get_multiplier_dict() -> dict[str, float]:
    """Return a simple source -> multiplier dict for the aggregator.

    Drop-in replacement for the fixed SIGNAL_WEIGHT_MULTIPLIERS.
    """
    calibrations = get_dynamic_multipliers()
    return {source: cal.multiplier for source, cal in calibrations.items()}


async def check_and_record_resolutions() -> int:
    """Check Gamma API for recently resolved markets and update calibration.

    Queries for markets that have predictions in our calibration table
    but haven't been resolved yet. Returns count of newly resolved markets.
    """
    try:
        d = db.get_db()
        # Get distinct unresolved market IDs
        rows = list(d.execute(
            "SELECT DISTINCT market_id FROM signal_calibration "
            "WHERE actual_outcome IS NULL"
        ).fetchall())
    except Exception as e:
        logger.warning("Failed to query unresolved markets: %s", e)
        return 0

    if not rows:
        return 0

    market_ids = [row[0] for row in rows]
    resolved_count = 0

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for market_id in market_ids:
                try:
                    async with session.get(
                        GAMMA_MARKET_URL,
                        params={"id": market_id},
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()

                    markets = data if isinstance(data, list) else [data]
                    for mkt in markets:
                        if not mkt.get("closed"):
                            continue

                        # Determine resolution outcome
                        outcome_prices = mkt.get("outcomePrices", "")
                        if isinstance(outcome_prices, str):
                            try:
                                import json
                                prices = json.loads(outcome_prices)
                            except (ValueError, TypeError):
                                continue
                        else:
                            prices = outcome_prices

                        if not prices or len(prices) < 2:
                            continue

                        # YES outcome = first price, should be 1.0 or 0.0 at resolution
                        yes_price = float(prices[0])
                        if yes_price >= 0.95:
                            actual = 1.0
                        elif yes_price <= 0.05:
                            actual = 0.0
                        else:
                            continue  # Not clearly resolved

                        record_resolution(market_id, actual)
                        resolved_count += 1
                        logger.info(
                            "Recorded resolution for %s: outcome=%.0f",
                            market_id[:20], actual,
                        )

                except Exception as e:
                    logger.debug("Failed to check resolution for %s: %s", market_id[:20], e)
                    continue

    except Exception as e:
        logger.warning("Resolution check session error: %s", e)

    if resolved_count > 0:
        logger.info("Recorded %d new market resolutions for calibration", resolved_count)

    return resolved_count
