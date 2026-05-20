"""Tests for core/db.py — all table creation and helper methods."""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import pytest

# DB isolation is handled by conftest.py's _isolate_db fixture (autouse=True)


def _get_db():
    import core.db as db_mod
    return db_mod.get_db()


class TestTableCreation:
    """Test that all tables are auto-created."""

    def test_all_tables_exist(self) -> None:
        db = _get_db()
        expected = {"trades", "positions", "signals", "bankroll", "llm_costs", "market_cache"}
        assert expected.issubset(set(db.table_names()))

    def test_ensure_tables_idempotent(self) -> None:
        import core.db as db_mod
        db_mod.ensure_tables()
        db_mod.ensure_tables()
        db = _get_db()
        assert "trades" in db.table_names()


class TestTradeHelpers:
    """Test trade CRUD operations."""

    def test_record_and_get_trade(self) -> None:
        from core.db import record_trade, get_open_trades
        record_trade(
            trade_id="t1",
            market_id="m1",
            token_id="tok1",
            side="BUY",
            price=0.65,
            size=10.0,
            status="PENDING",
        )
        trades = get_open_trades()
        assert len(trades) == 1
        assert trades[0]["id"] == "t1"
        assert trades[0]["side"] == "BUY"
        assert trades[0]["price"] == 0.65

    def test_update_trade_status(self) -> None:
        from core.db import record_trade, update_trade_status, get_open_trades
        record_trade("t2", "m1", "tok1", "BUY", 0.60, 5.0)
        update_trade_status("t2", "FILLED", fill_price=0.61, pnl=0.05)
        # Should no longer appear in open trades
        trades = get_open_trades()
        assert len(trades) == 0

        # Verify updated fields
        db = _get_db()
        row = db["trades"].get("t2")
        assert row["status"] == "FILLED"
        assert row["fill_price"] == 0.61
        assert row["pnl"] == 0.05

    def test_paper_trade_flag(self) -> None:
        from core.db import record_trade
        record_trade("t3", "m1", "tok1", "SELL", 0.70, 3.0, paper=True)
        db = _get_db()
        row = db["trades"].get("t3")
        assert row["paper"] == 1


class TestPositionHelpers:
    """Test position CRUD operations."""

    def test_upsert_and_get_position(self) -> None:
        from core.db import upsert_position, get_open_positions
        upsert_position("tok1", "m1", "Will X happen?", "BUY", 0.50, 10.0, 0.55)
        positions = get_open_positions()
        assert len(positions) == 1
        assert positions[0]["token_id"] == "tok1"
        assert positions[0]["unrealized_pnl"] == pytest.approx(0.50)  # (0.55-0.50)*10

    def test_upsert_updates_existing(self) -> None:
        from core.db import upsert_position, get_open_positions
        upsert_position("tok1", "m1", "Will X happen?", "BUY", 0.50, 10.0, 0.55)
        upsert_position("tok1", "m1", "Will X happen?", "BUY", 0.50, 10.0, 0.60)
        positions = get_open_positions()
        assert len(positions) == 1
        assert positions[0]["unrealized_pnl"] == pytest.approx(1.0)  # (0.60-0.50)*10

    def test_close_position(self) -> None:
        from core.db import upsert_position, close_position, get_open_positions
        upsert_position("tok1", "m1", "Q?", "BUY", 0.50, 10.0, 0.55)
        close_position("tok1")
        assert len(get_open_positions()) == 0


class TestSignalHelpers:
    """Test signal recording and retrieval."""

    def test_record_and_get_signals(self) -> None:
        from core.db import record_signal, get_latest_signals
        record_signal("m1", "news", 0.70, 0.8, "Breaking news", "gemini-flash")
        record_signal("m1", "polls", 0.65, 0.6, "Poll data", "gemini-flash")
        record_signal("m2", "news", 0.30, 0.5, "Other market", "gemini-flash")

        signals = get_latest_signals("m1")
        assert len(signals) == 2

    def test_signals_ordered_by_timestamp(self) -> None:
        from core.db import record_signal, get_latest_signals
        record_signal("m1", "src1", 0.5, 0.5, "first", "model1")
        record_signal("m1", "src2", 0.6, 0.6, "second", "model2")
        signals = get_latest_signals("m1")
        # Most recent first
        assert len(signals) == 2


class TestBankrollHelpers:
    """Test bankroll snapshots and P&L."""

    def test_snapshot_bankroll(self) -> None:
        from core.db import snapshot_bankroll
        snapshot_bankroll(1000.0, 800.0, 50.0, 10.0, 100.0)
        db = _get_db()
        rows = list(db["bankroll"].rows)
        assert len(rows) == 1
        assert rows[0]["total_value"] == 1000.0

    def test_get_daily_pnl(self) -> None:
        from core.db import record_trade, upsert_position, close_position, get_daily_pnl
        record_trade("t1", "m1", "tok1", "BUY_YES", 0.50, 10.0, status="FILLED", paper=True)
        upsert_position("tok1", "m1", "q", "BUY_YES", 0.50, 10.0, 0.50, paper=True)
        close_position("tok1", exit_price=1.0, realized_pnl=5.0)
        record_trade("t2", "m1", "tok2", "BUY_YES", 0.60, 5.0, status="FILLED", paper=True)
        upsert_position("tok2", "m1", "q", "BUY_YES", 0.60, 5.0, 0.60, paper=True)
        close_position("tok2", exit_price=0.20, realized_pnl=-2.0)
        assert get_daily_pnl() == pytest.approx(3.0)

    def test_get_total_pnl(self) -> None:
        from core.db import record_trade, upsert_position, close_position, get_total_pnl
        record_trade("t1", "m1", "tok1", "BUY_YES", 0.50, 10.0, status="FILLED", paper=True)
        upsert_position("tok1", "m1", "q", "BUY_YES", 0.50, 10.0, 0.50, paper=True)
        close_position("tok1", exit_price=1.0, realized_pnl=5.0)
        assert get_total_pnl() == pytest.approx(5.0)


class TestLLMCostHelpers:
    """Test LLM cost tracking."""

    def test_record_and_get_daily_cost(self) -> None:
        from core.db import record_llm_cost, get_daily_llm_cost
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        record_llm_cost(now, "gemini-flash", "summarize", 100, 50, 0.001)
        record_llm_cost(now, "claude-opus", "frontier", 200, 100, 0.05)
        assert get_daily_llm_cost() == pytest.approx(0.051)

    def test_get_monthly_cost(self) -> None:
        from core.db import record_llm_cost, get_monthly_llm_cost
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        record_llm_cost(now, "model", "task", 100, 50, 0.10)
        assert get_monthly_llm_cost() == pytest.approx(0.10)


class TestMarketCacheHelpers:
    """Test market cache operations."""

    def test_cache_and_retrieve_market(self) -> None:
        from core.db import cache_market, get_cached_market
        data = {"question": "Will X happen?", "liquidity": 5000}
        cache_market("cond1", data, category="politics")
        result = get_cached_market("cond1")
        assert result is not None
        assert result["data"]["question"] == "Will X happen?"
        assert result["category"] == "politics"

    def test_cache_miss_returns_none(self) -> None:
        from core.db import get_cached_market
        assert get_cached_market("nonexistent") is None

    def test_cache_upsert_overwrites(self) -> None:
        from core.db import cache_market, get_cached_market
        cache_market("cond1", {"v": 1})
        cache_market("cond1", {"v": 2})
        result = get_cached_market("cond1")
        assert result is not None
        assert result["data"]["v"] == 2
