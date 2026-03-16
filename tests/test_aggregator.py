"""Unit tests for the signal aggregator (Section 4D)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.aggregator import (
    AggregatedSignal,
    MIN_FRONTIER_CONFIDENCE,
    SIGNAL_WEIGHT_MULTIPLIERS,
    SignalAggregator,
    compute_preliminary_probability,
    _compute_effective_weight,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    source: str = "web_search",
    probability: float | None = 0.6,
    confidence: float = 0.7,
    reasoning: str = "Test reasoning",
    model_used: str = "cheap",
    data_points: int = 5,
) -> SignalResult:
    return SignalResult(
        source=source,
        probability=probability,
        confidence=confidence,
        reasoning=reasoning,
        model_used=model_used,
        data_points=data_points,
    )


def _make_mock_provider(signal: SignalResult) -> MagicMock:
    provider = MagicMock()
    provider.get_signal = AsyncMock(return_value=signal)
    return provider


def _make_frontier_response(
    final_probability: float = 0.65,
    confidence: float = 0.8,
    reasoning: str = "Frontier reasoning",
    signals_agreement: str = "agree",
    market_efficiency: str = "underpriced",
) -> dict:
    return {
        "final_probability": final_probability,
        "confidence": confidence,
        "reasoning": reasoning,
        "signals_agreement": signals_agreement,
        "market_efficiency_assessment": market_efficiency,
    }


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.call_json = AsyncMock(return_value=_make_frontier_response())
    llm.frontier = AsyncMock(return_value=json.dumps(_make_frontier_response()))
    return llm


# ---------------------------------------------------------------------------
# Test: weighted average computed correctly
# ---------------------------------------------------------------------------

class TestPreliminaryProbability:
    def test_single_signal(self):
        signals = [_make_signal(source="web_search", probability=0.6, confidence=0.8)]
        result = compute_preliminary_probability(signals)
        assert abs(result - 0.6) < 1e-6

    def test_multiple_signals_equal_weight(self):
        signals = [
            _make_signal(source="web_search", probability=0.4, confidence=1.0),
            _make_signal(source="web_search", probability=0.8, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        assert abs(result - 0.6) < 1e-6

    def test_confidence_weighted(self):
        """Higher confidence signal should have more influence."""
        signals = [
            _make_signal(source="web_search", probability=0.3, confidence=0.1),
            _make_signal(source="web_search", probability=0.9, confidence=0.9),
        ]
        result = compute_preliminary_probability(signals)
        # Weight: 0.1*1.5=0.15 for first, 0.9*1.5=1.35 for second
        expected = (0.3 * 0.15 + 0.9 * 1.35) / (0.15 + 1.35)
        assert abs(result - expected) < 1e-6

    def test_no_usable_signals_returns_default(self):
        signals = [
            _make_signal(source="web_search", probability=None, confidence=0.0),
        ]
        result = compute_preliminary_probability(signals)
        assert result == 0.5

    def test_empty_list(self):
        result = compute_preliminary_probability([])
        assert result == 0.5


# ---------------------------------------------------------------------------
# Test: resolution source signals get 2x weight multiplier
# ---------------------------------------------------------------------------

class TestResolutionSourceWeight:
    def test_resolution_crypto_2x(self):
        signals = [
            _make_signal(source="web_search", probability=0.4, confidence=1.0),
            _make_signal(source="resolution_crypto", probability=0.8, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        expected = (0.4 * 1.5 + 0.8 * 2.0) / (1.5 + 2.0)
        assert abs(result - expected) < 1e-6

    def test_prediction_markets_1_8x(self):
        signals = [
            _make_signal(source="web_search", probability=0.4, confidence=1.0),
            _make_signal(source="prediction_markets", probability=0.6, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        expected = (0.4 * 1.5 + 0.6 * 1.8) / (1.5 + 1.8)
        assert abs(result - expected) < 1e-6

    def test_effective_weight_multipliers(self):
        assert _compute_effective_weight(
            _make_signal(source="resolution_crypto", confidence=1.0)
        ) == SIGNAL_WEIGHT_MULTIPLIERS["resolution_crypto"]
        assert _compute_effective_weight(
            _make_signal(source="web_search", confidence=1.0)
        ) == SIGNAL_WEIGHT_MULTIPLIERS["web_search"]
        assert _compute_effective_weight(
            _make_signal(source="prediction_markets", confidence=1.0)
        ) == SIGNAL_WEIGHT_MULTIPLIERS["prediction_markets"]

    def test_unknown_source_1x(self):
        assert _compute_effective_weight(
            _make_signal(source="unknown_source", confidence=1.0)
        ) == 1.0


# ---------------------------------------------------------------------------
# Test: insufficient signals → returns None (skip market)
# ---------------------------------------------------------------------------

class TestInsufficientSignals:
    @pytest.mark.asyncio
    async def test_all_zero_confidence(self, mock_llm):
        providers = [
            _make_mock_provider(_make_signal(confidence=0.0, probability=None)),
            _make_mock_provider(_make_signal(confidence=0.0, probability=None)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is True
        assert "resolution_crypto" in result.skip_reason
        # Frontier should NOT have been called
        mock_llm.call_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_none_probability(self, mock_llm):
        providers = [
            _make_mock_provider(_make_signal(probability=None, confidence=0.5)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_all_providers_error(self, mock_llm):
        provider = MagicMock()
        provider.get_signal = AsyncMock(side_effect=RuntimeError("boom"))
        aggregator = SignalAggregator(llm=mock_llm, providers=[provider])

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        # All providers errored — no signals at all, returns skipped result
        assert result is not None
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_only_one_usable_signal_skips(self, mock_llm):
        """Even with resolution_crypto, need at least 2 usable signals."""
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.7, confidence=0.9)),
            _make_mock_provider(_make_signal(source="web_search", confidence=0.0, probability=None)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is True
        assert "only 1 usable" in result.skip_reason
        # Signal data should be preserved for UI
        assert len(result.all_signals) == 2
        mock_llm.call_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_math_signal_skips(self, mock_llm):
        """Two usable signals but no resolution_crypto → skip."""
        providers = [
            _make_mock_provider(_make_signal(source="web_search", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.55, confidence=0.7)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is True
        assert "resolution_crypto" in result.skip_reason
        # Signal data should be preserved for UI
        assert len(result.individual_signals) == 2
        mock_llm.call_json.assert_not_called()


# ---------------------------------------------------------------------------
# Test: frontier model confidence < 0.25 → skip market
# ---------------------------------------------------------------------------

class TestLowFrontierConfidence:
    @pytest.mark.asyncio
    async def test_low_confidence_skip(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            confidence=0.15,
            final_probability=0.6,
        ))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_skips(self, mock_llm):
        """Confidence just below threshold should skip (< not <=)."""
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            confidence=0.24,
        ))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_at_threshold_passes(self, mock_llm):
        """Confidence exactly at threshold should pass."""
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            confidence=0.25,
        ))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert isinstance(result, AggregatedSignal)


# ---------------------------------------------------------------------------
# Test: frontier model failure → raises, does NOT fall back to cheap
# ---------------------------------------------------------------------------

class TestFrontierFailure:
    @pytest.mark.asyncio
    async def test_frontier_failure_raises(self, mock_llm):
        from core.llm import LLMError

        mock_llm.call_json = AsyncMock(side_effect=LLMError("Frontier model failed"))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            with pytest.raises(LLMError, match="Frontier model failed"):
                await aggregator.aggregate(
                    market_question="Test?",
                    market_category="politics",
                    market_end_date="2026-12-31",
                    market_price=0.50,
                )


# ---------------------------------------------------------------------------
# Test: full pipeline with mixed signal confidences
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_mixed_signals(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            final_probability=0.72,
            confidence=0.85,
            reasoning="Strong evidence from resolution source",
            signals_agreement="mixed",
            market_efficiency="underpriced",
        ))

        providers = [
            _make_mock_provider(_make_signal(
                source="web_search", probability=0.55, confidence=0.6, data_points=10,
            )),
            _make_mock_provider(_make_signal(
                source="prediction_markets", probability=None, confidence=0.0, data_points=0,
            )),
            _make_mock_provider(_make_signal(
                source="resolution_crypto", probability=0.75, confidence=0.9, data_points=24,
            )),
        ]

        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Will Bitcoin reach $150,000?",
                market_category="crypto",
                market_end_date="2026-06-30",
                market_price=0.45,
            )

        assert result is not None
        assert isinstance(result, AggregatedSignal)
        assert result.final_probability == 0.72
        assert result.confidence == 0.85
        assert result.signals_agreement == "mixed"
        assert result.market_efficiency == "underpriced"
        assert result.market_question == "Will Bitcoin reach $150,000?"
        assert result.market_category == "crypto"
        assert result.market_price == 0.45

        # Only 2 usable signals (web_search + resolution_crypto)
        assert len(result.individual_signals) == 2
        assert result.total_data_points == 34  # 10 + 24

        # Verify preliminary probability was computed with weights
        # web_search: 0.55 * (0.6 * 1.5) = 0.495
        # crypto: 0.75 * (0.9 * 2.0) = 1.35
        # total weight: 0.9 + 1.8 = 2.7
        # preliminary = (0.495 + 1.35) / 2.7
        expected_prelim = (0.55 * 0.9 + 0.75 * 1.8) / (0.9 + 1.8)
        assert abs(result.preliminary_probability - expected_prelim) < 1e-6

        # Frontier was called exactly once
        mock_llm.call_json.assert_called_once()
        call_args = mock_llm.call_json.call_args
        assert call_args[1]["task_type"] == "estimate_probability"

    @pytest.mark.asyncio
    async def test_all_three_providers_usable(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            final_probability=0.60,
            confidence=0.75,
        ))

        providers = [
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.5, confidence=0.7)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.8)),
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.70, confidence=0.85)),
        ]

        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test all providers",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert len(result.individual_signals) == 3
        assert result.final_probability == 0.60


# ---------------------------------------------------------------------------
# Test: all results stored in signals SQLite table
# ---------------------------------------------------------------------------

class TestSignalStorage:
    @pytest.mark.asyncio
    async def test_signals_logged_to_db(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            confidence=0.8,
        ))

        providers = [
            _make_mock_provider(_make_signal(source="web_search", probability=0.6, confidence=0.7)),
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.7, confidence=0.9)),
        ]

        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Test DB logging",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        # Should have logged: aggregator result + 2 individual signals = 3 calls
        assert mock_db.record_signal.call_count == 3

        # Verify the aggregator result was logged
        calls = mock_db.record_signal.call_args_list
        sources_logged = [call.kwargs["signal_source"] for call in calls]
        assert "aggregator" in sources_logged
        assert "aggregator_input_web_search" in sources_logged
        assert "aggregator_input_resolution_crypto" in sources_logged

    @pytest.mark.asyncio
    async def test_skip_signal_logged_to_db(self, mock_llm):
        """Even when skipping, the skip reason should be logged."""
        providers = [
            _make_mock_provider(_make_signal(confidence=0.0, probability=None)),
        ]

        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Test skip logging",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is True
        mock_db.record_signal.assert_called_once()
        call_kwargs = mock_db.record_signal.call_args.kwargs
        assert call_kwargs["signal_source"] == "aggregator_skip"

    @pytest.mark.asyncio
    async def test_low_confidence_skip_logged(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(
            confidence=0.15,
        ))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Test low confidence",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is None
        # Should log the low-confidence skip
        calls = mock_db.record_signal.call_args_list
        sources = [c.kwargs["signal_source"] for c in calls]
        assert "aggregator_low_confidence" in sources


# ---------------------------------------------------------------------------
# Test: frontier prompt contains resolution source labels
# ---------------------------------------------------------------------------

class TestFrontierPrompt:
    @pytest.mark.asyncio
    async def test_prompt_has_resolution_labels(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(confidence=0.8))

        providers = [
            _make_mock_provider(_make_signal(source="web_search", probability=0.5, confidence=0.7)),
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.7, confidence=0.9)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            await aggregator.aggregate(
                market_question="Test prompt",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        # Check the prompt passed to frontier
        call_args = mock_llm.call_json.call_args
        prompt = call_args[0][0]

        # Resolution source should be labeled
        assert "(DIRECT RESOLUTION SOURCE)" in prompt
        assert "resolution_crypto" in prompt
        assert "web_search" in prompt

        # Resolution criteria mismatch warning should be present
        assert "resolution criteria" in prompt.lower()
        assert "data source" in prompt.lower() or "resolution source" in prompt.lower()

    @pytest.mark.asyncio
    async def test_prompt_has_superforecaster(self, mock_llm):
        mock_llm.call_json = AsyncMock(return_value=_make_frontier_response(confidence=0.8))
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.6, confidence=0.8)),
            _make_mock_provider(_make_signal(source="web_search", probability=0.55, confidence=0.6)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            await aggregator.aggregate(
                market_question="Test",
                market_category="politics",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        prompt = mock_llm.call_json.call_args[0][0]
        assert "superforecaster" in prompt
        assert "calibrated" in prompt.lower()
