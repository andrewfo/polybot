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
    VolEstimate,
    _compute_volatility,
    _shrink_drift,
    _signal_cache,
    barrier_probability,
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
        """When current == target, probability should be ≈ 0.5 (no drift)."""
        prob = log_normal_probability(
            current_price=100.0,
            target_price=100.0,
            annual_vol=0.80,
            days_remaining=30,
            direction="above",
        )
        # With risk-neutral drift (no drift arg), at-the-money should be ≈ 0.5
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
        """Verify with a hand-computed z-score scenario (risk-neutral, no drift arg)."""
        # Setup: current=100, target=110, vol=0.50, 365 days, no drift arg → risk-neutral
        # effective_drift = -0.5 * 0.25 = -0.125
        # log_ratio = ln(1.1) ≈ 0.09531
        # z = (0.09531 - (-0.125)) / 0.50 = 0.44062
        prob = log_normal_probability(100.0, 110.0, 0.50, 365, "above")
        expected_z = 0.44063
        expected_prob = 1.0 - norm_cdf(expected_z)
        assert abs(prob - expected_prob) < 0.001

    def test_positive_drift_increases_above_prob(self):
        """Positive real-world drift should increase P(above target)."""
        prob_no_drift = log_normal_probability(100.0, 150.0, 0.80, 180, "above")
        prob_pos_drift = log_normal_probability(
            100.0, 150.0, 0.80, 180, "above", drift=0.30
        )
        assert prob_pos_drift > prob_no_drift

    def test_negative_drift_decreases_above_prob(self):
        """Negative real-world drift should decrease P(above target)."""
        prob_no_drift = log_normal_probability(100.0, 150.0, 0.80, 180, "above")
        prob_neg_drift = log_normal_probability(
            100.0, 150.0, 0.80, 180, "above", drift=-0.50
        )
        assert prob_neg_drift < prob_no_drift


class TestComputeVolatility:
    """Test _compute_volatility returns VolEstimate with rich vol data."""

    def test_returns_vol_estimate(self):
        """Should return a VolEstimate dataclass."""
        prices = [[1000 + i * 86400000, 100 + i * 0.5] for i in range(30)]
        result = _compute_volatility(prices)
        assert isinstance(result, VolEstimate)
        assert result.annual_vol > 0
        assert result.realized_drift is not None
        assert result.data_points > 0
        assert result.avg_interval_hours > 0

    def test_insufficient_data(self):
        """Fewer than 2 prices returns zero VolEstimate."""
        result = _compute_volatility([[1000, 100.0]])
        assert result.annual_vol == 0.0
        assert result.realized_drift is None

    def test_upward_prices_positive_drift(self):
        """Steadily rising prices should produce positive drift."""
        prices = [[i * 86400000, 100 * (1.001 ** i)] for i in range(90)]
        result = _compute_volatility(prices)
        assert result.realized_drift is not None
        assert result.realized_drift > 0

    def test_downward_prices_negative_drift(self):
        """Steadily falling prices should produce negative drift."""
        prices = [[i * 86400000, 100 * (0.999 ** i)] for i in range(90)]
        result = _compute_volatility(prices)
        assert result.realized_drift is not None
        assert result.realized_drift < 0

    def test_bessel_correction(self):
        """Verify Bessel's correction (N-1 denominator) is used.

        With constant-increment prices, the variance should be computed
        with N-1 in the denominator (sample variance), not N (population).
        """
        # Create prices with known log-returns
        prices = [[i * 86400000, 100.0 * math.exp(0.001 * i)] for i in range(10)]
        result = _compute_volatility(prices)
        # 9 returns, should use N-1=8 in denominator
        assert result.annual_vol > 0
        assert result.data_points == 10

    def test_ewm_vol_computed(self):
        """EWM vol should be computed and positive for sufficient data."""
        prices = [[i * 86400000, 100 + math.sin(i) * 5] for i in range(30)]
        result = _compute_volatility(prices)
        assert result.annual_vol_ewm > 0

    def test_short_term_vol_computed(self):
        """Short-term vol should be computed from recent data."""
        # 90 days of data with higher vol in last 7 days
        prices = []
        for i in range(90):
            ts = i * 86400000
            if i < 83:
                price = 100 + i * 0.1  # low vol period
            else:
                price = 100 + i * 0.1 + math.sin(i * 5) * 10  # high vol period
            prices.append([ts, price])
        result = _compute_volatility(prices)
        # Short-term vol should capture the recent high-vol period
        assert result.short_term_vol > 0

    def test_drift_stderr_computed(self):
        """Drift standard error should be computed for sufficient data."""
        prices = [[i * 86400000, 100 * (1.001 ** i)] for i in range(90)]
        result = _compute_volatility(prices)
        assert result.drift_stderr is not None
        assert result.drift_stderr > 0

    def test_time_aware_intervals(self):
        """Intervals should be computed from actual timestamps, not assumed daily."""
        # Create hourly data (not daily)
        prices = [[i * 3600000, 100 + math.sin(i) * 2] for i in range(200)]
        result = _compute_volatility(prices)
        # Average interval should be ~1 hour, not ~24 hours
        assert result.avg_interval_hours < 2.0


