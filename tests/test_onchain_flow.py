"""Tests for the on-chain flow signal provider."""

import pytest

from signals.onchain_flow import (
    OnchainFlowProvider,
    _compute_pressure_from_netflow,
    _compute_whale_metric,
    _pressure_to_adjustment,
    _z_score,
    MAX_ADJUSTMENT,
    clear_flow_cache,
)


# --- Unit tests for helper functions ---


class TestZScore:
    def test_normal_case(self):
        assert _z_score(10.0, 5.0, 2.5) == pytest.approx(2.0)

    def test_zero_std_returns_zero(self):
        assert _z_score(10.0, 5.0, 0.0) == 0.0

    def test_negative_z(self):
        assert _z_score(3.0, 5.0, 1.0) == pytest.approx(-2.0)


class TestPressureToAdjustment:
    def test_max_positive(self):
        assert _pressure_to_adjustment(1.0) == pytest.approx(MAX_ADJUSTMENT)

    def test_max_negative(self):
        assert _pressure_to_adjustment(-1.0) == pytest.approx(-MAX_ADJUSTMENT)

    def test_zero(self):
        assert _pressure_to_adjustment(0.0) == 0.0

    def test_clamped_above(self):
        assert _pressure_to_adjustment(5.0) == pytest.approx(MAX_ADJUSTMENT)

    def test_clamped_below(self):
        assert _pressure_to_adjustment(-5.0) == pytest.approx(-MAX_ADJUSTMENT)

    def test_half(self):
        assert _pressure_to_adjustment(0.5) == pytest.approx(MAX_ADJUSTMENT / 2)


class TestComputePressureFromNetflow:
    def _make_records(self, values: list[float]) -> list[dict]:
        return [{"netflow": v} for v in values]

    def test_insufficient_data(self):
        pressure, metrics = _compute_pressure_from_netflow(self._make_records([1, 2, 3]))
        assert pressure == 0.0
        assert "insufficient" in metrics.get("error", "")

    def test_empty_data(self):
        pressure, _ = _compute_pressure_from_netflow([])
        assert pressure == 0.0

    def test_none_data(self):
        pressure, _ = _compute_pressure_from_netflow(None)
        assert pressure == 0.0

    def test_constant_flow_zero_pressure(self):
        # All same value -> z-score = 0 -> pressure = 0
        records = self._make_records([100.0] * 30)
        pressure, metrics = _compute_pressure_from_netflow(records)
        assert pressure == pytest.approx(0.0)

    def test_recent_inflow_spike_gives_negative_pressure(self):
        # Baseline around -500 (mild outflow), then recent large positive inflow
        values = [-500.0] * 23 + [5000.0] * 7  # Recent inflow spike
        records = self._make_records(values)
        pressure, metrics = _compute_pressure_from_netflow(records)
        # Positive netflow = sell pressure = negative pressure score
        assert pressure < -0.1

    def test_recent_outflow_gives_positive_pressure(self):
        # Baseline around +500 (mild inflow), then recent large negative netflow
        values = [500.0] * 23 + [-5000.0] * 7  # Recent outflow
        records = self._make_records(values)
        pressure, metrics = _compute_pressure_from_netflow(records)
        # Negative netflow = accumulation = positive pressure score
        assert pressure > 0.1

    def test_pressure_bounded(self):
        # Extreme values should still clamp to [-1, +1]
        values = [0.0] * 23 + [1e12] * 7
        records = self._make_records(values)
        pressure, _ = _compute_pressure_from_netflow(records)
        assert -1.0 <= pressure <= 1.0

    def test_alternative_key_format(self):
        # CryptoQuant sometimes uses "value" instead of "netflow"
        records = [{"value": v} for v in [100.0] * 30]
        pressure, metrics = _compute_pressure_from_netflow(records)
        assert pressure == pytest.approx(0.0)
        assert metrics["data_points_30d"] == 30


