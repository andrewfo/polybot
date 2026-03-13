"""Tests for market detail builders and raw evidence formatting."""

import pytest

from signals.base import SignalResult
from signals.aggregator import (
    AggregatedSignal,
    _format_raw_evidence,
)
from tui.widgets.detail_builders import (
    _build_frontier_section,
    _build_market_info,
    _build_probability_section,
    build_full_analysis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    source: str = "web_search",
    probability: float = 0.6,
    confidence: float = 0.7,
    reasoning: str = "Test reasoning",
    model_used: str = "cheap",
    data_points: int = 5,
    raw_data: dict | None = None,
) -> SignalResult:
    return SignalResult(
        source=source,
        probability=probability,
        confidence=confidence,
        reasoning=reasoning,
        model_used=model_used,
        data_points=data_points,
        raw_data=raw_data or {},
    )


def _make_aggregation(
    signals: list[SignalResult] | None = None,
    final_probability: float = 0.65,
    confidence: float = 0.8,
    market_price: float = 0.50,
) -> AggregatedSignal:
    if signals is None:
        signals = [_make_signal()]
    return AggregatedSignal(
        market_question="Will Bitcoin reach $150,000?",
        market_category="crypto",
        market_price=market_price,
        final_probability=final_probability,
        confidence=confidence,
        reasoning="Strong evidence from multiple sources",
        signals_agreement="agree",
        market_efficiency="underpriced",
        preliminary_probability=0.60,
        individual_signals=signals,
        frontier_model_used="frontier",
        total_data_points=15,
    )


# ---------------------------------------------------------------------------
# _format_raw_evidence tests
# ---------------------------------------------------------------------------

class TestFormatRawEvidence:
    """Tests for _format_raw_evidence()."""

    def test_crypto_full_data(self) -> None:
        signal = _make_signal(source="resolution_crypto", raw_data={
            "current_price": 142300,
            "target_price": 150000,
            "direction": "above",
            "annualized_vol": 0.78,
            "vol_source": "historical",
            "change_24h": 0.021,
            "terminal_prob": 0.38,
            "barrier_prob": 0.55,
            "model_prob": 0.55,
            "resolution_type": "barrier",
            "trend": "upward (+5.2% over 30 data points)",
        })
        result = _format_raw_evidence(signal)
        assert "Market data:" in result
        assert "$142,300" in result
        assert "$150,000" in result
        assert "above" in result
        assert "Terminal model" in result
        assert "Barrier model" in result
        assert "trend" in result.lower()

    def test_crypto_missing_fields(self) -> None:
        signal = _make_signal(source="resolution_crypto", raw_data={
            "current_price": 50000,
            "target_price": 60000,
            "direction": "above",
        })
        result = _format_raw_evidence(signal)
        assert "Market data:" in result
        assert "$50,000" in result

    def test_crypto_no_data(self) -> None:
        signal = _make_signal(source="resolution_crypto", raw_data={})
        assert _format_raw_evidence(signal) == ""

    def test_web_search_evidence(self) -> None:
        signal = _make_signal(source="web_search", raw_data={
            "key_evidence": ["BTC hit $140k", "Analyst predicts $160k"]
        })
        result = _format_raw_evidence(signal)
        assert "Web search evidence" in result
        assert "BTC hit $140k" in result

    def test_prediction_markets(self) -> None:
        signal = _make_signal(source="prediction_markets", raw_data={
            "matched_markets": [
                {"platform": "Metaculus", "title": "BTC $150k", "probability": 0.42},
            ]
        })
        result = _format_raw_evidence(signal)
        assert "Cross-platform" in result
        assert "Metaculus" in result

    def test_unknown_source(self) -> None:
        signal = _make_signal(source="unknown_source")
        assert _format_raw_evidence(signal) == ""


# ---------------------------------------------------------------------------
# Detail builders tests
# ---------------------------------------------------------------------------

class TestBuildMarketInfo:
    """Tests for _build_market_info()."""

    def test_basic_market(self) -> None:
        market = {
            "question": "Will BTC hit $150k?",
            "conditionId": "0xabc123",
            "_category": "crypto",
            "outcomePrices": '["0.62", "0.38"]',
            "liquidityNum": 50000,
            "volume24hr": 12000,
            "spread": 0.02,
            "endDate": "2026-12-31T00:00:00Z",
        }
        result = _build_market_info(market)
        assert "Will BTC hit $150k?" in result
        assert "crypto" in result
        assert "0xabc123" in result
        assert "62.0%" in result
        assert "$50,000" in result

    def test_missing_fields(self) -> None:
        market = {"question": "Test?"}
        result = _build_market_info(market)
        assert "Test?" in result
        assert "unknown" in result  # category


class TestBuildProbabilitySection:
    """Tests for _build_probability_section()."""

    def test_with_signals(self) -> None:
        signals = [
            _make_signal(source="resolution_crypto", probability=0.7, confidence=0.8),
            _make_signal(source="web_search", probability=0.6, confidence=0.5),
        ]
        agg = _make_aggregation(signals=signals)
        result = _build_probability_section(agg)
        assert "PROBABILITY COMPARISON" in result
        assert "Preliminary" in result
        assert "Frontier" in result

    def test_shows_agreement(self) -> None:
        signals = [_make_signal(source="web_search", confidence=0.7)]
        agg = _make_aggregation(signals=signals)
        result = _build_probability_section(agg)
        assert "agree" in result


class TestBuildFrontierSection:
    """Tests for _build_frontier_section()."""

    def test_shows_divergence(self) -> None:
        agg = _make_aggregation(final_probability=0.65, market_price=0.50)
        result = _build_frontier_section(agg)
        assert "15%" in result  # divergence shown in bar
        assert "FRONTIER REASONING" in result
        assert "Strong evidence" in result

    def test_skipped_shows_reason(self) -> None:
        agg = _make_aggregation()
        agg.skipped = True
        agg.skip_reason = "Low confidence"
        result = _build_frontier_section(agg)
        assert "SKIPPED" in result
        assert "Low confidence" in result


class TestBuildFullAnalysis:
    """Tests for the unified build_full_analysis() entry point."""

    def test_no_aggregation(self) -> None:
        market = {"question": "Test?"}
        result = build_full_analysis(market)
        assert "Test?" in result
        assert "No aggregation data" in result

    def test_with_aggregation(self) -> None:
        market = {"question": "Will BTC hit $150k?", "conditionId": "abc"}
        signals = [
            _make_signal(source="resolution_crypto", probability=0.7, confidence=0.8),
            _make_signal(source="web_search", probability=0.6, confidence=0.5),
        ]
        agg = _make_aggregation(signals=signals)
        result = build_full_analysis(market, aggregation=agg)
        assert "MARKET INFO" in result
        assert "PROBABILITY COMPARISON" in result
        assert "FRONTIER REASONING" in result
        assert "Strong evidence" in result

    def test_with_category_no_aggregation(self) -> None:
        market = {"question": "Test?", "_category": "crypto"}
        result = build_full_analysis(market)
        assert "insufficient signals" in result
