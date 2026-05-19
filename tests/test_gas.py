"""Tests for strategy/gas.py — gas-cost analysis."""

from unittest.mock import patch

import pytest

import strategy.gas as gas_mod
from strategy.gas import (
    GasAnalysis,
    analyze_gas_cost,
    estimate_round_trip_gas_cost_usd,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    gas_mod._cache.clear()
    yield
    gas_mod._cache.clear()


# ---------------------------------------------------------------------------
# estimate_round_trip_gas_cost_usd
# ---------------------------------------------------------------------------

class TestEstimateGasCost:
    @pytest.mark.asyncio
    async def test_uses_fallbacks_when_fetches_fail(self) -> None:
        with patch.object(gas_mod, "fetch_polygon_gas_price_gwei", return_value=50.0), \
             patch.object(gas_mod, "fetch_matic_usd_price", return_value=0.50):
            cost, gwei, matic = await estimate_round_trip_gas_cost_usd()
        # 500_000 * 50 gwei * 1e-9 = 0.025 MATIC × $0.50 = $0.0125
        assert gwei == 50.0
        assert matic == 0.50
        assert abs(cost - 0.0125) < 1e-6

    @pytest.mark.asyncio
    async def test_high_gas_high_matic(self) -> None:
        with patch.object(gas_mod, "fetch_polygon_gas_price_gwei", return_value=200.0), \
             patch.object(gas_mod, "fetch_matic_usd_price", return_value=2.00):
            cost, _, _ = await estimate_round_trip_gas_cost_usd()
        # 500_000 * 200 gwei * 1e-9 = 0.1 MATIC × $2 = $0.20
        assert abs(cost - 0.20) < 1e-6


# ---------------------------------------------------------------------------
# analyze_gas_cost gate
# ---------------------------------------------------------------------------

class TestAnalyzeGasCost:
    @pytest.mark.asyncio
    async def test_passes_when_ev_clears_ratio(self) -> None:
        with patch.object(gas_mod, "fetch_polygon_gas_price_gwei", return_value=50.0), \
             patch.object(gas_mod, "fetch_matic_usd_price", return_value=0.50):
            # gas_cost ~ $0.0125. With MIN_EV_GAS_RATIO=3.0, EV=$1 → ratio=80 → pass.
            result = await analyze_gas_cost(expected_value_usd=1.0)
        assert isinstance(result, GasAnalysis)
        assert result.passes_gate is True
        assert result.skip_reason == ""
        assert result.ev_to_gas_ratio > 3.0

    @pytest.mark.asyncio
    async def test_blocks_when_ev_too_small(self) -> None:
        # Force gas cost up so a tiny EV gets blocked
        with patch.object(gas_mod, "fetch_polygon_gas_price_gwei", return_value=500.0), \
             patch.object(gas_mod, "fetch_matic_usd_price", return_value=2.00):
            # gas_cost = 500_000 * 500e-9 * 2 = $0.50; EV=$0.10 → ratio 0.2 → block.
            result = await analyze_gas_cost(expected_value_usd=0.10)
        assert result.passes_gate is False
        assert "gas cost" in result.skip_reason
        assert result.ev_to_gas_ratio < 1.0

    @pytest.mark.asyncio
    async def test_zero_gas_cost_treated_as_pass(self) -> None:
        with patch.object(gas_mod, "fetch_polygon_gas_price_gwei", return_value=0.0), \
             patch.object(gas_mod, "fetch_matic_usd_price", return_value=0.50):
            result = await analyze_gas_cost(expected_value_usd=0.01)
        assert result.passes_gate is True
        assert result.gas_cost_usd == 0.0