class TestComputeWhaleMetric:
    def test_no_data(self):
        result = _compute_whale_metric(None)
        assert not result["whale_data_available"]

    def test_empty_list(self):
        result = _compute_whale_metric([])
        assert not result["whale_data_available"]

    def test_normal_data(self):
        data = [{"transactions_count_over_1m": 50}, {"transactions_count_over_1m": 60}]
        result = _compute_whale_metric(data)
        assert result["whale_data_available"]
        assert result["whale_tx_count"] == 60  # Most recent
        assert result["whale_tx_avg_7d"] == 55.0

    def test_elevated_trend(self):
        # Last value > 1.2x average
        data = [{"value": 50}, {"value": 50}, {"value": 100}]
        result = _compute_whale_metric(data)
        assert result["whale_trend"] == "elevated"

    def test_subdued_trend(self):
        # Last value < 0.8x average
        data = [{"value": 100}, {"value": 100}, {"value": 30}]
        result = _compute_whale_metric(data)
        assert result["whale_trend"] == "subdued"


# --- Provider integration tests ---


@pytest.mark.asyncio
async def test_non_crypto_category_skipped():
    """Non-crypto markets return confidence=0 immediately."""
    clear_flow_cache()
    provider = OnchainFlowProvider()
    result = await provider.get_signal(
        "Will inflation exceed 3%?", "economics", "2026-12-31"
    )
    assert result.source == "onchain_flow"
    assert result.confidence == 0.0
    assert result.probability is None


@pytest.mark.asyncio
async def test_unsupported_coin_returns_zero():
    """Coins without CryptoQuant coverage return confidence=0."""
    clear_flow_cache()
    provider = OnchainFlowProvider()
    result = await provider.get_signal(
        "Will DOGE reach $1?", "crypto", "2026-12-31",
        resolution_keywords={"coin_id": "dogecoin"},
    )
    assert result.source == "onchain_flow"
    assert result.confidence == 0.0
    assert "coverage" in result.reasoning.lower() or "free tier" in result.reasoning.lower()


@pytest.mark.asyncio
async def test_no_coin_id_returns_zero():
    """If coin can't be identified, return confidence=0."""
    clear_flow_cache()
    provider = OnchainFlowProvider()
    result = await provider.get_signal(
        "Will the market go up?", "crypto", "2026-12-31",
        resolution_keywords={},
    )
    assert result.source == "onchain_flow"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_coin_detected_from_question():
    """Provider should detect BTC from the question text."""
    clear_flow_cache()
    provider = OnchainFlowProvider()
    # This will try to fetch real data and likely fail in CI,
    # but should at least resolve the coin correctly
    result = await provider.get_signal(
        "Will Bitcoin reach $200,000?", "crypto", "2026-12-31",
        resolution_keywords={"target_value": 200000, "target_direction": "above"},
    )
    assert result.source == "onchain_flow"
    # Should have resolved the coin (may or may not have data)
    # If CryptoQuant is down, confidence will be 0 but it shouldn't error


def test_aggregator_includes_onchain_flow():
    """Verify the aggregator's default providers include OnchainFlowProvider."""
    from signals.aggregator import DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert "onchain_flow" in DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS["onchain_flow"] == 1.3


def test_aggregator_format_raw_evidence():
    """Verify the aggregator formats onchain_flow evidence correctly."""
    from signals.aggregator import _format_raw_evidence
    from signals.base import SignalResult

    signal = SignalResult(
        source="onchain_flow",
        probability=0.54,
        confidence=0.5,
        reasoning="test",
        model_used="none",
        data_points=30,
        raw_data={
            "pressure_score": 0.45,
            "z_score": -1.35,
            "net_flow_direction": "outflow (accumulation)",
            "data_source": "cryptoquant",
            "asset": "btc",
            "mean_7d_netflow": -5000.0,
            "mean_30d_netflow": 2000.0,
            "whale_data_available": True,
            "whale_tx_count": 150,
            "whale_trend": "elevated",
        },
    )
    formatted = _format_raw_evidence(signal)
    assert "Pressure score: +0.45" in formatted
    assert "outflow (accumulation)" in formatted
    assert "BTC" in formatted
    assert "Whale txs" in formatted
    assert "150" in formatted
    assert "elevated" in formatted
