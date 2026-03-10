"""SQLite state persistence for the trading bot.

Minimal implementation for Section 1 (llm_costs table only).
Section 2 will expand with trades, positions, signals, bankroll, market_cache tables.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import sqlite_utils

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"


def get_db() -> sqlite_utils.Database:
    """Return a Database instance, creating the data directory if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_utils.Database(str(DB_PATH))


def ensure_tables() -> None:
    """Create tables if they don't exist."""
    db = get_db()

    if "llm_costs" not in db.table_names():
        db["llm_costs"].create({
            "id": int,
            "timestamp": str,
            "model": str,
            "task_type": str,
            "input_tokens": int,
            "output_tokens": int,
            "cost_usd": float,
        }, pk="id", if_not_exists=True)
        logger.info("Created llm_costs table")


def record_llm_cost(
    timestamp: str,
    model: str,
    task_type: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Insert a cost record for an LLM call."""
    db = get_db()
    db["llm_costs"].insert({
        "timestamp": timestamp,
        "model": model,
        "task_type": task_type,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    })


def get_daily_llm_cost() -> float:
    """Return total LLM spend for today (UTC)."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = list(db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) as total FROM llm_costs WHERE timestamp LIKE ?",
        [f"{today}%"],
    ).fetchall())
    return float(rows[0][0]) if rows else 0.0


def get_monthly_llm_cost() -> float:
    """Return total LLM spend for this month (UTC)."""
    db = get_db()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = list(db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) as total FROM llm_costs WHERE timestamp LIKE ?",
        [f"{month}%"],
    ).fetchall())
    return float(rows[0][0]) if rows else 0.0


# Auto-create tables on import
ensure_tables()
