"""Tests for the polling signal provider (signals/polling.py).

All external dependencies (RSS feeds, RCP scraping, LLM) are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.polling import (
    CACHE_TTL_SECONDS,
    PollingSignalProvider,
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
    """Create a PollingSignalProvider with mocked LLM."""
    return PollingSignalProvider(llm=mock_llm)


def _make_rss_xml(entries):
    """Build a minimal RSS XML string for feedparser."""
    items = ""
    for e in entries:
        items += f"""
        <item>
            <title>{e['title']}</title>
            <description>{e.get('summary', '')}</description>
            <pubDate>{e.get('published', '')}</pubDate>
        </item>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
    <channel><title>Polls</title>{items}</channel>
    </rss>"""


def _make_rcp_html(rows):
    """Build a minimal HTML page with a polling table."""
    table_rows = ""
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        table_rows += f"<tr>{cells}</tr>"
    return f"<html><body><table>{table_rows}</table></body></html>"


class _FakeAsyncCtx:
    """Fake async context manager wrapping a response mock."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        pass


class _FakeSessionCtx:
    """Fake async context manager for aiohttp.ClientSession."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_economics_category_skipped(provider):
    """Economics category returns confidence=0, probability=None immediately."""
    result = await provider.get_signal(
        market_question="Will the Fed raise rates?",
        market_category="economics",
        market_end_date="2026-06-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert result.source == "polling"
    assert "dedicated resolution provider" in result.reasoning


@pytest.mark.asyncio
async def test_crypto_category_skipped(provider):
    """Crypto category returns confidence=0, probability=None immediately."""
    result = await provider.get_signal(
        market_question="Will Bitcoin reach $100k?",
        market_category="crypto",
        market_end_date="2026-06-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert result.source == "polling"
    assert "dedicated resolution provider" in result.reasoning


@pytest.mark.asyncio
async def test_unknown_category_no_data(provider):
    """Category with no data source returns confidence=0 gracefully."""
    result = await provider.get_signal(
        market_question="Will it snow in July?",
        market_category="weather",
        market_end_date="2026-07-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert "No structured data sources" in result.reasoning


@pytest.mark.asyncio
@patch("signals.polling.aiohttp.ClientSession")
async def test_politics_category_returns_probability(mock_session_cls, mock_llm):
    """Politics category fetches polling data and returns probability estimate."""
    # Setup RSS response
    rss_xml = _make_rss_xml([
        {"title": "Biden leads in new poll", "summary": "Biden 48%, Trump 45%"},
        {"title": "Senate race tightens", "summary": "Dem 47%, Rep 46%"},
    ])
    rss_resp = MagicMock()
    rss_resp.status = 200
    rss_resp.text = AsyncMock(return_value=rss_xml)

    # Setup RCP scrape response
    rcp_html = _make_rcp_html([
        ["Poll", "Biden", "Trump", "Spread"],
        ["Reuters", "48.0", "45.0", "Biden +3.0"],
    ])
    rcp_resp = MagicMock()
    rcp_resp.status = 200
    rcp_resp.text = AsyncMock(return_value=rcp_html)

    # Mock session.get to return different responses based on URL
    session_mock = MagicMock()

    def side_effect_get(url, **kwargs):
        if "fivethirtyeight" in url:
            return _FakeAsyncCtx(rss_resp)
        else:
            return _FakeAsyncCtx(rcp_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    # Mock LLM response
    mock_llm.call_json = AsyncMock(return_value={
        "probability": 0.65,
        "confidence": 0.7,
        "reasoning": "Polling data shows a consistent lead",
    })

    provider = PollingSignalProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will Biden win the election?",
        market_category="politics",
        market_end_date="2026-11-01",
    )

    assert result.source == "polling"
    assert result.probability == 0.65
    assert result.confidence == 0.7
    assert result.model_used == "cheap"
    assert result.data_points > 0
    # Verify LLM was called with cheap tier (task_type="classify")
    mock_llm.call_json.assert_called_once()
    call_args = mock_llm.call_json.call_args
    assert call_args[1].get("task_type", call_args[0][1] if len(call_args[0]) > 1 else None) == "classify"


@pytest.mark.asyncio
@patch("signals.polling.aiohttp.ClientSession")
async def test_no_structured_data_available(mock_session_cls, mock_llm):
    """When all data sources return empty, returns confidence=0."""
    # Both sources return empty responses
    empty_rss_resp = MagicMock()
    empty_rss_resp.status = 200
    empty_rss_resp.text = AsyncMock(return_value="<rss><channel></channel></rss>")

    empty_html_resp = MagicMock()
    empty_html_resp.status = 200
    empty_html_resp.text = AsyncMock(return_value="<html><body></body></html>")

    session_mock = MagicMock()

    def side_effect_get(url, **kwargs):
        if "fivethirtyeight" in url:
            return _FakeAsyncCtx(empty_rss_resp)
        else:
            return _FakeAsyncCtx(empty_html_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    provider = PollingSignalProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will candidate X win?",
        market_category="politics",
        market_end_date="2026-11-01",
    )

    assert result.confidence == 0.0
    assert result.probability is None
    assert "No structured data" in result.reasoning
    # LLM should NOT have been called since there was no data
    mock_llm.call_json.assert_not_called()


@pytest.mark.asyncio
@patch("signals.polling.aiohttp.ClientSession")
async def test_fetch_failure_graceful_degradation(mock_session_cls, mock_llm):
    """When data source fetches fail, returns confidence=0 gracefully."""
    # Both sources return errors
    error_resp = MagicMock()
    error_resp.status = 500
    error_resp.text = AsyncMock(return_value="Server Error")

    session_mock = MagicMock()
    session_mock.get = MagicMock(return_value=_FakeAsyncCtx(error_resp))
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    provider = PollingSignalProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will candidate X win?",
        market_category="politics",
        market_end_date="2026-11-01",
    )

    assert result.confidence == 0.0
    assert result.probability is None
    mock_llm.call_json.assert_not_called()


@pytest.mark.asyncio
async def test_cache_hit(provider, mock_llm):
    """Cached results are returned without re-fetching."""
    # Pre-populate cache
    cached_result = SignalResult(
        source="polling",
        probability=0.55,
        confidence=0.6,
        reasoning="Cached result",
        model_used="cheap",
        data_points=5,
    )
    import time
    _signal_cache["Will X win?"] = (cached_result, time.monotonic())

    result = await provider.get_signal(
        market_question="Will X win?",
        market_category="politics",
        market_end_date="2026-11-01",
    )

    assert result.probability == 0.55
    assert result.reasoning == "Cached result"
    # LLM should not have been called
    mock_llm.call_json.assert_not_called()
