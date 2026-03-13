"""Tests for signals/prediction_markets.py — keyword extraction, Jaccard matching, consensus."""

import pytest

from signals.prediction_markets import (
    _extract_search_keywords,
    _jaccard_similarity,
    PredictionMarketsSignalProvider,
)


# ---------------------------------------------------------------------------
# Keyword extraction (Change 4)
# ---------------------------------------------------------------------------

class TestExtractSearchKeywords:
    def test_basic_extraction(self) -> None:
        result = _extract_search_keywords("Will Ethereum be above $5000 by June 2026?")
        assert "Ethereum" in result
        assert "$5000" in result
        assert "June" in result
        assert "2026" in result

    def test_stops_words_removed(self) -> None:
        result = _extract_search_keywords("Will the price of Bitcoin be above $100k?")
        words = result.split()
        assert "Will" not in words or "the" not in words  # stop words removed
        assert any("Bitcoin" in w for w in words)

    def test_max_six_keywords(self) -> None:
        result = _extract_search_keywords(
            "Will the total cryptocurrency market capitalization exceed five trillion dollars in 2026?"
        )
        assert len(result.split()) <= 6

    def test_empty_question(self) -> None:
        result = _extract_search_keywords("")
        assert result == ""

    def test_numbers_kept(self) -> None:
        result = _extract_search_keywords("Will BTC hit $100000 by December 31 2026?")
        assert "$100000" in result
        assert "31" in result or "2026" in result


# ---------------------------------------------------------------------------
# Jaccard similarity (Change 5)
# ---------------------------------------------------------------------------

class TestJaccardSimilarity:
    def test_identical_strings(self) -> None:
        sim = _jaccard_similarity("Bitcoin price prediction", "Bitcoin price prediction")
        assert sim == 1.0

    def test_completely_different(self) -> None:
        sim = _jaccard_similarity("Bitcoin price prediction", "Lakers basketball game")
        assert sim == 0.0

    def test_partial_overlap(self) -> None:
        sim = _jaccard_similarity(
            "Will Bitcoin hit $100k?",
            "Bitcoin price reaching $100k by end of year",
        )
        assert 0.0 < sim < 1.0

    def test_stop_words_ignored(self) -> None:
        # "Will" and "the" are stop words — shouldn't inflate similarity
        sim = _jaccard_similarity(
            "Will the Lakers win?",
            "Will the Celtics win?",
        )
        # Only "win" overlaps after stop word removal
        assert sim < 0.5

    def test_empty_strings(self) -> None:
        assert _jaccard_similarity("", "something") == 0.0
        assert _jaccard_similarity("something", "") == 0.0
        assert _jaccard_similarity("", "") == 0.0

    def test_threshold_matching(self) -> None:
        # Very similar crypto questions should have meaningful overlap
        sim = _jaccard_similarity(
            "Will Bitcoin reach $100k by December?",
            "Will Bitcoin reach $100k by end of year?",
        )
        assert sim >= 0.30

    def test_unrelated_below_threshold(self) -> None:
        # Unrelated questions should score below 0.30
        sim = _jaccard_similarity(
            "Will Bitcoin reach $100k?",
            "Will the President win the election?",
        )
        assert sim < 0.30


# ---------------------------------------------------------------------------
# Match and compute consensus (Change 5)
# ---------------------------------------------------------------------------

