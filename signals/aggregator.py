"""Signal aggregator with frontier model final probability call.

Collects signals from all providers (news, polling, resolution_econ, resolution_crypto),
computes a weighted preliminary estimate, then makes the single FRONTIER MODEL call
that determines the final probability. This is the only place the expensive frontier
model is used in the signal pipeline.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import RESOLUTION_SIGNAL_WEIGHT
from core import db
from core.llm import LLMClient, LLMError
from signals.base import SignalProvider, SignalResult
from signals.news import NewsSignalProvider
from signals.polling import PollingSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.resolution_econ import EconomicsResolutionProvider

logger = logging.getLogger(__name__)

# Source-based weight multipliers for the preliminary estimate
SIGNAL_WEIGHT_MULTIPLIERS: dict[str, float] = {
    "resolution_econ": RESOLUTION_SIGNAL_WEIGHT,   # Direct resolution source — data from FRED
    "resolution_crypto": RESOLUTION_SIGNAL_WEIGHT,  # Direct resolution source — data from CoinGecko
    "polling": 1.5,                                  # Structured data
    "news": 1.0,                                     # Baseline
}

# Minimum frontier confidence to proceed
MIN_FRONTIER_CONFIDENCE = 0.4


@dataclass
class AggregatedSignal:
    """Final aggregated signal result with full audit trail."""

    market_question: str
    market_category: str
    market_price: float
    final_probability: float
    confidence: float
    reasoning: str
    signals_agreement: str          # "agree" | "mixed" | "disagree"
    market_efficiency: str          # "underpriced" | "overpriced" | "fair"
    preliminary_probability: float
    individual_signals: list[SignalResult]
    frontier_model_used: str
    total_data_points: int
    skipped: bool = False
    skip_reason: str = ""


def _compute_effective_weight(signal: SignalResult) -> float:
    """Compute the effective weight for a signal in the preliminary estimate."""
    multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
    return signal.confidence * multiplier


def compute_preliminary_probability(signals: list[SignalResult]) -> float:
    """Compute weighted average probability from usable signals.

    Each signal's weight is: confidence * source_multiplier.
    Resolution source signals get RESOLUTION_SIGNAL_WEIGHT multiplier.
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for signal in signals:
        if signal.probability is None or signal.confidence <= 0:
            continue
        ew = _compute_effective_weight(signal)
        weighted_sum += signal.probability * ew
        total_weight += ew

    if total_weight == 0:
        return 0.5  # Default if no usable signals

    return weighted_sum / total_weight


