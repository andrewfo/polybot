"""News and sentiment signal provider.

Fetches articles from Google News RSS and Reddit, summarizes with cheap LLM,
and produces an initial probability estimate. No API keys required.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import feedparser

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

REDDIT_USER_AGENT = "polymarket-bot/1.0 (signal research)"
GOOGLE_NEWS_USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Minimum articles needed for a meaningful signal
MIN_ARTICLES = 2

# Deduplication threshold (fraction of overlapping words)
DEDUP_SIMILARITY_THRESHOLD = 0.80


def _title_similarity(a: str, b: str) -> float:
    """Compute word-overlap similarity between two titles."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    smaller = min(len(words_a), len(words_b))
    return len(intersection) / smaller if smaller > 0 else 0.0


def _deduplicate_articles(articles: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove near-duplicate articles based on title similarity."""
    unique: list[dict[str, str]] = []
    for article in articles:
        is_dup = False
        for existing in unique:
            if _title_similarity(article["title"], existing["title"]) >= DEDUP_SIMILARITY_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            unique.append(article)
    return unique


async def _fetch_google_news(
    session: aiohttp.ClientSession, query: str, max_results: int = 10
) -> list[dict[str, str]]:
    """Fetch articles from Google News RSS for a search query."""
    url = f"https://news.google.com/rss/search?q={query}"
    articles: list[dict[str, str]] = []
    try:
        async with session.get(
            url,
            headers={"User-Agent": GOOGLE_NEWS_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Google News RSS returned %d for query '%s'", resp.status, query)
                return []
            text = await resp.text()

        feed = feedparser.parse(text)
        for entry in feed.entries[:max_results]:
            title = entry.get("title", "")
            snippet = entry.get("summary", entry.get("description", ""))
            published = entry.get("published", "")
            articles.append({
                "title": title,
                "snippet": snippet,
                "source": "google_news",
                "published": published,
            })
    except Exception as e:
        logger.warning("Error fetching Google News for '%s': %s", query, e)
    return articles


async def _fetch_reddit(
    session: aiohttp.ClientSession, query: str, max_results: int = 10
) -> list[dict[str, str]]:
    """Fetch posts from Reddit search API."""
    url = "https://www.reddit.com/search.json"
    params = {"q": query, "sort": "relevance", "t": "week", "limit": str(max_results)}
    articles: list[dict[str, str]] = []
    try:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": REDDIT_USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Reddit search returned %d for query '%s'", resp.status, query)
                return []
            data = await resp.json()

        children = data.get("data", {}).get("children", [])
        for child in children[:max_results]:
            post = child.get("data", {})
            title = post.get("title", "")
            selftext = post.get("selftext", "")
            snippet = selftext[:500] if selftext else title
            articles.append({
                "title": title,
                "snippet": snippet,
                "source": "reddit",
                "published": "",
            })
    except Exception as e:
        logger.warning("Error fetching Reddit for '%s': %s", query, e)
    return articles


class NewsSignalProvider(SignalProvider):
    """News and sentiment signal provider.

    Pipeline:
    1. Cheap LLM generates search queries from market question
    2. Fetch articles from Google News RSS + Reddit
    3. Deduplicate by title similarity
    4. Cheap LLM summarizes each article with YES/NO/NEUTRAL direction
    5. Cheap LLM produces initial probability estimate from evidence
    """

    name: str = "news"

    # Optional progress callback: (market_question, stage, detail) -> None
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
        """Produce a news-based signal for a market question."""
        # Check cache
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                logger.debug("Cache hit for news signal: %s", market_question[:60])
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(market_question, market_category, market_end_date)
        except Exception as e:
            logger.error("News signal pipeline failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="news",
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
        market_category: str,
        market_end_date: str,
    ) -> SignalResult:
        """Execute the full news signal pipeline."""
        # Step 1: Generate search queries
        self._emit(market_question, "queries", "generating search queries")
        queries = await self._generate_search_queries(market_question)
        if not queries:
            return SignalResult(
                source="news",
                probability=None,
                confidence=0.0,
                reasoning="Failed to generate search queries",
                model_used="none",
                data_points=0,
            )

        # Step 2: Fetch articles from all sources
        self._emit(market_question, "fetch", f"queries: {', '.join(queries)}")
        all_articles: list[dict[str, str]] = []
        async with aiohttp.ClientSession() as session:
            for query in queries:
                google_articles = await _fetch_google_news(session, query)
                reddit_articles = await _fetch_reddit(session, query)
                all_articles.extend(google_articles)
                all_articles.extend(reddit_articles)

        # Step 3: Deduplicate
        self._emit(market_question, "dedup", f"{len(all_articles)} articles fetched")
        unique_articles = _deduplicate_articles(all_articles)
        logger.info(
            "News signal: %d articles fetched, %d after dedup for '%s'",
            len(all_articles), len(unique_articles), market_question[:60],
        )

        # Insufficient data check
        if len(unique_articles) < MIN_ARTICLES:
            return SignalResult(
                source="news",
                probability=None,
                confidence=0.0,
                reasoning=f"Insufficient articles found ({len(unique_articles)} < {MIN_ARTICLES})",
                model_used="none",
                data_points=len(unique_articles),
                raw_data={"articles": unique_articles},
            )

        # Step 4: Summarize each article
        self._emit(market_question, "summarize", f"{len(unique_articles)} unique articles")
        summaries = await self._summarize_articles(market_question, unique_articles)

        # Step 5: Compile evidence and estimate probability
        self._emit(market_question, "estimate", f"{len(summaries)} summaries compiled")
        compiled = self._compile_summaries(summaries)
        result = await self._estimate_probability(market_question, compiled, len(unique_articles))

        result.raw_data = {
            "queries": queries,
            "article_count": len(unique_articles),
            "summaries": summaries,
        }

        return result

    async def _generate_search_queries(self, market_question: str) -> list[str]:
        """Use cheap LLM to generate 2-3 search queries from market question."""
        prompt = (
            f'Given this prediction market question: "{market_question}"\n'
            f"Generate 2-3 short search queries (3-6 words each) that would find relevant recent news.\n"
            f"Return as JSON array of strings, nothing else."
        )
        try:
            result = await self._llm.call_json(prompt, task_type="search_queries")
            if isinstance(result, list) and all(isinstance(q, str) for q in result):
                return result[:3]
            logger.warning("Unexpected search query format: %s", result)
            return []
        except Exception as e:
            logger.error("Failed to generate search queries: %s", e)
            return []

    async def _summarize_articles(
        self,
        market_question: str,
        articles: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Summarize each article with cheap LLM, indicating YES/NO/NEUTRAL direction."""
        summaries: list[dict[str, str]] = []
        for article in articles:
            prompt = (
                f'Market question: "{market_question}"\n'
                f'Article title: "{article["title"]}"\n'
                f'Article snippet: "{article["snippet"][:500]}"\n'
                f"Summarize in 2 sentences. State whether this evidence supports YES or NO "
                f"for the market question, or is neutral.\n"
                f'Respond as JSON: {{"summary": "...", "direction": "YES"|"NO"|"NEUTRAL"}}'
            )
            try:
                result = await self._llm.call_json(prompt, task_type="summarize")
                if isinstance(result, dict) and "summary" in result and "direction" in result:
                    summaries.append({
                        "title": article["title"],
                        "summary": result["summary"],
                        "direction": result["direction"],
                    })
                else:
                    logger.warning("Unexpected summary format for '%s'", article["title"][:40])
            except Exception as e:
                logger.warning("Failed to summarize article '%s': %s", article["title"][:40], e)
                # Graceful degradation: skip this article
                continue

        return summaries

    def _compile_summaries(self, summaries: list[dict[str, str]]) -> str:
        """Compile article summaries into a single evidence brief."""
        if not summaries:
            return "No evidence available."

        lines: list[str] = []
        for i, s in enumerate(summaries, 1):
            lines.append(f"{i}. [{s['direction']}] {s['title']}: {s['summary']}")
        return "\n".join(lines)

    async def _estimate_probability(
        self,
        market_question: str,
        compiled_summaries: str,
        data_points: int,
    ) -> SignalResult:
        """Use cheap LLM to estimate probability from compiled evidence."""
        prompt = (
            f'Market question: "{market_question}"\n'
            f"Evidence summaries:\n{compiled_summaries}\n"
            f"Based on this evidence, estimate the probability of YES (0.0 to 1.0).\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}\n'
            f"If there is insufficient evidence, set probability to null and confidence to 0."
        )
        try:
            result = await self._llm.call_json(prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))
                model_used = "cheap"

                # Validate probability
                if prob is not None:
                    prob = float(prob)
                    if not (0.0 <= prob <= 1.0):
                        prob = max(0.0, min(1.0, prob))

                # Validate confidence
                conf = max(0.0, min(1.0, conf))

                return SignalResult(
                    source="news",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used=model_used,
                    data_points=data_points,
                )
        except Exception as e:
            logger.error("Failed to estimate probability: %s", e)

        return SignalResult(
            source="news",
            probability=None,
            confidence=0.0,
            reasoning="Failed to estimate probability from evidence",
            model_used="none",
            data_points=data_points,
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
