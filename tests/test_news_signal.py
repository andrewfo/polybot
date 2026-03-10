"""Tests for the news signal provider (signals/news.py).

All external dependencies (Google News, Reddit, LLM) are mocked.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from signals.base import SignalProvider, SignalResult
from signals.news import (
    CACHE_TTL_SECONDS,
    NewsSignalProvider,
    _deduplicate_articles,
    _signal_cache,
    clear_signal_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear signal cache before each test."""
    clear_signal_cache()
    yield
    clear_signal_cache()


@pytest.fixture
def mock_llm():
    """Create a mock LLMClient."""
    llm = AsyncMock()
    return llm


@pytest.fixture
def provider(mock_llm):
    """Create a NewsSignalProvider with mocked LLM."""
    return NewsSignalProvider(llm=mock_llm)


def _make_google_rss(articles):
    """Build a minimal RSS XML string for feedparser."""
    items = ""
    for a in articles:
        items += f"""
        <item>
            <title>{a['title']}</title>
            <description>{a.get('snippet', '')}</description>
            <pubDate>{a.get('published', '')}</pubDate>
        </item>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
    <channel><title>Google News</title>{items}</channel>
    </rss>"""


def _make_reddit_response(posts):
    """Build a Reddit search JSON response."""
    children = []
    for p in posts:
        children.append({
            "data": {
                "title": p["title"],
                "selftext": p.get("snippet", ""),
            }
        })
    return {"data": {"children": children}}