def _build_frontier_prompt(
    question: str,
    category: str,
    market_price: float,
    end_date: str,
    signals: list[SignalResult],
    preliminary_prob: float,
) -> str:
    """Build the frontier model prompt from the plan spec."""
    signal_lines: list[str] = []
    for signal in signals:
        resolution_label = (
            " (DIRECT RESOLUTION SOURCE)"
            if signal.source.startswith("resolution_")
            else ""
        )
        signal_lines.append(
            f"- Source: {signal.source}{resolution_label}\n"
            f"  Estimate: {signal.probability}\n"
            f"  Confidence: {signal.confidence}\n"
            f"  Reasoning: {signal.reasoning}\n"
            f"  Data points analyzed: {signal.data_points}"
        )

    signals_block = "\n".join(signal_lines)

    return (
        f'You are a superforecaster analyzing a prediction market. Your job is to estimate the true probability of an event as accurately as possible.\n'
        f'\n'
        f'Market question: "{question}"\n'
        f'Market category: {category}\n'
        f'Current market price (implied probability): {market_price}\n'
        f'Market resolution date: {end_date}\n'
        f'\n'
        f'Signal analysis from multiple sources:\n'
        f'{signals_block}\n'
        f'\n'
        f'Preliminary weighted estimate: {preliminary_prob}\n'
        f'\n'
        f'Instructions:\n'
        f'1. Critically evaluate each signal source. Are any likely biased or unreliable?\n'
        f'2. Signals marked as "DIRECT RESOLUTION SOURCE" come from the actual data providers (FRED, CoinGecko) whose data would be used to resolve this market. Weight these more heavily than news or sentiment signals.\n'
        f'3. IMPORTANT: Check whether the market\'s resolution criteria specifies a particular data source, exchange, timestamp methodology, or TWAP that might differ from the signal data provided. If the resolution source differs from our data source (e.g., market resolves on Binance spot price but our data is from CoinGecko aggregated price), adjust your confidence downward accordingly.\n'
        f'4. Consider base rates for this type of event.\n'
        f'5. Consider what information the market might have that our signals don\'t.\n'
        f'6. Provide your final probability estimate.\n'
        f'7. Rate your overall confidence (0-1) in this estimate.\n'
        f'8. Explain your reasoning in 2-3 sentences.\n'
        f'\n'
        f'IMPORTANT: Be calibrated. If you\'re unsure, your probability should be closer to the market price, not further from it. Only diverge significantly from the market when evidence is strong.\n'
        f'\n'
        f'Respond as JSON only:\n'
        f'{{\n'
        f'  "final_probability": 0.XX,\n'
        f'  "confidence": 0.XX,\n'
        f'  "reasoning": "...",\n'
        f'  "signals_agreement": "agree"|"mixed"|"disagree",\n'
        f'  "market_efficiency_assessment": "underpriced"|"overpriced"|"fair"\n'
        f'}}'
    )


