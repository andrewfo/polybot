"""Tests for the on-chain flow signal provider."""

import pytest

from signals.onchain_flow import (
    OnchainFlowProvider,
    _pressure_to_adjustment,
    MAX_ADJUSTMENT,
    clear_flow_cache,
)


# --- Unit tests for helper functions ---


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
        # tanh-shaped mapping: pressure=0.5 reaches a meaningful fraction of cap,
        # not the linear midpoint. Endpoints (±1.0, 0) still hit cap / zero exactly.
        result = _pressure_to_adjustment(0.5)
        assert 0.4 * MAX_ADJUSTMENT < result < MAX_ADJUSTMENT

    def test_monotonic_and_odd(self):
        # Mapping must be strictly increasing and odd-symmetric around 0
        assert _pressure_to_adjustment(0.2) < _pressure_to_adjustment(0.6)
        assert _pressure_to_adjustment(-0.3) == pytest.approx(-_pressure_to_adjustment(0.3))


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
    result = await provider.get_signal(
        "Will Bitcoin reach $200,000?", "crypto", "2026-12-31",
        resolution_keywords={"target_value": 200000, "target_direction": "above"},
    )
    assert result.source == "onchain_flow"


def test_max_adjustment_is_18pp():
    """MAX_ADJUSTMENT is 0.18 — the prior 0.10 cap kept outputs stuck in [0.48, 0.52]."""
    assert MAX_ADJUSTMENT == 0.18


def test_aggregator_includes_onchain_flow():
    """Verify the aggregator's default providers include OnchainFlowProvider."""
    from signals.aggregator import DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert "onchain_flow" in DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS["onchain_flow"] == 1.3


def test_aggregator_format_raw_evidence_composite():
    """Verify the aggregator formats multi-source onchain_flow evidence correctly."""
    from signals.aggregator import _format_raw_evidence
    from signals.base import SignalResult

    signal = SignalResult(
        source="onchain_flow",
        probability=0.54,
        confidence=0.5,
        reasoning="test",
        model_used="none",
        data_points=14,
        raw_data={
            "pressure_score": 0.35,
            "data_source": "composite",
            "asset": "bitcoin",
            "sources_available": 4,
            "source_agreement": 0.75,
            "source_pressures": {
                "stablecoin_flow": 0.4,
                "tvl_trend": 0.3,
                "fear_greed": 0.2,
                "global_market": 0.5,
            },
            "weekly_change_pct": 1.5,
            "monthly_change_pct": 3.2,
            "total_stablecoin_supply": 150_000_000_000,
            "stablecoins_tracked": 10,
            "fear_greed_value": 65,
            "fear_greed_label": "Greed",
            "tvl_weekly_change_pct": 2.1,
            "current_tvl": 95_000_000_000,
            "market_cap_change_24h_pct": 1.8,
            "btc_dominance": 52.3,
        },
    )
    formatted = _format_raw_evidence(signal)
    assert "Composite pressure: +0.35" in formatted
    assert "BITCOIN" in formatted
    assert "4 sources" in formatted
    assert "Fear & Greed Index: 65/100" in formatted
    assert "DeFi TVL" in formatted
    assert "$150.0B" in formatted
    assert "Global market cap" in formatted


def test_aggregator_format_raw_evidence_backward_compat():
    """Single-source data still formats correctly (backward compat)."""
    from signals.aggregator import _format_raw_evidence
    from signals.base import SignalResult

    signal = SignalResult(
        source="onchain_flow",
        probability=0.54,
        confidence=0.4,
        reasoning="test",
        model_used="none",
        data_points=10,
        raw_data={
            "pressure_score": 0.45,
            "data_source": "composite",
            "asset": "bitcoin",
            "sources_available": 1,
            "source_agreement": 0,
            "weekly_change_pct": 1.5,
            "monthly_change_pct": 3.2,
            "total_stablecoin_supply": 150_000_000_000,
            "stablecoins_tracked": 10,
        },
    )
    formatted = _format_raw_evidence(signal)
    assert "Composite pressure: +0.45" in formatted
    assert "BITCOIN" in formatted
    assert "$150.0B" in formatted