class _FakeAsyncCtx:
    """Fake async context manager wrapping a response mock."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


def _build_mock_session(google_rss: str, reddit_json: dict):
    """Build a mock aiohttp.ClientSession that returns canned responses."""
    mock_resp_google = AsyncMock()
    mock_resp_google.status = 200
    mock_resp_google.text = AsyncMock(return_value=google_rss)

    mock_resp_reddit = AsyncMock()
    mock_resp_reddit.status = 200
    mock_resp_reddit.json = AsyncMock(return_value=reddit_json)

    def mock_get(url, **kwargs):
        if "news.google.com" in url:
            return _FakeAsyncCtx(mock_resp_google)
        else:
            return _FakeAsyncCtx(mock_resp_reddit)

    mock_session = MagicMock()
    mock_session.get = mock_get

    # ClientSession() used as async context manager
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_session_ctx


class TestSignalResultAndProvider:
    """Test that SignalResult and SignalProvider are properly defined."""

    def test_signal_result_fields(self):
        r = SignalResult(
            source="news",
            probability=0.65,
            confidence=0.7,
            reasoning="test",
            model_used="cheap",
            data_points=5,
            raw_data={"key": "val"},
        )
        assert r.source == "news"
        assert r.probability == 0.65
        assert r.confidence == 0.7
        assert r.data_points == 5
        assert r.raw_data == {"key": "val"}

    def test_signal_result_none_probability(self):
        r = SignalResult(
            source="news",
            probability=None,
            confidence=0.0,
            reasoning="insufficient",
            model_used="none",
            data_points=0,
        )
        assert r.probability is None
        assert r.raw_data == {}

    def test_signal_provider_is_abstract(self):
        p = SignalProvider()
        assert p.name == "base"

    @pytest.mark.asyncio
    async def test_signal_provider_raises(self):
        p = SignalProvider()
        with pytest.raises(NotImplementedError):
            await p.get_signal("question", "category", "2026-12-31")

    def test_imports_from_package(self):
        from signals import NewsSignalProvider, SignalProvider, SignalResult
        assert SignalResult is not None
        assert SignalProvider is not None
        assert NewsSignalProvider is not None


class TestDeduplication:
    """Test title deduplication logic."""

    def test_removes_near_identical(self):
        articles = [
            {"title": "Trump wins election in landslide victory"},
            {"title": "Trump wins election in landslide"},
            {"title": "Biden announces new climate policy"},
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 2
        assert result[0]["title"] == "Trump wins election in landslide victory"
        assert result[1]["title"] == "Biden announces new climate policy"

    def test_keeps_different_articles(self):
        articles = [
            {"title": "Fed raises interest rates"},
            {"title": "Bitcoin hits new all time high"},
            {"title": "Ukraine peace talks resume"},
        ]
        result = _deduplicate_articles(articles)
        assert len(result) == 3

    def test_empty_list(self):
        assert _deduplicate_articles([]) == []

    def test_single_article(self):
        articles = [{"title": "Only article"}]
        assert len(_deduplicate_articles(articles)) == 1


class TestNewsSignalPipeline:
    """Test the full news signal pipeline with mocked externals."""

    @pytest.mark.asyncio
    @patch("signals.news.db")
    async def test_sufficient_articles_returns_probability(self, mock_db, mock_llm, provider):
        """With enough articles, pipeline returns probability and confidence."""
        mock_llm.call_json = AsyncMock(side_effect=[
            ["election results 2026", "midterm polls latest"],
            {"summary": "Polls show tight race.", "direction": "NEUTRAL"},
            {"summary": "Republican lead grows.", "direction": "YES"},
            {"summary": "Democratic turnout surges.", "direction": "NO"},
            {"probability": 0.55, "confidence": 0.6, "reasoning": "Mixed evidence."},
        ])

        google_rss = _make_google_rss([
            {"title": "Election polls show tight race", "snippet": "Latest polls..."},
            {"title": "Republican candidate gains ground", "snippet": "New data shows..."},
        ])
        reddit_json = _make_reddit_response([
            {"title": "Democratic turnout expected high", "snippet": "Analysts predict..."},
        ])

        mock_session_ctx = _build_mock_session(google_rss, reddit_json)

        with patch("signals.news.aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await provider.get_signal(
                "Will Republicans win the 2026 midterms?",
                "politics",
                "2026-11-03",
            )

        assert result.source == "news"
        assert result.probability == 0.55
        assert result.confidence == 0.6
        assert result.data_points == 3
        assert result.reasoning == "Mixed evidence."
        mock_db.record_signal.assert_called_once()

    @pytest.mark.asyncio
    @patch("signals.news.db")
    async def test_fewer_than_2_articles_returns_none(self, mock_db, mock_llm, provider):
        """With fewer than 2 articles, returns confidence=0, probability=None."""
        mock_llm.call_json = AsyncMock(return_value=["test query"])

        google_rss = _make_google_rss([
            {"title": "One article", "snippet": "Just one."},
        ])
        reddit_json = _make_reddit_response([])

        mock_session_ctx = _build_mock_session(google_rss, reddit_json)

        with patch("signals.news.aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await provider.get_signal("Will X happen?", "general", "2026-12-31")

        assert result.probability is None
        assert result.confidence == 0.0
        assert result.data_points == 1

    @pytest.mark.asyncio
    @patch("signals.news.db")
    async def test_llm_failure_during_summarization_graceful(self, mock_db, mock_llm, provider):
        """If LLM fails during summarization, pipeline degrades gracefully."""
        call_count = 0

        async def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ["test query"]
            elif call_count <= 4:
                raise Exception("LLM timeout")
            else:
                return {"probability": None, "confidence": 0.0, "reasoning": "No summaries"}

        mock_llm.call_json = AsyncMock(side_effect=side_effect_fn)

        google_rss = _make_google_rss([
            {"title": "Article A", "snippet": "Text A"},
            {"title": "Article B", "snippet": "Text B"},
            {"title": "Article C", "snippet": "Text C"},
        ])
        reddit_json = _make_reddit_response([])

        mock_session_ctx = _build_mock_session(google_rss, reddit_json)

        with patch("signals.news.aiohttp.ClientSession", return_value=mock_session_ctx):
            result = await provider.get_signal("Will X happen?", "general", "2026-12-31")

        assert result.source == "news"
        assert isinstance(result, SignalResult)

    @pytest.mark.asyncio
    @patch("signals.news.db")
    async def test_cache_returns_same_result(self, mock_db, mock_llm, provider):
        """Cache prevents redundant pipeline runs within 30 minutes."""
        mock_llm.call_json = AsyncMock(side_effect=[
            ["test query"],
            {"summary": "Summary.", "direction": "YES"},
            {"summary": "Summary 2.", "direction": "NO"},
            {"probability": 0.7, "confidence": 0.5, "reasoning": "Evidence."},
        ])

        google_rss = _make_google_rss([
            {"title": "Article 1", "snippet": "Text"},
            {"title": "Article 2", "snippet": "Text 2"},
        ])
        reddit_json = _make_reddit_response([])

        mock_session_ctx = _build_mock_session(google_rss, reddit_json)

        with patch("signals.news.aiohttp.ClientSession", return_value=mock_session_ctx):
            result1 = await provider.get_signal("Will Y happen?", "general", "2026-12-31")
            result2 = await provider.get_signal("Will Y happen?", "general", "2026-12-31")

        assert result1.probability == result2.probability
        assert result1.confidence == result2.confidence
        assert mock_llm.call_json.call_count == 4

    @pytest.mark.asyncio
    @patch("signals.news.db")
    async def test_all_llm_calls_use_cheap_tier(self, mock_db, mock_llm, provider):
        """Verify all LLM calls route through cheap task types only."""
        task_types_used = []

        async def tracking_call_json(prompt, task_type):
            task_types_used.append(task_type)
            if task_type == "search_queries":
                return ["test query"]
            elif task_type == "summarize":
                return {"summary": "Summary.", "direction": "YES"}
            else:
                return {"probability": 0.5, "confidence": 0.5, "reasoning": "reason"}

        mock_llm.call_json = AsyncMock(side_effect=tracking_call_json)

        google_rss = _make_google_rss([
            {"title": "Article 1", "snippet": "Text"},
            {"title": "Article 2", "snippet": "Text 2"},
        ])
        reddit_json = _make_reddit_response([])

        mock_session_ctx = _build_mock_session(google_rss, reddit_json)

        with patch("signals.news.aiohttp.ClientSession", return_value=mock_session_ctx):
            await provider.get_signal("Will Z happen?", "general", "2026-12-31")

        cheap_task_types = {"search_queries", "summarize", "classify", "extract", "parse"}
        for tt in task_types_used:
            assert tt in cheap_task_types, f"Task type '{tt}' is not cheap tier"
