"""Tests for monitoring/telegram.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.telegram import _format_status_message, _is_authorized, create_telegram_app


def _make_positions():
    return [
        {
            "token_id": "tok1",
            "market_question": "Will BTC exceed $100k by end of June?",
            "side": "BUY_YES",
            "size": 50,
            "avg_entry": 0.450,
            "current_price": 0.520,
            "unrealized_pnl": 3.50,
        },
        {
            "token_id": "tok2",
            "market_question": "Will ETH hit $5k by July?",
            "side": "BUY_NO",
            "size": 30,
            "avg_entry": 0.600,
            "current_price": 0.550,
            "unrealized_pnl": -1.50,
        },
    ]


def _make_balance():
    return {
        "starting_balance": 200.0,
        "realized_pnl": 12.50,
        "deployed_capital": 40.50,
        "unrealized_pnl": 2.00,
        "available_cash": 172.00,
        "total_value": 214.50,
        "open_positions": 2,
    }


class TestFormatStatusMessage:
    def test_with_positions(self):
        msg = _format_status_message(
            positions=_make_positions(),
            balance=_make_balance(),
            daily_pnl=5.25,
            total_pnl=12.50,
            bot_running=True,
            bot_phase="aggregation",
        )
        assert "PAPER" in msg
        assert "RUNNING" in msg
        assert "aggregation" in msg
        assert "$214.50" in msg
        assert "$172.00" in msg
        assert "$40.50" in msg
        assert "$+5.25" in msg
        assert "$+12.50" in msg
        assert "Open Positions (2)" in msg
        assert "BUY_YES" in msg
        assert "BUY_NO" in msg
        assert "$3.50" in msg
        assert "$-1.50" in msg

    def test_no_positions(self):
        msg = _format_status_message(
            positions=[],
            balance=_make_balance(),
            daily_pnl=0.0,
            total_pnl=0.0,
            bot_running=False,
            bot_phase="idle",
        )
        assert "STOPPED" in msg
        assert "No open positions" in msg

    def test_long_question_truncated(self):
        positions = [{
            "market_question": "A" * 100,
            "side": "BUY_YES",
            "size": 10,
            "avg_entry": 0.5,
            "current_price": 0.6,
            "unrealized_pnl": 1.0,
        }]
        msg = _format_status_message(
            positions=positions,
            balance=_make_balance(),
            daily_pnl=0, total_pnl=0,
            bot_running=True, bot_phase="idle",
        )
        # Question should be truncated to 50 chars
        assert "A" * 51 not in msg
        assert "A" * 50 in msg


class TestIsAuthorized:
    def test_no_chat_id_configured(self):
        update = MagicMock()
        update.effective_chat.id = 12345
        with patch("monitoring.telegram.TELEGRAM_CHAT_ID", ""):
            assert _is_authorized(update) is True

    def test_matching_chat_id(self):
        update = MagicMock()
        update.effective_chat.id = 12345
        with patch("monitoring.telegram.TELEGRAM_CHAT_ID", "12345"):
            assert _is_authorized(update) is True

    def test_wrong_chat_id(self):
        update = MagicMock()
        update.effective_chat.id = 99999
        with patch("monitoring.telegram.TELEGRAM_CHAT_ID", "12345"):
            assert _is_authorized(update) is False


class TestCreateTelegramApp:
    def test_no_token_returns_none(self):
        with patch("monitoring.telegram.TELEGRAM_BOT_TOKEN", ""):
            assert create_telegram_app() is None

    def test_with_token_returns_app(self):
        with patch("monitoring.telegram.TELEGRAM_BOT_TOKEN", "123:FAKE_TOKEN"):
            app = create_telegram_app()
            assert app is not None


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_unauthorized_ignored(self):
        from monitoring.telegram import _status_command

        update = MagicMock()
        update.effective_chat.id = 99999
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch("monitoring.telegram.TELEGRAM_CHAT_ID", "12345"):
            await _status_command(update, MagicMock())

        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_sends_status(self):
        from monitoring.telegram import _status_command

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with (
            patch("monitoring.telegram.TELEGRAM_CHAT_ID", "12345"),
            patch("monitoring.telegram.PAPER_TRADING", True),
            patch("monitoring.telegram.TEST_BANKROLL", 200.0),
            patch("core.db.get_open_positions", return_value=_make_positions()),
            patch("core.db.get_paper_balance", return_value=_make_balance()),
            patch("core.db.get_daily_pnl", return_value=5.25),
            patch("core.db.get_total_pnl", return_value=12.50),
        ):
            await _status_command(update, MagicMock())

        update.message.reply_text.assert_called_once()
        msg = update.message.reply_text.call_args[0][0]
        assert "PAPER" in msg
        assert "Open Positions (2)" in msg
