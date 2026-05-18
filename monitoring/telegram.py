"""Telegram bot for on-demand status checks.

Send /status from your phone to get open positions, balance, P&L,
and bot state. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""

import logging
import time
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config.settings import (
    AGGREGATION_INTERVAL_MINUTES,
    DISCOVERY_INTERVAL_MINUTES,
    PAPER_TRADING,
    POSITION_CHECK_INTERVAL_MINUTES,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TEST_BANKROLL,
)

logger = logging.getLogger(__name__)


def _is_authorized(update: Update) -> bool:
    """Only respond to the configured chat ID."""
    if not TELEGRAM_CHAT_ID:
        return True  # No restriction if chat ID not set
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


def _format_eta(seconds: float | None) -> str:
    """Format seconds-remaining as 'Xh Ym' or 'now'."""
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "now"
    mins = int(seconds // 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h {mins % 60}m"


def _format_status_message(
    positions: list[dict[str, Any]],
    balance: dict[str, float],
    daily_pnl: float,
    total_pnl: float,
    bot_running: bool,
    bot_paused: bool,
    bot_phase: str,
    cycle_etas: dict[str, float | None],
) -> str:
    """Build the status message for Telegram."""
    mode = "PAPER" if PAPER_TRADING else "LIVE"
    if not bot_running:
        state = "STOPPED"
    elif bot_paused:
        state = "PAUSED"
    else:
        state = "RUNNING"

    lines = [
        f"{'📋'} Polybot Status [{mode}] — {state}",
        f"Phase: {bot_phase}",
        "",
    ]

    # Cycle timing
    lines.append(f"{'⏱'} Next Cycles")
    lines.append(
        f"  Discovery:    {_format_eta(cycle_etas.get('discovery'))}"
        f"  (every {DISCOVERY_INTERVAL_MINUTES}m)"
    )
    lines.append(
        f"  Aggregation:  {_format_eta(cycle_etas.get('aggregation'))}"
        f"  (every {AGGREGATION_INTERVAL_MINUTES}m)"
    )
    lines.append(
        f"  Positions:    {_format_eta(cycle_etas.get('position'))}"
        f"  (every {POSITION_CHECK_INTERVAL_MINUTES}m)"
    )
    lines.append("")

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
        bot_paused = False
        bot_phase = "unknown"
        cycle_etas: dict[str, float | None] = {
            "discovery": None, "aggregation": None, "position": None,
        }
        try:
            from web.server import _engine_ref
            if _engine_ref is not None:
                bot_running = _engine_ref.running
                bot_paused = _engine_ref.paused
                bot_phase = _engine_ref.phase
                now = time.time()

                def _eta(last: float | None, interval_min: int) -> float | None:
                    if last is None or not bot_running:
                        return None
                    return max(0.0, (last + interval_min * 60) - now)

                cycle_etas["discovery"] = _eta(
                    _engine_ref._discovery_last_run, DISCOVERY_INTERVAL_MINUTES
                )
                cycle_etas["aggregation"] = _eta(
                    _engine_ref._aggregation_last_run, AGGREGATION_INTERVAL_MINUTES
                )
                cycle_etas["position"] = _eta(
                    _engine_ref._position_last_run, POSITION_CHECK_INTERVAL_MINUTES
                )
        except (ImportError, AttributeError):
            pass

        msg = _format_status_message(
            positions=positions,
            balance=balance,
            daily_pnl=daily_pnl,
            total_pnl=total_pnl,
            bot_running=bot_running,
            bot_paused=bot_paused,
            bot_phase=bot_phase,
            cycle_etas=cycle_etas,
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
