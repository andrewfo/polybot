"""P&L tracking and metrics computation.

Provides bankroll snapshotting, daily/weekly/total P&L, and performance metrics.
All data stored in SQLite via core.db helpers.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import CHEAP_MODEL, FRONTIER_MODEL, TEST_BANKROLL
from core import db

logger = logging.getLogger(__name__)

# Sonar model identifier prefix
_SONAR_PREFIX = "perplexity/sonar"


async def snapshot_bankroll() -> None:
    """Take a bankroll snapshot if >1 hour since last.

    Calculates: available cash + sum of position values at current market prices.
    Stores in bankroll table. Skips if last snapshot < 1 hour ago.
    """
    database = db.get_db()

    # Check if last snapshot was less than 1 hour ago
    rows = list(database.execute(
        "SELECT timestamp FROM bankroll ORDER BY timestamp DESC LIMIT 1"
    ).fetchall())
    if rows:
        last_ts = datetime.fromisoformat(rows[0][0])
        if datetime.now(timezone.utc) - last_ts < timedelta(hours=1):
            return

    # Calculate current state
    balance = db.get_paper_balance(TEST_BANKROLL)
    realized_today = db.get_daily_pnl()
    realized_total = db.get_total_pnl()

    db.snapshot_bankroll(
        total_value=balance["total_value"],
        available_cash=balance["available_cash"],
        unrealized_pnl=balance["unrealized_pnl"],
        realized_pnl_today=realized_today,
        realized_pnl_total=realized_total,
    )
    logger.debug("Bankroll snapshot: $%.2f total", balance["total_value"])


def get_daily_pnl() -> dict[str, float]:
    """Return realized + unrealized P&L since midnight UTC."""
    realized = db.get_daily_pnl()
    positions = db.get_open_positions()
    unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    return {
        "realized": realized,
        "unrealized": unrealized,
        "total": realized + unrealized,
    }


def get_weekly_pnl() -> dict[str, float]:
    """Return realized P&L for the last 7 days."""
    database = db.get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    rows = list(database.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades "
        "WHERE pnl IS NOT NULL AND COALESCE(closed_at, timestamp) >= ?",
        [cutoff],
    ).fetchall())
    realized = float(rows[0][0]) if rows else 0.0
    positions = db.get_open_positions()
    unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    return {
        "realized": realized,
        "unrealized": unrealized,
        "total": realized + unrealized,
    }


def get_total_pnl() -> dict[str, float]:
    """Return all-time P&L."""
    realized = db.get_total_pnl()
    positions = db.get_open_positions()
    unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    return {
        "realized": realized,
        "unrealized": unrealized,
        "total": realized + unrealized,
    }


def get_metrics() -> dict[str, Any]:
    """Return performance metrics dict.

    Includes: win_rate, avg_win, avg_loss, profit_factor, max_drawdown,
    total_llm_costs, net_pnl_after_costs, roi.
    """
    database = db.get_db()

    # Get all closed trades with P&L
    trades = list(database.execute(
        "SELECT pnl FROM trades WHERE pnl IS NOT NULL AND status = 'FILLED'"
    ).fetchall())

    wins = [t[0] for t in trades if t[0] > 0]
    losses = [t[0] for t in trades if t[0] < 0]
    total_trades = len(trades)

    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    # Max drawdown from bankroll snapshots
    max_drawdown = _compute_max_drawdown(database)

    # Total LLM costs
    cost_rows = list(database.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_costs"
    ).fetchall())
    total_llm_costs = float(cost_rows[0][0]) if cost_rows else 0.0

    # Net P&L
    realized = db.get_total_pnl()
    net_pnl = realized - total_llm_costs

    # ROI based on starting bankroll
    roi = net_pnl / TEST_BANKROLL if TEST_BANKROLL > 0 else 0.0

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "total_llm_costs": total_llm_costs,
        "net_pnl_after_costs": net_pnl,
        "roi": roi,
    }


def get_cost_breakdown() -> dict[str, Any]:
    """Return LLM costs broken down by model tier, per day and month.

    Returns:
        {
            "by_tier": {"cheap": float, "frontier": float, "sonar": float},
            "daily": {"cheap": float, "frontier": float, "sonar": float, "total": float},
            "monthly": {"cheap": float, "frontier": float, "sonar": float, "total": float},
            "total": float,
        }
    """
    database = db.get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    month = datetime.now(timezone.utc).strftime("%Y-%m")

    def _tier_breakdown(rows: list) -> dict[str, float]:
        cheap = 0.0
        frontier = 0.0
        sonar = 0.0
        for row in rows:
            model, cost = row[0], float(row[1])
            if model and _SONAR_PREFIX in model:
                sonar += cost
            elif model and FRONTIER_MODEL in model:
                frontier += cost
            else:
                cheap += cost
        return {"cheap": cheap, "frontier": frontier, "sonar": sonar}

    # All-time by tier
    all_rows = list(database.execute(
        "SELECT model, COALESCE(SUM(cost_usd), 0) FROM llm_costs GROUP BY model"
    ).fetchall())
    by_tier = _tier_breakdown(all_rows)

    # Daily
    daily_rows = list(database.execute(
        "SELECT model, COALESCE(SUM(cost_usd), 0) FROM llm_costs "
        "WHERE timestamp LIKE ? GROUP BY model",
        [f"{today}%"],
    ).fetchall())
    daily = _tier_breakdown(daily_rows)
    daily["total"] = sum(daily.values())

    # Monthly
    monthly_rows = list(database.execute(
        "SELECT model, COALESCE(SUM(cost_usd), 0) FROM llm_costs "
        "WHERE timestamp LIKE ? GROUP BY model",
        [f"{month}%"],
    ).fetchall())
    monthly = _tier_breakdown(monthly_rows)
    monthly["total"] = sum(monthly.values())

    return {
        "by_tier": by_tier,
        "daily": daily,
        "monthly": monthly,
        "total": sum(by_tier.values()),
    }


def _compute_max_drawdown(database: Any) -> float:
    """Compute max drawdown from bankroll snapshots."""
    rows = list(database.execute(
        "SELECT total_value FROM bankroll ORDER BY timestamp ASC"
    ).fetchall())
    if not rows:
        return 0.0

    peak = rows[0][0]
    max_dd = 0.0
    for row in rows:
        val = row[0]
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd
