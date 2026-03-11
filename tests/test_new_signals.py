"""Tests for the new signal providers.

Tests 3 signal providers:
- WebSearchSignalProvider (Perplexity Sonar)
- PredictionMarketsSignalProvider (Metaculus + Kalshi + PredictIt)
- SerperSearchSignalProvider (Serper.dev)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ──────────────────────────── Shared fixtures ─────────────────────────────


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Prevent real DB writes in every test."""
    monkeypatch.setattr("core.db.record_signal", lambda **kw: None)
    monkeypatch.setattr("core.db.record_llm_cost", lambda **kw: None)


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.cheap = AsyncMock(return_value="test response")
    llm.sonar = AsyncMock(return_value='{"probability": 0.65, "confidence": 0.7, "reasoning": "test", "sources_found": 3, "key_evidence": ["ev1"]}')
    llm.call_json = AsyncMock(return_value={"probability": 0.6, "confidence": 0.7, "reasoning": "test"})
    llm._extract_json = MagicMock(side_effect=lambda text: json.loads(text) if text.strip().startswith("{") else None)
    return llm


# ──────────────────── WebSearchSignalProvider tests ───────────────────────


class TestWebSearchSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.web_search import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_basic_signal(self, mock_llm):
        from signals.web_search import WebSearchSignalProvider
        provider = WebSearchSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")

        assert result.source == "web_search"
        assert result.probability == 0.65
        assert result.confidence == 0.7
        assert result.model_used == "sonar"
        mock_llm.sonar.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit(self, mock_llm):
        from signals.web_search import WebSearchSignalProvider
        provider = WebSearchSignalProvider(llm=mock_llm)

        await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")
        await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")

        assert mock_llm.sonar.call_count == 1  # Second call hits cache

    @pytest.mark.asyncio
    async def test_sonar_failure_fallback(self, mock_llm):
        mock_llm.sonar = AsyncMock(return_value="not json at all")
        mock_llm._extract_json = MagicMock(return_value=None)

        from signals.web_search import WebSearchSignalProvider
        provider = WebSearchSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Test?", "other", "2026-12-31")

        assert result.source == "web_search"
        assert result.probability is None
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_sonar_exception(self, mock_llm):
        mock_llm.sonar = AsyncMock(side_effect=Exception("connection error"))

        from signals.web_search import WebSearchSignalProvider
        provider = WebSearchSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Test?", "other", "2026-12-31")

        assert result.source == "web_search"
        assert result.probability is None
        assert result.confidence == 0.0


# ──────────────── PredictionMarketsSignalProvider tests ───────────────────


class TestPredictionMarketsSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.prediction_markets import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_no_matches_found(self, mock_llm):
        from signals.prediction_markets import PredictionMarketsSignalProvider

        mock_llm.cheap = AsyncMock(return_value="test query")

        with patch("signals.prediction_markets._search_metaculus", return_value=[]), \
             patch("signals.prediction_markets._search_kalshi", return_value=[]), \
             patch("signals.prediction_markets._search_predictit", return_value=[]):
            provider = PredictionMarketsSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will X happen?", "other", "2026-12-31")

        assert result.source == "prediction_markets"
        assert result.probability is None

    @pytest.mark.asyncio
    async def test_matches_found_and_evaluated(self, mock_llm):
        from signals.prediction_markets import PredictionMarketsSignalProvider

        mock_llm.cheap = AsyncMock(return_value="test query")
        mock_llm.call_json = AsyncMock(return_value={
            "matching_indices": [1],
            "consensus_probability": 0.72,
            "confidence": 0.8,
            "reasoning": "Metaculus community agrees",
        })

        metaculus_data = [{
            "platform": "metaculus",
            "title": "Will X happen?",
            "probability": 0.72,
            "forecasters": 150,
        }]

        with patch("signals.prediction_markets._search_metaculus", return_value=metaculus_data), \
             patch("signals.prediction_markets._search_kalshi", return_value=[]), \
             patch("signals.prediction_markets._search_predictit", return_value=[]):
            provider = PredictionMarketsSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will X happen?", "other", "2026-12-31")

        assert result.source == "prediction_markets"
        assert result.probability == 0.72
        assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_platform_error_handled(self, mock_llm):
        from signals.prediction_markets import PredictionMarketsSignalProvider

        mock_llm.cheap = AsyncMock(return_value="test query")

        with patch("signals.prediction_markets._search_metaculus", side_effect=Exception("timeout")), \
             patch("signals.prediction_markets._search_kalshi", return_value=[]), \
             patch("signals.prediction_markets._search_predictit", return_value=[]):
            provider = PredictionMarketsSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will X happen?", "other", "2026-12-31")

        assert result.source == "prediction_markets"
        assert result.probability is None


