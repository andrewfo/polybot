"""Political/legislative signal provider using Congress.gov API.

Fetches bill status, voting data, and legislative activity from
the official Congress.gov API. Useful for markets about legislation
passing, government shutdowns, confirmations, etc.

Requires CONGRESS_API_KEY (free from api.data.gov, 5000 req/hr).
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp

from config.settings import CONGRESS_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour — legislation moves slowly

CONGRESS_API_BASE = "https://api.congress.gov/v3"
USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Categories handled
HANDLED_CATEGORIES = {"politics"}

# Legislative status progression (higher = more likely to pass)
BILL_STATUS_SCORES: dict[str, float] = {
    "introduced": 0.05,
    "referred_to_committee": 0.08,
    "reported_by_committee": 0.20,
    "passed_one_chamber": 0.35,
    "passed_both_chambers": 0.85,
    "sent_to_president": 0.90,
    "signed_into_law": 1.00,
    "vetoed": 0.10,
}


async def _search_bills(
    session: aiohttp.ClientSession,
    query: str,
    api_key: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search Congress.gov for bills matching the query."""
    bills: list[dict[str, Any]] = []
    try:
        params = {
            "query": query,
            "limit": str(limit),
            "sort": "updateDate+desc",
            "api_key": api_key,
        }
        async with session.get(
            f"{CONGRESS_API_BASE}/bill",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Congress.gov API returned %d for query '%s'", resp.status, query)
                return []
            data = await resp.json()

        for bill in data.get("bills", []):
            bills.append({
                "title": bill.get("title", ""),
                "number": bill.get("number", ""),
                "type": bill.get("type", ""),
                "congress": bill.get("congress", ""),
                "latest_action": bill.get("latestAction", {}).get("text", ""),
                "latest_action_date": bill.get("latestAction", {}).get("actionDate", ""),
                "origin_chamber": bill.get("originChamber", ""),
                "update_date": bill.get("updateDate", ""),
                "url": bill.get("url", ""),
            })
    except Exception as e:
        logger.warning("Error searching Congress.gov: %s", e)
    return bills


async def _get_bill_actions(
    session: aiohttp.ClientSession,
    congress: str,
    bill_type: str,
    bill_number: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Get action history for a specific bill."""
    actions: list[dict[str, Any]] = []
    try:
        params = {"api_key": api_key, "limit": "20"}
        url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type.lower()}/{bill_number}/actions"
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        for action in data.get("actions", []):
            actions.append({
                "date": action.get("actionDate", ""),
                "text": action.get("text", ""),
                "type": action.get("type", ""),
                "chamber": action.get("actionCode", ""),
            })
    except Exception as e:
        logger.warning("Error fetching bill actions: %s", e)
    return actions


async def _fetch_recent_legislation(
    session: aiohttp.ClientSession,
    api_key: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch recently updated legislation."""
    bills: list[dict[str, Any]] = []
    try:
        params = {
            "limit": str(limit),
            "sort": "updateDate+desc",
            "api_key": api_key,
        }
        async with session.get(
            f"{CONGRESS_API_BASE}/bill",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        for bill in data.get("bills", []):
            bills.append({
                "title": bill.get("title", ""),
                "number": bill.get("number", ""),
                "type": bill.get("type", ""),
                "congress": bill.get("congress", ""),
                "latest_action": bill.get("latestAction", {}).get("text", ""),
                "latest_action_date": bill.get("latestAction", {}).get("actionDate", ""),
            })
    except Exception as e:
        logger.warning("Error fetching recent legislation: %s", e)
    return bills


class PoliticalSignalProvider(SignalProvider):
    """Political/legislative signal provider.

    Pipeline:
    1. Check if market is politics category — skip otherwise
    2. Use cheap LLM to determine if market is about legislation
    3. Search Congress.gov for relevant bills
    4. Fetch bill action history
    5. Use cheap LLM to interpret legislative status and estimate probability
    """

    name: str = "political_data"

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
        if market_category not in HANDLED_CATEGORIES:
            return SignalResult(
                source="political_data",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' not handled by political provider",
                model_used="none",
                data_points=0,
            )

        if not CONGRESS_API_KEY:
            return SignalResult(
                source="political_data",
                probability=None,
                confidence=0.0,
                reasoning="CONGRESS_API_KEY not configured",
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
            result = await self._run_pipeline(market_question, market_end_date)
        except Exception as e:
            logger.error("Political signal failed for '%s': %s", market_question[:60], e)
            result = SignalResult(
                source="political_data",
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
            logger.warning("Failed to log political signal to DB: %s", e)

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
    ) -> SignalResult:
        # Step 1: Generate search query
        self._emit(market_question, "query", "extracting legislative search terms")
        search_query = await self._generate_search_query(market_question)

        # Step 2: Search Congress.gov
        self._emit(market_question, "search", f"searching Congress.gov for: {search_query}")
        async with aiohttp.ClientSession() as session:
            bills = await _search_bills(session, search_query, CONGRESS_API_KEY)

            # Also fetch recent legislation as context
            recent = await _fetch_recent_legislation(session, CONGRESS_API_KEY)

        if not bills and not recent:
            return SignalResult(
                source="political_data",
                probability=None,
                confidence=0.0,
                reasoning=f"No relevant legislation found on Congress.gov for: {search_query}",
                model_used="cheap",
                data_points=0,
                raw_data={"search_query": search_query},
            )

        # Step 3: Use LLM to interpret
        self._emit(market_question, "interpret", f"{len(bills)} bills found")
        return await self._interpret_legislative_data(
            market_question, market_end_date, bills, recent
        )

    async def _generate_search_query(self, market_question: str) -> str:
        prompt = (
            f'Given this political prediction market question: "{market_question}"\n'
            f'Generate a short search query (3-6 key words) to find relevant '
            f'legislation, bills, or congressional actions on Congress.gov.\n'
            f'Return ONLY the search query text, nothing else.'
        )
        try:
            result = await self._llm.cheap(prompt)
            return result.strip().strip('"').strip("'")[:100]
        except Exception:
            words = market_question.split()[:6]
            return " ".join(words)

    async def _interpret_legislative_data(
        self,
        market_question: str,
        market_end_date: str,
        bills: list[dict[str, Any]],
        recent: list[dict[str, Any]],
    ) -> SignalResult:
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date) if market_end_date else ""

        bills_text = ""
        for i, b in enumerate(bills[:10], 1):
            bills_text += (
                f'{i}. "{b["title"]}" ({b["type"]}{b["number"]})\n'
                f'   Latest action ({b["latest_action_date"]}): {b["latest_action"]}\n'
            )

        recent_text = ""
        for r in recent[:5]:
            recent_text += f'- "{r["title"]}" — {r["latest_action"]}\n'

        prompt = (
            f'Market question: "{market_question}"\n'
            f'{date_ctx}\n\n'
            f'Relevant bills from Congress.gov:\n{bills_text}\n'
            f'Recent legislative activity:\n{recent_text}\n\n'
            f'Based on this legislative data, estimate the probability of YES for the market question.\n'
            f'Consider: bill status, historical passage rates, political dynamics, timeline.\n'
            f'Respond as JSON:\n'
            f'{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "...", "relevant_bills": 0}}'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
        except Exception as e:
            return SignalResult(
                source="political_data",
                probability=None,
                confidence=0.0,
                reasoning=f"LLM interpretation failed: {e}",
                model_used="cheap",
                data_points=len(bills),
                raw_data={"bills": bills},
            )

        if not isinstance(result, dict):
            return SignalResult(
                source="political_data",
                probability=None,
                confidence=0.0,
                reasoning="Invalid LLM response format",
                model_used="cheap",
                data_points=len(bills),
            )

        prob = result.get("probability")
        conf = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))
        relevant_count = int(result.get("relevant_bills", len(bills)))

        if prob is not None:
            prob = max(0.0, min(1.0, float(prob)))
        conf = max(0.0, min(1.0, conf))

        return SignalResult(
            source="political_data",
            probability=prob,
            confidence=conf,
            reasoning=reasoning,
            model_used="cheap",
            data_points=relevant_count,
            raw_data={
                "bills": bills[:5],
                "recent_legislation": recent[:3],
            },
        )


def clear_signal_cache() -> None:
    _signal_cache.clear()
