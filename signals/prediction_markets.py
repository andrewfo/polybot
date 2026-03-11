"""Cross-platform prediction market consensus signal provider.

Fetches probabilities from Metaculus, Kalshi, and PredictIt for markets
that overlap with the Polymarket question. Uses cheap LLM to match
questions across platforms. No API keys required for any platform.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# API endpoints
METACULUS_API = "https://www.metaculus.com/api2/questions/"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
PREDICTIT_API = "https://www.predictit.org/api/marketdata/all/"


async def _search_metaculus(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search Metaculus for questions matching the query."""
    matches: list[dict[str, Any]] = []
    try:
        params = {
            "search": query,
            "limit": str(max_results),
            "status": "open",
            "type": "binary",
        }
        async with session.get(
            METACULUS_API,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Metaculus API returned %d for query '%s'", resp.status, query)
                return []
            data = await resp.json()

        results = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(results, list):
            return []

        for q in results[:max_results]:
            community_prediction = q.get("community_prediction", {})
            # Metaculus stores predictions in various formats
            prob = None
            if isinstance(community_prediction, dict):
                prob = community_prediction.get("full", {}).get("q2")
                if prob is None:
                    prob = community_prediction.get("q2")
            elif isinstance(community_prediction, (int, float)):
                prob = float(community_prediction)

            if prob is not None:
                matches.append({
                    "platform": "metaculus",
                    "title": q.get("title", ""),
                    "probability": float(prob),
                    "forecasters": q.get("number_of_predictions", 0),
                    "url": q.get("url", ""),
                })
    except Exception as e:
        logger.warning("Error searching Metaculus: %s", e)
    return matches


async def _search_kalshi(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search Kalshi for markets matching the query."""
    matches: list[dict[str, Any]] = []
    try:
        params = {"status": "open", "limit": str(max_results)}
        async with session.get(
            f"{KALSHI_API}/markets",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Kalshi API returned %d", resp.status)
                return []
            data = await resp.json()

        markets = data.get("markets", [])
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for market in markets:
            title = market.get("title", "")
            title_lower = title.lower()
            title_words = set(title_lower.split())
            # Simple keyword overlap matching
            overlap = len(query_words & title_words)
            if overlap >= min(2, len(query_words)):
                yes_price = market.get("yes_bid")
                if yes_price is None:
                    yes_price = market.get("last_price")
                if yes_price is not None:
                    # Kalshi prices are in cents (0-100)
                    prob = float(yes_price) / 100.0 if yes_price > 1 else float(yes_price)
                    matches.append({
                        "platform": "kalshi",
                        "title": title,
                        "probability": max(0.0, min(1.0, prob)),
                        "volume": market.get("volume", 0),
                        "ticker": market.get("ticker", ""),
                    })
    except Exception as e:
        logger.warning("Error searching Kalshi: %s", e)
    return matches[:max_results]


async def _search_predictit(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search PredictIt for markets matching the query."""
    matches: list[dict[str, Any]] = []
    try:
        async with session.get(
            PREDICTIT_API,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("PredictIt API returned %d", resp.status)
                return []
            data = await resp.json()

        markets = data.get("markets", [])
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for market in markets:
            name = market.get("name", "")
            name_lower = name.lower()
            name_words = set(name_lower.split())
            overlap = len(query_words & name_words)
            if overlap >= min(2, len(query_words)):
                contracts = market.get("contracts", [])
                for contract in contracts:
                    last_price = contract.get("lastTradePrice")
                    if last_price is not None and last_price > 0:
                        matches.append({
                            "platform": "predictit",
                            "title": f"{name} — {contract.get('name', '')}",
                            "probability": float(last_price),
                            "contract_name": contract.get("name", ""),
                        })
    except Exception as e:
        logger.warning("Error searching PredictIt: %s", e)
    return matches[:max_results]


class PredictionMarketsSignalProvider(SignalProvider):
    """Cross-platform prediction market consensus signal.

    Pipeline:
    1. Generate short search query from market question (cheap LLM)
    2. Search Metaculus, Kalshi, PredictIt in parallel
    3. Use cheap LLM to match results to the Polymarket question
    4. Aggregate matching probabilities into consensus estimate
    """

    name: str = "prediction_markets"

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
            logger.error("Prediction markets signal failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="prediction_markets",
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
            logger.warning("Failed to log prediction_markets signal to DB: %s", e)

    async def _run_pipeline(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
    ) -> SignalResult:
        import asyncio

        # Step 1: Generate a short search query
        self._emit(market_question, "query", "generating search terms")
        search_query = await self._generate_search_query(market_question)

        # Step 2: Search all platforms in parallel
        self._emit(market_question, "searching", "Metaculus + Kalshi + PredictIt")
        async with aiohttp.ClientSession() as session:
            metaculus_task = _search_metaculus(session, search_query)
            kalshi_task = _search_kalshi(session, search_query)
            predictit_task = _search_predictit(session, search_query)

            metaculus_results, kalshi_results, predictit_results = await asyncio.gather(
                metaculus_task, kalshi_task, predictit_task,
                return_exceptions=True,
            )

        # Collect all results, handling exceptions
        all_matches: list[dict[str, Any]] = []
        for results in [metaculus_results, kalshi_results, predictit_results]:
            if isinstance(results, Exception):
                logger.warning("Platform search error: %s", results)
                continue
            all_matches.extend(results)

        if not all_matches:
            return SignalResult(
                source="prediction_markets",
                probability=None,
                confidence=0.0,
                reasoning="No matching markets found on Metaculus, Kalshi, or PredictIt",
                model_used="none",
                data_points=0,
                raw_data={"search_query": search_query},
            )

        # Step 3: Use LLM to find best matches and extract consensus
        self._emit(market_question, "matching", f"{len(all_matches)} candidates found")
        return await self._evaluate_matches(market_question, all_matches, market_end_date)

    async def _generate_search_query(self, market_question: str) -> str:
        """Generate a short search query from the market question."""
        prompt = (
            f'Given this prediction market question: "{market_question}"\n'
            f'Generate a short search query (3-6 key words) that would match '
            f'similar questions on other prediction market platforms.\n'
            f'Return ONLY the search query text, nothing else.'
        )
        try:
            result = await self._llm.cheap(prompt)
            return result.strip().strip('"').strip("'")[:100]
        except Exception:
            # Fallback: use first 6 words of the question
            words = market_question.split()[:6]
            return " ".join(words)

    async def _evaluate_matches(
        self,
        market_question: str,
        matches: list[dict[str, Any]],
        market_end_date: str,
    ) -> SignalResult:
        """Use cheap LLM to evaluate which matches are relevant and compute consensus."""
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date) if market_end_date else ""

        matches_text = ""
        for i, m in enumerate(matches[:15], 1):
            matches_text += (
                f'{i}. [{m["platform"]}] "{m["title"]}" — probability: {m["probability"]:.2f}\n'
            )

        prompt = (
            f'Polymarket question: "{market_question}"\n'
            f'{date_ctx}\n'
            f'\n'
            f'The following markets were found on other prediction platforms:\n'
            f'{matches_text}\n'
            f'Which of these markets (by number) are asking essentially the same question '
            f'as the Polymarket question? Consider that question wording may differ but the '
            f'underlying event should be the same.\n'
            f'\n'
            f'For the matching markets, compute a consensus probability.\n'
            f'Respond as JSON:\n'
            f'{{"matching_indices": [1, 3, ...], "consensus_probability": 0.XX, '
            f'"confidence": 0.XX, "reasoning": "..."}}\n'
            f'If no markets match, set matching_indices to [] and consensus_probability to null.'
        )

        try:
            result = await self._llm.call_json(prompt, task_type="classify")
        except Exception as e:
            logger.warning("Failed to evaluate prediction market matches: %s", e)
            return SignalResult(
                source="prediction_markets",
                probability=None,
                confidence=0.0,
                reasoning=f"LLM evaluation failed: {e}",
                model_used="cheap",
                data_points=len(matches),
                raw_data={"matches": matches},
            )

        if not isinstance(result, dict):
            return SignalResult(
                source="prediction_markets",
                probability=None,
                confidence=0.0,
                reasoning="Invalid LLM response format",
                model_used="cheap",
                data_points=len(matches),
                raw_data={"matches": matches},
            )

        matching_indices = result.get("matching_indices", [])
        consensus_prob = result.get("consensus_probability")
        confidence = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))

        if not matching_indices or consensus_prob is None:
            return SignalResult(
                source="prediction_markets",
                probability=None,
                confidence=0.0,
                reasoning=f"No matching markets found across platforms. {reasoning}",
                model_used="cheap",
                data_points=0,
                raw_data={"matches": matches, "evaluation": result},
            )

        consensus_prob = max(0.0, min(1.0, float(consensus_prob)))
        confidence = max(0.0, min(1.0, confidence))

        # Collect matched markets for raw_data
        matched_markets = []
        for idx in matching_indices:
            if 1 <= idx <= len(matches):
                matched_markets.append(matches[idx - 1])

        return SignalResult(
            source="prediction_markets",
            probability=consensus_prob,
            confidence=confidence,
            reasoning=reasoning,
            model_used="cheap",
            data_points=len(matched_markets),
            raw_data={
                "matched_markets": matched_markets,
                "all_candidates": len(matches),
                "platforms_searched": ["metaculus", "kalshi", "predictit"],
            },
        )


def clear_signal_cache() -> None:
    _signal_cache.clear()