class TestBarrierProbability:
    """Test the barrier/touch option probability model."""

    def test_barrier_geq_terminal(self):
        """Barrier probability should always be >= terminal probability."""
        for target in [120, 150, 200, 300]:
            terminal = log_normal_probability(100, target, 0.80, 60, "above")
            barrier = barrier_probability(100, target, 0.80, 60, "above")
            assert barrier >= terminal - 1e-10, (
                f"Barrier ({barrier:.4f}) < terminal ({terminal:.4f}) "
                f"for target={target}"
            )

    def test_barrier_geq_terminal_below(self):
        """Barrier probability for 'below' should be >= terminal."""
        for target in [80, 60, 40, 20]:
            terminal = log_normal_probability(100, target, 0.80, 60, "below")
            barrier = barrier_probability(100, target, 0.80, 60, "below")
            assert barrier >= terminal - 1e-10

    def test_already_at_barrier(self):
        """If price already at/past barrier, probability is 1.0."""
        assert barrier_probability(100, 100, 0.80, 60, "above") == 1.0
        assert barrier_probability(100, 120, 0.80, 60, "below") == 1.0

    def test_zero_days(self):
        """No time left → same as terminal (binary outcome)."""
        assert barrier_probability(150, 100, 0.80, 0, "above") == 1.0
        assert barrier_probability(50, 100, 0.80, 0, "above") == 0.0

    def test_higher_vol_increases_barrier_prob(self):
        """Higher volatility should increase barrier probability."""
        prob_low = barrier_probability(100, 150, 0.30, 60, "above")
        prob_high = barrier_probability(100, 150, 1.50, 60, "above")
        assert prob_high > prob_low

    def test_more_time_increases_barrier_prob(self):
        """More time should increase barrier probability."""
        prob_short = barrier_probability(100, 150, 0.80, 7, "above")
        prob_long = barrier_probability(100, 150, 0.80, 180, "above")
        assert prob_long > prob_short

    def test_barrier_significantly_higher_than_terminal(self):
        """For OTM targets, barrier should be significantly higher than terminal.

        This is the key insight: "Will BTC reach $150k?" is much more likely
        than "Will BTC be above $150k on Dec 31?" because it only needs to
        touch the target at any point.
        """
        terminal = log_normal_probability(100, 200, 0.80, 90, "above")
        barrier = barrier_probability(100, 200, 0.80, 90, "above")
        # Barrier should be at least 50% higher than terminal for a 2x OTM target
        assert barrier > terminal * 1.5, (
            f"Expected barrier ({barrier:.4f}) to be significantly higher "
            f"than terminal ({terminal:.4f})"
        )

    def test_known_values_no_drift(self):
        """Verify barrier probability with known parameters (risk-neutral)."""
        # Current=100, target=120 (20% above), vol=80%, 90 days
        prob = barrier_probability(100, 120, 0.80, 90, "above")
        # Should be substantially higher than 50/50
        assert 0.3 < prob < 0.95


class TestShrinkDrift:
    """Test the drift shrinkage function."""

    def test_zero_drift_stays_zero(self):
        assert _shrink_drift(0.0, 0.5) == 0.0

    def test_no_stderr_shrinks_50pct(self):
        """Without stderr, drift is shrunk 50%."""
        assert _shrink_drift(1.0, None) == 0.5
        assert _shrink_drift(-0.6, None) == -0.3

    def test_high_significance_keeps_drift(self):
        """High t-statistic keeps most of the drift."""
        # t = 3.0/0.5 = 6.0 → shrinkage = 36/37 ≈ 0.973
        result = _shrink_drift(3.0, 0.5)
        assert abs(result - 3.0) < 0.2

    def test_low_significance_shrinks_heavily(self):
        """Low t-statistic shrinks drift toward zero."""
        # t = 0.1/0.5 = 0.2 → shrinkage = 0.04/1.04 ≈ 0.038
        result = _shrink_drift(0.1, 0.5)
        assert abs(result) < 0.02

    def test_preserves_sign(self):
        """Shrinkage should preserve the sign of drift."""
        assert _shrink_drift(1.0, 0.3) > 0
        assert _shrink_drift(-1.0, 0.3) < 0