class SignalAggregator:
    """Aggregates signals from all providers and calls the frontier model.

    This is the central orchestrator for the signal pipeline. It:
    1. Collects signals from all providers for a given market
    2. Filters out signals with confidence=0 or probability=None
    3. Computes a weighted preliminary estimate
    4. Makes the single FRONTIER MODEL call for the final probability
    5. Stores everything in the signals table for audit
    """

    def __init__(
        self,
        llm: LLMClient,
        providers: list[SignalProvider] | None = None,
        on_progress: Any = None,
    ) -> None:
        self._llm = llm
        self._on_progress = on_progress

        if providers is not None:
            self._providers = providers
        else:
            self._providers = [
                NewsSignalProvider(llm=llm),
                PollingSignalProvider(llm=llm),
                EconomicsResolutionProvider(llm=llm),
                CryptoResolutionProvider(llm=llm),
            ]

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        """Emit a progress update if a callback is registered."""
        if self._on_progress:
            try:
                self._on_progress(question, stage, detail)
            except Exception:
                pass

    async def aggregate(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        market_price: float,
        **kwargs: Any,
    ) -> AggregatedSignal | None:
        """Run all signal providers and produce a final aggregated estimate.

        Returns None if the market should be skipped (insufficient signals
        or low frontier confidence).

        Raises LLMError if the frontier model call fails — NEVER falls back
        to cheap model for this call.
        """
        self._emit(market_question, "collecting", "gathering signals from all providers")

        # Step 1: Collect signals from all providers
        raw_results = await asyncio.gather(
            *(
                provider.get_signal(
                    market_question, market_category, market_end_date, **kwargs
                )
                for provider in self._providers
            ),
            return_exceptions=True,
        )

        # Step 2: Filter out errors and unusable signals
        all_signals: list[SignalResult] = []
        for result in raw_results:
            if isinstance(result, Exception):
                logger.warning("Signal provider error: %s", result)
                continue
            all_signals.append(result)

        usable_signals = [
            s for s in all_signals
            if s.confidence > 0 and s.probability is not None
        ]

        self._emit(
            market_question, "filtering",
            f"{len(usable_signals)} usable of {len(all_signals)} total signals",
        )

        # Step 3: If 0 usable signals → skip this market
        if len(usable_signals) == 0:
            logger.info("No usable signals for '%s', skipping", market_question[:60])
            self._emit(market_question, "skip", "no usable signals")
            self._log_aggregated_signal(
                market_question, "aggregator_skip", None, 0.0,
                "No usable signals — skipping market", "none",
            )
            return None

        # Step 4: Compute weighted preliminary estimate
        preliminary_prob = compute_preliminary_probability(usable_signals)
        total_data_points = sum(s.data_points for s in usable_signals)

        self._emit(
            market_question, "preliminary",
            f"weighted estimate: {preliminary_prob:.2f} from {len(usable_signals)} signals",
        )

        # Step 5: FRONTIER MODEL CALL
        self._emit(market_question, "frontier", "calling frontier model for final estimate")
        prompt = _build_frontier_prompt(
            question=market_question,
            category=market_category,
            market_price=market_price,
            end_date=market_end_date,
            signals=usable_signals,
            preliminary_prob=preliminary_prob,
        )

        # This call uses the frontier model — NEVER falls back to cheap
        frontier_response = await self._llm.call_json(
            prompt, task_type="estimate_probability"
        )

        if not isinstance(frontier_response, dict):
            raise LLMError(
                f"Frontier model returned non-dict response: {type(frontier_response)}"
            )

        # Parse frontier response
        final_prob = float(frontier_response.get("final_probability", 0.5))
        final_conf = float(frontier_response.get("confidence", 0.0))
        reasoning = str(frontier_response.get("reasoning", ""))
        signals_agreement = str(frontier_response.get("signals_agreement", "mixed"))
        market_efficiency = str(frontier_response.get("market_efficiency_assessment", "fair"))

        # Clamp values
        final_prob = max(0.0, min(1.0, final_prob))
        final_conf = max(0.0, min(1.0, final_conf))

        self._emit(
            market_question, "frontier_done",
            f"P={final_prob:.2f} C={final_conf:.2f} — {reasoning[:80]}",
        )

        # Step 6: If confidence < 0.4 → skip market
        if final_conf < MIN_FRONTIER_CONFIDENCE:
            logger.info(
                "Frontier confidence %.2f < %.2f for '%s', skipping",
                final_conf, MIN_FRONTIER_CONFIDENCE, market_question[:60],
            )
            self._emit(market_question, "skip", f"frontier confidence too low ({final_conf:.2f})")
            self._log_aggregated_signal(
                market_question, "aggregator_low_confidence",
                final_prob, final_conf,
                f"Frontier confidence {final_conf:.2f} < {MIN_FRONTIER_CONFIDENCE} — skipping",
                "frontier",
            )
            return None

        # Step 7: Build final result with audit trail
        result = AggregatedSignal(
            market_question=market_question,
            market_category=market_category,
            market_price=market_price,
            final_probability=final_prob,
            confidence=final_conf,
            reasoning=reasoning,
            signals_agreement=signals_agreement,
            market_efficiency=market_efficiency,
            preliminary_probability=preliminary_prob,
            individual_signals=usable_signals,
            frontier_model_used="frontier",
            total_data_points=total_data_points,
        )

        # Store in signals table
        self._log_aggregated_signal(
            market_question, "aggregator",
            final_prob, final_conf, reasoning, "frontier",
        )

        # Also log individual signals
        for signal in usable_signals:
            self._log_aggregated_signal(
                market_question, f"aggregator_input_{signal.source}",
                signal.probability, signal.confidence,
                signal.reasoning[:500], signal.model_used,
            )

        self._emit(market_question, "done", f"final P={final_prob:.2f} C={final_conf:.2f}")

        return result

    def _log_aggregated_signal(
        self,
        market_question: str,
        signal_source: str,
        probability: float | None,
        confidence: float,
        reasoning: str,
        model_used: str,
    ) -> None:
        """Log a signal to the SQLite signals table."""
        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source=signal_source,
                probability=probability if probability is not None else -1.0,
                confidence=confidence,
                reasoning=reasoning[:1000],
                model_used=model_used,
            )
        except Exception as e:
            logger.warning("Failed to log aggregated signal to DB: %s", e)
