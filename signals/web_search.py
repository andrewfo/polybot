"""Perplexity Sonar search-grounded signal provider.

Uses Perplexity's Sonar model via OpenRouter to get web-search-backed
probability estimates. Works for ALL market categories — the model
performs live web searches and synthesizes results with citations.

Requires OPENROUTER_API_KEY (already configured). No additional API key needed.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, Optional

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 600  # 10 minutes (crypto moves fast)


class WebSearchSignalProvider(SignalProvider):
    """Search-grounded signal provider using Perplexity Sonar via OpenRouter.

    Pipeline:
    1. Send market question to Sonar model with probability estimation prompt
    2. Sonar performs live web searches, synthesizes findings
    3. Returns probability estimate with cited evidence

    This is the strongest universal signal — it combines web search + LLM
    reasoning in a single call with real-time information.
    """

    name: str = "web_search"

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
        # Check cache
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                logger.debug("Cache hit for web_search signal: %s", market_question[:60])
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_search(market_question, market_category, market_end_date)
        except Exception as e:
            logger.error("Web search signal failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="web_search",
                probability=None,
                confidence=0.0,
                reasoning=f"Search pipeline error: {e}",
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
            logger.warning("Failed to log web_search signal to DB: %s", e)

    async def _run_search(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
    ) -> SignalResult:
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date) if market_end_date else ""
        date_line = f"\n{date_ctx}\n" if date_ctx else ""

        self._emit(market_question, "searching", "Sonar web search")

        prompt = (
            f'You are a prediction market analyst. Search the web for the latest information '
            f'about this prediction market question and estimate its probability.\n'
            f'{date_line}'
            f'Market question: "{market_question}"\n'
            f'Category: {market_category}\n'
            f'Resolution date: {market_end_date}\n'
            f'\n'
            f'Search for the most recent and relevant information. Then:\n'
            f'1. Summarize the key evidence you found (cite sources)\n'
            f'2. Estimate the probability of YES (0.0 to 1.0)\n'
            f'3. Rate your confidence (0.0 to 1.0)\n'
            f'\n'
            f'IMPORTANT: Do NOT use prediction market prices (Polymarket, Kalshi, Metaculus, '
            f'PredictIt, etc.) as evidence. We need an INDEPENDENT estimate based on '
            f'fundamental data — news, prices, announcements, on-chain data, etc. '
            f'Citing a prediction market price as evidence provides zero informational value.\n'
            f'\n'
            f'Respond as JSON only:\n'
            f'{{"probability": 0.XX, "confidence": 0.XX, "reasoning": "...", '
            f'"sources_found": 0, "key_evidence": ["...", "..."]}}'
        )

        response_text = await self._llm.sonar(prompt)

        # Parse the response
        parsed = self._llm._extract_json(response_text)
        if not isinstance(parsed, dict):
            # If Sonar didn't return JSON, extract what we can
            return SignalResult(
                source="web_search",
                probability=None,
                confidence=0.0,
                reasoning=f"Failed to parse Sonar response: {response_text[:200]}",
                model_used="sonar",
                data_points=0,
                raw_data={"raw_response": response_text[:1000]},
            )

        prob = parsed.get("probability")
        conf = float(parsed.get("confidence", 0.0))
        reasoning = str(parsed.get("reasoning", ""))
        sources_found = int(parsed.get("sources_found", 0))
        key_evidence = parsed.get("key_evidence", [])

        if prob is not None:
            prob = float(prob)
            prob = max(0.0, min(1.0, prob))
        conf = max(0.0, min(1.0, conf))

        # Sonar is a search/synthesis model, not calibration-trained.
        # Its self-reported confidence is systematically inflated (0.6-0.8
        # even for highly uncertain events). Discount by 0.7x.
        conf *= 0.7

        return SignalResult(
            source="web_search",
            probability=prob,
            confidence=conf,
            reasoning=reasoning,
            model_used="sonar",
            data_points=max(sources_found, len(key_evidence)),
            raw_data={
                "key_evidence": key_evidence,
                "sources_found": sources_found,
                "raw_response": response_text[:2000],
            },
        )


def clear_signal_cache() -> None:
    _signal_cache.clear()
