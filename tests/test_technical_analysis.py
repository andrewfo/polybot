"""Unit tests for technical analysis signal provider."""

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals.base import SignalResult
from signals.technical_analysis import (
    TechnicalAnalysisProvider,
    compute_rsi,
    compute_macd,
    compute_bollinger_bands,
    compute_moving_averages,
    _summarize_indicators,
    clear_signal_cache,
)


# ---------------------------------------------------------------------------
# Test: RSI computation
# ---------------------------------------------------------------------------

class TestRSI:
    def test_all_gains(self):
        """All gains → RSI should be 100."""
        prices = [100 + i for i in range(20)]
        rsi = compute_rsi(prices)
        assert rsi == 100.0

    def test_all_losses(self):
        """All losses → RSI should be 0."""
        prices = [100 - i for i in range(20)]
        rsi = compute_rsi(prices)
        assert rsi == 0.0

    def test_mixed_prices(self):
        """Mixed → RSI between 0 and 100."""
        prices = [100, 102, 101, 103, 100, 104, 99, 105, 98, 106,
                  97, 107, 96, 108, 95, 109]
        rsi = compute_rsi(prices)
        assert rsi is not None
        assert 0 < rsi < 100

    def test_insufficient_data(self):
        prices = [100, 101, 102]
        assert compute_rsi(prices) is None

    def test_period_14(self):
        """Need at least 15 prices for period=14."""
        prices = list(range(15))
        rsi = compute_rsi(prices, period=14)
        assert rsi is not None


# ---------------------------------------------------------------------------
# Test: MACD computation
# ---------------------------------------------------------------------------

class TestMACD:
    def test_basic(self):
        # Need at least 35 prices (26 slow + 9 signal)
        prices = [100 + math.sin(i * 0.3) * 10 for i in range(50)]
        macd = compute_macd(prices)
        assert macd is not None
        assert "macd_line" in macd
        assert "signal_line" in macd
        assert "histogram" in macd
        assert macd["crossover"] in ("bullish", "bearish", "none")

    def test_insufficient_data(self):
        prices = list(range(20))
        assert compute_macd(prices) is None

    def test_uptrend(self):
        """Strong uptrend → MACD should be positive."""
        prices = [100 + i * 2 for i in range(50)]
        macd = compute_macd(prices)
        assert macd is not None
        assert macd["macd_line"] > 0


# ---------------------------------------------------------------------------
# Test: Bollinger Bands
# ---------------------------------------------------------------------------

class TestBollingerBands:
    def test_basic(self):
        prices = [100 + math.sin(i * 0.5) * 5 for i in range(30)]
        bb = compute_bollinger_bands(prices)
        assert bb is not None
        assert bb["upper"] > bb["middle"] > bb["lower"]
        assert 0.0 <= bb["pct_b"] <= 1.5  # Can exceed 1 if price > upper band

    def test_constant_prices(self):
        """Constant prices → bands collapse to middle."""
        prices = [100.0] * 25
        bb = compute_bollinger_bands(prices)
        assert bb is not None
        assert bb["upper"] == bb["middle"] == bb["lower"] == 100.0
        # %B = 0.5 when band_width is 0 (edge case)

    def test_insufficient_data(self):
        assert compute_bollinger_bands([100, 101]) is None


# ---------------------------------------------------------------------------
# Test: Moving Averages
# ---------------------------------------------------------------------------

class TestMovingAverages:
    def test_uptrend(self):
        """In uptrend, SMA20 > SMA50."""
        prices = [100 + i for i in range(60)]
        ma = compute_moving_averages(prices)
        assert ma is not None
        assert ma["trend"] == "bullish"
        assert ma["sma_20"] > ma["sma_50"]

    def test_downtrend(self):
        prices = [200 - i for i in range(60)]
        ma = compute_moving_averages(prices)
        assert ma is not None
        assert ma["trend"] == "bearish"
        assert ma["sma_20"] < ma["sma_50"]

    def test_insufficient_data(self):
        assert compute_moving_averages(list(range(40))) is None


