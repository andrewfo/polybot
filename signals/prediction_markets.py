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

from config import settings
from core import db, fetch_with_retry
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
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com/markets"

# Log once per process if Metaculus is skipped
_metaculus_skip_logged = False


async def _search_metaculus(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search Metaculus for questions matching the query.

    Requires METACULUS_API_TOKEN in settings. If not configured, silently
    skips (logs once per process to avoid spam).
    """
    global _metaculus_skip_logged

    token = settings.METACULUS_API_TOKEN
    if not token:
        if not _metaculus_skip_logged:
            logger.info("Metaculus API token not configured — skipping Metaculus. "
                        "Set METACULUS_API_TOKEN env var to enable.")
            _metaculus_skip_logged = True
        return []

    params = {
        "search": query,
        "limit": str(max_results),
        "status": "open",
        "type": "binary",
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Token {token}",
    }

    async def _attempt() -> list[dict[str, Any]]:
        async with session.get(
            METACULUS_API,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 403:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=403,
                    message="403 — check METACULUS_API_TOKEN",
                )
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label=f"Metaculus search ({query[:40]})")
    if data is None:
        return []

    matches: list[dict[str, Any]] = []
    results = data.get("results", data) if isinstance(data, dict) else data
    if not isinstance(results, list):
        return []

    for q in results[:max_results]:
        community_prediction = q.get("community_prediction", {})
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
    return matches


async def _search_kalshi(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search Kalshi for markets matching the query."""
    async def _attempt() -> dict[str, Any]:
        params = {"status": "open", "limit": str(max_results)}
        async with session.get(
            f"{KALSHI_API}/markets",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    data = await fetch_with_retry(_attempt, label="Kalshi markets")
    if data is None:
        return []

    matches: list[dict[str, Any]] = []
    markets = data.get("markets", [])
    query_lower = query.lower()
    query_words = set(query_lower.split())

    for market in markets:
        title = market.get("title", "")
        title_lower = title.lower()
        title_words = set(title_lower.split())
        overlap = len(query_words & title_words)
        if overlap >= min(2, len(query_words)):
            yes_price = market.get("yes_bid")
            if yes_price is None:
                yes_price = market.get("last_price")
            if yes_price is not None:
                prob = float(yes_price) / 100.0 if yes_price > 1 else float(yes_price)
                matches.append({
                    "platform": "kalshi",
                    "title": title,
                    "probability": max(0.0, min(1.0, prob)),
                    "volume": market.get("volume", 0),
                    "ticker": market.get("ticker", ""),
                })
    return matches[:max_results]


async def _search_polymarket_gamma(
    session: aiohttp.ClientSession, query: str, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search Polymarket Gamma API for related markets (cross-market consensus).

    Finds other Polymarket markets on similar topics to compare prices.
    This helps identify if our target market is mispriced relative to
    related markets on the same platform.
    """
    matches: list[dict[str, Any]] = []
    try:
        # Use Gamma's text_query parameter to search
        params = {
            "closed": "false",
            "active": "true",
            "limit": str(max_results * 3),  # fetch extra to filter
            "order": "volume24hr",
            "ascending": "false",
        }
        async with session.get(
            POLYMARKET_GAMMA_API,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Polymarket Gamma API returned %d", resp.status)
                return []
            markets = await resp.json()

        if not isinstance(markets, list):
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())

        for market in markets:
            question = market.get("question", "")
            question_lower = question.lower()
            question_words = set(question_lower.split())
            overlap = len(query_words & question_words)
            if overlap >= min(2, len(query_words)):
                # Parse outcome prices
                outcome_prices = market.get("outcomePrices", "[]")
                if isinstance(outcome_prices, str):
                    import json
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except (json.JSONDecodeError, TypeError):
                        outcome_prices = []
                if outcome_prices and len(outcome_prices) >= 1:
                    try:
                        prob = float(outcome_prices[0])
                    except (ValueError, TypeError, IndexError):
                        continue
                    matches.append({
                        "platform": "polymarket",
                        "title": question,
                        "probability": max(0.0, min(1.0, prob)),
                        "volume": float(market.get("volume24hr", 0) or 0),
                        "liquidity": float(market.get("liquidityNum", 0) or 0),
                    })
    except Exception as e:
        logger.warning("Error searching Polymarket Gamma: %s", e)
    return matches[:max_results]


STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over",
    "and", "but", "or", "nor", "not", "so", "yet",
    "it", "its", "this", "that", "these", "those",
    "what", "which", "who", "whom", "when", "where", "how", "why",
    "if", "then", "than", "both", "each", "any", "all", "more",
    "other", "some", "such", "no", "only", "same", "very",
})


def _extract_search_keywords(question: str) -> str:
    """Extract meaningful search keywords from a market question.

    Strips stop words, keeps numbers/capitalized/meaningful words.
    Returns up to 6 keywords joined by space.
    """
    words = question.replace("?", "").replace(",", "").replace("'", "").split()
    keywords = []
    for w in words:
        w_lower = w.lower()
        # Keep numbers, $ amounts, dates
        if any(c.isdigit() for c in w):
            keywords.append(w.strip("\"'"))
        # Keep words not in stop words
        elif w_lower not in STOP_WORDS and len(w_lower) > 1:
            keywords.append(w.strip("\"'"))
    return " ".join(keywords[:6])


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Word-set Jaccard similarity after removing stop words."""
    words_a = {w.lower() for w in text_a.split() if w.lower() not in STOP_WORDS and len(w) > 1}
    words_b = {w.lower() for w in text_b.split() if w.lower() not in STOP_WORDS and len(w) > 1}
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


class PredictionMarketsSignalProvider(SignalProvider):
    """Cross-platform prediction market consensus signal.

    Pipeline:
    1. Extract search keywords from market question (deterministic)
    2. Search Metaculus, Kalshi, Polymarket Gamma in parallel
    3. Match results by string similarity (deterministic)
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

        # Step 1: Extract search keywords (deterministic, no LLM)
        self._emit(market_question, "query", "extracting search keywords")
        search_query = _extract_search_keywords(market_question)

        # Step 2: Search all platforms in parallel
        self._emit(market_question, "searching", "Metaculus + Kalshi + Polymarket Gamma")
        async with aiohttp.ClientSession() as session:
            metaculus_task = _search_metaculus(session, search_query)
            kalshi_task = _search_kalshi(session, search_query)
            polymarket_task = _search_polymarket_gamma(session, search_query)

            metaculus_results, kalshi_results, predictit_results = await asyncio.gather(
                metaculus_task, kalshi_task, polymarket_task,
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
                reasoning="No matching markets found on Metaculus, Kalshi, or Polymarket Gamma",
                model_used="none",
                data_points=0,
                raw_data={"search_query": search_query},
            )

        # Step 3: Match by string similarity (deterministic, no LLM)
        self._emit(market_question, "matching", f"{len(all_matches)} candidates found")
        return self._match_and_compute_consensus(market_question, all_matches)

    def _match_and_compute_consensus(
        self,
        market_question: str,
        matches: list[dict[str, Any]],
        similarity_threshold: float = 0.40,
    ) -> SignalResult:
        """Match candidates by string similarity and compute consensus.

        Filters matches by Jaccard similarity threshold (0.40, tightened
        from 0.30 to reduce false positive matches), then computes a
        weighted consensus probability where weight = similarity × log(1 + volume).
        """
        import math

        scored_matches = []
        for m in matches:
            title = m.get("title", "")
            sim = _jaccard_similarity(market_question, title)
            if sim >= similarity_threshold:
                scored_matches.append((m, sim))

        if not scored_matches:
            return SignalResult(
                source="prediction_markets",
                probability=None,
                confidence=0.0,
                reasoning="No matching markets found across platforms (similarity threshold not met)",
                model_used="none",
                data_points=0,
                raw_data={"all_candidates": len(matches)},
            )

        # Weighted consensus: similarity × log(1 + volume) for liquidity weighting
        total_weight = 0.0
        weighted_prob = 0.0
        for m, sim in scored_matches:
            volume = float(m.get("volume", 0) or m.get("liquidity", 0) or 0)
            w = sim * math.log1p(max(volume, 1.0))
            weighted_prob += m["probability"] * w
            total_weight += w

        consensus_prob = weighted_prob / total_weight if total_weight > 0 else 0.5
        consensus_prob = max(0.0, min(1.0, consensus_prob))

        # Confidence from match count + average similarity
        avg_sim = sum(sim for _, sim in scored_matches) / len(scored_matches)
        match_count_factor = min(1.0, len(scored_matches) / 3.0)  # 3+ matches = full credit
        confidence = min(0.9, avg_sim * 0.6 + match_count_factor * 0.4)

        matched_markets = [{**m, "similarity": round(sim, 3)} for m, sim in scored_matches]
        platforms = list({m.get("platform", "?") for m in matched_markets})
        reasoning = (
            f"Cross-platform consensus from {len(scored_matches)} matching markets "
            f"(avg similarity {avg_sim:.2f}) on {', '.join(platforms)}"
        )

        return SignalResult(
            source="prediction_markets",
            probability=consensus_prob,
            confidence=confidence,
            reasoning=reasoning,
            model_used="none",
            data_points=len(scored_matches),
            raw_data={
                "matched_markets": matched_markets,
                "all_candidates": len(matches),
                "platforms_searched": ["metaculus", "kalshi", "polymarket_gamma"],
            },
        )



def clear_signal_cache() -> None:
    _signal_cache.clear()