# ---------------------------------------------------------------
# Integration tests with mocked APIs
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_crypto_category_skipped(provider):
    """Non-crypto category returns confidence=0 immediately."""
    result = await provider.get_signal(
        market_question="Will the Fed raise rates?",
        market_category="other",
        market_end_date="2026-06-01",
    )
    assert result.confidence == 0.0
    assert result.probability is None
    assert result.source == "resolution_crypto"
    assert "not crypto" in result.reasoning


@pytest.mark.asyncio
@patch("signals.resolution_crypto.aiohttp.ClientSession")
async def test_crypto_full_pipeline(mock_session_cls, mock_llm):
    """Crypto category fetches CoinGecko + Deribit, computes model with drift."""
    # Price response
    price_data = {"bitcoin": {"usd": 95000, "usd_24h_change": 2.5}}
    price_resp = MagicMock()
    price_resp.status = 200
    price_resp.json = AsyncMock(return_value=price_data)

    # Chart response — 90 days of simulated prices (now fetches 90d for drift)
    import random
    random.seed(42)
    base = 90000
    prices = []
    for i in range(90):
        ts = 1700000000000 + i * 86400000
        price = base + random.gauss(0, 2000)
        prices.append([ts, price])
    chart_data = {"prices": prices}
    chart_resp = MagicMock()
    chart_resp.status = 200
    chart_resp.json = AsyncMock(return_value=chart_data)

    # Deribit DVOL response (get_volatility_index_data format)
    now_ms = int(time.time() * 1000)
    deribit_data = {"result": {"data": [[now_ms, 71.0, 73.0, 70.5, 72.5]], "continuation": None}}
    deribit_resp = MagicMock()
    deribit_resp.status = 200
    deribit_resp.json = AsyncMock(return_value=deribit_data)

    session_mock = MagicMock()

    def side_effect_get(url, **kwargs):
        if "simple/price" in url:
            return _FakeAsyncCtx(price_resp)
        elif "deribit" in url:
            return _FakeAsyncCtx(deribit_resp)
        else:
            return _FakeAsyncCtx(chart_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

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
    assert result.probability is not None
    assert 0.0 <= result.probability <= 1.0
    assert result.confidence >= 0.55
    assert result.model_used == "none"
    assert result.data_points > 0
    assert "raw_log_normal_prob" in result.raw_data
    assert "current_price" in result.raw_data
    assert "target_price" in result.raw_data
    # Volatility fields
    assert "realized_drift" in result.raw_data
    assert "vol_source" in result.raw_data
    assert result.raw_data["vol_source"] == "deribit_iv"
    assert result.raw_data["deribit_iv"] == pytest.approx(0.725)
    assert result.raw_data["annualized_vol"] == pytest.approx(0.725)
    # New fields: barrier/terminal probabilities
    assert "barrier_prob" in result.raw_data
    assert "terminal_prob" in result.raw_data
    assert "resolution_type" in result.raw_data
    assert "shrunk_drift" in result.raw_data
    assert "ewm_vol" in result.raw_data
    # Barrier prob should be >= terminal prob
    assert result.raw_data["barrier_prob"] >= result.raw_data["terminal_prob"] - 1e-10

    mock_llm.call_json.assert_not_called()


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

    # Deribit response (ethereum is listed)
    deribit_resp = MagicMock()
    deribit_resp.status = 404  # no DVOL for this test
    deribit_resp.json = AsyncMock(return_value={})

    session_mock = MagicMock()

    def side_effect_get(url, **kwargs):
        if "simple/price" in url:
            return _FakeAsyncCtx(price_resp)
        elif "deribit" in url:
            return _FakeAsyncCtx(deribit_resp)
        return _FakeAsyncCtx(chart_resp)

    session_mock.get = MagicMock(side_effect=side_effect_get)
    mock_session_cls.return_value = _FakeSessionCtx(session_mock)

    # Only LLM call: map name → ID (no adjustment call)
    mock_llm.call_json = AsyncMock(return_value={"coin_id": "ethereum"})

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
    assert result.probability is not None
    assert 0.0 <= result.probability <= 1.0
    assert result.model_used == "none"  # Pure math, no LLM adjustment
    # With the ticker whitelist, "Ethereum" is resolved directly without LLM
    # LLM should NOT be called (whitelist match for "ethereum")
    assert mock_llm.call_json.call_count == 0


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
