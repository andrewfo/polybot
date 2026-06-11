"""Tests for the Phase 4 paper-run validation summary (core.db.get_paper_summary)."""

from datetime import datetime, timedelta, timezone

import pytest

from core.db import PRE_FRONTIER_GATE_SKIP_REASON, get_paper_summary

PRE_FIX_TS = "2026-05-18T12:00:00+00:00"  # before LEARNING_DATA_CUTOFF


def _get_db():
    import core.db as db_mod
    return db_mod.get_db()


def _ts(days_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _insert_closed_trade(d, tid, market_id, pnl, price=0.5, size=20.0,
                         ts=None, closed_at=None, paper=1):
    ts = ts or _ts(2)
    d["trades"].insert({
        "id": tid, "market_id": market_id, "token_id": f"tok_{tid}",
        "side": "BUY_YES", "price": price, "size": size, "timestamp": ts,
        "status": "FILLED", "fill_price": price, "pnl": pnl, "paper": paper,
        "closed_at": closed_at or ts, "exit_price": price + pnl / size,
        "close_reason": "take_profit" if pnl > 0 else "stop_loss",
    })


def _insert_frontier_decision(d, market_id, est, price, ts=None, skip_reason=""):
    d["frontier_decisions"].insert({
        "market_id": market_id, "estimated_prob": est, "effective_prob": est,
        "market_price": price, "edge": abs(est - price), "kelly_fraction": 0.1,
        "bet_size_usd": 10.0, "confidence": 0.8, "should_trade": 1,
        "skip_reason": skip_reason, "timestamp": ts or _ts(2),
    })


def _insert_resolved_calibration(d, market_id, source, pred, actual, ts=None):
    d["signal_calibration"].insert({
        "market_id": market_id, "signal_source": source,
        "predicted_probability": pred, "actual_outcome": actual,
        "market_question": "q", "timestamp": ts or _ts(2), "resolved_at": _ts(1),
    })


class TestEmptyWindow:
    def test_empty_db_not_ready(self):
        summary = get_paper_summary()
        assert summary["total_trades"] == 0
        assert summary["closed_trades"] == 0
        assert summary["win_rate"] == 0.0
        assert summary["net_pnl"] == 0.0
        assert summary["days_running"] == 0.0
        assert summary["profit_concentration"] is None
        assert summary["frontier_brier"] is None
        assert summary["ready_for_live"] is False
        assert "NOT READY" in summary["recommendation"]


class TestBasicStats:
    def test_pnl_win_rate_and_llm_netting(self):
        from core.db import record_llm_cost
        d = _get_db()
        _insert_closed_trade(d, "t1", "m1", pnl=4.0)
        _insert_closed_trade(d, "t2", "m2", pnl=2.0)
        _insert_closed_trade(d, "t3", "m3", pnl=-3.0)
        record_llm_cost(_ts(1), "anthropic/claude-opus-4-6", "decide", 100, 50, 1.5)

        summary = get_paper_summary()

        assert summary["total_trades"] == 3
        assert summary["closed_trades"] == 3
        assert summary["wins"] == 2
        assert summary["losses"] == 1
        assert summary["win_rate"] == pytest.approx(2 / 3, abs=1e-3)
        assert summary["gross_pnl"] == pytest.approx(3.0)
        assert summary["llm_cost"] == pytest.approx(1.5)
        assert summary["net_pnl"] == pytest.approx(1.5)
        assert summary["days_running"] == pytest.approx(2.0, abs=0.1)

    def test_avg_return_per_trade(self):
        d = _get_db()
        # $10 notional each (0.5 * 20): returns +40% and -20%
        _insert_closed_trade(d, "t1", "m1", pnl=4.0)
        _insert_closed_trade(d, "t2", "m2", pnl=-2.0)
        summary = get_paper_summary()
        assert summary["avg_return_per_trade"] == pytest.approx(0.10, abs=1e-3)

    def test_profit_concentration(self):
        d = _get_db()
        _insert_closed_trade(d, "t1", "m_big", pnl=6.0)
        _insert_closed_trade(d, "t2", "m_big", pnl=2.0)
        _insert_closed_trade(d, "t3", "m_small", pnl=2.0)
        summary = get_paper_summary()
        # m_big contributes 8 of 10 gross
        assert summary["top_market_id"] == "m_big"
        assert summary["profit_concentration"] == pytest.approx(0.8)
        assert summary["criteria"]["profit_concentration"]["passed"] is False

    def test_pre_fix_rows_excluded(self):
        from core.db import record_llm_cost
        d = _get_db()
        _insert_closed_trade(d, "t_pre", "m1", pnl=50.0, ts=PRE_FIX_TS, closed_at=PRE_FIX_TS)
        record_llm_cost(PRE_FIX_TS, "anthropic/claude-opus-4-6", "decide", 100, 50, 9.0)
        _insert_closed_trade(d, "t_post", "m2", pnl=1.0)

        summary = get_paper_summary()
        assert summary["closed_trades"] == 1
        assert summary["gross_pnl"] == pytest.approx(1.0)
        assert summary["llm_cost"] == 0.0

    def test_live_trades_excluded(self):
        d = _get_db()
        _insert_closed_trade(d, "t_live", "m1", pnl=5.0, paper=0)
        summary = get_paper_summary()
        assert summary["closed_trades"] == 0


class TestBrierComparison:
    def test_frontier_vs_market_brier(self):
        d = _get_db()
        # Market A resolved YES: frontier 0.8 (err 0.04), market 0.6 (err 0.16)
        _insert_frontier_decision(d, "mA", est=0.8, price=0.6)
        _insert_resolved_calibration(d, "mA", "resolution_crypto", 0.9, 1.0)
        # Market B resolved NO: frontier 0.3 (err 0.09), market 0.4 (err 0.16)
        _insert_frontier_decision(d, "mB", est=0.3, price=0.4)
        _insert_resolved_calibration(d, "mB", "resolution_crypto", 0.2, 0.0)

        summary = get_paper_summary()

        assert summary["brier_comparison_samples"] == 2
        assert summary["frontier_brier"] == pytest.approx(0.065, abs=1e-3)
        assert summary["market_price_brier"] == pytest.approx(0.16, abs=1e-3)
        # Per-signal Brier from calibration rows
        rc = summary["brier_by_signal"]["resolution_crypto"]
        assert rc["samples"] == 2
        assert rc["brier"] == pytest.approx(((0.9 - 1) ** 2 + 0.2**2) / 2, abs=1e-3)

    def test_pre_gate_skips_excluded(self):
        d = _get_db()
        _insert_frontier_decision(
            d, "mA", est=0.55, price=0.5, skip_reason=PRE_FRONTIER_GATE_SKIP_REASON
        )
        _insert_resolved_calibration(d, "mA", "resolution_crypto", 0.9, 1.0)
        summary = get_paper_summary()
        assert summary["brier_comparison_samples"] == 0
        assert summary["frontier_brier"] is None

    def test_latest_decision_per_market(self):
        d = _get_db()
        _insert_frontier_decision(d, "mA", est=0.9, price=0.5, ts=_ts(3))
        _insert_frontier_decision(d, "mA", est=0.6, price=0.5, ts=_ts(1))
        _insert_resolved_calibration(d, "mA", "resolution_crypto", 0.9, 1.0)
        summary = get_paper_summary()
        assert summary["brier_comparison_samples"] == 1
        # Uses the latest estimate (0.6): (0.6 - 1)^2 = 0.16
        assert summary["frontier_brier"] == pytest.approx(0.16, abs=1e-3)


class TestReadinessGate:
    def test_all_criteria_pass(self, monkeypatch):
        monkeypatch.setattr("config.settings.PAPER_RUN_MIN_DAYS", 1.0)
        monkeypatch.setattr("config.settings.PAPER_RUN_MIN_CLOSED_TRADES", 3)
        monkeypatch.setattr("config.settings.PAPER_RUN_MIN_BRIER_SAMPLES", 1)

        d = _get_db()
        _insert_closed_trade(d, "t1", "m1", pnl=4.0)
        _insert_closed_trade(d, "t2", "m2", pnl=4.0)
        _insert_closed_trade(d, "t3", "m3", pnl=4.0)
        _insert_closed_trade(d, "t4", "m4", pnl=4.0)
        _insert_closed_trade(d, "t5", "m5", pnl=4.0)
        _insert_frontier_decision(d, "m1", est=0.9, price=0.5)
        _insert_resolved_calibration(d, "m1", "resolution_crypto", 0.9, 1.0)

        summary = get_paper_summary()

        assert all(c["passed"] for c in summary["criteria"].values()), summary["criteria"]
        assert summary["ready_for_live"] is True
        assert "READY" in summary["recommendation"]

    def test_frontier_losing_to_market_adds_demotion_advice(self, monkeypatch):
        monkeypatch.setattr("config.settings.PAPER_RUN_MIN_BRIER_SAMPLES", 1)
        d = _get_db()
        # Frontier worse than market: resolved YES, frontier 0.3, market 0.7
        _insert_frontier_decision(d, "m1", est=0.3, price=0.7)
        _insert_resolved_calibration(d, "m1", "resolution_crypto", 0.9, 1.0)

        summary = get_paper_summary()

        assert summary["criteria"]["frontier_beats_market"]["passed"] is False
        assert "demoting the frontier" in summary["recommendation"]

    def test_insufficient_brier_samples_fails_criterion(self):
        d = _get_db()
        _insert_frontier_decision(d, "m1", est=0.9, price=0.5)
        _insert_resolved_calibration(d, "m1", "resolution_crypto", 0.9, 1.0)
        summary = get_paper_summary()
        # Default PAPER_RUN_MIN_BRIER_SAMPLES = 30 > 1 sample
        assert summary["criteria"]["frontier_beats_market"]["passed"] is False
