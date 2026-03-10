"""SQLite state persistence for the trading bot.

Tables: trades, positions, signals, bankroll, llm_costs, market_cache.
All tables are auto-created on first import.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_utils

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"


def get_db() -> sqlite_utils.Database:
    """Return a Database instance, creating the data directory if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite_utils.Database(str(DB_PATH))


def ensure_tables() -> None:
    """Create all tables if they don't exist."""
    db = get_db()

    if "trades" not in db.table_names():
        db["trades"].create({
            "id": str,
            "market_id": str,
            "token_id": str,
            "side": str,
            "price": float,
            "size": float,
            "timestamp": str,
            "status": str,
            "fill_price": float,
            "pnl": float,
            "paper": int,
        }, pk="id", if_not_exists=True)
        logger.info("Created trades table")

    if "positions" not in db.table_names():
        db["positions"].create({
            "token_id": str,
            "market_id": str,
            "market_question": str,
            "side": str,
            "avg_entry": float,
            "size": float,
            "current_price": float,
            "unrealized_pnl": float,
            "opened_at": str,
            "last_updated": str,
            "paper": int,
        }, pk="token_id", if_not_exists=True)
        logger.info("Created positions table")

    if "signals" not in db.table_names():
        db["signals"].create({
            "id": int,
            "market_id": str,
            "signal_source": str,
            "probability": float,
            "confidence": float,
            "reasoning": str,
            "model_used": str,
            "timestamp": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created signals table")

    if "bankroll" not in db.table_names():
        db["bankroll"].create({
            "timestamp": str,
            "total_value": float,
            "available_cash": float,
            "unrealized_pnl": float,
            "realized_pnl_today": float,
            "realized_pnl_total": float,
        }, pk="timestamp", if_not_exists=True)
        logger.info("Created bankroll table")

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

    if "market_cache" not in db.table_names():
        db["market_cache"].create({
            "condition_id": str,
            "data": str,
            "fetched_at": str,
            "category": str,
        }, pk="condition_id", if_not_exists=True)
        logger.info("Created market_cache table")


# ---------------------------------------------------------------------------
# Trade helpers
# ---------------------------------------------------------------------------

def record_trade(
    trade_id: str,
    market_id: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    status: str = "PENDING",
    paper: bool = False,
) -> None:
    """Insert a new trade record."""
    db = get_db()
    db["trades"].insert({
        "id": trade_id,
        "market_id": market_id,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "fill_price": None,
        "pnl": None,
        "paper": int(paper),
    })


def update_trade_status(
    trade_id: str,
    status: str,
    fill_price: float | None = None,
    pnl: float | None = None,
) -> None:
    """Update the status (and optionally fill_price/pnl) of a trade."""
    db = get_db()
    updates: dict[str, Any] = {"status": status}
    if fill_price is not None:
        updates["fill_price"] = fill_price
    if pnl is not None:
        updates["pnl"] = pnl
    db["trades"].update(trade_id, updates)


def get_open_trades() -> list[dict[str, Any]]:
    """Return all trades with status PENDING."""
    db = get_db()
    return list(db["trades"].rows_where("status = ?", ["PENDING"]))


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def upsert_position(
    token_id: str,
    market_id: str,
    market_question: str,
    side: str,
    avg_entry: float,
    size: float,
    current_price: float,
    paper: bool = False,
) -> None:
    """Insert or update a position."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    unrealized_pnl = (current_price - avg_entry) * size if side == "BUY" else (avg_entry - current_price) * size
    db["positions"].upsert({
        "token_id": token_id,
        "market_id": market_id,
        "market_question": market_question,
        "side": side,
        "avg_entry": avg_entry,
        "size": size,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "opened_at": now,
        "last_updated": now,
        "paper": int(paper),
    }, pk="token_id")


def close_position(token_id: str) -> None:
    """Remove a position (closed)."""
    db = get_db()
    db["positions"].delete(token_id)


def get_open_positions() -> list[dict[str, Any]]:
    """Return all open positions."""
    db = get_db()
    return list(db["positions"].rows)


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------

def record_signal(
    market_id: str,
    signal_source: str,
    probability: float,
    confidence: float,
    reasoning: str,
    model_used: str,
) -> None:
    """Insert a signal record."""
    db = get_db()
    db["signals"].insert({
        "market_id": market_id,
        "signal_source": signal_source,
        "probability": probability,
        "confidence": confidence,
        "reasoning": reasoning,
        "model_used": model_used,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def get_latest_signals(market_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent signals for a market."""
    db = get_db()
    return list(db.execute(
        "SELECT * FROM signals WHERE market_id = ? ORDER BY timestamp DESC LIMIT ?",
        [market_id, limit],
    ).fetchall())


# ---------------------------------------------------------------------------
# Bankroll helpers
# ---------------------------------------------------------------------------

def snapshot_bankroll(
    total_value: float,
    available_cash: float,
    unrealized_pnl: float,
    realized_pnl_today: float,
    realized_pnl_total: float,
) -> None:
    """Insert a bankroll snapshot."""
    db = get_db()
    db["bankroll"].insert({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_value": total_value,
        "available_cash": available_cash,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl_today": realized_pnl_today,
        "realized_pnl_total": realized_pnl_total,
    })


def get_daily_pnl() -> float:
    """Return realized P&L for today (UTC)."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = list(db.execute(
        "SELECT COALESCE(SUM(pnl), 0) as total FROM trades "
        "WHERE pnl IS NOT NULL AND timestamp LIKE ?",
        [f"{today}%"],
    ).fetchall())
    return float(rows[0][0]) if rows else 0.0


def get_total_pnl() -> float:
    """Return total realized P&L across all time."""
    db = get_db()
    rows = list(db.execute(
        "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE pnl IS NOT NULL"
    ).fetchall())
    return float(rows[0][0]) if rows else 0.0


# ---------------------------------------------------------------------------
# LLM cost helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Market cache helpers
# ---------------------------------------------------------------------------

def cache_market(
    condition_id: str,
    data: dict[str, Any],
    category: str = "",
) -> None:
    """Cache market data as JSON blob."""
    db = get_db()
    db["market_cache"].upsert({
        "condition_id": condition_id,
        "data": json.dumps(data),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "category": category,
    }, pk="condition_id")


def get_cached_market(condition_id: str) -> dict[str, Any] | None:
    """Retrieve cached market data, or None if not cached."""
    db = get_db()
    try:
        row = db["market_cache"].get(condition_id)
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result
    except Exception:
        return None


def clear_pipeline_cache() -> None:
    """Clear the market_cache table. Called on bot stop to reset pipeline state."""
    db = get_db()
    if "market_cache" in db.table_names():
        db["market_cache"].delete_where()
        logger.info("Cleared market_cache table")


# Auto-create tables on import
ensure_tables()
