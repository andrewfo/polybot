"""Signal aggregator with frontier model final probability call.

Collects signals from 4 providers (resolution_crypto, web_search,
prediction_markets, onchain_flow), computes a weighted preliminary
estimate, then makes the single FRONTIER MODEL call that determines
the final probability. This is the only place the expensive frontier
model is used in the signal pipeline.
"""

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import (
    DIVERGENCE_CONFIDENCE_THRESHOLD,
    MAX_DIVERGENCE_ANY_CONFIDENCE,
    MAX_DIVERGENCE_LOW_CONFIDENCE,
    RESOLUTION_SIGNAL_WEIGHT,
    USE_LOG_ODDS_AVERAGING,
)
from core import db
from core.llm import LLMClient, LLMError
from signals.base import SignalProvider, SignalResult
from signals.calibration import get_multiplier_dict, record_prediction
from signals.onchain_flow import OnchainFlowProvider
from signals.prediction_markets import PredictionMarketsSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.web_search import WebSearchSignalProvider
from signals.temporal import build_frontier_system_prompt

logger = logging.getLogger(__name__)

# Default source-based weight multipliers (used as fallback when calibration
# data is insufficient). Dynamic multipliers from signals/calibration.py
# override these when enough resolved predictions exist.
DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS: dict[str, float] = {
    "resolution_crypto": RESOLUTION_SIGNAL_WEIGHT,   # CoinGecko-based math model (not the actual resolution source)
    "prediction_markets": 1.8,                       # Cross-platform market consensus (strong)
    "web_search": 1.5,                               # Search-grounded LLM (Perplexity Sonar)
    "onchain_flow": 1.3,                             # Exchange flow + whale txs (meaningful but noisy)
}


def _get_signal_weight_multipliers() -> dict[str, float]:
    """Get current signal weight multipliers (dynamic if calibrated, else defaults)."""
    try:
        dynamic = get_multiplier_dict()
        if dynamic:
            return dynamic
    except Exception as e:
        logger.debug("Calibration lookup failed, using defaults: %s", e)
    return DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS


# Active multipliers — refreshed per aggregation cycle
SIGNAL_WEIGHT_MULTIPLIERS: dict[str, float] = DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS

# Minimum frontier confidence to proceed
MIN_FRONTIER_CONFIDENCE = 0.35


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
    all_signals: list[SignalResult] = field(default_factory=list)  # includes unusable signals for UI display
    frontier_model_used: str = ""
    total_data_points: int = 0
    skipped: bool = False
    skip_reason: str = ""


def _compute_effective_weight(signal: SignalResult) -> float:
    """Compute the effective weight for a signal in the preliminary estimate."""
    multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
    return signal.confidence * multiplier


def _log_odds_average(signals: list[SignalResult]) -> float:
    """Compute weighted average probability in log-odds space.

    More calibrated at extreme probabilities than linear averaging.
    Converts each probability to log-odds, averages with weights,
    then converts back to probability space.
    """
    EPS = 1e-6  # Prevent log(0)
    total_weight = 0.0
    weighted_log_odds = 0.0

    for signal in signals:
        if signal.probability is None or signal.confidence <= 0:
            continue
        p = max(EPS, min(1.0 - EPS, signal.probability))
        log_odds = math.log(p / (1.0 - p))
        ew = _compute_effective_weight(signal)
        weighted_log_odds += log_odds * ew
        total_weight += ew

    if total_weight == 0:
        return 0.5

    avg_log_odds = weighted_log_odds / total_weight
    return 1.0 / (1.0 + math.exp(-avg_log_odds))


def _compute_signals_agreement(signals: list[SignalResult]) -> str:
    """Pre-compute signal agreement using confidence-weighted standard deviation.

    Weights disagreement by confidence so a low-confidence outlier doesn't
    register as real disagreement.
    Returns "agree" if weighted stdev < 0.05, "mixed" if < 0.15, else "disagree".
    """
    usable = [(s.probability, s.confidence) for s in signals
              if s.probability is not None and s.confidence > 0]
    if len(usable) < 2:
        return "agree"

    total_conf = sum(c for _, c in usable)
    if total_conf == 0:
        return "agree"

    weighted_mean = sum(p * c for p, c in usable) / total_conf
    weighted_var = sum(c * (p - weighted_mean) ** 2 for p, c in usable) / total_conf
    weighted_stdev = math.sqrt(weighted_var)

    if weighted_stdev < 0.05:
        return "agree"
    elif weighted_stdev < 0.15:
        return "mixed"
    else:
        return "disagree"


