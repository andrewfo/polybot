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


# --- Market-aware baseline (fix for the flat ~0.48-for-every-market output) ---


class TestBaselineProbability:
    def test_far_above_target_terminal_is_unlikely(self):
        """BTC at $77k, 'above $120k at expiry' in 7 days → near zero."""
        from signals.onchain_flow import _baseline_probability
        p = _baseline_probability(
            current_price=77_000, target_price=120_000, daily_vol=0.03,
            days_remaining=7, resolution_type="terminal", target_direction="above",
        )
        assert p is not None and p < 0.05

    def test_already_above_target_terminal_is_likely(self):
        """BTC at $77k, 'above $60k at expiry' in 7 days → near certain."""
        from signals.onchain_flow import _baseline_probability
        p = _baseline_probability(
            current_price=77_000, target_price=60_000, daily_vol=0.03,
            days_remaining=7, resolution_type="terminal", target_direction="above",
        )
        assert p is not None and p > 0.95

    def test_below_direction_inverts_terminal(self):
        from signals.onchain_flow import _baseline_probability
        p_above = _baseline_probability(77_000, 80_000, 0.03, 7, "terminal", "above")
        p_below = _baseline_probability(77_000, 80_000, 0.03, 7, "terminal", "below")
        assert abs((p_above + p_below) - 1.0) < 1e-9

    def test_barrier_doubles_terminal_tail(self):
        """Touch probability ≈ 2x the terminal tail probability."""
        from signals.onchain_flow import _baseline_probability
        p_term = _baseline_probability(77_000, 85_000, 0.03, 10, "terminal", "above")
        p_barrier = _baseline_probability(77_000, 85_000, 0.03, 10, "barrier", "above")
        assert p_barrier is not None and p_term is not None
        assert abs(p_barrier - min(0.98, 2 * p_term)) < 1e-9

    def test_barrier_below_spot_uses_lower_tail(self):
        """'Dip to $70k' from $77k → touch prob of the downside level."""
        from signals.onchain_flow import _baseline_probability
        p = _baseline_probability(77_000, 70_000, 0.03, 10, "barrier", "below")
        assert p is not None and 0.02 <= p < 0.5

    def test_invalid_inputs_return_none(self):
        from signals.onchain_flow import _baseline_probability
        assert _baseline_probability(0, 80_000, 0.03, 7, "terminal", "above") is None
        assert _baseline_probability(77_000, 0, 0.03, 7, "terminal", "above") is None
        assert _baseline_probability(77_000, 80_000, 0.0, 7, "terminal", "above") is None
        assert _baseline_probability(77_000, 80_000, 0.03, 0, "terminal", "above") is None

    def test_different_markets_get_different_probabilities(self):
        """The defect being fixed: distinct targets must produce distinct
        baselines (all 81 resolved predictions previously sat at ~0.48)."""
        from signals.onchain_flow import _baseline_probability
        p_near = _baseline_probability(77_000, 78_000, 0.03, 7, "barrier", "above")
        p_far = _baseline_probability(77_000, 95_000, 0.03, 7, "barrier", "above")
        assert p_near is not None and p_far is not None
        assert p_near - p_far > 0.3


@pytest.mark.asyncio
async def test_get_signal_uses_market_baseline():
    """With coin price/vol data and a target, probability reflects the market
    (not a flat 0.5 anchor) and the flow tilt is applied on top."""
    from unittest.mock import patch
    clear_flow_cache()

    flow_metrics = {
        "data_source": "composite",
        "asset": "bitcoin",
        "pressure_score": 0.5,
        "sources_available": 5,
        "source_agreement": 1.0,
        "stablecoins_tracked": 5,
        "coin_current_price": 77_000.0,
        "coin_daily_vol": 0.03,
    }

    provider = OnchainFlowProvider()
    with patch("signals.onchain_flow._fetch_flow_data", return_value=(0.5, flow_metrics)):
        # Far target: baseline near 0 — even +0.5 pressure keeps P low
        far = await provider.get_signal(
            "Will Bitcoin reach $150,000 in June?", "crypto",
            "2026-06-18T00:00:00Z",
            resolution_keywords={
                "coin_id": "bitcoin", "target_value": 150_000,
                "target_direction": "above", "resolution_type": "barrier",
            },
        )
        # Near target: baseline high
        near = await provider.get_signal(
            "Will Bitcoin reach $78,000 in June?", "crypto",
            "2026-06-18T00:00:00Z",
            resolution_keywords={
                "coin_id": "bitcoin", "target_value": 78_000,
                "target_direction": "above", "resolution_type": "barrier",
            },
        )

    assert far.probability is not None and near.probability is not None
    assert far.probability < 0.30
    assert near.probability > 0.70
    assert "baseline=" in far.reasoning


@pytest.mark.asyncio
async def test_get_signal_falls_back_to_flat_anchor_without_target():
    """No target data → legacy 0.5-anchored behavior."""
    from unittest.mock import patch
    clear_flow_cache()

    flow_metrics = {
        "data_source": "composite",
        "asset": "bitcoin",
        "pressure_score": 0.5,
        "sources_available": 3,
        "source_agreement": 1.0,
        "stablecoins_tracked": 5,
    }

    provider = OnchainFlowProvider()
    with patch("signals.onchain_flow._fetch_flow_data", return_value=(0.5, flow_metrics)):
        result = await provider.get_signal(
            "Will Bitcoin do something?", "crypto", "2026-06-18T00:00:00Z",
            resolution_keywords={"coin_id": "bitcoin"},
        )

    assert result.probability is not None
    # 0.5 + tanh-shaped adjustment for pressure 0.5 (positive, < cap)
    assert 0.5 < result.probability <= 0.5 + MAX_ADJUSTMENT


def test_aggregator_includes_onchain_flow():
    """Verify the aggregator knows onchain_flow (benched at weight 0 until it
    earns its way back via calibration — see docs/PROFITABILITY_FIX_PLAN.md)."""
    from signals.aggregator import DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert "onchain_flow" in DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS
    assert DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS["onchain_flow"] == 0.0


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
