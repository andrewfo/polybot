"""Tests for the economics resolution signal provider (signals/resolution_econ.py).

All external dependencies (FRED API, LLM) are mocked.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.resolution_econ import (
    CACHE_TTL_SECONDS,
    EconomicsResolutionProvider,
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
    """Create an EconomicsResolutionProvider with mocked LLM."""
    return EconomicsResolutionProvider(llm=mock_llm)


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


def _make_fred_response(series_id: str, values: list[tuple[str, str]]) -> dict:
    """Build a FRED API JSON response."""
    observations = [{"date": date, "value": val} for date, val in values]
    return {"observations": observations}


@pytest.mark.asyncio
async def test_non_economics_category_skipped(provider):
    """Non-economics category returns confidence=0 immediately."""
    result = await provider.get_signal(
        market_question="Will Bitcoin hit $100k?",
        market_category="crypto",
        market_end_date="2026-06-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert result.source == "resolution_econ"
    assert "not economics" in result.reasoning


@pytest.mark.asyncio
async def test_politics_category_skipped(provider):
    """Politics category returns confidence=0 immediately."""
    result = await provider.get_signal(
        market_question="Will Biden win?",
        market_category="politics",
        market_end_date="2026-11-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None


@pytest.mark.asyncio
@patch("signals.resolution_econ.aiohttp.ClientSession")
async def test_economics_rate_fetches_fred(mock_session_cls, mock_llm):
    """Economics category with rate indicator fetches FEDFUNDS, produces SignalResult."""
    # Build FRED responses for FEDFUNDS and DFF
    fred_data = _make_fred_response("FEDFUNDS", [
        ("2026-02-01", "5.50"),
        ("2026-01-01", "5.50"),
        ("2025-12-01", "5.25"),
        ("2025-11-01", "5.25"),
        ("2025-10-01", "5.00"),
        ("2025-09-01", "5.00"),
    ])

    fred_resp = MagicMock()
    fred_resp.status = 200
    fred_resp.json = AsyncMock(return_value=fred_data)

    session_mock = MagicMock()
    session_mock.get = MagicMock(return_value=_FakeAsyncCtx(fred_resp))
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    mock_llm.call_json = AsyncMock(return_value={
        "probability": 0.35,
        "confidence": 0.8,
        "reasoning": "Fed funds rate has been rising steadily, rate cut unlikely",
    })

    provider = EconomicsResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will the Fed cut rates by June 2026?",
        market_category="economics",
        market_end_date="2026-06-01",
        resolution_keywords={"indicator_type": "rate"},
    )

    assert result.source == "resolution_econ"
    assert result.probability == 0.35
    assert result.confidence == 0.8
    assert result.model_used == "cheap"
    assert result.data_points > 0

    # Verify LLM called with cheap tier
    mock_llm.call_json.assert_called_once()
    call_args = mock_llm.call_json.call_args
    assert call_args[1].get("task_type") == "classify"


@pytest.mark.asyncio
@patch("signals.resolution_econ.aiohttp.ClientSession")
async def test_fred_api_failure_graceful(mock_session_cls, mock_llm):
    """FRED API failure returns confidence=0 gracefully."""
    error_resp = MagicMock()
    error_resp.status = 500
    error_resp.json = AsyncMock(return_value={})

    session_mock = MagicMock()
    session_mock.get = MagicMock(return_value=_FakeAsyncCtx(error_resp))
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    provider = EconomicsResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will inflation rise?",
        market_category="economics",
        market_end_date="2026-06-01",
        resolution_keywords={"indicator_type": "inflation"},
    )

    assert result.confidence == 0.0
    assert result.probability is None
    assert "No data" in result.reasoning
    mock_llm.call_json.assert_not_called()


@pytest.mark.asyncio
async def test_cache_prevents_redundant_fetches(provider, mock_llm):
    """Cached results are returned without re-fetching FRED data."""
    cached_result = SignalResult(
        source="resolution_econ",
        probability=0.45,
        confidence=0.75,
        reasoning="Cached econ result",
        model_used="cheap",
        data_points=12,
    )
    _signal_cache["Will unemployment drop?"] = (cached_result, time.monotonic())

    result = await provider.get_signal(
        market_question="Will unemployment drop?",
        market_category="economics",
        market_end_date="2026-06-01",
        resolution_keywords={"indicator_type": "employment"},
    )

    assert result.probability == 0.45
    assert result.reasoning == "Cached econ result"
    mock_llm.call_json.assert_not_called()


@pytest.mark.asyncio
@patch("signals.resolution_econ.aiohttp.ClientSession")
async def test_default_indicator_uses_treasury(mock_session_cls, mock_llm):
    """When indicator_type is 'other', default series DGS10/T10Y2Y are used."""
    fred_data = _make_fred_response("DGS10", [
        ("2026-02-01", "4.25"),
        ("2026-01-01", "4.30"),
    ])

    fred_resp = MagicMock()
    fred_resp.status = 200
    fred_resp.json = AsyncMock(return_value=fred_data)

    session_mock = MagicMock()
    session_mock.get = MagicMock(return_value=_FakeAsyncCtx(fred_resp))
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    mock_llm.call_json = AsyncMock(return_value={
        "probability": 0.50,
        "confidence": 0.5,
        "reasoning": "Neutral outlook based on treasury data",
    })

    provider = EconomicsResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will 10-year treasury yield exceed 5%?",
        market_category="economics",
        market_end_date="2026-12-01",
        resolution_keywords={"indicator_type": "other"},
    )

    assert result.probability == 0.50
    assert result.model_used == "cheap"
    # Verify FRED was called (session.get was called at least once)
    assert session_mock.get.call_count >= 1
