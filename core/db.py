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

_shared_db: sqlite_utils.Database | None = None


def get_db() -> sqlite_utils.Database:
    """Return a shared Database instance with WAL mode and busy timeout.

    Using a single connection avoids 'database is locked' errors when
    multiple async coroutines write concurrently.
    """
    global _shared_db
    if _shared_db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _shared_db = sqlite_utils.Database(str(DB_PATH))
        _shared_db.execute("PRAGMA journal_mode=WAL")
        _shared_db.execute("PRAGMA busy_timeout=5000")
    return _shared_db


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
            "order_id": str,
            "placed_at": str,
            "market_question": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created trades table")

    else:
        columns = {col.name for col in db["trades"].columns}
        if "order_id" not in columns:
            db.execute("ALTER TABLE trades ADD COLUMN order_id TEXT")
            logger.info("Added order_id column to trades table")
        if "placed_at" not in columns:
            db.execute("ALTER TABLE trades ADD COLUMN placed_at TEXT")
            logger.info("Added placed_at column to trades table")
        if "market_question" not in columns:
            db.execute("ALTER TABLE trades ADD COLUMN market_question TEXT")
            logger.info("Added market_question column to trades table")

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
            "status": str,
            "exit_price": float,
            "realized_pnl": float,
        }, pk="token_id", if_not_exists=True)
        logger.info("Created positions table")
    else:
        columns = {col.name for col in db["positions"].columns}
        if "status" not in columns:
            db.execute("ALTER TABLE positions ADD COLUMN status TEXT DEFAULT 'open'")
            logger.info("Added status column to positions table")
        if "realized_pnl" not in columns:
            db.execute("ALTER TABLE positions ADD COLUMN realized_pnl FLOAT")
            logger.info("Added realized_pnl column to positions table")
        if "exit_price" not in columns:
            db.execute("ALTER TABLE positions ADD COLUMN exit_price FLOAT")
            logger.info("Added exit_price column to positions table")

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
            "raw_data": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created signals table")
    else:
        # Migrate: add raw_data column if missing
        columns = {col.name for col in db["signals"].columns}
        if "raw_data" not in columns:
            db.execute("ALTER TABLE signals ADD COLUMN raw_data TEXT")
            logger.info("Added raw_data column to signals table")

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

    if "frontier_decisions" not in db.table_names():
        db["frontier_decisions"].create({
            "id": int,
            "market_id": str,
            "estimated_prob": float,
            "effective_prob": float,
            "market_price": float,
            "edge": float,
            "kelly_fraction": float,
            "bet_size_usd": float,
            "confidence": float,
            "should_trade": int,
            "skip_reason": str,
            "timestamp": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created frontier_decisions table")

    if "skipped_markets" not in db.table_names():
        db["skipped_markets"].create({
            "id": int,
            "market_id": str,
            "skip_reason": str,
            "market_price_at_skip": float,
            "estimated_prob": float,
            "confidence": float,
            "timestamp": str,
            "resolution_outcome": float,
        }, pk="id", if_not_exists=True)
        logger.info("Created skipped_markets table")

    if "parameter_overrides" not in db.table_names():
        db["parameter_overrides"].create({
            "parameter": str,
            "original_value": float,
            "current_value": float,
            "applied_at": str,
            "source_report_ts": str,
            "confidence": float,
            "sample_count": int,
            "reason": str,
            "active": int,
        }, pk="parameter", if_not_exists=True)
        logger.info("Created parameter_overrides table")

    if "parameter_change_snapshots" not in db.table_names():
        db["parameter_change_snapshots"].create({
            "id": int,
            "parameter": str,
            "old_value": float,
            "new_value": float,
            "applied_at": str,
            "snapshot_window_days": int,
            "pre_win_rate": float,
            "pre_edge_efficiency": float,
            "pre_profit_factor": float,
            "post_win_rate": float,
            "post_edge_efficiency": float,
            "post_profit_factor": float,
            "verdict": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created parameter_change_snapshots table")

    if "market_regimes" not in db.table_names():
        db["market_regimes"].create({
            "date": str,
            "regime": str,
            "btc_30d_return": float,
            "btc_30d_vol": float,
        }, pk="date", if_not_exists=True)
        logger.info("Created market_regimes table")

    if "signal_calibration" not in db.table_names():
        db["signal_calibration"].create({
            "id": int,
            "market_id": str,
            "signal_source": str,
            "predicted_probability": float,
            "actual_outcome": float,
            "market_question": str,
            "timestamp": str,
            "resolved_at": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created signal_calibration table")

    if "signal_multiplier_history" not in db.table_names():
        db["signal_multiplier_history"].create({
            "id": int,
            "timestamp": str,
            "source": str,
            "brier_score": float,
            "sample_count": int,
            "multiplier": float,
            "is_default": int,
        }, pk="id", if_not_exists=True)
        logger.info("Created signal_multiplier_history table")


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
    order_id: str | None = None,
    placed_at: str | None = None,
    market_question: str | None = None,
) -> None:
    """Insert a new trade record."""
    db = get_db()
    row: dict[str, Any] = {
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
    }
    if order_id is not None:
        row["order_id"] = order_id
    if placed_at is not None:
        row["placed_at"] = placed_at
    if market_question is not None:
        row["market_question"] = market_question
    db["trades"].insert(row)


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


def _compute_resolution_status(trade: dict[str, Any], position: dict[str, Any] | None) -> str:
    """Derive resolution status from trade + position state.

    Returns one of:
      pending_fill  — order placed, not yet filled
      open_winning  — position open, currently profitable
      open_losing   — position open, currently at a loss
      open_flat     — position open, roughly break-even
      won           — position closed with profit
      lost          — position closed with loss
      expired       — order cancelled / never filled
    """
    status = (trade.get("status") or "").upper()

    if status in ("CANCELLED", "EXPIRED"):
        return "expired"
    if status == "PENDING":
        return "pending_fill"

    # FILLED — check if position is still open or closed
    if trade.get("pnl") is not None:
        return "won" if trade["pnl"] > 0 else "lost"

    # No PnL recorded yet — check live position
    if position and (position.get("status") or "open") != "closed":
        entry = trade.get("price") or 0
        current = position.get("current_price") or entry
        size = position.get("size") or trade.get("size") or 0
        unrealized = (current - entry) * size if entry > 0 else 0
        if abs(unrealized) < 0.01:
            return "open_flat"
        return "open_winning" if unrealized > 0 else "open_losing"

    # Position closed but trade pnl not set — check position's realized_pnl
    if position and position.get("status") == "closed":
        rpnl = position.get("realized_pnl", 0) or 0
        return "won" if rpnl > 0 else "lost"

    # Filled but no position data — treat as open_flat
    return "open_flat"


def _enrich_trades_with_resolution(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add resolution_status field to each trade by joining with positions."""
    db = get_db()
    # Bulk-fetch positions for all token_ids in the trade list
    token_ids = {t.get("token_id", "") for t in trades if t.get("token_id")}
    positions_by_token: dict[str, dict[str, Any]] = {}
    if token_ids and "positions" in db.table_names():
        placeholders = ",".join("?" for _ in token_ids)
        pos_rows = db.execute(
            f"SELECT * FROM positions WHERE token_id IN ({placeholders})",
            list(token_ids),
        ).fetchall()
        if pos_rows:
            pos_cols = [col.name for col in db["positions"].columns]
            for row in pos_rows:
                pos = {pos_cols[i]: row[i] for i in range(len(pos_cols))}
                positions_by_token[pos["token_id"]] = pos

    for trade in trades:
        pos = positions_by_token.get(trade.get("token_id", ""))
        trade["resolution_status"] = _compute_resolution_status(trade, pos)
    return trades


def get_all_trades(limit: int = 200) -> list[dict[str, Any]]:
    """Return all trades ordered by timestamp descending."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
        [limit],
    ).fetchall()
    columns = [col.name for col in db["trades"].columns]
    trades = [{columns[i]: row[i] for i in range(len(columns))} for row in rows]
    return _enrich_trades_with_resolution(trades)


def get_trade_with_context(trade_id: str) -> dict[str, Any] | None:
    """Return a single trade with linked frontier decision and signals."""
    db = get_db()
    try:
        trade = dict(db["trades"].get(trade_id))
    except Exception:
        return None

    market_id = trade.get("market_id", "")
    result: dict[str, Any] = {"trade": trade, "frontier_decision": None, "signals": []}

    # Find the closest frontier decision for this market (by timestamp)
    fd = None
    try:
        fd_rows = db.execute(
            "SELECT * FROM frontier_decisions WHERE market_id = ? "
            "ORDER BY ABS(julianday(timestamp) - julianday(?)) LIMIT 1",
            [market_id, trade.get("timestamp", "")],
        ).fetchall()
        if fd_rows:
            fd_cols = [col.name for col in db["frontier_decisions"].columns]
            fd = {fd_cols[i]: fd_rows[0][i] for i in range(len(fd_cols))}
            result["frontier_decision"] = fd
    except Exception:
        pass

    # Get signals for this market
    try:
        sig_rows = db.execute(
            "SELECT * FROM signals WHERE market_id = ? ORDER BY timestamp DESC LIMIT 20",
            [market_id],
        ).fetchall()
        if sig_rows:
            sig_cols = [col.name for col in db["signals"].columns]
            result["signals"] = [{sig_cols[i]: row[i] for i in range(len(sig_cols))} for row in sig_rows]
    except Exception:
        pass

    # Backfill missing trade fields from related data
    if not trade.get("market_question"):
        # Try frontier decision, then positions table
        if fd and fd.get("market_question"):
            trade["market_question"] = fd["market_question"]
        else:
            try:
                pos_rows = db.execute(
                    "SELECT market_question FROM positions WHERE market_id = ? LIMIT 1",
                    [market_id],
                ).fetchall()
                if pos_rows and pos_rows[0][0]:
                    trade["market_question"] = pos_rows[0][0]
            except Exception:
                pass

    if not trade.get("placed_at"):
        trade["placed_at"] = trade.get("timestamp")

    if trade.get("fill_price") is None and trade.get("status") in ("FILLED", "filled"):
        trade["fill_price"] = trade.get("price")

    if trade.get("paper") and not trade.get("order_id"):
        trade["order_id"] = f"paper-{trade_id[:8]}"

    # Compute resolution status
    pos = None
    token_id = trade.get("token_id", "")
    if token_id and "positions" in db.table_names():
        try:
            pos = dict(db["positions"].get(token_id))
        except Exception:
            pass
    trade["resolution_status"] = _compute_resolution_status(trade, pos)

    return result


def get_recent_trade_count(hours: int = 1) -> int:
    """Return the number of trades placed in the last N hours."""
    db = get_db()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = list(db.execute(
        "SELECT COUNT(*) FROM trades WHERE timestamp >= ?",
        [cutoff],
    ).fetchall())
    return int(rows[0][0]) if rows else 0


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
    # Both BUY_YES and BUY_NO buy a token — profit when token price rises
    unrealized_pnl = (current_price - avg_entry) * size
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
        "status": "open",
        "exit_price": None,
        "realized_pnl": None,
    }, pk="token_id")


def close_position(
    token_id: str,
    exit_price: float = 0.0,
    realized_pnl: float = 0.0,
) -> None:
    """Mark a position as closed and record realized PnL.

    Updates status to 'closed', sets current_price to exit_price,
    and records the realized PnL. Also updates the matching trade's pnl
    field so that get_total_pnl() and metrics stay consistent.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        db["positions"].update(token_id, {
            "status": "closed",
            "current_price": exit_price,
            "exit_price": exit_price,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": realized_pnl,  # legacy compat
            "last_updated": now,
        })
    except Exception:
        # Fallback: delete if update fails (old schema)
        db["positions"].delete(token_id)

    # Also update the most recent FILLED trade for this token so
    # get_total_pnl() / get_daily_pnl() / metrics reflect the PnL
    try:
        trade_rows = db.execute(
            "SELECT id FROM trades WHERE token_id = ? AND status = 'FILLED' "
            "AND pnl IS NULL ORDER BY timestamp DESC LIMIT 1",
            [token_id],
        ).fetchall()
        if trade_rows:
            db["trades"].update(trade_rows[0][0], {"pnl": realized_pnl})
    except Exception:
        pass


def get_open_positions() -> list[dict[str, Any]]:
    """Return all open positions (status != 'closed')."""
    db = get_db()
    try:
        columns = {col.name for col in db["positions"].columns}
        if "status" in columns:
            return list(db["positions"].rows_where(
                "status IS NULL OR status != 'closed'"
            ))
    except Exception:
        pass
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
    raw_data: str | None = None,
) -> None:
    """Insert a signal record."""
    db = get_db()
    row: dict[str, Any] = {
        "market_id": market_id,
        "signal_source": signal_source,
        "probability": probability,
        "confidence": confidence,
        "reasoning": reasoning,
        "model_used": model_used,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if raw_data is not None:
        row["raw_data"] = raw_data
    db["signals"].insert(row)


def get_latest_signals(market_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent signals for a market."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM signals WHERE market_id = ? ORDER BY timestamp DESC LIMIT ?",
        [market_id, limit],
    ).fetchall()
    columns = [col.name for col in db["signals"].columns]
    return [{columns[i]: row[i] for i in range(len(columns))} for row in rows]


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


def get_paper_balance(starting_bankroll: float) -> dict[str, float]:
    """Compute paper trading balance breakdown.

    Returns dict with starting_balance, realized_pnl, deployed_capital,
    unrealized_pnl, available_cash, total_value.

    Realized PnL comes from closed positions (where close_position() stores it).
    This is the authoritative source for paper trades — trades.pnl is not
    reliably set for paper trades.
    """
    db = get_db()

    # Realized PnL from closed positions (authoritative for paper trades)
    closed_rows = list(db.execute(
        "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions "
        "WHERE status = 'closed' AND realized_pnl IS NOT NULL"
    ).fetchall())
    realized_pnl = float(closed_rows[0][0]) if closed_rows else 0.0

    positions = get_open_positions()
    deployed_capital = sum(p["size"] * p["avg_entry"] for p in positions)
    unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
    available_cash = starting_bankroll + realized_pnl - deployed_capital
    total_value = available_cash + sum(p["size"] * p["current_price"] for p in positions)
    return {
        "starting_balance": starting_bankroll,
        "realized_pnl": realized_pnl,
        "deployed_capital": deployed_capital,
        "unrealized_pnl": unrealized_pnl,
        "available_cash": available_cash,
        "total_value": total_value,
        "open_positions": len(positions),
    }


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


def get_gamma_id_for_condition(condition_id: str) -> str | None:
    """Look up the Gamma numeric market ID for a condition_id from cache.

    Returns the Gamma ID string (e.g., '573655') or None if not in cache.
    """
    cached = get_cached_market(condition_id)
    if cached and isinstance(cached.get("data"), dict):
        gamma_id = cached["data"].get("_gamma_id")
        if gamma_id:
            return str(gamma_id)
    return None


def clear_pipeline_cache() -> None:
    """Clear the market_cache table. Called on bot stop to reset pipeline state."""
    db = get_db()
    if "market_cache" in db.table_names():
        db["market_cache"].delete_where()
        logger.info("Cleared market_cache table")


def snapshot_multipliers(
    multipliers: dict[str, "Any"],
) -> None:
    """Persist a snapshot of calibration multipliers for historical analysis.

    ``multipliers`` is source -> ProviderCalibration (from calibration.py).
    """
    from datetime import datetime, timezone
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for source, cal in multipliers.items():
        rows.append({
            "timestamp": now,
            "source": source,
            "brier_score": cal.brier_score,
            "sample_count": cal.sample_count,
            "multiplier": cal.multiplier,
            "is_default": int(cal.is_default),
        })
    if rows:
        db["signal_multiplier_history"].insert_all(rows)


# ---------------------------------------------------------------------------
# Frontier decision audit trail
# ---------------------------------------------------------------------------

def record_frontier_decision(
    market_id: str,
    estimated_prob: float,
    effective_prob: float,
    market_price: float,
    edge: float,
    kelly_fraction: float,
    bet_size_usd: float,
    confidence: float,
    should_trade: bool,
    skip_reason: str = "",
) -> None:
    """Record a frontier model decision for post-hoc analysis."""
    db = get_db()
    db["frontier_decisions"].insert({
        "market_id": market_id,
        "estimated_prob": estimated_prob,
        "effective_prob": effective_prob,
        "market_price": market_price,
        "edge": edge,
        "kelly_fraction": kelly_fraction,
        "bet_size_usd": bet_size_usd,
        "confidence": confidence,
        "should_trade": int(should_trade),
        "skip_reason": skip_reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def record_skipped_market(
    market_id: str,
    skip_reason: str,
    market_price: float = 0.0,
    estimated_prob: float = 0.0,
    confidence: float = 0.0,
) -> None:
    """Record a skipped market for later resolution analysis."""
    db = get_db()
    db["skipped_markets"].insert({
        "market_id": market_id,
        "skip_reason": skip_reason,
        "market_price_at_skip": market_price,
        "estimated_prob": estimated_prob,
        "confidence": confidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolution_outcome": None,
    })


# ---------------------------------------------------------------------------
# Parameter override helpers
# ---------------------------------------------------------------------------

def get_active_overrides() -> dict[str, float]:
    """Return {parameter_name: current_value} for all active overrides."""
    db = get_db()
    try:
        if "parameter_overrides" not in db.table_names():
            return {}
        rows = list(db["parameter_overrides"].rows_where("active = 1"))
        return {row["parameter"]: float(row["current_value"]) for row in rows}
    except Exception:
        return {}


# Auto-create tables on import
ensure_tables()
