"""Tests for the crypto resolution signal provider (signals/resolution_crypto.py).

Includes pure math tests for the log-normal model (no mocks needed) and
integration tests with mocked CoinGecko/LLM dependencies.
"""

import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.resolution_crypto import (
    CACHE_TTL_SECONDS,
    CryptoResolutionProvider,
    _signal_cache,
    clear_signal_cache,
    log_normal_probability,
    norm_cdf,
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
    """Create a CryptoResolutionProvider with mocked LLM."""
    return CryptoResolutionProvider(llm=mock_llm)


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


# ---------------------------------------------------------------
# Pure math tests — no mocks needed
# ---------------------------------------------------------------


class TestNormCdf:
    """Test the normal CDF implementation."""

    def test_zero_gives_half(self):
        """norm_cdf(0) == 0.5 exactly."""
        assert norm_cdf(0.0) == 0.5

    def test_large_positive(self):
        """norm_cdf(large) ≈ 1.0."""
        assert norm_cdf(5.0) > 0.999

    def test_large_negative(self):
        """norm_cdf(-large) ≈ 0.0."""
        assert norm_cdf(-5.0) < 0.001

    def test_known_value_z1(self):
        """norm_cdf(1.0) ≈ 0.8413."""
        assert abs(norm_cdf(1.0) - 0.8413) < 0.001

    def test_known_value_neg1(self):
        """norm_cdf(-1.0) ≈ 0.1587."""
        assert abs(norm_cdf(-1.0) - 0.1587) < 0.001

    def test_symmetry(self):
        """norm_cdf(x) + norm_cdf(-x) ≈ 1.0."""
        for x in [0.5, 1.0, 2.0, 3.0]:
            assert abs(norm_cdf(x) + norm_cdf(-x) - 1.0) < 1e-10


class TestLogNormalProbability:
    """Test the log-normal price model."""

    def test_at_the_money(self):
        """When current == target, probability should be ≈ 0.5."""
        prob = log_normal_probability(
            current_price=100.0,
            target_price=100.0,
            annual_vol=0.80,
            days_remaining=30,
            direction="above",
        )
        # With drift = -0.5 * vol^2, at-the-money should be slightly above 0.5
        # because drift is negative (risk-neutral), so P(stay above) > 0.5 when target == current
        assert abs(prob - 0.5) < 0.15

    def test_deep_otm_above(self):
        """Target >> current → probability of reaching target is low."""
        prob = log_normal_probability(
            current_price=100.0,
            target_price=500.0,
            annual_vol=0.50,
            days_remaining=30,
            direction="above",
        )
        assert prob < 0.1

    def test_deep_itm_above(self):
        """Target << current → probability of staying above is high."""
        prob = log_normal_probability(
            current_price=500.0,
            target_price=100.0,
            annual_vol=0.50,
            days_remaining=30,
            direction="above",
        )
        assert prob > 0.9

    def test_below_direction(self):
        """Direction 'below' flips the probability."""
        prob_above = log_normal_probability(
            current_price=100.0,
            target_price=200.0,
            annual_vol=0.80,
            days_remaining=60,
            direction="above",
        )
        prob_below = log_normal_probability(
            current_price=100.0,
            target_price=200.0,
            annual_vol=0.80,
            days_remaining=60,
            direction="below",
        )
        # They should sum to ~1.0
        assert abs(prob_above + prob_below - 1.0) < 1e-10

    def test_zero_days_remaining_above(self):
        """No time left → binary: current vs target."""
        # Current above target → 1.0
        assert log_normal_probability(150.0, 100.0, 0.8, 0, "above") == 1.0
        # Current below target → 0.0
        assert log_normal_probability(50.0, 100.0, 0.8, 0, "above") == 0.0

    def test_zero_days_remaining_below(self):
        """No time left → binary for below direction."""
        assert log_normal_probability(50.0, 100.0, 0.8, 0, "below") == 1.0
        assert log_normal_probability(150.0, 100.0, 0.8, 0, "below") == 0.0

    def test_zero_volatility(self):
        """Zero vol → deterministic: current compared to target."""
        assert log_normal_probability(150.0, 100.0, 0.0, 30, "above") == 1.0
        assert log_normal_probability(50.0, 100.0, 0.0, 30, "above") == 0.0

    def test_higher_vol_increases_otm_prob(self):
        """Higher volatility should increase probability of reaching an OTM target."""
        prob_low_vol = log_normal_probability(100.0, 200.0, 0.3, 60, "above")
        prob_high_vol = log_normal_probability(100.0, 200.0, 1.5, 60, "above")
        assert prob_high_vol > prob_low_vol

    def test_more_time_increases_otm_prob(self):
        """More time should increase probability of reaching an OTM target."""
        prob_short = log_normal_probability(100.0, 200.0, 0.8, 7, "above")
        prob_long = log_normal_probability(100.0, 200.0, 0.8, 180, "above")
        assert prob_long > prob_short

    def test_known_z_score(self):
        """Verify with a hand-computed z-score scenario."""
        # Setup: current=100, target=110, vol=0.50, 365 days
        # log_ratio = ln(110/100) = ln(1.1) ≈ 0.09531
        # drift = -0.5 * 0.25 = -0.125
        # time_years = 1.0
        # z = (0.09531 - (-0.125) * 1.0) / (0.50 * 1.0) = (0.09531 + 0.125) / 0.50 = 0.22031 / 0.50 = 0.44062
        # P(above) = 1 - norm_cdf(0.44062) ≈ 1 - 0.6703 ≈ 0.3297
        prob = log_normal_probability(100.0, 110.0, 0.50, 365, "above")
        expected_z = 0.44063
        expected_prob = 1.0 - norm_cdf(expected_z)
        assert abs(prob - expected_prob) < 0.001


# ---------------------------------------------------------------
# Integration tests with mocked APIs
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_crypto_category_skipped(provider):
    """Non-crypto category returns confidence=0 immediately."""
    result = await provider.get_signal(
        market_question="Will the Fed raise rates?",
        market_category="economics",
        market_end_date="2026-06-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert result.source == "resolution_crypto"
    assert "not crypto" in result.reasoning


@pytest.mark.asyncio
@patch("signals.resolution_crypto.aiohttp.ClientSession")
async def test_crypto_full_pipeline(mock_session_cls, mock_llm):
    """Crypto category fetches CoinGecko, computes model, adjusts via LLM."""
    # Price response
    price_data = {"bitcoin": {"usd": 95000, "usd_24h_change": 2.5}}
    price_resp = MagicMock()
    price_resp.status = 200
    price_resp.json = AsyncMock(return_value=price_data)

    # Chart response — 30 days of simulated prices
    import random
    random.seed(42)
    base = 90000
    prices = []
    for i in range(30):
        ts = 1700000000000 + i * 86400000
        price = base + random.gauss(0, 2000)
        prices.append([ts, price])
    chart_data = {"prices": prices}
    chart_resp = MagicMock()
    chart_resp.status = 200
    chart_resp.json = AsyncMock(return_value=chart_data)

    session_mock = MagicMock()
    call_count = 0

    def side_effect_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "simple/price" in url:
            return _FakeAsyncCtx(price_resp)
        else:
            return _FakeAsyncCtx(chart_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    # LLM adjustment response
    mock_llm.call_json = AsyncMock(return_value={
        "probability": 0.42,
        "confidence": 0.75,
        "reasoning": "Model shows moderate probability, upward trend supports slight increase",
    })

    provider = CryptoResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will Bitcoin reach $150,000 by end of 2026?",
        market_category="crypto",
        market_end_date="2026-12-31",
        resolution_keywords={
            "coin_id": "bitcoin",
            "target_value": 150000,
            "target_direction": "above",
        },
    )

    assert result.source == "resolution_crypto"
    assert result.probability == 0.42
    assert result.confidence == 0.75
    assert result.model_used == "cheap"
    assert result.data_points > 0
    # raw_data should contain both model_prob and adjusted_prob
    assert "model_prob" in result.raw_data
    assert "adjusted_prob" in result.raw_data

    # Verify LLM called with cheap tier
    mock_llm.call_json.assert_called_once()
    call_args = mock_llm.call_json.call_args
    assert call_args[1].get("task_type") == "classify"


@pytest.mark.asyncio
@patch("signals.resolution_crypto.db")
@patch("signals.resolution_crypto.aiohttp.ClientSession")
async def test_missing_coin_id_llm_maps_it(mock_session_cls, mock_db, mock_llm):
    """When coin_id is missing, cheap LLM maps coin name → CoinGecko ID."""
    # Mock db methods
    mock_db.get_cached_market.return_value = None
    mock_db.cache_market = MagicMock()
    mock_db.record_signal = MagicMock()

    # Price response
    price_data = {"ethereum": {"usd": 3500, "usd_24h_change": -1.2}}
    price_resp = MagicMock()
    price_resp.status = 200
    price_resp.json = AsyncMock(return_value=price_data)

    # Chart response
    prices = [[1700000000000 + i * 86400000, 3400 + i * 10] for i in range(30)]
    chart_data = {"prices": prices}
    chart_resp = MagicMock()
    chart_resp.status = 200
    chart_resp.json = AsyncMock(return_value=chart_data)

    session_mock = MagicMock()

    def side_effect_get(url, **kwargs):
        if "simple/price" in url:
            return _FakeAsyncCtx(price_resp)
        return _FakeAsyncCtx(chart_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    # First LLM call: map name → ID. Second: adjust probability.
    mock_llm.call_json = AsyncMock(side_effect=[
        {"coin_id": "ethereum"},
        {"probability": 0.30, "confidence": 0.6, "reasoning": "ETH unlikely to reach target"},
    ])

    provider = CryptoResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will Ethereum reach $10,000?",
        market_category="crypto",
        market_end_date="2026-12-31",
        resolution_keywords={
            "target_value": 10000,
            "target_direction": "above",
        },
    )

    assert result.source == "resolution_crypto"
    assert result.probability == 0.30
    # Verify LLM was called twice: extract (map) + classify (adjust)
    assert mock_llm.call_json.call_count == 2
    first_call = mock_llm.call_json.call_args_list[0]
    assert first_call[1].get("task_type") == "extract"


@pytest.mark.asyncio
@patch("signals.resolution_crypto.aiohttp.ClientSession")
async def test_coingecko_failure_graceful(mock_session_cls, mock_llm):
    """CoinGecko API failure returns graceful degradation."""
    error_resp = MagicMock()
    error_resp.status = 429  # rate limited
    error_resp.json = AsyncMock(return_value={})

    session_mock = MagicMock()
    session_mock.get = MagicMock(return_value=_FakeAsyncCtx(error_resp))
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    provider = CryptoResolutionProvider(llm=mock_llm)
    result = await provider.get_signal(
        market_question="Will Bitcoin reach $200k?",
        market_category="crypto",
        market_end_date="2026-12-31",
        resolution_keywords={"coin_id": "bitcoin", "target_value": 200000},
    )

    assert result.confidence == 0.0
    assert result.probability is None
    assert "Failed to fetch" in result.reasoning


@pytest.mark.asyncio
async def test_cache_prevents_redundant_fetches(provider, mock_llm):
    """Cached results are returned without re-fetching CoinGecko data."""
    cached_result = SignalResult(
        source="resolution_crypto",
        probability=0.35,
        confidence=0.7,
        reasoning="Cached crypto result",
        model_used="cheap",
        data_points=30,
    )
    _signal_cache["Will BTC reach $100k?"] = (cached_result, time.monotonic())

    result = await provider.get_signal(
        market_question="Will BTC reach $100k?",
        market_category="crypto",
        market_end_date="2026-12-31",
        resolution_keywords={"coin_id": "bitcoin"},
    )

    assert result.probability == 0.35
    assert result.reasoning == "Cached crypto result"
    mock_llm.call_json.assert_not_called()