# ──────────────── SerperSearchSignalProvider tests ────────────────────────


class TestSerperSearchSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.serper_search import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_skips_without_api_key(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.serper_search.SERPER_API_KEY", "")
        from signals.serper_search import SerperSearchSignalProvider
        provider = SerperSearchSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Test?", "other", "2026-12-31")

        assert result.probability is None
        assert "not configured" in result.reasoning

    @pytest.mark.asyncio
    async def test_with_search_results(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.serper_search.SERPER_API_KEY", "test_key")
        from signals.serper_search import SerperSearchSignalProvider

        mock_llm.call_json = AsyncMock(side_effect=[
            # First call: generate queries
            ["query1", "query2"],
            # Second call: interpret results
            {"probability": 0.55, "confidence": 0.6, "reasoning": "Mixed evidence"},
        ])

        fake_web = [{"title": "Article 1", "snippet": "Info about topic", "link": "http://example.com", "position": 1}]
        fake_news = [{"title": "News 1", "snippet": "Breaking news", "link": "http://news.com", "source": "CNN", "date": "2 hours ago", "is_news": True}]

        with patch("signals.serper_search._serper_web_search", return_value=fake_web), \
             patch("signals.serper_search._serper_news_search", return_value=fake_news):
            provider = SerperSearchSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will X happen?", "other", "2026-12-31")

        assert result.source == "serper_search"
        assert result.probability == 0.55
        assert result.confidence == 0.6

    @pytest.mark.asyncio
    async def test_no_search_results(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.serper_search.SERPER_API_KEY", "test_key")
        from signals.serper_search import SerperSearchSignalProvider

        mock_llm.call_json = AsyncMock(return_value=["query1"])

        with patch("signals.serper_search._serper_web_search", return_value=[]), \
             patch("signals.serper_search._serper_news_search", return_value=[]):
            provider = SerperSearchSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Obscure question?", "other", "2026-12-31")

        assert result.probability is None



# ──────────────── Aggregator integration tests ────────────────────────────


class TestAggregatorWithNewProviders:
    @pytest.mark.asyncio
    async def test_weight_multipliers_include_new_sources(self):
        from signals.aggregator import SIGNAL_WEIGHT_MULTIPLIERS

        # All 9 sources should have weight multipliers
        assert len(SIGNAL_WEIGHT_MULTIPLIERS) == 9
        assert "web_search" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "prediction_markets" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "serper_search" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "resolution_econ" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "resolution_crypto" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "news" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "monte_carlo" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "technical_analysis" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "historical_base_rate" in SIGNAL_WEIGHT_MULTIPLIERS

    @pytest.mark.asyncio
    async def test_new_evidence_formatting(self):
        from signals.aggregator import _format_raw_evidence
        from signals.base import SignalResult

        # Test web_search evidence
        signal = SignalResult(
            source="web_search", probability=0.6, confidence=0.7,
            reasoning="test", model_used="sonar", data_points=3,
            raw_data={"key_evidence": ["Evidence 1", "Evidence 2"]},
        )
        evidence = _format_raw_evidence(signal)
        assert "Perplexity Sonar" in evidence
        assert "Evidence 1" in evidence

        # Test prediction_markets evidence
        signal = SignalResult(
            source="prediction_markets", probability=0.65, confidence=0.8,
            reasoning="test", model_used="cheap", data_points=2,
            raw_data={"matched_markets": [
                {"platform": "metaculus", "title": "Test Q", "probability": 0.65},
            ]},
        )
        evidence = _format_raw_evidence(signal)
        assert "metaculus" in evidence

        # Test serper_search evidence
        signal = SignalResult(
            source="serper_search", probability=0.55, confidence=0.6,
            reasoning="test", model_used="cheap", data_points=5,
            raw_data={"evidence_preview": "Some search evidence here"},
        )
        evidence = _format_raw_evidence(signal)
        assert "Search evidence" in evidence
