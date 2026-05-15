"""Web-integrated notification system.

Formats and logs notifications. The web dashboard reads from the log buffer
(LogBuffer handler attached to root logger) via GET /api/logs.
Works standalone for testing — just logs, no web dependency.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategy.kelly import TradeDecision

logger = logging.getLogger("polybot.notifications")


class Notifier:
    """Formats and logs notifications. Web dashboard reads from log buffer."""

    async def send(self, message: str, level: str = "info") -> None:
        """Log a notification message at the given level.

        level: 'info', 'warning', 'alert', 'critical'
        Web dashboard polls log buffer via GET /api/logs.
        """
        log_level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "alert": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(level, logging.INFO)
        logger.log(log_level, message)

    async def send_trade(self, trade_decision: "TradeDecision") -> None:
        """Format and log trade execution details."""
        td = trade_decision
        msg = (
            f"TRADE EXECUTED | {td.market_question[:80]} | "
            f"Side: {td.side} | Size: ${td.bet_size_usd:.2f} | "
            f"Price: {td.market_price:.3f} | Edge: {td.edge:+.3f} | "
            f"Confidence: {td.confidence:.2f}"
        )
        logger.info(msg)

    async def send_position_closed(self, position: dict, pnl: float) -> None:
        """Format and log position closure with P&L."""
        question = position.get("market_question", position.get("market_id", "unknown"))
        entry = position.get("avg_entry", 0)
        exit_p = position.get("exit_price", position.get("current_price", 0))
        size = position.get("size", 0)
        pnl_pct = (pnl / (entry * size) * 100) if (entry * size) > 0 else 0

        msg = (
            f"POSITION CLOSED | {question[:80]} | "
            f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%) | "
            f"Entry: {entry:.3f} → Exit: {exit_p:.3f} | "
            f"Size: {size:.1f} shares"
        )
        if pnl >= 0:
            logger.info(msg)
        else:
            logger.warning(msg)

    async def send_health_alert(self, issue: str) -> None:
        """Format and log health check failure with severity."""
        msg = f"HEALTH ALERT | {issue}"
        logger.error(msg)
