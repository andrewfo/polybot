"""Economics resolution source signal provider.

Fetches data directly from FRED (Federal Reserve Economic Data) — the actual
resolution source for economics markets. Maps indicator types to FRED series
IDs and uses cheap LLM to interpret trends.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp

from config.settings import FRED_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

# Map indicator types to FRED series IDs
INDICATOR_SERIES: dict[str, list[str]] = {
    "rate": ["FEDFUNDS", "DFF"],
    "inflation": ["CPIAUCSL"],
    "employment": ["UNRATE"],
    "gdp": ["GDP"],
}
DEFAULT_SERIES = ["DGS10", "T10Y2Y"]

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


async def _fetch_fred_series(
    session: aiohttp.ClientSession, series_id: str
) -> list[dict[str, str]]:
    """Fetch latest observations from a FRED series."""
    params = {
        "series_id": series_id,
        "file_type": "json",
        "api_key": FRED_API_KEY,
        "sort_order": "desc",
        "limit": "12",
    }
    try:
        async with session.get(
            FRED_OBS_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("FRED API returned %d for %s", resp.status, series_id)
                return []
            data = await resp.json()
        observations = data.get("observations", [])
        # Filter out observations with value == "."
        return [obs for obs in observations if obs.get("value", ".") != "."]
    except Exception as e:
        logger.warning("Error fetching FRED series %s: %s", series_id, e)
        return []


def _compute_trend(observations: list[dict[str, str]]) -> str:
    """Describe trend from FRED observations (newest first)."""
    if not observations:
        return "No data available"
    values: list[float] = []
    for obs in observations:
        try:
            values.append(float(obs["value"]))
        except (KeyError, ValueError):
            continue
    if not values:
        return "No numeric data available"
    newest = values[0]
    oldest = values[-1]
    if oldest == 0:
        change_pct = 0.0
    else:
        change_pct = ((newest - oldest) / abs(oldest)) * 100
    if change_pct > 1:
        direction = "rising"
    elif change_pct < -1:
        direction = "falling"
    else:
        direction = "stable"
    return (
        f"Current: {newest}, Previous ({len(values)} periods): {oldest}, "
        f"Change: {change_pct:+.2f}% ({direction})"
    )


def _format_fred_data(
    series_data: dict[str, list[dict[str, str]]]
) -> str:
    """Format FRED data into a text block for the LLM."""
    lines: list[str] = []
    for series_id, observations in series_data.items():
        if not observations:
            lines.append(f"{series_id}: No data available")
            continue
        trend = _compute_trend(observations)
        lines.append(f"{series_id}: {trend}")
        # Show last few values
        recent_vals = []
        for obs in observations[:6]:
            date = obs.get("date", "")
            val = obs.get("value", "")
            recent_vals.append(f"  {date}: {val}")
        lines.extend(recent_vals)
    return "\n".join(lines)


class EconomicsResolutionProvider(SignalProvider):
    """Resolution source signal provider for economics markets.

    Pipeline:
    1. If category != economics → return confidence=0 immediately
    2. Map indicator_type to FRED series IDs
    3. Fetch latest observations from FRED
    4. Compute trends, format data
    5. Cheap LLM interprets data in context of market question
    """

    name: str = "resolution_econ"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: LLMClient,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._llm = llm
        self._on_progress = on_progress

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        """Emit a progress update if a callback is registered."""
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
        """Produce a FRED-based signal for an economics market question."""
        # Gate: skip non-economics categories
        if market_category.lower() != "economics":
            return SignalResult(
                source="resolution_econ",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' is not economics",
                model_used="none",
                data_points=0,
            )

        # Check cache
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                logger.debug("Cache hit for econ signal: %s", market_question[:60])
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(
                market_question, market_end_date, kwargs
            )
        except Exception as e:
            logger.error(
                "Econ signal pipeline failed for '%s': %s",
                market_question[:60], e,
            )
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="resolution_econ",
                probability=None,
                confidence=0.0,
                reasoning=f"Pipeline error: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        # Cache result
        _signal_cache[cache_key] = (result, time.monotonic())

        # Log to DB
        self._log_signal(market_question, result)

        self._emit(market_question, "done", result.reasoning[:100])

        return result

    def _log_signal(self, market_question: str, result: SignalResult) -> None:
        """Log signal result to the signals SQLite table."""
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

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        """Execute the full FRED signal pipeline."""
        resolution_keywords = kwargs.get("resolution_keywords", {})
        indicator_type = resolution_keywords.get("indicator_type", "other")

        # Map indicator type to FRED series
        series_ids = INDICATOR_SERIES.get(indicator_type, DEFAULT_SERIES)

        # Fetch data from FRED
        self._emit(market_question, "fred", f"fetching {', '.join(series_ids)}")
        series_data: dict[str, list[dict[str, str]]] = {}
        total_data_points = 0

        async with aiohttp.ClientSession() as session:
            for series_id in series_ids:
                observations = await _fetch_fred_series(session, series_id)
                series_data[series_id] = observations
                total_data_points += len(observations)

        if total_data_points == 0:
            return SignalResult(
                source="resolution_econ",
                probability=None,
                confidence=0.0,
                reasoning="No data available from FRED",
                model_used="none",
                data_points=0,
                raw_data={"series_ids": series_ids},
            )

        # Format data for LLM
        formatted_data = _format_fred_data(series_data)

        # Cheap LLM interpretation
        self._emit(market_question, "interpret", f"{total_data_points} observations")
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)
        prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n"
            f"\n"
            f"Current economic data from Federal Reserve (FRED):\n"
            f"{formatted_data}\n"
            f"\n"
            f"Based on this official economic data, estimate the probability of YES (0.0 to 1.0).\n"
            f"This data comes directly from the resolution source (Federal Reserve / government statistics).\n"
            f"Weight it heavily.\n"
            f"\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))

                if prob is not None:
                    prob = float(prob)
                    if not (0.0 <= prob <= 1.0):
                        prob = max(0.0, min(1.0, prob))

                conf = max(0.0, min(1.0, conf))

                self._emit(market_question, "done", reasoning[:100])

                return SignalResult(
                    source="resolution_econ",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=total_data_points,
                    raw_data={
                        "formatted_data": formatted_data,
                        "series_ids": series_ids,
                        "indicator_type": indicator_type,
                    },
                )
        except Exception as e:
            logger.error("Failed to interpret FRED data: %s", e)

        return SignalResult(
            source="resolution_econ",
            probability=None,
            confidence=0.0,
            reasoning="Failed to interpret FRED data",
            model_used="none",
            data_points=total_data_points,
            raw_data={"formatted_data": formatted_data},
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
