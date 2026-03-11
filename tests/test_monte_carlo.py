"""Unit tests for Monte Carlo simulation signal provider."""

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.monte_carlo import (
    MonteCarloProvider,
    _run_gbm_simulation,
    _run_bootstrap_simulation,
    _compute_volatility,
    _extract_fred_changes,
    clear_signal_cache,
)


# ---------------------------------------------------------------------------
# Test: GBM simulation
# ---------------------------------------------------------------------------

class TestGBMSimulation:
    def test_basic_above(self):
        """GBM simulation should return probability between 0 and 1."""
        result = _run_gbm_simulation(
            current_price=100.0,
            target_price=110.0,
            annual_vol=0.80,
            days_remaining=30,
            direction="above",
            n_sims=1000,
        )
        assert 0.0 <= result["mc_probability"] <= 1.0
        assert result["n_simulations"] == 1000
        assert result["mean_final"] > 0
        assert result["pct_5"] < result["pct_95"]

    def test_basic_below(self):
        result = _run_gbm_simulation(
            current_price=100.0,
            target_price=90.0,
            annual_vol=0.80,
            days_remaining=30,
            direction="below",
            n_sims=1000,
        )
        assert 0.0 <= result["mc_probability"] <= 1.0

    def test_zero_days_above_hit(self):
        """Zero days remaining, already above target → probability 1."""
        result = _run_gbm_simulation(
            current_price=120.0, target_price=100.0,
            annual_vol=0.80, days_remaining=0, direction="above",
        )
        assert result["mc_probability"] == 1.0

    def test_zero_days_above_miss(self):
        """Zero days, below target → probability 0."""
        result = _run_gbm_simulation(
            current_price=80.0, target_price=100.0,
            annual_vol=0.80, days_remaining=0, direction="above",
        )
        assert result["mc_probability"] == 0.0

    def test_zero_vol(self):
        """Zero volatility → deterministic outcome."""
        result = _run_gbm_simulation(
            current_price=100.0, target_price=110.0,
            annual_vol=0.0, days_remaining=30, direction="above",
        )
        assert result["mc_probability"] == 0.0

    def test_far_above_target_high_probability(self):
        """Target well below current price → high probability."""
        result = _run_gbm_simulation(
            current_price=100.0, target_price=50.0,
            annual_vol=0.50, days_remaining=30, direction="above",
            n_sims=2000,
        )
        assert result["mc_probability"] > 0.8

    def test_percentiles_ordered(self):
        result = _run_gbm_simulation(
            current_price=100.0, target_price=100.0,
            annual_vol=0.80, days_remaining=60, direction="above",
            n_sims=2000,
        )
        assert result["pct_5"] <= result["pct_25"]
        assert result["pct_25"] <= result["median_final"]
        assert result["median_final"] <= result["pct_75"]
        assert result["pct_75"] <= result["pct_95"]


# ---------------------------------------------------------------------------
# Test: Bootstrap simulation
# ---------------------------------------------------------------------------

class TestBootstrapSimulation:
    def test_basic(self):
        changes = [0.1, -0.05, 0.2, -0.1, 0.15, 0.0, -0.02, 0.08]
        result = _run_bootstrap_simulation(
            current_value=5.0, target_value=5.2,
            historical_changes=changes, direction="above",
            n_sims=1000,
        )
        assert 0.0 <= result["mc_probability"] <= 1.0
        assert result["n_simulations"] == 1000

    def test_empty_changes(self):
        result = _run_bootstrap_simulation(
            current_value=5.0, target_value=5.2,
            historical_changes=[], direction="above",
        )
        assert result["mc_probability"] == 0.5
        assert result["n_simulations"] == 0

    def test_all_positive_changes(self):
        """All positive changes + target above → should have some hits."""
        changes = [0.5, 0.3, 0.4, 0.6, 0.2]
        result = _run_bootstrap_simulation(
            current_value=5.0, target_value=5.1,
            historical_changes=changes, direction="above",
            n_sims=1000,
        )
        assert result["mc_probability"] == 1.0  # All changes push above target


