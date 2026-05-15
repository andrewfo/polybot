"""Tests for monitoring/pnl.py."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core import db
from monitoring import pnl


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a fresh temporary database for each test."""
    test_db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.ensure_tables()
    return test_db_path


@pytest.fixture
def seed_trades():
    """Insert sample trades."""
    database = db.get_db()
    now = datetime.now(timezone.utc)
    trades = [
        {"id": "t1", "market_id": "m1", "token_id": "tok1", "side": "BUY_YES",
         "price": 0.50, "size": 20, "timestamp": now.isoformat(),
         "status": "FILLED", "fill_price": 0.50, "pnl": 5.0, "paper": 1},
        {"id": "t2", "market_id": "m2", "token_id": "tok2", "side": "BUY_NO",
         "price": 0.40, "size": 30, "timestamp": now.isoformat(),
         "status": "FILLED", "fill_price": 0.40, "pnl": -3.0, "paper": 1},
        {"id": "t3", "market_id": "m3", "token_id": "tok3", "side": "BUY_YES",
         "price": 0.60, "size": 10, "timestamp": (now - timedelta(days=10)).isoformat(),
         "status": "FILLED", "fill_price": 0.60, "pnl": 8.0, "paper": 1},
    ]
    for t in trades:
        database["trades"].insert(t)
    return trades


@pytest.fixture
def seed_positions():
    """Insert sample open positions."""
    db.upsert_position(
        token_id="tok_open1",
        market_id="m_open1",
        market_question="Will BTC hit 100k?",
        side="BUY_YES",
        avg_entry=0.50,
        size=20.0,
        current_price=0.60,
        paper=True,
    )


@pytest.fixture
def seed_llm_costs():
    """Insert sample LLM costs."""
    database = db.get_db()
    now = datetime.now(timezone.utc)
    costs = [
        {"timestamp": now.isoformat(), "model": "google/gemini-2.0-flash-lite-001",
         "task_type": "classify", "input_tokens": 500, "output_tokens": 100, "cost_usd": 0.001},
        {"timestamp": now.isoformat(), "model": "anthropic/claude-opus-4-6",
         "task_type": "frontier", "input_tokens": 2000, "output_tokens": 500, "cost_usd": 0.05},
        {"timestamp": now.isoformat(), "model": "perplexity/sonar",
         "task_type": "web_search", "input_tokens": 1000, "output_tokens": 300, "cost_usd": 0.01},
        {"timestamp": (now - timedelta(days=35)).isoformat(), "model": "google/gemini-2.0-flash-lite-001",
         "task_type": "classify", "input_tokens": 400, "output_tokens": 80, "cost_usd": 0.0008},
    ]
    for c in costs:
        database["llm_costs"].insert(c)


def test_get_daily_pnl(seed_trades, seed_positions):
    result = pnl.get_daily_pnl()
    # t1 (+5) + t2 (-3) = 2.0 realized today
    assert result["realized"] == pytest.approx(2.0)
    # Position: (0.60 - 0.50) * 20 = 2.0 unrealized
    assert result["unrealized"] == pytest.approx(2.0)
    assert result["total"] == pytest.approx(4.0)


def test_get_weekly_pnl(seed_trades, seed_positions):
    result = pnl.get_weekly_pnl()
    # t1 (+5) + t2 (-3) = 2.0 within 7 days; t3 is 10 days ago
    assert result["realized"] == pytest.approx(2.0)
    assert result["unrealized"] == pytest.approx(2.0)


def test_get_total_pnl(seed_trades, seed_positions):
    result = pnl.get_total_pnl()
    # All: 5 + (-3) + 8 = 10.0
    assert result["realized"] == pytest.approx(10.0)
    assert result["unrealized"] == pytest.approx(2.0)
    assert result["total"] == pytest.approx(12.0)


def test_get_metrics(seed_trades, seed_llm_costs):
    metrics = pnl.get_metrics()
    assert metrics["total_trades"] == 3
    # 2 wins (t1, t3), 1 loss (t2)
    assert metrics["win_rate"] == pytest.approx(2 / 3)
    assert metrics["avg_win"] == pytest.approx(6.5)  # (5+8)/2
    assert metrics["avg_loss"] == pytest.approx(-3.0)
    assert metrics["profit_factor"] == pytest.approx(13.0 / 3.0)
    assert metrics["total_llm_costs"] > 0
    assert "net_pnl_after_costs" in metrics
    assert "roi" in metrics


def test_get_cost_breakdown(seed_llm_costs):
    breakdown = pnl.get_cost_breakdown()
    assert breakdown["by_tier"]["cheap"] == pytest.approx(0.001 + 0.0008)
    assert breakdown["by_tier"]["frontier"] == pytest.approx(0.05)
    assert breakdown["by_tier"]["sonar"] == pytest.approx(0.01)
    # Daily should exclude the 35-day-old cost
    assert breakdown["daily"]["cheap"] == pytest.approx(0.001)
    assert breakdown["daily"]["frontier"] == pytest.approx(0.05)
    assert breakdown["daily"]["sonar"] == pytest.approx(0.01)
    # Monthly should also exclude 35-day-old
    assert breakdown["monthly"]["cheap"] == pytest.approx(0.001)
    assert breakdown["total"] == pytest.approx(0.001 + 0.0008 + 0.05 + 0.01)


@pytest.mark.asyncio
async def test_snapshot_bankroll_creates_entry():
    await pnl.snapshot_bankroll()
    database = db.get_db()
    rows = list(database["bankroll"].rows)
    assert len(rows) == 1
    assert rows[0]["total_value"] > 0


@pytest.mark.asyncio
async def test_snapshot_bankroll_skips_if_recent():
    # First snapshot
    await pnl.snapshot_bankroll()
    database = db.get_db()
    assert len(list(database["bankroll"].rows)) == 1
    # Second call within 1 hour — should skip
    await pnl.snapshot_bankroll()
    assert len(list(database["bankroll"].rows)) == 1


def test_max_drawdown_computation():
    database = db.get_db()
    now = datetime.now(timezone.utc)
    # Simulate: 1000 → 1100 → 900 → 1050
    snapshots = [
        (now - timedelta(hours=4), 1000),
        (now - timedelta(hours=3), 1100),
        (now - timedelta(hours=2), 900),
        (now - timedelta(hours=1), 1050),
    ]
    for ts, val in snapshots:
        database["bankroll"].insert({
            "timestamp": ts.isoformat(),
            "total_value": val,
            "available_cash": val,
            "unrealized_pnl": 0,
            "realized_pnl_today": 0,
            "realized_pnl_total": 0,
        })
    dd = pnl._compute_max_drawdown(database)
    # Peak 1100, trough 900 → drawdown = 200/1100 ≈ 0.1818
    assert dd == pytest.approx(200 / 1100, rel=1e-3)