# ---------------------------------------------------------------------------
# Test: Indicator summary
# ---------------------------------------------------------------------------

class TestSummarizeIndicators:
    def test_all_indicators(self):
        summary = _summarize_indicators(
            rsi=72.5,
            macd={"macd_line": 1.5, "signal_line": 0.8, "histogram": 0.7, "crossover": "bullish"},
            bollinger={"upper": 110, "middle": 100, "lower": 90, "band_width": 20, "pct_b": 0.65, "current_price": 103},
            ma={"sma_20": 105, "sma_50": 100, "trend": "bullish", "crossover": "none", "spread_pct": 5.0},
        )
        assert "OVERBOUGHT" in summary
        assert "MACD" in summary
        assert "Bollinger" in summary
        assert "Moving Averages" in summary

    def test_no_indicators(self):
        assert _summarize_indicators(None, None, None, None) == "No indicators computed"

    def test_oversold(self):
        summary = _summarize_indicators(rsi=25.0, macd=None, bollinger=None, ma=None)
        assert "OVERSOLD" in summary


# ---------------------------------------------------------------------------
# Test: Provider category gating
# ---------------------------------------------------------------------------

class TestTechnicalAnalysisProvider:
    @pytest.mark.asyncio
    async def test_skip_non_crypto(self):
        llm = MagicMock()
        provider = TechnicalAnalysisProvider(llm=llm)
        result = await provider.get_signal(
            market_question="Will inflation rise?",
            market_category="economics",
            market_end_date="2026-12-31",
        )
        assert result.probability is None
        assert result.confidence == 0.0
        assert result.source == "technical_analysis"

    @pytest.mark.asyncio
    async def test_crypto_with_data(self):
        """Full pipeline with mocked CoinGecko data."""
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={
            "probability": 0.62,
            "confidence": 0.5,
            "reasoning": "TA test reasoning",
        })

        # Generate 90 days of sinusoidal price data
        chart_data = [[i * 86400000, 100 + math.sin(i * 0.1) * 15] for i in range(90)]

        with patch("signals.technical_analysis._fetch_chart", new_callable=AsyncMock, return_value=chart_data), \
             patch("signals.technical_analysis.db"):
            provider = TechnicalAnalysisProvider(llm=llm)
            clear_signal_cache()
            result = await provider.get_signal(
                market_question="Will ETH hit $5000?",
                market_category="crypto",
                market_end_date="2026-12-31",
                resolution_keywords={"coin_id": "ethereum"},
            )

        assert result.source == "technical_analysis"
        assert result.probability == 0.62
        assert result.confidence == 0.5
        assert result.model_used == "cheap"
        assert result.data_points == 90

    @pytest.mark.asyncio
    async def test_insufficient_chart_data(self):
        llm = MagicMock()
        llm.call_json = AsyncMock(return_value={"coin_id": "bitcoin"})

        with patch("signals.technical_analysis._fetch_chart", new_callable=AsyncMock, return_value=[[0, 100]]), \
             patch("signals.technical_analysis.db"):
            provider = TechnicalAnalysisProvider(llm=llm)
            clear_signal_cache()
            result = await provider.get_signal(
                market_question="Will BTC hit $200k?",
                market_category="crypto",
                market_end_date="2026-12-31",
                resolution_keywords={"coin_id": "bitcoin"},
            )
        assert result.probability is None
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        llm = MagicMock()
        provider = TechnicalAnalysisProvider(llm=llm)
        clear_signal_cache()

        cached_result = SignalResult(
            source="technical_analysis", probability=0.55, confidence=0.5,
            reasoning="cached TA", model_used="cheap", data_points=90,
        )
        from signals.technical_analysis import _signal_cache
        import time
        _signal_cache["cached TA question?"] = (cached_result, time.monotonic())

        result = await provider.get_signal(
            market_question="cached TA question?",
            market_category="crypto",
            market_end_date="2026-12-31",
        )
        assert result.probability == 0.55