class TestMatchAndComputeConsensus:
    def _make_provider(self):
        from unittest.mock import MagicMock
        llm = MagicMock()
        return PredictionMarketsSignalProvider(llm=llm)

    def test_matching_markets(self) -> None:
        provider = self._make_provider()
        matches = [
            {"platform": "metaculus", "title": "Will Bitcoin reach $100k?", "probability": 0.60},
            {"platform": "kalshi", "title": "Bitcoin price above $100k", "probability": 0.55},
            {"platform": "polymarket", "title": "Lakers NBA championship", "probability": 0.30},
        ]
        result = provider._match_and_compute_consensus(
            "Will Bitcoin hit $100k?", matches
        )
        assert result.probability is not None
        assert result.source == "prediction_markets"
        assert result.model_used == "none"
        assert result.data_points >= 1
        # Lakers match should be excluded
        matched_titles = [m["title"] for m in result.raw_data["matched_markets"]]
        assert "Lakers NBA championship" not in matched_titles

    def test_no_matches(self) -> None:
        provider = self._make_provider()
        matches = [
            {"platform": "metaculus", "title": "Will it rain tomorrow?", "probability": 0.70},
        ]
        result = provider._match_and_compute_consensus(
            "Will Bitcoin hit $100k?", matches
        )
        assert result.probability is None
        assert result.confidence == 0.0

    def test_empty_matches(self) -> None:
        provider = self._make_provider()
        result = provider._match_and_compute_consensus("Will BTC hit $100k?", [])
        assert result.probability is None
        assert result.confidence == 0.0

    def test_consensus_weighted_by_similarity(self) -> None:
        provider = self._make_provider()
        # Two matches with different similarities should weight differently
        matches = [
            {"platform": "metaculus", "title": "Will Bitcoin hit $100k?", "probability": 0.80},
            {"platform": "kalshi", "title": "Bitcoin price", "probability": 0.40},
        ]
        result = provider._match_and_compute_consensus(
            "Will Bitcoin hit $100k?", matches
        )
        if result.probability is not None:
            # Higher similarity match (first) should pull consensus toward 0.80
            assert result.probability > 0.40


# ---------------------------------------------------------------------------
# Signals agreement (Change 6)
# ---------------------------------------------------------------------------

class TestSignalsAgreement:
    def test_agree(self) -> None:
        from signals.aggregator import _compute_signals_agreement
        from signals.base import SignalResult
        signals = [
            SignalResult(source="a", probability=0.60, confidence=0.8, reasoning="", model_used="", data_points=1),
            SignalResult(source="b", probability=0.62, confidence=0.8, reasoning="", model_used="", data_points=1),
            SignalResult(source="c", probability=0.58, confidence=0.8, reasoning="", model_used="", data_points=1),
        ]
        assert _compute_signals_agreement(signals) == "agree"

    def test_disagree(self) -> None:
        from signals.aggregator import _compute_signals_agreement
        from signals.base import SignalResult
        signals = [
            SignalResult(source="a", probability=0.30, confidence=0.8, reasoning="", model_used="", data_points=1),
            SignalResult(source="b", probability=0.70, confidence=0.8, reasoning="", model_used="", data_points=1),
        ]
        assert _compute_signals_agreement(signals) == "disagree"

    def test_mixed(self) -> None:
        from signals.aggregator import _compute_signals_agreement
        from signals.base import SignalResult
        signals = [
            SignalResult(source="a", probability=0.45, confidence=0.8, reasoning="", model_used="", data_points=1),
            SignalResult(source="b", probability=0.65, confidence=0.8, reasoning="", model_used="", data_points=1),
        ]
        assert _compute_signals_agreement(signals) == "mixed"

    def test_single_signal(self) -> None:
        from signals.aggregator import _compute_signals_agreement
        from signals.base import SignalResult
        signals = [
            SignalResult(source="a", probability=0.50, confidence=0.8, reasoning="", model_used="", data_points=1),
        ]
        assert _compute_signals_agreement(signals) == "agree"


# ---------------------------------------------------------------------------
# Log-odds averaging (Change 7)
# ---------------------------------------------------------------------------

class TestLogOddsAverage:
    def test_symmetric(self) -> None:
        from signals.aggregator import _log_odds_average, SIGNAL_WEIGHT_MULTIPLIERS
        from signals.base import SignalResult
        signals = [
            SignalResult(source="resolution_crypto", probability=0.80, confidence=1.0, reasoning="", model_used="", data_points=1),
            SignalResult(source="resolution_crypto", probability=0.20, confidence=1.0, reasoning="", model_used="", data_points=1),
        ]
        result = _log_odds_average(signals)
        assert abs(result - 0.5) < 0.01

    def test_extreme_probabilities(self) -> None:
        from signals.aggregator import _log_odds_average
        from signals.base import SignalResult
        signals = [
            SignalResult(source="resolution_crypto", probability=0.99, confidence=1.0, reasoning="", model_used="", data_points=1),
            SignalResult(source="resolution_crypto", probability=0.95, confidence=1.0, reasoning="", model_used="", data_points=1),
        ]
        result = _log_odds_average(signals)
        assert 0.95 < result < 1.0

    def test_no_signals(self) -> None:
        from signals.aggregator import _log_odds_average
        result = _log_odds_average([])
        assert result == 0.5
