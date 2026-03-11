"""Polling and structured data signal provider.

Handles politics and general categories using RSS feeds and scraped polling data.
Economics and crypto categories are skipped — they have dedicated resolution providers.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp
from bs4 import BeautifulSoup

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

# Categories handled by dedicated resolution providers — skip immediately
SKIP_CATEGORIES = {"economics", "crypto"}

# User agent for HTTP requests
USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Polling data sources by category
POLLING_SOURCES = {
    "politics": [
        {
            "name": "FiveThirtyEight",
            "type": "rss",
            "url": "https://projects.fivethirtyeight.com/polls/rss.xml",
        },
        {
            "name": "RealClearPolitics",
            "type": "scrape",
            "url": "https://www.realclearpolitics.com/epolls/latest_polls/",
        },
    ],
}


async def _fetch_rss(
    session: aiohttp.ClientSession, url: str
) -> list[dict[str, str]]:
    """Fetch and parse an RSS feed, returning entries as dicts."""
    import feedparser

    entries: list[dict[str, str]] = []
    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("RSS feed returned %d for %s", resp.status, url)
                return []
            text = await resp.text()

        feed = feedparser.parse(text)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            summary = entry.get("summary", entry.get("description", ""))
            published = entry.get("published", "")
            entries.append({
                "title": title,
                "summary": summary,
                "published": published,
                "source": url,
            })
    except Exception as e:
        logger.warning("Error fetching RSS from %s: %s", url, e)
    return entries


async def _scrape_rcp(
    session: aiohttp.ClientSession, url: str
) -> list[dict[str, str]]:
    """Scrape polling average tables from RealClearPolitics."""
    entries: list[dict[str, str]] = []
    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("RCP scrape returned %d for %s", resp.status, url)
                return []
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        # Extract polling table rows
        tables = soup.find_all("table")
        for table in tables[:3]:  # Limit to first 3 tables
            rows = table.find_all("tr")
            for row in rows[:15]:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    text_parts = [cell.get_text(strip=True) for cell in cells]
                    row_text = " | ".join(text_parts)
                    if row_text.strip():
                        entries.append({
                            "title": text_parts[0] if text_parts else "",
                            "summary": row_text,
                            "published": "",
                            "source": "RealClearPolitics",
                        })

        # Also extract any article links/headlines
        links = soup.find_all("a", class_=True)
        for link in links[:10]:
            title = link.get_text(strip=True)
            if title and len(title) > 15:
                entries.append({
                    "title": title,
                    "summary": title,
                    "published": "",
                    "source": "RealClearPolitics",
                })
    except Exception as e:
        logger.warning("Error scraping RCP from %s: %s", url, e)
    return entries


def _format_structured_data(entries: list[dict[str, str]], max_entries: int = 20) -> str:
    """Format raw polling entries into a structured text block for the LLM."""
    if not entries:
        return ""
    lines: list[str] = []
    for i, entry in enumerate(entries[:max_entries], 1):
        source = entry.get("source", "unknown")
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        published = entry.get("published", "")
        date_str = f" ({published})" if published else ""
        lines.append(f"{i}. [{source}]{date_str} {title}: {summary}")
    return "\n".join(lines)


class PollingSignalProvider(SignalProvider):
    """Structured data signal provider for politics and general categories.

    Pipeline:
    1. If category is economics or crypto → return confidence=0 immediately
    2. Select data sources based on market category
    3. Fetch and parse structured data (RSS + scraping)
    4. Cheap LLM interprets data in context of market question
    """

    name: str = "polling"

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
        """Produce a polling-based signal for a market question."""
        # Skip economics and crypto — handled by dedicated resolution providers
        category_lower = market_category.lower()
        if category_lower in SKIP_CATEGORIES:
            return SignalResult(
                source="polling",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' handled by dedicated resolution provider",
                model_used="none",
                data_points=0,
            )

        # Check if we have data sources for this category
        if category_lower not in POLLING_SOURCES:
            return SignalResult(
                source="polling",
                probability=None,
                confidence=0.0,
                reasoning=f"No structured data sources for category '{market_category}'",
                model_used="none",
                data_points=0,
            )

        # Check cache
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                logger.debug("Cache hit for polling signal: %s", market_question[:60])
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(
                market_question, category_lower, market_end_date
            )
        except Exception as e:
            logger.error(
                "Polling signal pipeline failed for '%s': %s",
                market_question[:60], e,
            )
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="polling",
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
        category: str,
        market_end_date: str,
    ) -> SignalResult:
        """Execute the full polling signal pipeline."""
        sources = POLLING_SOURCES.get(category, [])
        all_entries: list[dict[str, str]] = []

        self._emit(market_question, "polling", f"fetching {len(sources)} sources")
        async with aiohttp.ClientSession() as session:
            for source in sources:
                source_type = source["type"]
                url = source["url"]

                if source_type == "rss":
                    entries = await _fetch_rss(session, url)
                elif source_type == "scrape":
                    entries = await _scrape_rcp(session, url)
                else:
                    logger.warning("Unknown source type: %s", source_type)
                    continue

                all_entries.extend(entries)

        if not all_entries:
            return SignalResult(
                source="polling",
                probability=None,
                confidence=0.0,
                reasoning="No structured data available from polling sources",
                model_used="none",
                data_points=0,
            )

        # Format data for LLM
        structured_data = _format_structured_data(all_entries)

        # Use cheap LLM to interpret data
        prompt = (
            f'Market question: "{market_question}"\n'
            f"Relevant data:\n{structured_data}\n"
            f'Based on this data, estimate the probability of YES (0.0 to 1.0).\n'
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))

                # Validate probability
                if prob is not None:
                    prob = float(prob)
                    if not (0.0 <= prob <= 1.0):
                        prob = max(0.0, min(1.0, prob))

                # Validate confidence
                conf = max(0.0, min(1.0, conf))

                return SignalResult(
                    source="polling",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(all_entries),
                    raw_data={
                        "structured_data": structured_data,
                        "entry_count": len(all_entries),
                    },
                )
        except Exception as e:
            logger.error("Failed to interpret polling data: %s", e)

        return SignalResult(
            source="polling",
            probability=None,
            confidence=0.0,
            reasoning="Failed to interpret polling data",
            model_used="none",
            data_points=len(all_entries),
            raw_data={"structured_data": structured_data},
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
