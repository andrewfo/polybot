"""Tests for strategy/depth.py — order book depth analysis."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategy.depth import (
    DepthAnalysis,
    OrderBookLevel,
    analyze_depth,
    compute_slippage,
    fetch_order_book,
    find_max_fillable_at_slippage,
)


# ---------------------------------------------------------------------------
# compute_slippage tests
# ---------------------------------------------------------------------------

class TestComputeSlippage:
    def test_empty_book(self) -> None:
        avg, slip, max_fill = compute_slippage([], 100.0)
        assert avg == 0.0
        assert slip == 1.0
        assert max_fill == 0.0

    def test_single_level_full_fill(self) -> None:
        """Single ask level with enough depth — no slippage."""
        levels = [OrderBookLevel(price=0.50, size=1000.0)]
        avg, slip, max_fill = compute_slippage(levels, 100.0)
        assert abs(avg - 0.50) < 1e-9
        assert slip == 0.0
        assert abs(max_fill - 500.0) < 1e-9  # 0.50 * 1000

    def test_two_levels_partial_walk(self) -> None:
        """Bet consumes first level entirely and part of second."""
        levels = [
            OrderBookLevel(price=0.50, size=100.0),  # $50 at this level
            OrderBookLevel(price=0.55, size=200.0),   # $110 at this level
        ]
        # Bet $80: consume $50 from first level (100 shares), $30 from second (54.5 shares)
        avg, slip, max_fill = compute_slippage(levels, 80.0)

        expected_shares = 100.0 + (30.0 / 0.55)
        expected_avg = 80.0 / expected_shares
        assert abs(avg - expected_avg) < 1e-6
        assert slip > 0  # Must have some slippage
        assert abs(max_fill - 160.0) < 1e-9  # 50 + 110

    def test_bet_exceeds_book(self) -> None:
        """Bet larger than entire book — consumes everything."""
        levels = [
            OrderBookLevel(price=0.50, size=100.0),  # $50
            OrderBookLevel(price=0.60, size=50.0),    # $30
        ]
        avg, slip, max_fill = compute_slippage(levels, 200.0)
        # Only consumed $80 worth
        total_cost = 50.0 + 30.0
        total_shares = 100.0 + 50.0
        expected_avg = total_cost / total_shares
        assert abs(avg - expected_avg) < 1e-6
        assert abs(max_fill - 80.0) < 1e-9

    def test_zero_slippage_at_single_level(self) -> None:
        """All shares at best price → slippage = 0."""
        levels = [OrderBookLevel(price=0.40, size=5000.0)]
        avg, slip, _ = compute_slippage(levels, 50.0)
        assert abs(slip) < 1e-9

    def test_increasing_slippage_with_size(self) -> None:
        """Larger bets should produce more slippage."""
        levels = [
            OrderBookLevel(price=0.50, size=100.0),
            OrderBookLevel(price=0.55, size=100.0),
            OrderBookLevel(price=0.60, size=100.0),
        ]
        _, slip_small, _ = compute_slippage(levels, 10.0)
        _, slip_large, _ = compute_slippage(levels, 100.0)
        assert slip_large >= slip_small


# ---------------------------------------------------------------------------
# find_max_fillable_at_slippage tests
# ---------------------------------------------------------------------------

class TestFindMaxFillable:
    def test_empty_book(self) -> None:
        assert find_max_fillable_at_slippage([], 0.03) == 0.0

    def test_single_level_no_slippage(self) -> None:
        """Single price level → no slippage → max is full book."""
        levels = [OrderBookLevel(price=0.50, size=1000.0)]
        result = find_max_fillable_at_slippage(levels, 0.03)
        assert abs(result - 500.0) < 1.0  # Full book = $500

    def test_tight_slippage_limit(self) -> None:
        """Very tight slippage limit → only first level available."""
        levels = [
            OrderBookLevel(price=0.50, size=100.0),  # $50
            OrderBookLevel(price=0.60, size=100.0),   # $60, 20% higher
        ]
        result = find_max_fillable_at_slippage(levels, 0.01)
        # With 1% max slippage, should be close to first level only ($50)
        assert result <= 55.0
        assert result >= 45.0

    def test_generous_slippage_uses_full_book(self) -> None:
        """Very generous slippage → can use full book."""
        levels = [
            OrderBookLevel(price=0.50, size=100.0),
            OrderBookLevel(price=0.51, size=100.0),
        ]
        result = find_max_fillable_at_slippage(levels, 0.50)
        max_total = 50.0 + 51.0
        assert abs(result - max_total) < 1.0


# ---------------------------------------------------------------------------
# fetch_order_book tests (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchOrderBook:
    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "asks": [
                {"price": "0.55", "size": "100"},
                {"price": "0.50", "size": "200"},
                {"price": "0.60", "size": "50"},
            ],
            "bids": [],
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("strategy.depth.aiohttp.ClientSession", return_value=mock_session):
            levels = await fetch_order_book("token_123")

        assert len(levels) == 3
        # Should be sorted by price ascending
        assert levels[0].price == 0.50
        assert levels[1].price == 0.55
        assert levels[2].price == 0.60

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("strategy.depth.aiohttp.ClientSession", return_value=mock_session):
            levels = await fetch_order_book("token_123")

        assert levels == []

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        with patch("strategy.depth.aiohttp.ClientSession", side_effect=Exception("timeout")):
            levels = await fetch_order_book("token_123")
        assert levels == []


# ---------------------------------------------------------------------------
# analyze_depth integration tests (mocked fetch)
# ---------------------------------------------------------------------------

class TestAnalyzeDepth:
    @pytest.mark.asyncio
    async def test_sufficient_depth(self) -> None:
        levels = [
            OrderBookLevel(price=0.50, size=500.0),
            OrderBookLevel(price=0.51, size=300.0),
        ]
        with patch("strategy.depth.fetch_order_book", new_callable=AsyncMock, return_value=levels):
            result = await analyze_depth("tok1", "BUY_YES", 50.0)

        assert isinstance(result, DepthAnalysis)
        assert result.skip_reason == ""
        assert result.adjusted_bet_usd == 50.0
        assert result.best_price == 0.50
        assert result.slippage == 0.0  # All fills at best price
        assert result.total_depth_usd > 0

    @pytest.mark.asyncio
    async def test_empty_book_skips(self) -> None:
        with patch("strategy.depth.fetch_order_book", new_callable=AsyncMock, return_value=[]):
            result = await analyze_depth("tok1", "BUY_YES", 50.0)

        assert result.skip_reason == "no order book data"
        assert result.adjusted_bet_usd == 0.0

    @pytest.mark.asyncio
    async def test_insufficient_depth_skips(self) -> None:
        levels = [OrderBookLevel(price=0.50, size=10.0)]  # Only $5 depth
        with patch("strategy.depth.fetch_order_book", new_callable=AsyncMock, return_value=levels):
            result = await analyze_depth("tok1", "BUY_YES", 50.0)

        assert "insufficient depth" in result.skip_reason
        assert result.adjusted_bet_usd == 0.0

    @pytest.mark.asyncio
    async def test_high_slippage_reduces_bet(self) -> None:
        levels = [
            OrderBookLevel(price=0.50, size=200.0),   # $100 at best price
            OrderBookLevel(price=0.55, size=400.0),   # $220 at +10%
            OrderBookLevel(price=0.60, size=200.0),   # $120 at +20%
        ]
        with patch("strategy.depth.fetch_order_book", new_callable=AsyncMock, return_value=levels):
            result = await analyze_depth("tok1", "BUY_YES", 400.0)

        # Slippage for full $400 would be high — bet should be reduced
        assert result.adjusted_bet_usd < 400.0
        assert result.adjusted_bet_usd > 0


# ---------------------------------------------------------------------------
# TradeDecision depth fields
# ---------------------------------------------------------------------------

class TestTradeDecisionDepthFields:
    def test_default_depth_fields(self) -> None:
        from strategy.kelly import TradeDecision
        d = TradeDecision(
            market_id="m1", token_id="t1", market_question="test?",
            side="BUY_YES", estimated_prob=0.6, effective_prob=0.58,
            market_price=0.50, edge=0.08, full_kelly_fraction=0.1,
            adjusted_fraction=0.025, bet_size_usd=25.0,
            expected_value=2.0, confidence=0.8,
            should_trade=True, skip_reason="",
        )
        assert d.depth_total_usd == 0.0
        assert d.depth_slippage == 0.0
        assert d.depth_adjusted is False
