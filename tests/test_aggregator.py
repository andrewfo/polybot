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
        signals = [_make_signal(source="prediction_markets", probability=0.6, confidence=0.8)]
        result = compute_preliminary_probability(signals)
        # With log-odds averaging (now default), single signal → same result
        assert abs(result - 0.6) < 1e-6

    def test_multiple_signals_equal_weight(self):
        signals = [
            _make_signal(source="prediction_markets", probability=0.4, confidence=1.0),
            _make_signal(source="prediction_markets", probability=0.8, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        # Log-odds average of 0.4 and 0.8 with equal weights:
        # log-odds(0.4) = log(0.4/0.6) ≈ -0.405, log-odds(0.8) = log(0.8/0.2) ≈ 1.386
        # avg ≈ 0.490 → prob ≈ 0.620 (slightly above linear 0.6 due to asymmetry)
        import math
        lo1 = math.log(0.4 / 0.6)
        lo2 = math.log(0.8 / 0.2)
        avg_lo = (lo1 + lo2) / 2  # equal weights cancel
        expected = 1.0 / (1.0 + math.exp(-avg_lo))
        assert abs(result - expected) < 1e-4

    def test_confidence_weighted(self):
        """Higher confidence signal should have more influence."""
        signals = [
            _make_signal(source="prediction_markets", probability=0.3, confidence=0.1),
            _make_signal(source="prediction_markets", probability=0.9, confidence=0.9),
        ]
        result = compute_preliminary_probability(signals)
        # With log-odds: high-confidence 0.9 signal should dominate
        assert result > 0.7  # Strongly pulled toward 0.9

    def test_benched_signal_has_no_influence(self):
        """web_search is benched (weight 0) — it must not move the estimate."""
        signals = [
            _make_signal(source="prediction_markets", probability=0.6, confidence=0.8),
            _make_signal(source="web_search", probability=0.95, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        assert abs(result - 0.6) < 1e-6

    def test_all_benched_signals_returns_default(self):
        """Only zero-weight sources → no usable weight → default 0.5."""
        signals = [
            _make_signal(source="web_search", probability=0.9, confidence=0.9),
            _make_signal(source="onchain_flow", probability=0.8, confidence=0.8),
        ]
        result = compute_preliminary_probability(signals)
        assert result == 0.5

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
    def test_resolution_crypto_higher_weight(self):
        """Resolution crypto (2.5x) should dominate benched web_search (0x)."""
        signals = [
            _make_signal(source="web_search", probability=0.4, confidence=1.0),
            _make_signal(source="resolution_crypto", probability=0.8, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        # With web_search benched, resolution_crypto fully determines the result
        assert result > 0.6

    def test_prediction_markets_weight(self):
        signals = [
            _make_signal(source="web_search", probability=0.4, confidence=1.0),
            _make_signal(source="prediction_markets", probability=0.6, confidence=1.0),
        ]
        result = compute_preliminary_probability(signals)
        # Log-odds result should be close to the weighted average
        assert 0.4 < result < 0.7

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
            confidence=0.35,
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

        # Verify preliminary probability was computed (log-odds averaging)
        # Should be between 0.55 and 0.75, weighted toward resolution_crypto
        assert 0.55 <= result.preliminary_probability <= 0.80

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


# ---------------------------------------------------------------------------
# Test: pre-frontier edge gate (Phase 1 cost control)
# ---------------------------------------------------------------------------

def _make_web_search_provider(signal: SignalResult | None = None) -> MagicMock:
    """Mock provider classified as the gated (paid Sonar) web_search provider."""
    provider = _make_mock_provider(
        signal or _make_signal(source="web_search", probability=0.6, confidence=0.7)
    )
    provider.name = "web_search"
    return provider


class TestPreFrontierGate:
    @pytest.mark.asyncio
    async def test_gate_skips_sonar_and_frontier_on_low_edge(self, mock_llm):
        """Free-signal prelim edge below threshold → no Sonar, no frontier."""
        ws_provider = _make_web_search_provider()
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.51, confidence=0.9)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.50, confidence=0.8)),
            ws_provider,
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Test gate?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
                condition_id="0xgate",
            )

        assert result is not None
        assert result.skipped is True
        assert "pre-frontier gate" in result.skip_reason
        # Preliminary probability from free signals preserved for the UI
        assert 0.45 < result.preliminary_probability < 0.55
        # Neither the paid Sonar provider nor the frontier model was called
        ws_provider.get_signal.assert_not_called()
        mock_llm.call_json.assert_not_called()
        # Skip recorded in frontier_decisions for the learning engine
        mock_db.record_frontier_decision.assert_called_once()
        fd_kwargs = mock_db.record_frontier_decision.call_args[1]
        assert fd_kwargs["market_id"] == "0xgate"
        assert fd_kwargs["should_trade"] is False
        assert fd_kwargs["skip_reason"] == "prelim edge below pre-frontier gate"
        assert fd_kwargs["bet_size_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_gate_passes_on_high_edge(self, mock_llm):
        """Free-signal prelim edge above threshold → Sonar and frontier both run."""
        ws_provider = _make_web_search_provider()
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.70, confidence=0.9)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.65, confidence=0.8)),
            ws_provider,
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"):
            result = await aggregator.aggregate(
                market_question="Test gate pass?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        assert result is not None
        assert result.skipped is False
        ws_provider.get_signal.assert_called_once()
        mock_llm.call_json.assert_called_once()
        # web_search signal made it into the aggregation
        assert any(s.source == "web_search" for s in result.individual_signals)

    @pytest.mark.asyncio
    async def test_gate_not_applied_when_free_signals_insufficient(self, mock_llm):
        """Free signals cannot qualify alone → web_search still runs (no gating)."""
        ws_provider = _make_web_search_provider(
            _make_signal(source="web_search", probability=0.52, confidence=0.7)
        )
        providers = [
            # Only one usable free signal — even at zero edge the market must
            # fall through so the Sonar signal can complete the 2+ requirement.
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.50, confidence=0.9)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=None, confidence=0.0)),
            ws_provider,
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Test insufficient free?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        ws_provider.get_signal.assert_called_once()
        mock_llm.call_json.assert_called_once()
        assert result is not None
        assert result.skipped is False
        mock_db.record_frontier_decision.assert_not_called()

    @pytest.mark.asyncio
    async def test_gate_disabled_with_zero_threshold(self, mock_llm):
        """PRE_FRONTIER_EDGE_THRESHOLD=0 disables the gate entirely."""
        ws_provider = _make_web_search_provider()
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.50, confidence=0.9)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.50, confidence=0.8)),
            ws_provider,
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"), \
             patch("signals.aggregator.PRE_FRONTIER_EDGE_THRESHOLD", 0.0):
            result = await aggregator.aggregate(
                market_question="Test gate disabled?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
            )

        ws_provider.get_signal.assert_called_once()
        mock_llm.call_json.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_gate_skip_records_calibration_predictions(self, mock_llm):
        """Free signals that ran before a gate skip still feed calibration."""
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.51, confidence=0.9)),
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.50, confidence=0.8)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"), \
             patch("signals.aggregator.record_prediction") as mock_rp:
            result = await aggregator.aggregate(
                market_question="Test gate calibration?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
                condition_id="0xcal",
            )

        assert result is not None and result.skipped is True
        recorded_sources = {c[1]["signal_source"] for c in mock_rp.call_args_list}
        assert recorded_sources == {"resolution_crypto", "prediction_markets"}

    @pytest.mark.asyncio
    async def test_event_market_gate(self, mock_llm):
        """Gate applies to event markets using event weight multipliers."""
        ws_provider = _make_web_search_provider()
        providers = [
            _make_mock_provider(_make_signal(source="prediction_markets", probability=0.50, confidence=0.8)),
            _make_mock_provider(_make_signal(source="onchain_flow", probability=0.51, confidence=0.6)),
            ws_provider,
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db") as mock_db:
            result = await aggregator.aggregate(
                market_question="Will event happen?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
                market_type="event",
            )

        assert result is not None
        assert result.skipped is True
        assert "pre-frontier gate" in result.skip_reason
        ws_provider.get_signal.assert_not_called()
        mock_llm.call_json.assert_not_called()
        mock_db.record_frontier_decision.assert_called_once()


# ---------------------------------------------------------------------------
# Test: benched signals (Phase 2 — web_search disabled, onchain_flow weight 0)
# ---------------------------------------------------------------------------

class TestBenchedSignals:
    def test_default_providers_exclude_web_search_when_disabled(self, mock_llm):
        """ENABLE_WEB_SEARCH_SIGNAL=False → Sonar provider not constructed."""
        aggregator = SignalAggregator(llm=mock_llm)
        names = [getattr(p, "name", "") for p in aggregator._providers]
        assert "web_search" not in names
        assert set(names) == {"resolution_crypto", "prediction_markets", "onchain_flow"}

    def test_default_providers_include_web_search_when_enabled(self, mock_llm):
        with patch("signals.aggregator.ENABLE_WEB_SEARCH_SIGNAL", True):
            aggregator = SignalAggregator(llm=mock_llm)
        names = [getattr(p, "name", "") for p in aggregator._providers]
        assert "web_search" in names

    @pytest.mark.asyncio
    async def test_benched_onchain_flow_still_counts_and_calibrates(self, mock_llm):
        """A zero-weight signal still satisfies the 2+ requirement, reaches the
        frontier prompt, and records calibration — it just can't move prelim."""
        providers = [
            _make_mock_provider(_make_signal(source="resolution_crypto", probability=0.70, confidence=0.9)),
            _make_mock_provider(_make_signal(source="onchain_flow", probability=0.95, confidence=0.8)),
        ]
        aggregator = SignalAggregator(llm=mock_llm, providers=providers)

        with patch("signals.aggregator.db"), \
             patch("signals.aggregator.record_prediction") as mock_rp:
            result = await aggregator.aggregate(
                market_question="Test benched onchain?",
                market_category="crypto",
                market_end_date="2026-12-31",
                market_price=0.50,
                condition_id="0xbench",
            )

        assert result is not None and result.skipped is False
        # onchain_flow counted toward the 2+ usable requirement
        assert {s.source for s in result.individual_signals} == {"resolution_crypto", "onchain_flow"}
        # ...but with weight 0 it did not move the preliminary estimate
        assert abs(result.preliminary_probability - 0.70) < 1e-6
        # ...and its prediction was still recorded for calibration earn-back
        recorded = {c[1]["signal_source"] for c in mock_rp.call_args_list}
        assert "onchain_flow" in recorded
        # frontier still sees the onchain_flow evidence in the prompt
        prompt = mock_llm.call_json.call_args[0][0]
        assert "onchain_flow" in prompt
