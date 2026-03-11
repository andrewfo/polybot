"""Serper.dev web search signal provider.

Uses Serper.dev API to fetch structured Google search results, then uses
cheap LLM to interpret results for probability estimation. Provides
higher-quality search results than RSS scraping.

Requires SERPER_API_KEY (free: 2,500 searches/month from serper.dev).
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp

from config.settings import SERPER_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

SERPER_API_URL = "https://google.serper.dev/search"
SERPER_NEWS_URL = "https://google.serper.dev/news"


async def _serper_web_search(
    session: aiohttp.ClientSession,
    query: str,
    api_key: str,
    num_results: int = 10,
) -> list[dict[str, Any]]:
    """Perform a web search via Serper.dev."""
    results: list[dict[str, Any]] = []
    try:
        payload = {"q": query, "num": num_results}
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }
        async with session.post(
            SERPER_API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning("Serper web search returned %d for '%s'", resp.status, query)
                return []
            data = await resp.json()

        # Extract organic results
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
                "position": item.get("position", 0),
            })

        # Also include knowledge graph if available
        kg = data.get("knowledgeGraph", {})
        if kg:
            results.append({
                "title": f"Knowledge Graph: {kg.get('title', '')}",
                "snippet": kg.get("description", ""),
                "link": kg.get("website", ""),
                "position": 0,
                "is_knowledge_graph": True,
            })

        # Include answer box if available
        ab = data.get("answerBox", {})
        if ab:
            answer = ab.get("answer") or ab.get("snippet") or ab.get("title", "")
            if answer:
                results.insert(0, {
                    "title": "Google Answer Box",
                    "snippet": answer,
                    "link": ab.get("link", ""),
                    "position": 0,
                    "is_answer_box": True,
                })
    except Exception as e:
        logger.warning("Error in Serper web search: %s", e)
    return results


async def _serper_news_search(
    session: aiohttp.ClientSession,
    query: str,
    api_key: str,
    num_results: int = 10,
) -> list[dict[str, Any]]:
    """Perform a news search via Serper.dev."""
    results: list[dict[str, Any]] = []
    try:
        payload = {"q": query, "num": num_results}
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }
        async with session.post(
            SERPER_NEWS_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning("Serper news search returned %d for '%s'", resp.status, query)
                return []
            data = await resp.json()

        for item in data.get("news", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "is_news": True,
            })
    except Exception as e:
        logger.warning("Error in Serper news search: %s", e)
    return results


class SerperSearchSignalProvider(SignalProvider):
    """Serper.dev web search signal provider.

    Pipeline:
    1. Generate 2 search queries from market question (cheap LLM)
    2. Perform web + news searches via Serper.dev
    3. Compile results into evidence brief
    4. Use cheap LLM to estimate probability from search evidence
    """

    name: str = "serper_search"

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
        if not SERPER_API_KEY:
            return SignalResult(
                source="serper_search",
                probability=None,
                confidence=0.0,
                reasoning="SERPER_API_KEY not configured",
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
            result = await self._run_pipeline(market_question, market_category, market_end_date)
        except Exception as e:
            logger.error("Serper search signal failed for '%s': %s", market_question[:60], e)
            result = SignalResult(
                source="serper_search",
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
            logger.warning("Failed to log serper_search signal to DB: %s", e)

    async def _run_pipeline(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
    ) -> SignalResult:
        import asyncio

        # Step 1: Generate search queries
        self._emit(market_question, "queries", "generating search queries")
        queries = await self._generate_queries(market_question)

        # Step 2: Perform web + news searches in parallel
        self._emit(market_question, "searching", f"queries: {', '.join(queries)}")
        all_web: list[dict[str, Any]] = []
        all_news: list[dict[str, Any]] = []

        async with aiohttp.ClientSession() as session:
            tasks = []
            for query in queries:
                tasks.append(_serper_web_search(session, query, SERPER_API_KEY))
                tasks.append(_serper_news_search(session, query, SERPER_API_KEY))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    continue
                if i % 2 == 0:
                    all_web.extend(result)
                else:
                    all_news.extend(result)

        total_results = len(all_web) + len(all_news)
        if total_results == 0:
            return SignalResult(
                source="serper_search",
                probability=None,
                confidence=0.0,
                reasoning="No search results found",
                model_used="cheap",
                data_points=0,
                raw_data={"queries": queries},
            )

        # Step 3: Compile and interpret
        self._emit(market_question, "interpret", f"{total_results} results found")
        return await self._interpret_results(
            market_question, market_end_date, all_web, all_news, total_results
        )

    async def _generate_queries(self, market_question: str) -> list[str]:
        prompt = (
            f'Given this prediction market question: "{market_question}"\n'
            f'Generate 2 focused search queries that would find the most relevant, '
            f'recent information to answer this question.\n'
            f'Return as JSON array of strings only.'
        )
        try:
            result = await self._llm.call_json(prompt, task_type="search_queries")
            if isinstance(result, list):
                return [str(q) for q in result[:2]]
        except Exception:
            pass
        return [market_question[:100]]

    async def _interpret_results(
        self,
        market_question: str,
        market_end_date: str,
        web_results: list[dict[str, Any]],
        news_results: list[dict[str, Any]],
        total_count: int,
    ) -> SignalResult:
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date) if market_end_date else ""

        # Build evidence brief
        evidence_lines: list[str] = []

        # Answer box / knowledge graph first (most authoritative)
        for r in web_results:
            if r.get("is_answer_box"):
                evidence_lines.append(f"[DIRECT ANSWER] {r['snippet']}")
            elif r.get("is_knowledge_graph"):
                evidence_lines.append(f"[KNOWLEDGE] {r['title']}: {r['snippet']}")

        # Top web results
        for r in web_results[:8]:
            if not r.get("is_answer_box") and not r.get("is_knowledge_graph"):
                evidence_lines.append(f"[WEB] {r['title']}: {r['snippet']}")

        # News results
        for r in news_results[:6]:
            date = f" ({r['date']})" if r.get("date") else ""
            source = f" [{r['source']}]" if r.get("source") else ""
            evidence_lines.append(f"[NEWS{date}]{source} {r['title']}: {r['snippet']}")

        evidence = "\n".join(evidence_lines[:20])

        prompt = (
            f'Market question: "{market_question}"\n'
            f'{date_ctx}\n\n'
            f'Search evidence:\n{evidence}\n\n'
            f'Based on this search evidence, estimate the probability of YES (0.0 to 1.0).\n'
            f'Respond as JSON:\n'
            f'{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}\n'
            f'If evidence is insufficient or contradictory, set low confidence.'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
        except Exception as e:
            return SignalResult(
                source="serper_search",
                probability=None,
                confidence=0.0,
                reasoning=f"LLM interpretation failed: {e}",
                model_used="cheap",
                data_points=total_count,
            )

        if not isinstance(result, dict):
            return SignalResult(
                source="serper_search",
                probability=None,
                confidence=0.0,
                reasoning="Invalid LLM response",
                model_used="cheap",
                data_points=total_count,
            )

        prob = result.get("probability")
        conf = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))

        if prob is not None:
            prob = max(0.0, min(1.0, float(prob)))
        conf = max(0.0, min(1.0, conf))

        return SignalResult(
            source="serper_search",
            probability=prob,
            confidence=conf,
            reasoning=reasoning,
            model_used="cheap",
            data_points=total_count,
            raw_data={
                "web_results": len(web_results),
                "news_results": len(news_results),
                "evidence_preview": evidence[:500],
            },
        )


def clear_signal_cache() -> None:
    _signal_cache.clear()
