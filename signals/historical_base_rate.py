"""Historical base rate signal provider.

Computes empirical frequencies from historical data to answer: "How often has
this type of event happened in the past?" Uses FRED data for economics and
CoinGecko data for crypto. Pure statistical analysis with cheap LLM interpretation.
"""

import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config.settings import FRED_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes (base rates don't change fast)

COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
USER_AGENT = "polymarket-bot/1.0 (signal research)"

INDICATOR_SERIES: dict[str, list[str]] = {
    "rate": ["FEDFUNDS", "DFF"],
    "inflation": ["CPIAUCSL"],
    "employment": ["UNRATE"],
    "gdp": ["GDP"],
}
DEFAULT_SERIES = ["DGS10"]


async def _fetch_coingecko_chart(
    session: aiohttp.ClientSession, coin_id: str, days: int = 365
) -> list[list[float]] | None:
    """Fetch long-term price history for base rate analysis."""
    url = COINGECKO_CHART_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "days": str(days)}
    try:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        return data.get("prices", [])
    except Exception as e:
        logger.warning("Error fetching CoinGecko chart for %s: %s", coin_id, e)
        return None


async def _fetch_fred_series(
    session: aiohttp.ClientSession, series_id: str, limit: int = 120
) -> list[dict[str, str]]:
    """Fetch extended FRED history for base rate computation."""
    params = {
        "series_id": series_id,
        "file_type": "json",
        "api_key": FRED_API_KEY,
        "sort_order": "desc",
        "limit": str(limit),
    }
    try:
        async with session.get(
            FRED_OBS_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        return [obs for obs in data.get("observations", []) if obs.get("value", ".") != "."]
    except Exception as e:
        logger.warning("Error fetching FRED series %s: %s", series_id, e)
        return []


def compute_crypto_base_rates(
    prices: list[list[float]],
    target_price: float,
    direction: str,
    window_days: int = 30,
) -> dict[str, Any]:
    """Compute historical base rates for crypto price targets.

    Looks at rolling windows of `window_days` to count how often price
    moved from level X to cross target Y in that timeframe.
    """
    if len(prices) < window_days + 1:
        return {"base_rate": None, "sample_size": 0}

    price_values = [p[1] for p in prices if p[1] > 0]
    if len(price_values) < window_days + 1:
        return {"base_rate": None, "sample_size": 0}

    current_price = price_values[-1]
    # Compute the ratio: target / current
    target_ratio = target_price / current_price if current_price > 0 else 1.0

    # Count rolling windows where price achieved similar move
    hits = 0
    total_windows = 0
    max_move_up = 0.0
    max_move_down = 0.0
    all_moves: list[float] = []

    for i in range(len(price_values) - window_days):
        start_price = price_values[i]
        end_price = price_values[i + window_days]
        if start_price <= 0:
            continue

        move_ratio = end_price / start_price
        move_pct = (move_ratio - 1.0) * 100
        all_moves.append(move_pct)
        max_move_up = max(max_move_up, move_pct)
        max_move_down = min(max_move_down, move_pct)

        if direction == "below":
            # How often did price drop by at least the required ratio?
            required_ratio = target_price / start_price if start_price > 0 else 0
            if move_ratio <= required_ratio:
                hits += 1
        else:
            # How often did price rise by at least the required ratio?
            required_ratio = target_ratio
            if move_ratio >= required_ratio:
                hits += 1

        total_windows += 1

    if total_windows == 0:
        return {"base_rate": None, "sample_size": 0}

    # Compute statistics on moves
    mean_move = sum(all_moves) / len(all_moves)
    variance = sum((m - mean_move) ** 2 for m in all_moves) / len(all_moves)
    std_move = math.sqrt(variance)

    return {
        "base_rate": hits / total_windows,
        "sample_size": total_windows,
        "hits": hits,
        "mean_move_pct": mean_move,
        "std_move_pct": std_move,
        "max_move_up_pct": max_move_up,
        "max_move_down_pct": max_move_down,
        "target_move_pct": (target_ratio - 1.0) * 100,
    }


def compute_econ_base_rates(
    observations: list[dict[str, str]],
    target_value: float,
    direction: str,
) -> dict[str, Any]:
    """Compute historical base rates for economic indicators.

    Counts how often the indicator has been at or beyond the target value.
    Also computes how often it moved from current-like levels to cross the target.
    """
    values: list[float] = []
    for obs in observations:
        try:
            values.append(float(obs["value"]))
        except (KeyError, ValueError):
            continue

    if len(values) < 5:
        return {"base_rate": None, "sample_size": 0}

    current_value = values[0]  # newest first

    # Level-based: how often has indicator been at/beyond target?
    if direction in ("below", "cut"):
        level_hits = sum(1 for v in values if v < target_value)
    else:
        level_hits = sum(1 for v in values if v >= target_value)

    level_rate = level_hits / len(values)

    # Transition-based: from similar starting levels, how often did it reach target?
    # Define "similar" as within 10% of current value
    tolerance = abs(current_value * 0.10) if current_value != 0 else 0.5
    transition_hits = 0
    transition_total = 0

    # Values are newest-first; reverse for chronological
    chrono_values = list(reversed(values))
    for i in range(len(chrono_values) - 1):
        if abs(chrono_values[i] - current_value) <= tolerance:
            next_val = chrono_values[i + 1]
            transition_total += 1
            if direction in ("below", "cut"):
                if next_val < target_value:
                    transition_hits += 1
            else:
                if next_val >= target_value:
                    transition_hits += 1

    transition_rate = transition_hits / transition_total if transition_total > 0 else None

    # Statistics
    mean_val = sum(values) / len(values)
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    std_val = math.sqrt(variance)
    min_val = min(values)
    max_val = max(values)

    return {
        "base_rate": level_rate,
        "level_hits": level_hits,
        "sample_size": len(values),
        "transition_rate": transition_rate,
        "transition_hits": transition_hits,
        "transition_total": transition_total,
        "current_value": current_value,
        "mean_value": mean_val,
        "std_value": std_val,
        "min_value": min_val,
        "max_value": max_val,
    }


class HistoricalBaseRateProvider(SignalProvider):
    """Historical base rate signal provider.

    For crypto: analyzes rolling windows in CoinGecko price history to compute
    how often similar price moves have occurred historically.
    For economics: analyzes FRED history to compute how often the indicator
    has been at or moved to the target level.
    """

    name: str = "historical_base_rate"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: LLMClient,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._llm = llm
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
        category = market_category.lower()
        if category not in ("crypto", "economics"):
            return SignalResult(
                source="historical_base_rate",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' not supported",
                model_used="none",
                data_points=0,
            )

        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                self._emit(market_question, "cache")
                return cached_result

        try:
            if category == "crypto":
                result = await self._run_crypto_base_rate(market_question, market_end_date, kwargs)
            else:
                result = await self._run_econ_base_rate(market_question, market_end_date, kwargs)
        except Exception as e:
            logger.error("Historical base rate failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="historical_base_rate",
                probability=None,
                confidence=0.0,
                reasoning=f"Pipeline error: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        _signal_cache[cache_key] = (result, time.monotonic())
        self._log_signal(market_question, result)
        self._emit(market_question, "done", result.reasoning[:100])
        return result

    def _log_signal(self, market_question: str, result: SignalResult) -> None:
        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source=result.source,
                probability=result.probability if result.probability is not None else -1.0,
                confidence=result.confidence,
                reasoning=result.reasoning[:1000],
                model_used=result.model_used,
            )
        except Exception as e:
            logger.warning("Failed to log signal to DB: %s", e)

    async def _run_crypto_base_rate(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        resolution_keywords = kwargs.get("resolution_keywords", {})
        coin_id = resolution_keywords.get("coin_id")
        target_value = resolution_keywords.get("target_value")
        target_direction = resolution_keywords.get("target_direction", "above")

        if not coin_id:
            prompt = (
                f'What is the CoinGecko API coin ID for the cryptocurrency in: "{market_question}"?\n'
                f'Respond as JSON: {{"coin_id": "the_id"}}'
            )
            try:
                result = await self._llm.call_json(prompt, task_type="extract")
                if isinstance(result, dict):
                    coin_id = result.get("coin_id")
            except Exception:
                pass

        if not coin_id:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning="Could not determine coin ID",
                model_used="none", data_points=0,
            )

        if target_value is None:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning="No target value for base rate analysis",
                model_used="none", data_points=0,
            )

        target_price = float(target_value)

        # Compute days remaining for window
        try:
            end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            days_remaining = max(1, int((end_dt - datetime.now(timezone.utc)).total_seconds() / 86400))
        except (ValueError, TypeError):
            days_remaining = 30

        self._emit(market_question, "br_fetch", f"fetching {coin_id} 365d history")
        async with aiohttp.ClientSession() as session:
            chart_data = await _fetch_coingecko_chart(session, coin_id, days=365)

        if not chart_data or len(chart_data) < 60:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning=f"Insufficient history for {coin_id}",
                model_used="none", data_points=0,
            )

        self._emit(market_question, "br_compute", f"analyzing {len(chart_data)} points, {days_remaining}d window")
        # Use the actual days remaining as the window
        window = min(days_remaining, 90)  # Cap at 90 for meaningful sample
        stats = compute_crypto_base_rates(chart_data, target_price, target_direction, window)

        if stats["base_rate"] is None:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning="Could not compute base rates",
                model_used="none", data_points=0,
            )

        # Cheap LLM interprets
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)
        interpret_prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n\n"
            f"Historical base rate analysis for {coin_id}:\n"
            f"- Target: ${target_price:,.2f} ({target_direction})\n"
            f"- Required move: {stats['target_move_pct']:+.1f}%\n"
            f"- Window: {window} days\n"
            f"- Historical base rate: {stats['base_rate']:.1%} ({stats['hits']}/{stats['sample_size']} windows)\n"
            f"- Mean {window}d move: {stats['mean_move_pct']:+.1f}%\n"
            f"- Std dev of moves: {stats['std_move_pct']:.1f}%\n"
            f"- Max upward move: {stats['max_move_up_pct']:+.1f}%\n"
            f"- Max downward move: {stats['max_move_down_pct']:+.1f}%\n\n"
            f"Based on this historical frequency data, estimate the probability.\n"
            f"Historical base rates are a strong anchor — adjust only if current conditions clearly differ.\n\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        raw_data = {
            "coin_id": coin_id,
            "target_price": target_price,
            "target_direction": target_direction,
            "window_days": window,
            "base_rate": stats["base_rate"],
            "hits": stats["hits"],
            "sample_size": stats["sample_size"],
            "target_move_pct": stats["target_move_pct"],
            "mean_move_pct": stats["mean_move_pct"],
            "std_move_pct": stats["std_move_pct"],
            "max_up": stats["max_move_up_pct"],
            "max_down": stats["max_move_down_pct"],
        }

        try:
            result = await self._llm.call_json(interpret_prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))
                if prob is not None:
                    prob = max(0.0, min(1.0, float(prob)))
                conf = max(0.0, min(1.0, conf))
                return SignalResult(
                    source="historical_base_rate",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(chart_data),
                    raw_data=raw_data,
                )
        except Exception as e:
            logger.error("Failed to interpret crypto base rates: %s", e)

        return SignalResult(
            source="historical_base_rate",
            probability=stats["base_rate"],
            confidence=0.4,
            reasoning=f"Raw base rate: {stats['base_rate']:.1%} over {stats['sample_size']} historical {window}d windows",
            model_used="none",
            data_points=len(chart_data),
            raw_data=raw_data,
        )

    async def _run_econ_base_rate(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        resolution_keywords = kwargs.get("resolution_keywords", {})
        indicator_type = resolution_keywords.get("indicator_type", "other")
        target_value = resolution_keywords.get("target_value")
        target_direction = resolution_keywords.get("target_direction", "above")

        series_ids = INDICATOR_SERIES.get(indicator_type, DEFAULT_SERIES)
        primary_series = series_ids[0]

        if target_value is None:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning="No target value for economics base rate",
                model_used="none", data_points=0,
            )

        target = float(target_value)

        self._emit(market_question, "br_fetch", f"fetching {primary_series} extended history")
        async with aiohttp.ClientSession() as session:
            observations = await _fetch_fred_series(session, primary_series, limit=120)

        if len(observations) < 10:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning=f"Insufficient FRED history for {primary_series}",
                model_used="none", data_points=0,
            )

        self._emit(market_question, "br_compute", f"analyzing {len(observations)} periods")
        stats = compute_econ_base_rates(observations, target, target_direction)

        if stats["base_rate"] is None:
            return SignalResult(
                source="historical_base_rate", probability=None, confidence=0.0,
                reasoning="Could not compute economics base rates",
                model_used="none", data_points=0,
            )

        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)

        transition_line = ""
        if stats["transition_rate"] is not None:
            transition_line = (
                f"- Transition rate (from similar levels): {stats['transition_rate']:.1%} "
                f"({stats['transition_hits']}/{stats['transition_total']} transitions)\n"
            )

        interpret_prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n\n"
            f"Historical base rate analysis for {primary_series}:\n"
            f"- Current value: {stats['current_value']}\n"
            f"- Target: {target} ({target_direction})\n"
            f"- Level base rate: {stats['base_rate']:.1%} ({stats['level_hits']}/{stats['sample_size']} periods)\n"
            f"{transition_line}"
            f"- Historical range: [{stats['min_value']:.2f}, {stats['max_value']:.2f}]\n"
            f"- Mean: {stats['mean_value']:.2f}, Std dev: {stats['std_value']:.2f}\n\n"
            f"The level base rate shows how often this indicator has historically been at/beyond the target.\n"
            f"The transition rate (if available) shows how often it moved there from current-like levels.\n"
            f"Weight transition rate more heavily as it's conditional on current state.\n\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        raw_data = {
            "series": primary_series,
            "base_rate": stats["base_rate"],
            "level_hits": stats["level_hits"],
            "sample_size": stats["sample_size"],
            "transition_rate": stats["transition_rate"],
            "transition_hits": stats["transition_hits"],
            "transition_total": stats["transition_total"],
            "current_value": stats["current_value"],
            "mean_value": stats["mean_value"],
            "std_value": stats["std_value"],
            "min_value": stats["min_value"],
            "max_value": stats["max_value"],
        }

        try:
            result = await self._llm.call_json(interpret_prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))
                if prob is not None:
                    prob = max(0.0, min(1.0, float(prob)))
                conf = max(0.0, min(1.0, conf))
                return SignalResult(
                    source="historical_base_rate",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(observations),
                    raw_data=raw_data,
                )
        except Exception as e:
            logger.error("Failed to interpret econ base rates: %s", e)

        return SignalResult(
            source="historical_base_rate",
            probability=stats["base_rate"],
            confidence=0.3,
            reasoning=f"Raw base rate: {stats['base_rate']:.1%} from {stats['sample_size']} periods",
            model_used="none",
            data_points=len(observations),
            raw_data=raw_data,
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