def compute_preliminary_probability(signals: list[SignalResult]) -> float:
    """Compute weighted average probability from usable signals.

    Each signal's weight is: confidence * source_multiplier.
    Resolution source signals get RESOLUTION_SIGNAL_WEIGHT multiplier.

    When USE_LOG_ODDS_AVERAGING is True, averages in log-odds space
    for better calibration at extreme probabilities.
    """
    if USE_LOG_ODDS_AVERAGING:
        return _log_odds_average(signals)

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


def _format_raw_evidence(signal: SignalResult) -> str:
    """Extract key raw data from a signal for the frontier model prompt.

    Gives the frontier model the actual evidence (article titles, prices,
    FRED values), not just the cheap model's interpretation.
    """
    raw = signal.raw_data or {}
    source = signal.source

    if source == "resolution_crypto":
        current = raw.get("current_price")
        target = raw.get("target_price")
        direction = raw.get("direction", "?")
        vol = raw.get("annualized_vol")
        change_24h = raw.get("change_24h")
        terminal_prob = raw.get("terminal_prob")
        barrier_prob = raw.get("barrier_prob")
        model_prob = raw.get("model_prob")
        resolution_type = raw.get("resolution_type", "barrier")
        trend = raw.get("trend")
        vol_source = raw.get("vol_source", "historical")
        historical_vol = raw.get("historical_vol")
        ewm_vol = raw.get("ewm_vol")
        short_term_vol = raw.get("short_term_vol")
        realized_drift = raw.get("realized_drift")
        shrunk_drift = raw.get("shrunk_drift")
        deribit_iv = raw.get("deribit_iv")
        price_7d_ago = raw.get("price_7d_ago")
        if current is None and target is None:
            return ""
        lines = ["  Market data:"]
        if current is not None and target is not None:
            distance_pct = raw.get("distance_pct", 0)
            distance_usd = abs(current - target)
            lines.append(f"  - Current: ${current:,.0f} | Target: ${target:,.0f} ({direction}, {distance_pct:+.1f}% / ${distance_usd:,.0f} away)")
        # Price trajectory (7d)
        if current is not None and price_7d_ago is not None and price_7d_ago > 0:
            pct_7d = (current - price_7d_ago) / price_7d_ago
            lines.append(f"  - Price: ${price_7d_ago:,.0f} → ${current:,.0f} over 7d ({pct_7d:+.1%})")
        if change_24h is not None and vol is not None:
            vol_label = f"{vol:.0%} ({vol_source})"
            if historical_vol is not None and ewm_vol is not None:
                vol_label += f" [hist={historical_vol:.0%}, ewm={ewm_vol:.0%}"
                if short_term_vol is not None:
                    vol_label += f", 7d={short_term_vol:.0%}"
                vol_label += "]"
            # Vol regime label
            vol_regime = "low" if vol < 0.40 else "moderate" if vol < 0.80 else "high" if vol < 1.50 else "extreme"
            lines.append(f"  - 24h: {change_24h:+.1%} | Annual vol: {vol_label} ({vol_regime} regime)")
        if deribit_iv is not None:
            lines.append(f"  - Deribit implied vol: {deribit_iv:.0%}")
        if shrunk_drift is not None:
            drift_info = f"{shrunk_drift:+.1%}/yr"
            if realized_drift is not None and abs(realized_drift - shrunk_drift) > 0.01:
                drift_info += f" (raw={realized_drift:+.1%}, shrunk for noise)"
            lines.append(f"  - Drift estimate: {drift_info}")
        elif realized_drift is not None:
            lines.append(f"  - Realized drift (90d): {realized_drift:+.1%}/yr")
        # Show both model probabilities so frontier can assess
        lines.append(f"  - Resolution type: {resolution_type}")
        if terminal_prob is not None and barrier_prob is not None:
            lines.append(f"  - Terminal model (price at expiry): {terminal_prob:.4f}")
            lines.append(f"  - Barrier model (price touches target anytime): {barrier_prob:.4f}")
            lines.append(f"  - Selected model probability: {model_prob:.4f}")
        elif model_prob is not None:
            lines.append(f"  - Model probability: {model_prob:.4f}")
        if trend:
            lines.append(f"  - 90-day trend: {trend}")
        return "\n".join(lines)

    if source == "web_search":
        evidence = raw.get("key_evidence", [])
        if not evidence:
            return ""
        lines = ["  Web search evidence (Perplexity Sonar):"]
        for item in evidence[:5]:
            lines.append(f"  - {item}")
        return "\n".join(lines)

    if source == "prediction_markets":
        matched = raw.get("matched_markets", [])
        if not matched:
            return ""
        lines = ["  Cross-platform market prices:"]
        for m in matched[:5]:
            lines.append(f"  - [{m.get('platform', '?')}] \"{m.get('title', '')}\" = {m.get('probability', '?')}")
        return "\n".join(lines)

    if source == "onchain_flow":
        pressure = raw.get("pressure_score")
        if pressure is None:
            return ""
        data_source = raw.get("data_source", "unknown")
        flow_dir = raw.get("net_flow_direction", "unknown")
        z = raw.get("z_score", 0.0)
        asset = raw.get("asset", "?").upper()
        lines = [f"  On-chain flow data ({data_source}, {asset}):"]
        lines.append(f"  - Pressure score: {pressure:+.2f} (z-score: {z:+.2f})")
        lines.append(f"  - Net flow direction: {flow_dir}")
        if raw.get("mean_7d_netflow") is not None:
            lines.append(f"  - 7d avg netflow: {raw['mean_7d_netflow']:,.0f} | 30d avg: {raw.get('mean_30d_netflow', 0):,.0f}")
        if raw.get("whale_data_available"):
            whale_count = raw.get("whale_tx_count", "?")
            whale_trend = raw.get("whale_trend", "?")
            lines.append(f"  - Whale txs (>$1M): {whale_count} ({whale_trend})")
        return "\n".join(lines)

    return ""


