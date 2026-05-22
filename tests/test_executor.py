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
    check_entry_spread,
    check_trade_rate,
    compute_limit_price,
    effective_stop_loss_pct,
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

class TestCheckTradeRate:
    @patch("strategy.executor.db")
    def test_under_limit(self, mock_db):
        mock_db.get_recent_trade_count.return_value = 1
        ok, reason = check_trade_rate()
        assert ok is True

    @patch("strategy.executor.db")
    def test_at_limit(self, mock_db):
        mock_db.get_recent_trade_count.return_value = 50
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


class TestCheckEntrySpread:
    def test_narrow_spread_ok(self):
        # 0.55/0.50 → 9.5% relative spread → ok
        ok, reason = check_entry_spread({"bestBid": "0.50", "bestAsk": "0.55"})
        assert ok is True

    def test_wide_spread_blocked(self):
        # 0.09/0.04 → ~77% relative spread → blocked
        ok, reason = check_entry_spread({"bestBid": "0.04", "bestAsk": "0.09"})
        assert ok is False
        assert "spread too wide" in reason

    def test_missing_data_passes(self):
        ok, _ = check_entry_spread({})
        assert ok is True


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
        assert abs(price - (0.55 + 0.02)) < 0.001

    def test_buy_no(self):
        decision = _make_decision(side="BUY_NO")
        market = _make_market_data(bestBid="0.50")
        price, token_id = compute_limit_price(decision, market)
        assert token_id == "tok-no"
        assert abs(price - ((1.0 - 0.50) + 0.02)) < 0.001

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
        mock_db.get_paper_balance.return_value = {"available_cash": 900.0}

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
        # 12 open positions exceeds MAX_OPEN_POSITIONS (10) guardrail
        mock_db.get_open_positions.return_value = [
            {"token_id": str(i), "market_id": f"mkt_{i}", "market_question": f"Q{i}"}
            for i in range(12)
        ]
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_paper_balance.return_value = {"available_cash": 1000.0}

        executor = PaperExecutor()
        decision = _make_decision()
        market = _make_market_data()

        trade_id = await executor.execute_trade(decision, market, 1000.0)
        assert trade_id is None
        mock_db.record_trade.assert_not_called()

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_updates_pnl(self, mock_db, mock_book):
        # Mid 0.55 (10% gain, below 12% TP), bid 0.54 (8% realizable, also below TP).
        mock_book.return_value = {"best_bid": 0.54, "best_ask": 0.56, "mid": 0.55}
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "Will BTC reach 100k?",
                "side": "BUY_YES",
                "avg_entry": 0.50,
                "size": 100.0,
                "current_price": 0.50,
                "paper": 1,
            }
        ]

        executor = PaperExecutor()
        await executor.manage_positions()

        mock_db.close_position.assert_not_called()
        mock_db.upsert_position.assert_called_once()
        call_kwargs = mock_db.upsert_position.call_args
        # current_price column holds the mark (mid), not the bid
        assert call_kwargs.kwargs.get("current_price") == pytest.approx(0.55)

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

    def test_effective_stop_loss_floors_at_high_price(self):
        # Mid/high entry: base 10% stop already wider than 3-tick floor, base wins.
        assert effective_stop_loss_pct(0.50, 0.10) == pytest.approx(0.10)
        assert effective_stop_loss_pct(0.30, 0.10) == pytest.approx(0.10)

    def test_effective_stop_loss_widens_at_low_price(self):
        # At $0.08 entry, 3 ticks = $0.03 = 37.5%, beats base 10%.
        assert effective_stop_loss_pct(0.08, 0.10) == pytest.approx(0.375)
        # At $0.15 entry, 3 ticks = $0.03 = 20%, beats base 10%.
        assert effective_stop_loss_pct(0.15, 0.10) == pytest.approx(0.20)

    def test_effective_stop_loss_degenerate_entry(self):
        assert effective_stop_loss_pct(0.0, 0.10) == pytest.approx(0.10)

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_low_price_skips_stop_at_15pct(self, mock_db, mock_book):
        # Entry $0.08 -> dyn_sl=37.5%. Bid $0.068 = -15% should NOT stop.
        mock_book.return_value = {"best_bid": 0.068, "best_ask": 0.072, "mid": 0.070}
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-low",
                "market_id": "cond-low",
                "market_question": "low-price market",
                "side": "BUY_YES",
                "avg_entry": 0.08,
                "size": 100.0,
                "current_price": 0.08,
                "paper": 1,
            }
        ]
        executor = PaperExecutor()
        await executor.manage_positions()
        mock_db.close_position.assert_not_called()
        mock_db.upsert_position.assert_called_once()

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_low_price_stops_at_40pct(self, mock_db, mock_book):
        # Entry $0.08 -> dyn_sl=37.5%. Bid $0.045 = -43.75% should stop.
        mock_book.return_value = {"best_bid": 0.045, "best_ask": 0.055, "mid": 0.050}
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-low",
                "market_id": "cond-low",
                "market_question": "low-price market",
                "side": "BUY_YES",
                "avg_entry": 0.08,
                "size": 100.0,
                "current_price": 0.08,
                "paper": 1,
            }
        ]
        executor = PaperExecutor()
        await executor.manage_positions()
        mock_db.close_position.assert_called_once()
        kwargs = mock_db.close_position.call_args.kwargs
        assert kwargs["reason"] == "stop_loss"
        # exit_price column reflects the realizable bid, not the mid
        assert kwargs["exit_price"] == pytest.approx(0.045)

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_high_price_still_stops_at_10pct(self, mock_db, mock_book):
        # Entry $0.50, bid $0.44 = -12% should stop (base 10% applies).
        mock_book.return_value = {"best_bid": 0.44, "best_ask": 0.46, "mid": 0.45}
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-hi",
                "market_id": "cond-hi",
                "market_question": "mid-price market",
                "side": "BUY_YES",
                "avg_entry": 0.50,
                "size": 100.0,
                "current_price": 0.50,
                "paper": 1,
            }
        ]
        executor = PaperExecutor()
        await executor.manage_positions()
        mock_db.close_position.assert_called_once()
        kwargs = mock_db.close_position.call_args.kwargs
        assert kwargs["reason"] == "stop_loss"

    @pytest.mark.asyncio
    @patch("strategy.executor.PAPER_REALISTIC_PRICING", False)
    @patch("strategy.executor._fetch_gamma_price", new_callable=AsyncMock)
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_legacy_mid_when_flag_off(self, mock_db, mock_book, mock_mid):
        # With flag off, _fetch_gamma_book is not consulted; mid drives both
        # mark and TP/SL evaluation (legacy behavior).
        mock_mid.return_value = 0.44  # -12% mid on entry 0.50; base SL=10% trips.
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "legacy",
                "side": "BUY_YES",
                "avg_entry": 0.50,
                "size": 100.0,
                "current_price": 0.50,
                "paper": 1,
            }
        ]
        executor = PaperExecutor()
        await executor.manage_positions()
        mock_book.assert_not_called()
        mock_db.close_position.assert_called_once()
        kwargs = mock_db.close_position.call_args.kwargs
        # Legacy path closes at mid, not bid
        assert kwargs["exit_price"] == pytest.approx(0.44)

    @pytest.mark.asyncio
    @patch("strategy.executor._fetch_gamma_price", new_callable=AsyncMock)
    @patch("strategy.executor._fetch_gamma_book", new_callable=AsyncMock)
    @patch("strategy.executor.db")
    async def test_manage_positions_falls_back_to_mid_when_book_unavailable(self, mock_db, mock_book, mock_mid):
        # Flag on but Gamma returned no bid/ask (e.g. one-sided book); falls
        # back to _fetch_gamma_price's mid path so we still mark the position.
        mock_book.return_value = None
        mock_mid.return_value = 0.55
        mock_db.get_open_positions.return_value = [
            {
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "fallback",
                "side": "BUY_YES",
                "avg_entry": 0.50,
                "size": 100.0,
                "current_price": 0.50,
                "paper": 1,
            }
        ]
        executor = PaperExecutor()
        await executor.manage_positions()
        mock_book.assert_called_once()
        mock_mid.assert_called_once()
        mock_db.close_position.assert_not_called()
        mock_db.upsert_position.assert_called_once()


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
        mock_db.get_paper_balance.return_value = {"available_cash": 900.0}

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
        mock_db.get_paper_balance.return_value = {"available_cash": 900.0}

        mock_client = AsyncMock()
        mock_client.place_limit_order.return_value = "order-1"

        executor = TradeExecutor(mock_client)
        decision = _make_decision(side="BUY_YES")
        market = _make_market_data(bestBid="0.58", bestAsk="0.60")

        await executor.execute_trade(decision, market, 1000.0)
        call_kwargs = mock_client.place_limit_order.call_args
        price = call_kwargs.kwargs.get("price") or call_kwargs[1].get("price")
        assert abs(price - 0.62) < 0.001  # 0.60 + 0.02 (cross the spread)

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_buy_no_price(self, mock_db):
        mock_db.get_open_positions.return_value = []
        mock_db.get_recent_trade_count.return_value = 0
        mock_db.get_total_pnl.return_value = 0.0
        mock_db.get_daily_pnl.return_value = 0.0
        mock_db.get_paper_balance.return_value = {"available_cash": 900.0}

        mock_client = AsyncMock()
        mock_client.place_limit_order.return_value = "order-2"

        executor = TradeExecutor(mock_client)
        decision = _make_decision(side="BUY_NO")
        market = _make_market_data(bestBid="0.50", bestAsk="0.52")

        await executor.execute_trade(decision, market, 1000.0)
        call_kwargs = mock_client.place_limit_order.call_args
        price = call_kwargs.kwargs.get("price") or call_kwargs[1].get("price")
        token_id = call_kwargs.kwargs.get("token_id") or call_kwargs[1].get("token_id")
        assert abs(price - 0.52) < 0.001  # (1 - 0.50) + 0.02 (cross the spread)
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

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_monitor_orders_records_actual_clob_fill(self, mock_db):
        # Limit was 0.52, but CLOB filled at 0.49 (better fill). The DB row
        # must reflect 0.49, not the original limit.
        mock_db.get_open_trades.return_value = [
            {
                "id": "t1",
                "paper": 0,
                "order_id": "order-filled",
                "placed_at": datetime.now(timezone.utc).isoformat(),
                "price": 0.52,
                "size": 50.0,
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "Test?",
                "side": "BUY_YES",
            }
        ]
        mock_client = AsyncMock()
        # CLOB no longer has the order open → it filled
        mock_client.get_open_orders.return_value = []
        mock_client.get_order_fill.return_value = {"fill_price": 0.49, "filled_size": 50.0}

        executor = TradeExecutor(mock_client)
        await executor.monitor_orders()

        mock_client.get_order_fill.assert_called_once_with("order-filled")
        mock_db.update_trade_status.assert_called_once_with("t1", "FILLED", fill_price=0.49)
        upsert_kwargs = mock_db.upsert_position.call_args.kwargs
        assert upsert_kwargs["avg_entry"] == pytest.approx(0.49)
        assert upsert_kwargs["current_price"] == pytest.approx(0.49)

    @pytest.mark.asyncio
    @patch("strategy.executor.db")
    async def test_monitor_orders_falls_back_to_limit_when_fill_unavailable(self, mock_db):
        # If get_order_fill returns None (API blip), the trade is still
        # recorded against the limit price so we don't lose the position.
        mock_db.get_open_trades.return_value = [
            {
                "id": "t1",
                "paper": 0,
                "order_id": "order-filled",
                "placed_at": datetime.now(timezone.utc).isoformat(),
                "price": 0.52,
                "size": 50.0,
                "token_id": "tok-1",
                "market_id": "cond-1",
                "market_question": "Test?",
                "side": "BUY_YES",
            }
        ]
        mock_client = AsyncMock()
        mock_client.get_open_orders.return_value = []
        mock_client.get_order_fill.return_value = None

        executor = TradeExecutor(mock_client)
        await executor.monitor_orders()

        mock_db.update_trade_status.assert_called_once_with("t1", "FILLED", fill_price=0.52)
