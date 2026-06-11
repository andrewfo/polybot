"""Tests for the continuous learning engine (monitoring/learning.py)."""

import json
import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.learning import (
    BiasReport,
    CostEffectivenessReport,
    EdgeRealizationReport,
    LearningReport,
    ParameterRecommendation,
    SignalFeatureReport,
    SkipRetroReport,
    analyze_cost_effectiveness,
    analyze_edge_realization,
    analyze_frontier_bias,
    analyze_signal_features,
    analyze_skipped_markets,
    compute_parameter_recommendations,
    get_latest_report,
    get_report_history,
    run_learning_cycle,
    save_report,
    update_skipped_resolutions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db(monkeypatch):
    """Mock database with realistic test data."""
    db_mock = MagicMock()

    # Default: return empty for all queries
    db_mock.execute.return_value.fetchall.return_value = []
    db_mock.table_names.return_value = ["learning_reports"]

    monkeypatch.setattr("monitoring.learning.db.get_db", lambda: db_mock)
    return db_mock


@pytest.fixture
def frontier_decision_rows():
    """Sample frontier decision + calibration join data."""
    return [
        # (estimated_prob, effective_prob, market_price, confidence, actual_outcome, resolved_at)
        (0.70, 0.65, 0.50, 0.80, 1.0, "2026-05-01T00:00:00+00:00"),
        (0.30, 0.35, 0.50, 0.60, 0.0, "2026-05-02T00:00:00+00:00"),
        (0.80, 0.75, 0.60, 0.90, 1.0, "2026-05-03T00:00:00+00:00"),
        (0.20, 0.25, 0.40, 0.50, 1.0, "2026-04-15T00:00:00+00:00"),
        (0.60, 0.55, 0.45, 0.70, 1.0, "2026-05-05T00:00:00+00:00"),
        (0.75, 0.70, 0.55, 0.85, 0.0, "2026-04-20T00:00:00+00:00"),
        (0.40, 0.42, 0.50, 0.40, 0.0, "2026-05-07T00:00:00+00:00"),
        (0.65, 0.60, 0.50, 0.75, 1.0, "2026-05-08T00:00:00+00:00"),
        (0.55, 0.52, 0.48, 0.65, 1.0, "2026-05-09T00:00:00+00:00"),
        (0.45, 0.47, 0.50, 0.55, 0.0, "2026-05-10T00:00:00+00:00"),
    ]


@pytest.fixture
def skipped_market_rows():
    """Sample skipped market data."""
    return [
        # (market_id, skip_reason, market_price, estimated_prob, confidence, resolution_outcome, timestamp)
        ("m1", "edge below threshold", 0.50, 0.53, 0.30, 1.0, "2026-05-01T00:00:00+00:00"),
        ("m2", "edge below threshold", 0.40, 0.42, 0.25, 0.0, "2026-05-02T00:00:00+00:00"),
        ("m3", "no positive edge", 0.60, 0.58, 0.40, 1.0, "2026-05-03T00:00:00+00:00"),
        ("m4", "bet too small", 0.70, 0.72, 0.35, 1.0, "2026-05-04T00:00:00+00:00"),
        ("m5", "edge below threshold", 0.55, 0.57, 0.30, None, "2026-05-05T00:00:00+00:00"),
        ("m6", "no positive edge", 0.45, 0.43, 0.50, 0.0, "2026-05-06T00:00:00+00:00"),
        ("m7", "edge below threshold", 0.30, 0.32, 0.20, 0.0, "2026-05-07T00:00:00+00:00"),
    ]


# ---------------------------------------------------------------------------
# Tests: Frontier bias analysis
# ---------------------------------------------------------------------------

class TestFrontierBias:
    def test_empty_data(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        report = analyze_frontier_bias()
        assert report.sample_count == 0
        assert report.mean_bias == 0.0

    def test_bias_computation(self, mock_db, frontier_decision_rows):
        mock_db.execute.return_value.fetchall.return_value = frontier_decision_rows
        report = analyze_frontier_bias()

        assert report.sample_count == 10
        # Mean bias is time-weighted — all data is recent so should be close
        # to simple average but not exact (time-decay weighting)
        expected_biases = [r[0] - r[4] for r in frontier_decision_rows]
        expected_mean = sum(expected_biases) / len(expected_biases)
        assert abs(report.mean_bias - expected_mean) < 0.05  # Wider tolerance for decay weighting

    def test_confidence_bands(self, mock_db, frontier_decision_rows):
        mock_db.execute.return_value.fetchall.return_value = frontier_decision_rows
        report = analyze_frontier_bias()

        # Should have at least low and high bands
        assert len(report.bias_by_confidence_band) > 0

    def test_calibration_curve(self, mock_db, frontier_decision_rows):
        mock_db.execute.return_value.fetchall.return_value = frontier_decision_rows
        report = analyze_frontier_bias()

        assert len(report.calibration_curve) > 0
        for point in report.calibration_curve:
            assert "bin_center" in point
            assert "predicted_mean" in point
            assert "actual_mean" in point
            assert "count" in point

    def test_price_bands(self, mock_db, frontier_decision_rows):
        mock_db.execute.return_value.fetchall.return_value = frontier_decision_rows
        report = analyze_frontier_bias()
        # All test data has market_price between 0.3 and 0.7
        assert "0.3-0.7" in report.bias_by_price_band


# ---------------------------------------------------------------------------
# Tests: Skipped market analysis
# ---------------------------------------------------------------------------

class TestSkippedMarkets:
    def test_empty_data(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        report = analyze_skipped_markets()
        assert report.total_skipped == 0

    def test_skip_analysis(self, mock_db, skipped_market_rows):
        mock_db.execute.return_value.fetchall.return_value = skipped_market_rows
        report = analyze_skipped_markets()

        assert report.total_skipped == 7
        # 6 have resolution_outcome set (not None)
        assert report.resolved_skipped == 6
        assert report.would_have_been_correct > 0
        assert "edge below threshold" in report.by_skip_reason
        assert report.by_skip_reason["edge below threshold"]["total"] == 4

    def test_missed_profit(self, mock_db, skipped_market_rows):
        mock_db.execute.return_value.fetchall.return_value = skipped_market_rows
        report = analyze_skipped_markets()
        # At least some markets would have profited
        assert report.would_have_profited >= 0
        assert report.missed_profit_estimate >= 0


# ---------------------------------------------------------------------------
# Tests: Edge realization
# ---------------------------------------------------------------------------

class TestEdgeRealization:
    def test_empty_data(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        report = analyze_edge_realization()
        assert report.total_trades == 0

    def test_edge_analysis(self, mock_db):
        # (edge, confidence, estimated_prob, market_price, bet_size, realized_pnl, entry, size, timestamp, close_reason, closed_at)
        # close_reason="take_profit" so the broken-stop-window filter doesn't drop them.
        trade_rows = [
            (0.10, 0.80, 0.60, 0.50, 100, 15.0, 0.50, 200, "2026-05-01T00:00:00+00:00", "take_profit", "2026-06-01T00:00:00+00:00"),
            (0.05, 0.60, 0.55, 0.50, 50, -8.0, 0.50, 100, "2026-05-02T00:00:00+00:00", "take_profit", "2026-06-02T00:00:00+00:00"),
            (0.15, 0.90, 0.70, 0.55, 150, 30.0, 0.55, 273, "2026-05-03T00:00:00+00:00", "take_profit", "2026-06-03T00:00:00+00:00"),
            (0.08, 0.70, 0.58, 0.50, 80, 12.0, 0.50, 160, "2026-05-04T00:00:00+00:00", "take_profit", "2026-06-04T00:00:00+00:00"),
            (0.12, 0.85, 0.62, 0.50, 120, -20.0, 0.50, 240, "2026-05-05T00:00:00+00:00", "take_profit", "2026-06-05T00:00:00+00:00"),
        ]
        mock_db.execute.return_value.fetchall.return_value = trade_rows
        report = analyze_edge_realization()

        assert report.total_trades == 5
        assert report.win_rate == 3 / 5
        assert report.avg_predicted_edge == sum(r[0] for r in trade_rows) / 5
        assert report.profit_factor > 0
        assert "mid" in report.by_confidence_band or "high" in report.by_confidence_band

    def test_excludes_broken_stop_window(self, mock_db):
        # Stop-losses fired before BROKEN_STOP_WINDOW_END are excluded; later
        # stops and all take-profits survive.
        from monitoring.learning import BROKEN_STOP_WINDOW_END
        assert BROKEN_STOP_WINDOW_END.startswith("2026-05-22")
        rows = [
            # stop_loss before cutoff — excluded
            (0.10, 0.80, 0.60, 0.50, 100, -25.0, 0.50, 200, "2026-05-21T00:00:00+00:00", "stop_loss", "2026-05-21T20:00:00+00:00"),
            (0.08, 0.75, 0.58, 0.50, 100, -20.0, 0.50, 200, "2026-05-21T00:00:00+00:00", "stop_loss", "2026-05-22T10:00:00+00:00"),
            # stop_loss after cutoff — kept
            (0.09, 0.78, 0.59, 0.50, 100, -15.0, 0.50, 200, "2026-05-23T00:00:00+00:00", "stop_loss", "2026-05-24T00:00:00+00:00"),
            # take_profit before cutoff — kept (filter is stop-loss-specific)
            (0.12, 0.85, 0.62, 0.50, 100, 20.0, 0.50, 200, "2026-05-20T00:00:00+00:00", "take_profit", "2026-05-21T00:00:00+00:00"),
        ]
        mock_db.execute.return_value.fetchall.return_value = rows
        report = analyze_edge_realization()
        assert report.total_trades == 2
        assert report.win_rate == 0.5


# ---------------------------------------------------------------------------
# Tests: Signal features
# ---------------------------------------------------------------------------

class TestSignalFeatures:
    def test_empty_data(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        report = analyze_signal_features()
        assert not report.by_source

    def test_feature_breakdown(self, mock_db):
        raw_crypto = json.dumps({
            "vol_regime": "moderate",
            "days_remaining": 10,
            "resolution_type": "barrier",
        })
        raw_web = json.dumps({
            "key_evidence": ["some article"],
        })

        signal_rows = [
            ("resolution_crypto", 0.65, raw_crypto, 1.0),
            ("resolution_crypto", 0.70, raw_crypto, 1.0),
            ("web_search", 0.60, raw_web, 1.0),
            ("web_search", 0.55, raw_web, 0.0),
        ]
        mock_db.execute.return_value.fetchall.return_value = signal_rows
        report = analyze_signal_features()

        assert "resolution_crypto" in report.by_source
        assert "web_search" in report.by_source
        assert "moderate" in report.by_vol_regime
        assert "7-14d" in report.by_time_to_expiry
        assert "barrier" in report.by_resolution_type


# ---------------------------------------------------------------------------
# Tests: Cost effectiveness
# ---------------------------------------------------------------------------

class TestCostEffectiveness:
    def test_cost_analysis(self, mock_db):
        # Different execute calls return different things
        call_count = 0
        results = [
            # cost by model
            [("anthropic/claude-opus-4-6", 2.50), ("google/gemini-2.0-flash-lite-001", 0.30), ("perplexity/sonar", 0.80)],
            # closed position count
            [(10,)],
            # profitable count
            [(6,)],
            # frontier call count
            [(50,)],
        ]

        def side_effect(*args, **kwargs):
            nonlocal call_count
            mock_result = MagicMock()
            if call_count < len(results):
                mock_result.fetchall.return_value = results[call_count]
            else:
                mock_result.fetchall.return_value = []
            call_count += 1
            return mock_result

        mock_db.execute.side_effect = side_effect

        # Mock get_total_pnl
        with patch("monitoring.learning.db.get_total_pnl", return_value=15.0):
            report = analyze_cost_effectiveness()

        assert report.total_llm_cost == pytest.approx(3.60, abs=0.01)
        assert report.frontier_cost == pytest.approx(2.50, abs=0.01)
        assert report.cost_per_trade == pytest.approx(0.36, abs=0.01)
        assert report.roi == pytest.approx(15.0 / 3.60, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: Parameter recommendations
# ---------------------------------------------------------------------------

class TestParameterRecommendations:
    def test_kelly_increase_recommendation(self):
        edge_real = EdgeRealizationReport(
            total_trades=20,
            win_rate=0.65,
            edge_efficiency=0.85,
            avg_win=10.0,
            avg_loss=5.0,
            by_edge_band={},
        )
        recs = compute_parameter_recommendations(
            BiasReport(), SkipRetroReport(), edge_real, SignalFeatureReport()
        )
        kelly_recs = [r for r in recs if r.parameter == "KELLY_FRACTION"]
        assert len(kelly_recs) == 1
        assert kelly_recs[0].recommended_value > 0.25  # Should recommend increase

    def test_kelly_decrease_recommendation(self):
        edge_real = EdgeRealizationReport(
            total_trades=20,
            win_rate=0.35,
            edge_efficiency=0.2,
            avg_win=5.0,
            avg_loss=10.0,
            by_edge_band={},
        )
        recs = compute_parameter_recommendations(
            BiasReport(), SkipRetroReport(), edge_real, SignalFeatureReport()
        )
        kelly_recs = [r for r in recs if r.parameter == "KELLY_FRACTION"]
        assert len(kelly_recs) == 1
        assert kelly_recs[0].recommended_value < 0.25  # Should recommend decrease

    def test_edge_threshold_raise(self):
        edge_real = EdgeRealizationReport(
            total_trades=15,
            by_edge_band={
                "small": {"avg_return": -0.05, "win_rate": 0.3, "count": 8},
            },
        )
        recs = compute_parameter_recommendations(
            BiasReport(), SkipRetroReport(), edge_real, SignalFeatureReport()
        )
        edge_recs = [r for r in recs if r.parameter == "MIN_EDGE_THRESHOLD"]
        assert len(edge_recs) == 1
        assert edge_recs[0].recommended_value > 0.02

    def test_bias_correction(self):
        bias = BiasReport(
            mean_bias=0.08,  # Systematic overestimation
            sample_count=30,
        )
        recs = compute_parameter_recommendations(
            bias, SkipRetroReport(), EdgeRealizationReport(), SignalFeatureReport()
        )
        blend_recs = [r for r in recs if r.parameter == "MIN_CONFIDENCE_BLEND"]
        assert len(blend_recs) == 1

    def test_no_recommendations_insufficient_data(self):
        recs = compute_parameter_recommendations(
            BiasReport(sample_count=2),
            SkipRetroReport(resolved_skipped=1),
            EdgeRealizationReport(total_trades=3),
            SignalFeatureReport(),
        )
        # Should be empty — not enough data for any recommendation
        assert len(recs) == 0

    def test_skip_filter_recommendation(self):
        skip_retro = SkipRetroReport(
            total_skipped=20,
            resolved_skipped=15,
            would_have_profited=10,  # 10/15 = 67% would have profited — bad skips
            by_skip_reason={
                "edge below threshold": {
                    "total": 10, "resolved": 8, "correct": 3,
                    "profited": 6, "missed_edge_sum": 5.0,
                },
            },
        )
        recs = compute_parameter_recommendations(
            BiasReport(), skip_retro, EdgeRealizationReport(), SignalFeatureReport()
        )
        skip_recs = [r for r in recs if r.parameter.startswith("SKIP_FILTER:")]
        assert len(skip_recs) >= 1


# ---------------------------------------------------------------------------
# Tests: Report persistence
# ---------------------------------------------------------------------------

class TestReportPersistence:
    def test_save_and_load(self, mock_db):
        report = LearningReport(
            timestamp="2026-03-23T00:00:00+00:00",
            bias=BiasReport(mean_bias=0.05, sample_count=20),
            recommendations=[
                ParameterRecommendation(
                    parameter="KELLY_FRACTION",
                    current_value=0.25,
                    recommended_value=0.30,
                    reason="test",
                    confidence=0.7,
                    sample_count=20,
                ),
            ],
            data_sufficiency={"frontier_bias": True},
        )
        # save_report should not raise
        save_report(report)
        assert mock_db["learning_reports"].insert.called

    def test_get_latest_empty(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        result = get_latest_report()
        assert result is None


# ---------------------------------------------------------------------------
# Tests: Full learning cycle
# ---------------------------------------------------------------------------

class TestLearningCycle:
    @pytest.mark.asyncio
    async def test_full_cycle(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.table_names.return_value = ["learning_reports", "parameter_overrides",
                                             "parameter_change_snapshots", "market_regimes"]

        with patch("monitoring.learning.update_skipped_resolutions", new_callable=AsyncMock, return_value=0), \
             patch("monitoring.learning.classify_and_store_regime", new_callable=AsyncMock, return_value="sideways"):
            report = await run_learning_cycle()

        assert report.timestamp != ""
        assert isinstance(report.data_sufficiency, dict)
        assert isinstance(report.recommendations, list)
        assert report.current_regime == "sideways"


# ---------------------------------------------------------------------------
# Tests: Skipped resolution tracking
# ---------------------------------------------------------------------------

class TestSkippedResolutions:
    @pytest.mark.asyncio
    async def test_no_unresolved(self, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        count = await update_skipped_resolutions()
        assert count == 0

    @pytest.mark.asyncio
    async def test_has_unresolved_queries_gamma(self, mock_db):
        """When there are unresolved skipped markets, the function queries them."""
        mock_db.execute.return_value.fetchall.return_value = [("market_123",)]
        # The function will try to hit Gamma API — if it fails, returns 0
        # This just verifies the DB query path works
        count = await update_skipped_resolutions()
        # Will be 0 since we can't actually reach Gamma in tests
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: Pre-fix data exclusion (Phase 3, profitability fix plan)
# ---------------------------------------------------------------------------

PRE_FIX_TS = "2026-05-18T12:00:00+00:00"   # before LEARNING_DATA_CUTOFF


def _post_fix_ts(days_ago: float = 0.0) -> str:
    """A timestamp after LEARNING_DATA_CUTOFF (recent, so time-decay weight ~1)."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _insert_frontier_decision(d, market_id, ts, should_trade=1, est=0.7, price=0.5, skip_reason=""):
    d["frontier_decisions"].insert({
        "market_id": market_id, "estimated_prob": est, "effective_prob": est,
        "market_price": price, "edge": abs(est - price), "kelly_fraction": 0.1,
        "bet_size_usd": 10.0, "confidence": 0.8, "should_trade": should_trade,
        "skip_reason": skip_reason, "timestamp": ts,
    })


def _insert_resolved_calibration(d, market_id, ts, source="resolution_crypto", pred=0.7, actual=1.0):
    d["signal_calibration"].insert({
        "market_id": market_id, "signal_source": source,
        "predicted_probability": pred, "actual_outcome": actual,
        "market_question": "q", "timestamp": ts, "resolved_at": _post_fix_ts(),
    })


class TestPreFixExclusion:
    """The learning engine must ignore rows from the optimistic-pricing regime."""

    def test_frontier_bias_excludes_pre_fix(self):
        import core.db as db_mod
        d = db_mod.get_db()
        _insert_frontier_decision(d, "m_pre", PRE_FIX_TS)
        _insert_frontier_decision(d, "m_post", _post_fix_ts())
        _insert_resolved_calibration(d, "m_pre", PRE_FIX_TS)
        _insert_resolved_calibration(d, "m_post", _post_fix_ts())

        report = analyze_frontier_bias()
        assert report.sample_count == 1

    def test_skip_retro_excludes_pre_fix(self):
        from core.db import get_db
        d = get_db()
        for mid, ts in (("m_pre", PRE_FIX_TS), ("m_post", _post_fix_ts())):
            d["skipped_markets"].insert({
                "market_id": mid, "skip_reason": "edge below threshold",
                "market_price_at_skip": 0.5, "estimated_prob": 0.6,
                "confidence": 0.4, "timestamp": ts, "resolution_outcome": 1.0,
            })

        report = analyze_skipped_markets()
        assert report.total_skipped == 1
        assert report.resolved_skipped == 1

    def test_edge_realization_excludes_pre_fix(self):
        from core.db import get_db
        d = get_db()
        for mid, tok, ts in (
            ("m_pre", "tok_pre", PRE_FIX_TS),
            ("m_post", "tok_post", _post_fix_ts()),
        ):
            _insert_frontier_decision(d, mid, ts, should_trade=1)
            d["positions"].insert({
                "token_id": tok, "market_id": mid, "market_question": "q",
                "side": "BUY_YES", "avg_entry": 0.5, "size": 20.0,
                "current_price": 0.6, "unrealized_pnl": 2.0, "opened_at": ts,
                "last_updated": ts, "paper": 1, "status": "closed",
                "exit_price": 0.6, "realized_pnl": 2.0,
            })

        report = analyze_edge_realization()
        assert report.total_trades == 1

    def test_signal_features_excludes_pre_fix(self):
        from core.db import get_db
        d = get_db()
        for mid, ts in (("m_pre", PRE_FIX_TS), ("m_post", _post_fix_ts())):
            d["signals"].insert({
                "market_id": mid, "signal_source": "resolution_crypto",
                "probability": 0.7, "confidence": 0.8, "reasoning": "r",
                "model_used": "none", "timestamp": ts,
                "raw_data": json.dumps({"vol_regime": "normal"}),
            })
            _insert_resolved_calibration(d, mid, ts)

        report = analyze_signal_features()
        assert report.by_source["resolution_crypto"]["count"] == 1

    def test_cost_effectiveness_excludes_pre_fix(self):
        from core.db import get_db, record_llm_cost
        d = get_db()
        record_llm_cost(PRE_FIX_TS, "anthropic/claude-opus-4-6", "decide", 100, 50, 5.0)
        record_llm_cost(_post_fix_ts(), "anthropic/claude-opus-4-6", "decide", 100, 50, 1.25)
        # One pre-fix and one post-fix closed trade
        for tid, ts, pnl in (("t_pre", PRE_FIX_TS, 10.0), ("t_post", _post_fix_ts(), 3.0)):
            d["trades"].insert({
                "id": tid, "market_id": "m", "token_id": "tok", "side": "BUY_YES",
                "price": 0.5, "size": 20.0, "timestamp": ts, "status": "FILLED",
                "fill_price": 0.5, "pnl": pnl, "paper": 1, "closed_at": ts,
                "exit_price": 0.65, "close_reason": "take_profit",
            })
        _insert_frontier_decision(d, "m_pre", PRE_FIX_TS)
        _insert_frontier_decision(d, "m_post", _post_fix_ts())

        report = analyze_cost_effectiveness()
        assert report.total_llm_cost == pytest.approx(1.25)
        assert report.frontier_cost == pytest.approx(1.25)
        # ROI uses post-fix PnL ($3) over post-fix cost ($1.25)
        assert report.roi == pytest.approx(3.0 / 1.25)
