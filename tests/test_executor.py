"""Tests for strategy/executor.py — risk guardrails, PaperExecutor, TradeExecutor."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategy.executor import (
    AutoStopError,
    PaperExecutor,
    TradeExecutor,
    check_all_guardrails,
    check_daily_loss,
    check_drawdown,
    check_position_count,
    check_trade_rate,
    compute_limit_price,
)
from strategy.kelly import TradeDecision

# Note: ClobClientWrapper is not imported here to avoid triggering py_clob_client
# auth side effects. TradeExecutor tests use unspec'd AsyncMock instead.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_decision(**overrides) -> TradeDecision:
    """Create a TradeDecision with sensible defaults."""
    defaults = dict(
        market_id="cond-123",
        token_id="tok-yes",
        market_question="Will BTC reach 100k?",
        side="BUY_YES",
        estimated_prob=0.65,
        effective_prob=0.60,
        market_price=0.50,
        edge=0.10,
        full_kelly_fraction=0.10,
        adjusted_fraction=0.025,
        bet_size_usd=25.0,
        expected_value=2.50,
        confidence=0.80,
        should_trade=True,
        skip_reason="",
    )
    defaults.update(overrides)
    return TradeDecision(**defaults)


def _make_market_data(**overrides) -> dict:
    defaults = dict(
        conditionId="cond-123",
        clobTokenIds=["tok-yes", "tok-no"],
        bestAsk="0.55",
        bestBid="0.50",
        outcomePrices='["0.52","0.48"]',
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Risk guardrail tests
# ---------------------------------------------------------------------------

class TestCheckPositionCount:
    @patch("strategy.executor.db")
    def test_under_limit(self, mock_db):
        mock_db.get_open_positions.return_value = [{"token_id": "a"}]
        ok, reason = check_position_count()
        assert ok is True
        assert reason == ""

    @patch("strategy.executor.db")
    def test_at_limit(self, mock_db):
        mock_db.get_open_positions.return_value = [{"token_id": str(i)} for i in range(5)]
        ok, reason = check_position_count()
        assert ok is False
        assert "position limit" in reason


class TestCheckTradeRate:
    @patch("strategy.executor.db")
    def test_under_limit(self, mock_db):
        mock_db.get_recent_trade_count.return_value = 1
        ok, reason = check_trade_rate()
        assert ok is True

    @patch("strategy.executor.db")
    def test_at_limit(self, mock_db):
        mock_db.get_recent_trade_count.return_value = 3
        ok, reason = check_trade_rate()
        assert ok is False
        assert "trade rate" in reason


class TestCheckDrawdown:
    @patch("strategy.executor.db")
    def test_within_limit(self, mock_db):
        mock_db.get_total_pnl.return_value = -100.0
        ok, reason = check_drawdown(1000.0)
        assert ok is True

    @patch("strategy.executor.db")
    def test_exceeds_limit(self, mock_db):
        mock_db.get_total_pnl.return_value = -350.0
        with pytest.raises(AutoStopError, match="max drawdown"):
            check_drawdown(1000.0)


class TestCheckDailyLoss:
    @patch("strategy.executor.db")
    def test_within_limit(self, mock_db):
        mock_db.get_daily_pnl.return_value = -50.0
        ok, reason = check_daily_loss(1000.0)
        assert ok is True

    @patch("strategy.executor.db")
    def test_exceeds_limit(self, mock_db):
        mock_db.get_daily_pnl.return_value = -200.0
        with pytest.raises(AutoStopError, match="max daily loss"):
            check_daily_loss(1000.0)


# ---------------------------------------------------------------------------
# compute_limit_price tests
# ---------------------------------------------------------------------------

class TestComputeLimitPrice:
    def test_buy_yes(self):
        decision = _make_decision(side="BUY_YES")
        market = _make_market_data(bestAsk="0.55")
        price, token_id = compute_limit_price(decision, market)
        assert token_id == "tok-yes"
        assert abs(price - (0.55 - 0.02)) < 0.001

    def test_buy_no(self):
        decision = _make_decision(side="BUY_NO")
        market = _make_market_data(bestBid="0.50")
        price, token_id = compute_limit_price(decision, market)
        assert token_id == "tok-no"
        assert abs(price - (0.50 - 0.02)) < 0.001

    def test_clamp_low(self):
        decision = _make_decision(side="BUY_YES")
        market = _make_market_data(bestAsk="0.01")
        price, _ = compute_limit_price(decision, market)
        assert price >= 0.01

    def test_clamp_high(self):
        decision = _make_decision(side="BUY_YES")
        market = _make_market_data(bestAsk="1.05")
        price, _ = compute_limit_price(decision, market)
        assert price <= 0.99


# ---------------------------------------------------------------------------
# PaperExecutor tests
# ---------------------------------------------------------------------------

class TestPaperExecutor:
    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_execute_trade_success(self, mock_db):
        mock_db.get_open_positions.return_value = []
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0

        executor = PaperExecutor()
        decision = _make_decision()
        market = _make_market_data()

        trade_id = await executor.execute_trade(decision, market, 1000.0)
        assert trade_id is not None
        mock_db.record_trade.assert_called_once()
        mock_db.upsert_position.assert_called_once()

        # Verify paper=True in record_trade call
        call_kwargs = mock_db.record_trade.call_args
        assert call_kwargs[1].get("paper") is True or call_kwargs.kwargs.get("paper") is True

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_execute_trade_blocked_by_guardrail(self, mock_db):
        mock_db.get_open_positions.return_value = [{"token_id": str(i)} for i in range(5)]
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0

        executor = PaperExecutor()
        decision = _make_decision()
        market = _make_market_data()

        trade_id = await executor.execute_trade(decision, market, 1000.0)
        assert trade_id is None
        mock_db.record_trade.assert_not_called()

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_price", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_updates_pnl(self, mock_db, mock_fetch):
        mock_fetch.return_value = 0.55  # 10% gain — below 12% take-profit threshold
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "Will BTC reach 100k?",
                "side": "BUY",
                "avg_entry": 0.50,
                "size": 100.0,
                "current_price": 0.50,
                "paper": 1,
            }
        ]

        executor = PaperExecutor()
        await executor.manage_positions()

        mock_db.upsert_position.assert_called_once()
        call_kwargs = mock_db.upsert_position.call_args
        assert call_kwargs.kwargs.get("current_price") == 0.55 or call_kwargs[1].get("current_price") == 0.55

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_auto_stop_raises(self, mock_db):
        mock_db.get_total_pnl.return_value = -400.0
        mock_db.get_daily_pnl.return_value = 0.0

        executor = PaperExecutor()
        decision = _make_decision()
        market = _make_market_data()

        with pytest.raises(AutoStopError):
            await executor.execute_trade(decision, market, 1000.0)

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_monitor_orders_fills_pending(self, mock_db):
        mock_db.get_open_trades.return_value = [
            {"id": "t1", "paper": 1, "price": 0.55}
        ]

        executor = PaperExecutor()
        await executor.monitor_orders()
        mock_db.update_trade_status.assert_called_once_with("t1", "FILLED", fill_price=0.55)


# ---------------------------------------------------------------------------
# TradeExecutor tests
# ---------------------------------------------------------------------------

class TestTradeExecutor:
    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_execute_trade_calls_clob(self, mock_db):
        mock_db.get_open_positions.return_value = []
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0

        mock_client = AsyncMock()
        mock_client.place_limit_order.return_value = "order-abc"

        executor = TradeExecutor(mock_client)
        decision = _make_decision()
        market = _make_market_data()

        trade_id = await executor.execute_trade(decision, market, 1000.0)
        assert trade_id is not None
        mock_client.place_limit_order.assert_called_once()

        # Verify correct token_id and side
        call_kwargs = mock_client.place_limit_order.call_args
        assert call_kwargs.kwargs.get("side") == "BUY" or call_kwargs[1].get("side") == "BUY"

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_buy_yes_price(self, mock_db):
        mock_db.get_open_positions.return_value = []
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0

        mock_client = AsyncMock()
        mock_client.place_limit_order.return_value = "order-1"

        executor = TradeExecutor(mock_client)
        decision = _make_decision(side="BUY_YES")
        market = _make_market_data(bestAsk="0.60")

        await executor.execute_trade(decision, market, 1000.0)
        call_kwargs = mock_client.place_limit_order.call_args
        price = call_kwargs.kwargs.get("price") or call_kwargs[1].get("price")
        assert abs(price - 0.58) < 0.001  # 0.60 - 0.02

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_buy_no_price(self, mock_db):
        mock_db.get_open_positions.return_value = []
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0

        mock_client = AsyncMock()
        mock_client.place_limit_order.return_value = "order-2"

        executor = TradeExecutor(mock_client)
        decision = _make_decision(side="BUY_NO")
        market = _make_market_data(bestBid="0.50")

        await executor.execute_trade(decision, market, 1000.0)
        call_kwargs = mock_client.place_limit_order.call_args
        price = call_kwargs.kwargs.get("price") or call_kwargs[1].get("price")
        token_id = call_kwargs.kwargs.get("token_id") or call_kwargs[1].get("token_id")
        assert abs(price - 0.48) < 0.001  # (1 - 0.50) - 0.02
        assert token_id == "tok-no"

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_monitor_orders_expires_stale(self, mock_db):
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        mock_db.get_open_trades.return_value = [
            {
                "id": "t1",
                "paper": 0,
                "order_id": "order-stale",
                "placed_at": stale_time,
                "price": 0.55,
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "Test?",
                "side": "BUY",
                "size": 10.0,
            }
        ]

        mock_client = AsyncMock()
        mock_client.get_open_orders.return_value = [{"id": "order-stale"}]
        mock_client.cancel_order.return_value = True

        executor = TradeExecutor(mock_client)
        await executor.monitor_orders()

        mock_client.cancel_order.assert_called_once_with("order-stale")
        mock_db.update_trade_status.assert_called_once_with("t1", "EXPIRED")
