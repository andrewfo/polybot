"""Wikipedia pageviews attention signal provider.

Uses the Wikimedia Pageviews API to detect attention spikes for topics
related to market questions. Sudden spikes in Wikipedia pageviews correlate
with breaking news and can predict market movement.

No API key required. Rate limit: 200 requests/second. Completely free.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour — pageview patterns are slower-moving

WIKI_API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
USER_AGENT = "polymarket-bot/1.0 (prediction market research; contact: bot@polymarket.research)"

# Attention spike thresholds
SPIKE_RATIO_THRESHOLD = 2.0     # 2x the 30-day average = notable spike
MAJOR_SPIKE_RATIO = 5.0         # 5x = major attention event


async def _fetch_pageviews(
    session: aiohttp.ClientSession,
    article: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Fetch daily pageviews for a Wikipedia article over the given period."""
    views: list[dict[str, Any]] = []
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        # URL-encode the article title (spaces → underscores in Wikipedia)
        article_encoded = article.replace(" ", "_")

        url = (
            f"{WIKI_API_BASE}/en.wikipedia/all-access/user/"
            f"{article_encoded}/daily/{start_str}/{end_str}"
        )

        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Wikipedia pageviews returned %d for '%s'", resp.status, article)
                return []
            data = await resp.json()

        for item in data.get("items", []):
            views.append({
                "date": item.get("timestamp", ""),
                "views": item.get("views", 0),
            })
    except Exception as e:
        logger.warning("Error fetching Wikipedia pageviews for '%s': %s", article, e)
    return views


