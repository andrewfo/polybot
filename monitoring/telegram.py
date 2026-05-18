"""Telegram bot for on-demand status checks.

Send /status from your phone to get open positions, balance, P&L,
and bot state. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""

import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import PAPER_TRADING, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TEST_BANKROLL

logger = logging.getLogger(__name__)


def _is_authorized(update: Update) -> bool:
    """Only respond to the configured chat ID."""
    if not TELEGRAM_CHAT_ID:
        return True  # No restriction if chat ID not set
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


def _format_status_message(
    positions: list[dict[str, Any]],
    balance: dict[str, float],
    daily_pnl: float,
    total_pnl: float,
    bot_running: bool,
    bot_phase: str,
) -> str:
    """Build the status message for Telegram."""
    mode = "PAPER" if PAPER_TRADING else "LIVE"
    state = "RUNNING" if bot_running else "STOPPED"

    lines = [
        f"{'📋'} Polybot Status [{mode}] — {state}",
        f"Phase: {bot_phase}",
        "",
    ]

    # Balance
    lines.append(f"{'💰'} Balance")
    lines.append(f"  Total value: ${balance.get('total_value', 0):.2f}")
    lines.append(f"  Available:   ${balance.get('available_cash', 0):.2f}")
    lines.append(f"  Deployed:    ${balance.get('deployed_capital', 0):.2f}")
    lines.append(f"  Unrealized:  ${balance.get('unrealized_pnl', 0):+.2f}")
    lines.append(f"  Realized:    ${balance.get('realized_pnl', 0):+.2f}")
    lines.append("")

    # P&L
    lines.append(f"{'📊'} P&L")
    lines.append(f"  Today:    ${daily_pnl:+.2f}")
    lines.append(f"  All-time: ${total_pnl:+.2f}")
    lines.append("")

    # Positions
    if positions:
        lines.append(f"{'📈'} Open Positions ({len(positions)})")
        for p in positions:
            question = p.get("market_question", "?")[:50]
            side = p.get("side", "?")
            size = p.get("size", 0)
            entry = p.get("avg_entry", 0)
            current = p.get("current_price", 0)
            upnl = p.get("unrealized_pnl", 0)
            sign = "+" if upnl >= 0 else ""
            lines.append(f"  {side} {question}")
            lines.append(f"    {size:.0f} shares @ {entry:.3f} → {current:.3f} ({sign}${upnl:.2f})")
    else:
        lines.append("No open positions.")

    return "\n".join(lines)


async def _status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not _is_authorized(update):
        return

    try:
        from core.db import get_daily_pnl, get_open_positions, get_paper_balance, get_total_pnl

        positions = get_open_positions()
        balance = get_paper_balance(TEST_BANKROLL) if PAPER_TRADING else {
            "total_value": 0, "available_cash": 0, "deployed_capital": 0,
            "unrealized_pnl": 0, "realized_pnl": 0,
        }
        daily_pnl = get_daily_pnl()
        total_pnl = get_total_pnl()

        # Try to get bot engine state from web server
        bot_running = False
        bot_phase = "unknown"
        try:
            from web.server import _engine_ref
            if _engine_ref is not None:
                bot_running = _engine_ref.running
                bot_phase = _engine_ref.phase
        except (ImportError, AttributeError):
            pass

        msg = _format_status_message(
            positions=positions,
            balance=balance,
            daily_pnl=daily_pnl,
            total_pnl=total_pnl,
            bot_running=bot_running,
            bot_phase=bot_phase,
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error("Telegram /status failed: %s", e)
        await update.message.reply_text(f"Error fetching status: {e}")


def create_telegram_app() -> Application | None:
    """Build the Telegram Application. Returns None if token not configured."""
    if not TELEGRAM_BOT_TOKEN:
        logger.info("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return None

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("status", _status_command))
    logger.info("Telegram bot configured (chat_id filter: %s)",
                TELEGRAM_CHAT_ID or "none")
    return app
