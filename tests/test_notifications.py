"""Tests for monitoring/notifications.py."""

import logging

import pytest

from monitoring.notifications import Notifier
from strategy.kelly import TradeDecision


@pytest.fixture
def notifier():
    return Notifier()


@pytest.mark.asyncio
async def test_send_info(notifier, caplog):
    with caplog.at_level(logging.INFO, logger="polybot.notifications"):
        await notifier.send("Test info message", level="info")
    assert "Test info message" in caplog.text


@pytest.mark.asyncio
async def test_send_warning(notifier, caplog):
    with caplog.at_level(logging.WARNING, logger="polybot.notifications"):
        await notifier.send("Something concerning", level="warning")
    assert "Something concerning" in caplog.text


@pytest.mark.asyncio
async def test_send_critical(notifier, caplog):
    with caplog.at_level(logging.CRITICAL, logger="polybot.notifications"):
        await notifier.send("System failure", level="critical")
    assert "System failure" in caplog.text


@pytest.mark.asyncio
async def test_send_trade(notifier, caplog):
    td = TradeDecision(
        market_id="0x123",
        token_id="tok_abc",
        market_question="Will BTC exceed $100k by end of June?",
        side="BUY_YES",
        estimated_prob=0.65,
        effective_prob=0.60,
        market_price=0.50,
        edge=0.10,
        full_kelly_fraction=0.20,
        adjusted_fraction=0.05,
        bet_size_usd=25.0,
        expected_value=0.10,
        confidence=0.75,
        should_trade=True,
        skip_reason="",
    )
    with caplog.at_level(logging.INFO, logger="polybot.notifications"):
        await notifier.send_trade(td)
    assert "TRADE EXECUTED" in caplog.text
    assert "BUY_YES" in caplog.text
    assert "$25.00" in caplog.text


@pytest.mark.asyncio
async def test_send_position_closed_profit(notifier, caplog):
    position = {
        "market_question": "Will ETH hit $5k?",
        "avg_entry": 0.40,
        "exit_price": 0.60,
        "current_price": 0.60,
        "size": 50.0,
    }
    with caplog.at_level(logging.INFO, logger="polybot.notifications"):
        await notifier.send_position_closed(position, pnl=10.0)
    assert "POSITION CLOSED" in caplog.text
    assert "$+10.00" in caplog.text or "$10.00" in caplog.text


@pytest.mark.asyncio
async def test_send_position_closed_loss(notifier, caplog):
    position = {
        "market_question": "Will SOL hit $500?",
        "avg_entry": 0.60,
        "exit_price": 0.30,
        "current_price": 0.30,
        "size": 40.0,
    }
    with caplog.at_level(logging.WARNING, logger="polybot.notifications"):
        await notifier.send_position_closed(position, pnl=-12.0)
    assert "POSITION CLOSED" in caplog.text
    assert "-$12.00" in caplog.text or "$-12.00" in caplog.text


@pytest.mark.asyncio
async def test_send_health_alert(notifier, caplog):
    with caplog.at_level(logging.ERROR, logger="polybot.notifications"):
        await notifier.send_health_alert("Wallet gas critically low: 0.03 MATIC")
    assert "HEALTH ALERT" in caplog.text
    assert "Wallet gas" in caplog.text


@pytest.mark.asyncio
async def test_works_without_web_dashboard(notifier, caplog):
    """Notifications should work standalone without web server running."""
    with caplog.at_level(logging.INFO, logger="polybot.notifications"):
        await notifier.send("standalone test")
    assert "standalone test" in caplog.text