# ---------------------------------------------------------------------------
# Test: Volatility computation
# ---------------------------------------------------------------------------

class TestComputeVolatility:
    def test_constant_prices(self):
        prices = [[i * 86400000, 100.0] for i in range(30)]
        assert _compute_volatility(prices) == 0.0

    def test_real_prices(self):
        prices = [[i * 86400000, 100 + i * 0.5] for i in range(30)]
        vol = _compute_volatility(prices)
        assert vol > 0

    def test_insufficient_data(self):
        assert _compute_volatility([[0, 100.0]]) == 0.0
        assert _compute_volatility([]) == 0.0


# ---------------------------------------------------------------------------
# Test: FRED changes extraction
# ---------------------------------------------------------------------------

class TestExtractFredChanges:
    def test_basic(self):
        obs = [
            {"value": "5.5", "date": "2026-03-01"},
            {"value": "5.3", "date": "2026-02-01"},
            {"value": "5.0", "date": "2026-01-01"},
        ]
        changes = _extract_fred_changes(obs)
        # Reversed to chronological: 5.0, 5.3, 5.5
        # Changes: 0.3, 0.2
        assert len(changes) == 2
        assert abs(changes[0] - 0.3) < 1e-6
        assert abs(changes[1] - 0.2) < 1e-6

    def test_insufficient_data(self):
        assert _extract_fred_changes([{"value": "5.0"}]) == []
        assert _extract_fred_changes([]) == []


# ---------------------------------------------------------------------------
# Test: Provider category gating
# ---------------------------------------------------------------------------

class TestMonteCarloProvider:
    @pytest.mark.asyncio
    async def test_skip_non_supported_category(self):
        llm = MagicMock()
        provider = MonteCarloProvider(llm=llm)
        result = await provider.get_signal(
            market_question="test?",
            market_category="other",
            market_end_date="2026-12-31",
        )
        assert result.probability is None
        assert result.confidence == 0.0
        assert result.source == "monte_carlo"

    @pytest.mark.asyncio
    async def test_crypto_no_coin_id(self):
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={"coin_id": None})
        provider = MonteCarloProvider(llm=llm)
        with patch("signals.monte_carlo.db"):
            result = await provider.get_signal(
                market_question="Will dogecoin hit $1?",
                market_category="crypto",
                market_end_date="2026-12-31",
            )
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_economics_category(self):
        """Economics should attempt bootstrap MC."""
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={
            "probability": 0.45,
            "confidence": 0.6,
            "reasoning": "MC bootstrap test",
        })

        fred_obs = [{"value": str(5.0 + i * 0.1), "date": f"2026-0{min(i+1,9)}-01"} for i in range(10)]

        with patch("signals.monte_carlo._fetch_fred_series", new_callable=AsyncMock, return_value=fred_obs), \
             patch("signals.monte_carlo.db"):
            provider = MonteCarloProvider(llm=llm)
            clear_signal_cache()
            result = await provider.get_signal(
                market_question="Will Fed funds rate exceed 6%?",
                market_category="economics",
                market_end_date="2026-12-31",
                resolution_keywords={
                    "indicator_type": "rate",
                    "target_value": 6.0,
                    "target_direction": "above",
                },
            )
        assert result.source == "monte_carlo"
        assert result.probability == 0.45

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        llm = MagicMock()
        provider = MonteCarloProvider(llm=llm)
        clear_signal_cache()

        # Prime cache
        cached_result = SignalResult(
            source="monte_carlo", probability=0.55, confidence=0.7,
            reasoning="cached", model_used="cheap", data_points=100,
        )
        from signals.monte_carlo import _signal_cache
        import time
        _signal_cache["cached question?"] = (cached_result, time.monotonic())

        result = await provider.get_signal(
            market_question="cached question?",
            market_category="crypto",
            market_end_date="2026-12-31",
        )
        assert result.probability == 0.55
        assert result.reasoning == "cached"
