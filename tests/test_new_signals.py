"""Tests for the new signal providers.

Tests all 6 new signal providers:
- WebSearchSignalProvider (Perplexity Sonar)
- PredictionMarketsSignalProvider (Metaculus + Kalshi + PredictIt)
- SportsOddsSignalProvider (The Odds API)
- PoliticalSignalProvider (Congress.gov)
- WikiAttentionSignalProvider (Wikipedia pageviews)
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


# ──────────────── SportsOddsSignalProvider tests ──────────────────────────


class TestSportsOddsSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.sports_odds import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_skips_non_sports(self, mock_llm):
        from signals.sports_odds import SportsOddsSignalProvider
        provider = SportsOddsSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")

        assert result.source == "sports_odds"
        assert result.probability is None
        assert "not handled" in result.reasoning

    @pytest.mark.asyncio
    async def test_skips_without_api_key(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.sports_odds.ODDS_API_KEY", "")
        from signals.sports_odds import SportsOddsSignalProvider
        provider = SportsOddsSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Lakers win?", "sports", "2026-12-31")

        assert result.probability is None
        assert "not configured" in result.reasoning

    @pytest.mark.asyncio
    async def test_with_valid_odds(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.sports_odds.ODDS_API_KEY", "test_key")
        from signals.sports_odds import SportsOddsSignalProvider

        mock_llm.call_json = AsyncMock(return_value={
            "sport_key": "nba",
            "team_a": "Lakers",
            "team_b": "Celtics",
            "target_outcome": "Lakers win",
        })

        fake_events = [{
            "id": "ev1",
            "home_team": "Los Angeles Lakers",
            "away_team": "Boston Celtics",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Los Angeles Lakers", "price": 2.10},
                            {"name": "Boston Celtics", "price": 1.80},
                        ],
                    }],
                },
            ],
        }]

        with patch("signals.sports_odds._fetch_odds_for_sport", return_value=fake_events):
            provider = SportsOddsSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will Lakers win?", "sports", "2026-12-31")

        assert result.source == "sports_odds"
        assert result.probability is not None
        assert 0.0 <= result.probability <= 1.0

    @pytest.mark.asyncio
    async def test_no_matching_event(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.sports_odds.ODDS_API_KEY", "test_key")
        from signals.sports_odds import SportsOddsSignalProvider

        mock_llm.call_json = AsyncMock(return_value={
            "sport_key": "nba",
            "team_a": "Lakers",
            "team_b": "Celtics",
            "target_outcome": "Lakers win",
        })

        # Events with completely different teams
        fake_events = [{
            "id": "ev1",
            "home_team": "Golden State Warriors",
            "away_team": "Miami Heat",
            "bookmakers": [],
        }]

        with patch("signals.sports_odds._fetch_odds_for_sport", return_value=fake_events):
            provider = SportsOddsSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will Lakers win?", "sports", "2026-12-31")

        assert result.probability is None


# ──────────────── PoliticalSignalProvider tests ───────────────────────────


class TestPoliticalSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.political import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_skips_non_politics(self, mock_llm):
        from signals.political import PoliticalSignalProvider
        provider = PoliticalSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")

        assert result.source == "political_data"
        assert result.probability is None

    @pytest.mark.asyncio
    async def test_skips_without_api_key(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.political.CONGRESS_API_KEY", "")
        from signals.political import PoliticalSignalProvider
        provider = PoliticalSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Will bill pass?", "politics", "2026-12-31")

        assert result.probability is None
        assert "not configured" in result.reasoning

    @pytest.mark.asyncio
    async def test_with_legislative_data(self, mock_llm, monkeypatch):
        monkeypatch.setattr("signals.political.CONGRESS_API_KEY", "test_key")
        from signals.political import PoliticalSignalProvider

        mock_llm.cheap = AsyncMock(return_value="infrastructure bill")
        mock_llm.call_json = AsyncMock(return_value={
            "probability": 0.35,
            "confidence": 0.6,
            "reasoning": "Bill is still in committee",
            "relevant_bills": 2,
        })

        fake_bills = [{
            "title": "Infrastructure Bill",
            "number": "1234",
            "type": "HR",
            "congress": "119",
            "latest_action": "Referred to committee",
            "latest_action_date": "2026-03-01",
            "origin_chamber": "House",
            "update_date": "2026-03-10",
            "url": "",
        }]

        with patch("signals.political._search_bills", return_value=fake_bills), \
             patch("signals.political._fetch_recent_legislation", return_value=[]):
            provider = PoliticalSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will infrastructure bill pass?", "politics", "2026-12-31")

        assert result.source == "political_data"
        assert result.probability == 0.35
        assert result.confidence == 0.6


# ──────────────── WikiAttentionSignalProvider tests ───────────────────────


class TestWikiAttentionSignalProvider:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from signals.wiki_attention import clear_signal_cache
        clear_signal_cache()
        yield
        clear_signal_cache()

    @pytest.mark.asyncio
    async def test_basic_attention_signal(self, mock_llm):
        from signals.wiki_attention import WikiAttentionSignalProvider

        mock_llm.call_json = AsyncMock(side_effect=[
            # First call: identify articles
            ["Bitcoin", "Cryptocurrency"],
            # Second call: interpret attention
            {"probability": 0.6, "confidence": 0.3, "reasoning": "Normal attention", "attention_signal": "neutral"},
        ])

        fake_views = [{"date": f"202603{d:02d}", "views": 1000 + d * 10} for d in range(1, 31)]

        with patch("signals.wiki_attention._fetch_pageviews", return_value=fake_views):
            provider = WikiAttentionSignalProvider(llm=mock_llm)
            result = await provider.get_signal("Will BTC hit 100k?", "crypto", "2026-12-31")

        assert result.source == "wiki_attention"
        assert result.probability == 0.6
        # Confidence capped at 0.5 for attention signals
        assert result.confidence <= 0.5

    @pytest.mark.asyncio
    async def test_no_articles_identified(self, mock_llm):
        from signals.wiki_attention import WikiAttentionSignalProvider

        mock_llm.call_json = AsyncMock(return_value=[])

        provider = WikiAttentionSignalProvider(llm=mock_llm)
        result = await provider.get_signal("Some obscure question?", "other", "2026-12-31")

        assert result.probability is None

    @pytest.mark.asyncio
    async def test_spike_detection(self):
        from signals.wiki_attention import _analyze_attention

        # Simulate a spike: last 3 days have 5x the views
        views = [{"date": f"day{i}", "views": 100} for i in range(27)]
        views += [{"date": f"day{i}", "views": 500} for i in range(27, 30)]

        analysis = _analyze_attention(views)
        assert analysis["spike_detected"] is True
        assert analysis["spike_ratio"] >= 4.0

    @pytest.mark.asyncio
    async def test_no_spike(self):
        from signals.wiki_attention import _analyze_attention

        # Flat views — no spike
        views = [{"date": f"day{i}", "views": 100} for i in range(30)]

        analysis = _analyze_attention(views)
        assert analysis["spike_detected"] is False


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


# ──────────────── Odds conversion tests ───────────────────────────────────


class TestOddsConversion:
    def test_decimal_odds_to_prob(self):
        from signals.sports_odds import _decimal_odds_to_prob
        assert abs(_decimal_odds_to_prob(2.0) - 0.5) < 0.001
        assert abs(_decimal_odds_to_prob(4.0) - 0.25) < 0.001
        assert abs(_decimal_odds_to_prob(1.5) - 0.667) < 0.01
        assert _decimal_odds_to_prob(1.0) == 1.0

    def test_extract_consensus_odds(self):
        from signals.sports_odds import _extract_consensus_odds

        event = {
            "bookmakers": [
                {
                    "key": "bm1",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Team A", "price": 2.0},
                            {"name": "Team B", "price": 2.0},
                        ],
                    }],
                },
                {
                    "key": "bm2",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Team A", "price": 2.2},
                            {"name": "Team B", "price": 1.8},
                        ],
                    }],
                },
            ],
        }

        consensus = _extract_consensus_odds(event)
        assert "Team A" in consensus
        assert "Team B" in consensus
        # Normalized probabilities should sum to ~1.0
        assert abs(sum(consensus.values()) - 1.0) < 0.01


# ──────────────── Aggregator integration tests ────────────────────────────


class TestAggregatorWithNewProviders:
    @pytest.mark.asyncio
    async def test_weight_multipliers_include_new_sources(self):
        from signals.aggregator import SIGNAL_WEIGHT_MULTIPLIERS

        # All new sources should have weight multipliers
        assert "web_search" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "prediction_markets" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "sports_odds" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "political_data" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "serper_search" in SIGNAL_WEIGHT_MULTIPLIERS
        assert "wiki_attention" in SIGNAL_WEIGHT_MULTIPLIERS

        # Weak signal should have low weight
        assert SIGNAL_WEIGHT_MULTIPLIERS["wiki_attention"] < 1.0

        # Resolution sources should have high weight
        assert SIGNAL_WEIGHT_MULTIPLIERS["sports_odds"] >= 2.0

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

        # Test sports_odds evidence
        signal = SignalResult(
            source="sports_odds", probability=0.55, confidence=0.7,
            reasoning="test", model_used="cheap", data_points=5,
            raw_data={"consensus_odds": {"Team A": 0.55, "Team B": 0.45}, "bookmaker_count": 5},
        )
        evidence = _format_raw_evidence(signal)
        assert "5 bookmakers" in evidence

        # Test wiki_attention evidence
        signal = SignalResult(
            source="wiki_attention", probability=0.5, confidence=0.3,
            reasoning="test", model_used="cheap", data_points=2,
            raw_data={"attention_data": [
                {"article": "Bitcoin", "spike_detected": True, "spike_ratio": 3.5},
            ]},
        )
        evidence = _format_raw_evidence(signal)
        assert "SPIKE" in evidence
        assert "Bitcoin" in evidence