def _build_frontier_prompt(
    question: str,
    category: str,
    market_price: float,
    end_date: str,
    signals: list[SignalResult],
    preliminary_prob: float,
    date_context_line: str = "",
) -> str:
    """Build the frontier model user prompt from the plan spec.

    The system prompt (with date/calibration) is built separately via
    build_frontier_system_prompt() and sent as a system message.
    """
    signal_lines: list[str] = []
    for signal in signals:
        resolution_label = (
            " (DIRECT RESOLUTION SOURCE)"
            if signal.source.startswith("resolution_")
            else ""
        )
        evidence = _format_raw_evidence(signal)
        evidence_block = f"\n{evidence}" if evidence else ""
        signal_lines.append(
            f"- Source: {signal.source}{resolution_label}\n"
            f"  Estimate: {signal.probability}\n"
            f"  Confidence: {signal.confidence}\n"
            f"  Reasoning: {signal.reasoning}\n"
            f"  Data points analyzed: {signal.data_points}"
            f"{evidence_block}"
        )

    signals_block = "\n".join(signal_lines)

    date_line = f"\n{date_context_line}\n" if date_context_line else ""

    return (
        f'You are a superforecaster analyzing a prediction market. Your job is to estimate the true probability of an event as accurately as possible.\n'
        f'{date_line}'
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
        f'Signal agreement (pre-computed): {_compute_signals_agreement(signals)}\n'
        f'\n'
        f'Instructions:\n'
        f'1. Critically evaluate each signal source. Are any likely biased or unreliable?\n'
        f'2. Signals marked as "DIRECT RESOLUTION SOURCE" come from the actual data providers (FRED, CoinGecko) whose data would be used to resolve this market. Weight these more heavily than news or sentiment signals.\n'
        f'3. IMPORTANT: Check whether the market\'s resolution criteria specifies a particular data source, exchange, timestamp methodology, or TWAP that might differ from the signal data provided. If the resolution source differs from our data source (e.g., market resolves on Binance spot price but our data is from CoinGecko aggregated price), adjust your confidence downward accordingly.\n'
        f'3b. For crypto markets showing both Terminal and Barrier model probabilities: evaluate which resolution type actually applies. "Will price reach X by date Y" is barrier (touch anytime). "Will price be above X on date Y" is terminal (price at expiry). If the selected model seems wrong for the market question, use the other model\'s probability instead.\n'
        f'4. Consider base rates for this type of event.\n'
        f'5. Consider what information the market might have that our signals don\'t.\n'
        f'6. Provide your final probability estimate.\n'
        f'7. Rate your overall confidence (0-1) in this estimate.\n'
        f'8. Explain your reasoning in 2-3 sentences.\n'
        f'\n'
        f'IMPORTANT: Be calibrated. Do not anchor to the market price — form your own estimate from the evidence. If your data-driven estimate disagrees with the market, trust the data. Markets can be wrong, especially in mid-to-low liquidity crypto markets.\n'
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
    1. Collects signals from 4 providers for a given market
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
                CryptoResolutionProvider(llm=llm),
                WebSearchSignalProvider(llm=llm),
                PredictionMarketsSignalProvider(llm=llm),
                OnchainFlowProvider(llm=llm),
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
        condition_id: str = "",
        **kwargs: Any,
    ) -> AggregatedSignal | None:
        """Run all signal providers and produce a final aggregated estimate.

        Returns None if the market should be skipped (insufficient signals
        or low frontier confidence).

        Raises LLMError if the frontier model call fails — NEVER falls back
        to cheap model for this call.
        """
        self._emit(market_question, "collecting", "gathering signals from all providers")

        # Refresh dynamic weight multipliers from calibration data
        global SIGNAL_WEIGHT_MULTIPLIERS
        SIGNAL_WEIGHT_MULTIPLIERS = _get_signal_weight_multipliers()

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

        # Step 3: Require at least 2 usable signals AND the math-based signal
        has_math_signal = any(
            s.source == "resolution_crypto" for s in usable_signals
        )
        if len(usable_signals) < 2 or not has_math_signal:
            reasons = []
            if len(usable_signals) < 2:
                reasons.append(f"only {len(usable_signals)} usable signals (need 2+)")
            if not has_math_signal:
                reasons.append("missing resolution_crypto math signal")
            reason = "; ".join(reasons)
            logger.info("Insufficient signals for '%s': %s, skipping", market_question[:60], reason)
            self._emit(market_question, "skip", reason)
            self._log_aggregated_signal(
                market_question, "aggregator_skip", None, 0.0,
                f"Insufficient signals — {reason} — skipping market", "none",
            )
            return AggregatedSignal(
                market_question=market_question,
                market_category=market_category,
                market_price=market_price,
                final_probability=0.0,
                confidence=0.0,
                reasoning="",
                signals_agreement="--",
                market_efficiency="--",
                preliminary_probability=0.0,
                individual_signals=usable_signals,
                all_signals=all_signals,
                skipped=True,
                skip_reason=reason,
            )

        # Step 4: Compute weighted preliminary estimate
        preliminary_prob = compute_preliminary_probability(usable_signals)
        total_data_points = sum(s.data_points for s in usable_signals)

        self._emit(
            market_question, "preliminary",
            f"weighted estimate: {preliminary_prob:.2f} from {len(usable_signals)} signals",
        )

        # Step 5: FRONTIER MODEL CALL
        self._emit(market_question, "frontier", "calling frontier model for final estimate")

        # Build dynamic system prompt with date context and calibration
        from signals.temporal import format_date_context_line
        system_prompt = build_frontier_system_prompt(market_end_date)
        date_context_line = format_date_context_line(market_end_date)

        prompt = _build_frontier_prompt(
            question=market_question,
            category=market_category,
            market_price=market_price,
            end_date=market_end_date,
            signals=usable_signals,
            preliminary_prob=preliminary_prob,
            date_context_line=date_context_line,
        )

        # Log full prompts for audit trail
        logger.debug("Frontier system prompt: %s", system_prompt[:500])
        logger.debug("Frontier user prompt: %s", prompt[:500])

        # This call uses the frontier model — NEVER falls back to cheap
        frontier_response = await self._llm.call_json(
            prompt, task_type="estimate_probability", system=system_prompt
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

        # Step 6a: Post-response divergence sanity check
        divergence = abs(final_prob - market_price)
        if (
            divergence > MAX_DIVERGENCE_ANY_CONFIDENCE
            or (divergence > MAX_DIVERGENCE_LOW_CONFIDENCE and final_conf < DIVERGENCE_CONFIDENCE_THRESHOLD)
        ):
            logger.warning(
                "Frontier divergence sanity check FAILED for '%s': "
                "estimate=%.2f, market=%.2f, divergence=%.2f, confidence=%.2f",
                market_question[:60], final_prob, market_price, divergence, final_conf,
            )
            self._emit(
                market_question, "skip",
                f"divergence sanity check failed (div={divergence:.2f}, conf={final_conf:.2f})",
            )
            self._log_aggregated_signal(
                market_question, "aggregator_divergence_skip",
                final_prob, final_conf,
                f"Divergence {divergence:.2f} exceeds threshold — skipping "
                f"(estimate={final_prob:.2f}, market={market_price:.2f}, conf={final_conf:.2f})",
                "frontier",
            )
            return None

        # Step 6b: If confidence < threshold -> skip market
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
            all_signals=all_signals,
            frontier_model_used="frontier",
            total_data_points=total_data_points,
        )

        # Store in signals table (with full prompt audit trail)
        self._log_aggregated_signal(
            market_question, "aggregator",
            final_prob, final_conf, reasoning, "frontier",
            raw_data={"system_prompt": system_prompt, "user_prompt": prompt},
        )

        # Also log individual signals and record for calibration
        for signal in usable_signals:
            self._log_aggregated_signal(
                market_question, f"aggregator_input_{signal.source}",
                signal.probability, signal.confidence,
                signal.reasoning[:500], signal.model_used,
            )
            # Record prediction for dynamic calibration tracking
            if signal.probability is not None:
                record_prediction(
                    market_id=condition_id if condition_id else market_question[:200],
                    signal_source=signal.source,
                    predicted_probability=signal.probability,
                    market_question=market_question,
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
        raw_data: dict[str, Any] | None = None,
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
                raw_data=json.dumps(raw_data) if raw_data else None,
            )
        except Exception as e:
            logger.warning("Failed to log aggregated signal to DB: %s", e)