def _analyze_attention(views: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze pageview data for attention patterns."""
    if not views:
        return {"spike_detected": False, "spike_ratio": 0.0}

    daily_views = [v["views"] for v in views]

    if len(daily_views) < 7:
        return {"spike_detected": False, "spike_ratio": 0.0}

    # Compare recent (last 3 days) vs baseline (previous 27 days)
    recent = daily_views[-3:]
    baseline = daily_views[:-3] if len(daily_views) > 3 else daily_views

    avg_recent = sum(recent) / len(recent) if recent else 0
    avg_baseline = sum(baseline) / len(baseline) if baseline else 1

    spike_ratio = avg_recent / max(avg_baseline, 1)

    # Trend direction (positive = increasing attention)
    if len(daily_views) >= 7:
        first_half = daily_views[:len(daily_views) // 2]
        second_half = daily_views[len(daily_views) // 2:]
        trend = (sum(second_half) / len(second_half)) / max(sum(first_half) / len(first_half), 1)
    else:
        trend = 1.0

    return {
        "spike_detected": spike_ratio >= SPIKE_RATIO_THRESHOLD,
        "spike_ratio": round(spike_ratio, 2),
        "avg_recent_views": round(avg_recent),
        "avg_baseline_views": round(avg_baseline),
        "trend_ratio": round(trend, 2),
        "total_views_30d": sum(daily_views),
        "peak_views": max(daily_views),
    }


class WikiAttentionSignalProvider(SignalProvider):
    """Wikipedia pageviews attention signal.

    Pipeline:
    1. Use cheap LLM to identify 2-3 Wikipedia article titles related to the market
    2. Fetch 30-day pageview data for each article
    3. Detect attention spikes (recent views vs baseline)
    4. Use cheap LLM to interpret attention patterns for probability
    """

    name: str = "wiki_attention"

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
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(market_question, market_category, market_end_date)
        except Exception as e:
            logger.error("Wiki attention signal failed for '%s': %s", market_question[:60], e)
            result = SignalResult(
                source="wiki_attention",
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
            logger.warning("Failed to log wiki_attention signal to DB: %s", e)

    async def _run_pipeline(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
    ) -> SignalResult:
        import asyncio

        # Step 1: Identify relevant Wikipedia articles
        self._emit(market_question, "identify", "finding relevant Wikipedia articles")
        articles = await self._identify_articles(market_question)

        if not articles:
            return SignalResult(
                source="wiki_attention",
                probability=None,
                confidence=0.0,
                reasoning="Could not identify relevant Wikipedia articles",
                model_used="cheap",
                data_points=0,
            )

        # Step 2: Fetch pageviews for all articles in parallel
        self._emit(market_question, "fetch", f"fetching pageviews for {len(articles)} articles")
        async with aiohttp.ClientSession() as session:
            tasks = [_fetch_pageviews(session, article) for article in articles]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Step 3: Analyze attention patterns
        attention_data: list[dict[str, Any]] = []
        for article, result in zip(articles, results):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch pageviews for '%s': %s", article, result)
                continue
            analysis = _analyze_attention(result)
            analysis["article"] = article
            attention_data.append(analysis)

        if not attention_data:
            return SignalResult(
                source="wiki_attention",
                probability=None,
                confidence=0.0,
                reasoning="No pageview data available for identified articles",
                model_used="cheap",
                data_points=0,
                raw_data={"articles": articles},
            )

        # Step 4: Interpret attention data
        self._emit(market_question, "interpret", f"analyzing {len(attention_data)} attention signals")
        return await self._interpret_attention(
            market_question, market_category, market_end_date, attention_data
        )

    async def _identify_articles(self, market_question: str) -> list[str]:
        """Use cheap LLM to identify 2-3 Wikipedia article titles."""
        prompt = (
            f'Given this prediction market question: "{market_question}"\n'
            f'List 2-3 Wikipedia article titles that are directly relevant to this topic.\n'
            f'Use exact Wikipedia article titles (e.g., "Donald Trump", "Bitcoin", '
            f'"2024 United States presidential election").\n'
            f'Return as JSON array of strings only.'
        )
        try:
            result = await self._llm.call_json(prompt, task_type="extract")
            if isinstance(result, list):
                return [str(a) for a in result[:3]]
        except Exception as e:
            logger.warning("Failed to identify Wikipedia articles: %s", e)
        return []

    async def _interpret_attention(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        attention_data: list[dict[str, Any]],
    ) -> SignalResult:
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date) if market_end_date else ""

        attention_text = ""
        any_spike = False
        total_data_points = 0
        for data in attention_data:
            spike_label = "SPIKE DETECTED" if data.get("spike_detected") else "normal"
            if data.get("spike_detected"):
                any_spike = True
            attention_text += (
                f'- "{data["article"]}": {spike_label}\n'
                f'  Recent avg: {data.get("avg_recent_views", 0)} views/day, '
                f'Baseline avg: {data.get("avg_baseline_views", 0)} views/day, '
                f'Spike ratio: {data.get("spike_ratio", 0)}x, '
                f'Trend: {data.get("trend_ratio", 1.0)}x\n'
            )
            total_data_points += data.get("total_views_30d", 0)

        prompt = (
            f'Market question: "{market_question}"\n'
            f'Category: {market_category}\n'
            f'{date_ctx}\n\n'
            f'Wikipedia attention data (30-day pageviews):\n{attention_text}\n'
            f'A "spike" means recent views are significantly above the 30-day baseline, '
            f'indicating increased public attention to this topic.\n\n'
            f'Based on this attention data, does the spike (or lack thereof) suggest '
            f'the market event is more or less likely?\n'
            f'Note: Attention alone is a weak signal — use low confidence unless the pattern is very clear.\n'
            f'Respond as JSON:\n'
            f'{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "...", '
            f'"attention_signal": "bullish"|"bearish"|"neutral"}}'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
        except Exception as e:
            return SignalResult(
                source="wiki_attention",
                probability=None,
                confidence=0.0,
                reasoning=f"LLM interpretation failed: {e}",
                model_used="cheap",
                data_points=len(attention_data),
                raw_data={"attention_data": attention_data},
            )

        if not isinstance(result, dict):
            return SignalResult(
                source="wiki_attention",
                probability=None,
                confidence=0.0,
                reasoning="Invalid LLM response",
                model_used="cheap",
                data_points=len(attention_data),
            )

        prob = result.get("probability")
        conf = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))

        if prob is not None:
            prob = max(0.0, min(1.0, float(prob)))
        # Cap confidence — attention is inherently a weak signal
        conf = min(0.5, max(0.0, conf))

        return SignalResult(
            source="wiki_attention",
            probability=prob,
            confidence=conf,
            reasoning=reasoning,
            model_used="cheap",
            data_points=len(attention_data),
            raw_data={
                "attention_data": attention_data,
                "any_spike": any_spike,
            },
        )


def clear_signal_cache() -> None:
    _signal_cache.clear()
