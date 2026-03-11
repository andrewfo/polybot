"""Unit tests for historical base rate signal provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.historical_base_rate import (
    HistoricalBaseRateProvider,
    compute_crypto_base_rates,
    compute_econ_base_rates,
    clear_signal_cache,
)


# ---------------------------------------------------------------------------
# Test: Crypto base rates
# ---------------------------------------------------------------------------

class TestCryptoBaseRates:
    def test_basic_above(self):
        # Price rises steadily: 100, 101, 102, ..., 159
        prices = [[i * 86400000, 100 + i] for i in range(60)]
        stats = compute_crypto_base_rates(prices, target_price=110.0, direction="above", window_days=10)
        assert stats["base_rate"] is not None
        assert 0 <= stats["base_rate"] <= 1.0
        assert stats["sample_size"] > 0
        assert stats["hits"] >= 0

    def test_basic_below(self):
        prices = [[i * 86400000, 200 - i] for i in range(60)]
        stats = compute_crypto_base_rates(prices, target_price=180.0, direction="below", window_days=20)
        assert stats["base_rate"] is not None
        assert stats["base_rate"] > 0  # Price falls below 180 in 20 days

    def test_insufficient_data(self):
        prices = [[0, 100], [86400000, 101]]
        stats = compute_crypto_base_rates(prices, target_price=110.0, direction="above", window_days=30)
        assert stats["base_rate"] is None
        assert stats["sample_size"] == 0

    def test_impossible_target(self):
        """Flat prices, target far above → base rate near 0."""
        prices = [[i * 86400000, 100.0] for i in range(100)]
        stats = compute_crypto_base_rates(prices, target_price=200.0, direction="above", window_days=10)
        assert stats["base_rate"] == 0.0

    def test_statistics_present(self):
        prices = [[i * 86400000, 100 + i * 0.5] for i in range(60)]
        stats = compute_crypto_base_rates(prices, target_price=110.0, direction="above", window_days=10)
        assert "mean_move_pct" in stats
        assert "std_move_pct" in stats
        assert "max_move_up_pct" in stats
        assert "max_move_down_pct" in stats
        assert "target_move_pct" in stats


# ---------------------------------------------------------------------------
# Test: Economics base rates
# ---------------------------------------------------------------------------

class TestEconBaseRates:
    def test_basic(self):
        # Observations newest-first: 5.5, 5.3, 5.0, 4.8, 4.5, 4.2, 3.9
        obs = [{"value": str(5.5 - i * 0.3)} for i in range(10)]
        stats = compute_econ_base_rates(obs, target_value=5.0, direction="above")
        assert stats["base_rate"] is not None
        assert stats["sample_size"] == 10

    def test_level_rate_calculation(self):
        """3 out of 5 values >= 5.0."""
        obs = [
            {"value": "5.5"},
            {"value": "5.2"},
            {"value": "4.8"},
            {"value": "5.0"},
            {"value": "4.5"},
        ]
        stats = compute_econ_base_rates(obs, target_value=5.0, direction="above")
        assert stats["level_hits"] == 3  # 5.5, 5.2, 5.0
        assert stats["base_rate"] == 3 / 5

    def test_below_direction(self):
        obs = [{"value": str(5.0 - i * 0.2)} for i in range(10)]
        stats = compute_econ_base_rates(obs, target_value=4.5, direction="below")
        assert stats["base_rate"] is not None
        assert stats["level_hits"] > 0

    def test_transition_rate(self):
        """Test that transition rate is computed from similar starting levels."""
        # current_value = 5.0 (first observation)
        # Tolerance = 0.5 (10% of 5.0)
        obs = [
            {"value": "5.0"},
            {"value": "5.1"},
            {"value": "4.9"},
            {"value": "5.0"},
            {"value": "5.2"},
            {"value": "4.8"},
        ]
        stats = compute_econ_base_rates(obs, target_value=5.1, direction="above")
        assert stats["transition_rate"] is not None
        assert stats["transition_total"] > 0

    def test_insufficient_data(self):
        obs = [{"value": "5.0"}]
        stats = compute_econ_base_rates(obs, target_value=5.0, direction="above")
        assert stats["base_rate"] is None

    def test_statistics(self):
        obs = [{"value": str(5.0 + i * 0.1)} for i in range(20)]
        stats = compute_econ_base_rates(obs, target_value=5.5, direction="above")
        assert "mean_value" in stats
        assert "std_value" in stats
        assert "min_value" in stats
        assert "max_value" in stats


# ---------------------------------------------------------------------------
# Test: Provider
# ---------------------------------------------------------------------------

class TestHistoricalBaseRateProvider:
    @pytest.mark.asyncio
    async def test_skip_other_category(self):
        llm = MagicMock()
        provider = HistoricalBaseRateProvider(llm=llm)
        result = await provider.get_signal(
            market_question="test?",
            market_category="other",
            market_end_date="2026-12-31",
        )
        assert result.probability is None
        assert result.confidence == 0.0
        assert result.source == "historical_base_rate"

    @pytest.mark.asyncio
    async def test_crypto_full_pipeline(self):
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={
            "probability": 0.35,
            "confidence": 0.6,
            "reasoning": "Base rate test",
        })

        chart_data = [[i * 86400000, 100 + i * 0.3] for i in range(200)]

        with patch("signals.historical_base_rate._fetch_coingecko_chart", new_callable=AsyncMock, return_value=chart_data), \
             patch("signals.historical_base_rate.db"):
            provider = HistoricalBaseRateProvider(llm=llm)
            clear_signal_cache()
            result = await provider.get_signal(
                market_question="Will BTC hit $200k?",
                market_category="crypto",
                market_end_date="2026-12-31",
                resolution_keywords={
                    "coin_id": "bitcoin",
                    "target_value": 200000,
                    "target_direction": "above",
                },
            )

        assert result.source == "historical_base_rate"
        assert result.probability == 0.35
        assert result.confidence == 0.6

    @pytest.mark.asyncio
    async def test_econ_full_pipeline(self):
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={
            "probability": 0.55,
            "confidence": 0.7,
            "reasoning": "Econ base rate test",
        })

        fred_obs = [{"value": str(5.0 + i * 0.05)} for i in range(30)]

        with patch("signals.historical_base_rate._fetch_fred_series", new_callable=AsyncMock, return_value=fred_obs), \
             patch("signals.historical_base_rate.db"):
            provider = HistoricalBaseRateProvider(llm=llm)
            clear_signal_cache()
            result = await provider.get_signal(
                market_question="Will Fed funds rate hit 6%?",
                market_category="economics",
                market_end_date="2026-12-31",
                resolution_keywords={
                    "indicator_type": "rate",
                    "target_value": 6.0,
                    "target_direction": "above",
                },
            )

        assert result.source == "historical_base_rate"
        assert result.probability == 0.55

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        llm = MagicMock()
        provider = HistoricalBaseRateProvider(llm=llm)
        clear_signal_cache()

        cached_result = SignalResult(
            source="historical_base_rate", probability=0.40, confidence=0.5,
            reasoning="cached", model_used="cheap", data_points=100,
        )
        from signals.historical_base_rate import _signal_cache
        import time
        _signal_cache["cached br question?"] = (cached_result, time.monotonic())

        result = await provider.get_signal(
            market_question="cached br question?",
            market_category="crypto",
            market_end_date="2026-12-31",
        )
        assert result.probability == 0.40
