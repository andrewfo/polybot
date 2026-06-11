"""Tests for scripts/reset_learning_state.py — Phase 3 learning-state reset."""

from datetime import datetime, timezone

from scripts.reset_learning_state import (
    REGIME_TABLES,
    deactivate_overrides,
    reset_learning_state,
    tag_data_regimes,
)

CUTOFF = "2026-05-22T20:30:00+00:00"
PRE_TS = "2026-05-18T12:00:00+00:00"
POST_TS = "2026-06-01T12:00:00+00:00"


def _get_db():
    import core.db as db_mod
    return db_mod.get_db()


def _insert_override(d, parameter="KELLY_FRACTION", original=0.25, current=0.312, active=1):
    d["parameter_overrides"].insert({
        "parameter": parameter,
        "original_value": original,
        "current_value": current,
        "applied_at": PRE_TS,
        "source_report_ts": PRE_TS,
        "confidence": 0.8,
        "sample_count": 50,
        "reason": "92% win rate (optimistic pricing)",
        "active": active,
    })


class TestDeactivateOverrides:
    def test_deactivates_active_override(self):
        from core.db import get_active_overrides
        d = _get_db()
        _insert_override(d)
        assert get_active_overrides() == {"KELLY_FRACTION": 0.312}

        deactivated = deactivate_overrides()

        assert [r["parameter"] for r in deactivated] == ["KELLY_FRACTION"]
        assert get_active_overrides() == {}
        # Effective param falls back to the configured default
        from config.settings import get_effective_param
        assert get_effective_param("KELLY_FRACTION", 0.25) == 0.25

    def test_inactive_overrides_untouched(self):
        d = _get_db()
        _insert_override(d, active=0)
        assert deactivate_overrides() == []

    def test_no_overrides(self):
        assert deactivate_overrides() == []


class TestTagDataRegimes:
    def test_tags_pre_and_post_rows(self):
        d = _get_db()
        d["trades"].insert({
            "id": "t_pre", "market_id": "m", "token_id": "tok", "side": "BUY_YES",
            "price": 0.5, "size": 10.0, "timestamp": PRE_TS, "status": "FILLED",
            "paper": 1,
        })
        d["trades"].insert({
            "id": "t_post", "market_id": "m", "token_id": "tok", "side": "BUY_YES",
            "price": 0.5, "size": 10.0, "timestamp": POST_TS, "status": "FILLED",
            "paper": 1,
        })
        d["signal_calibration"].insert({
            "market_id": "m", "signal_source": "resolution_crypto",
            "predicted_probability": 0.7, "actual_outcome": 1.0,
            "market_question": "q", "timestamp": PRE_TS, "resolved_at": POST_TS,
        })

        counts = tag_data_regimes(CUTOFF)

        assert counts["trades"] == {"pre_fix": 1, "post_fix": 1}
        assert counts["signal_calibration"] == {"pre_fix": 1, "post_fix": 0}
        regimes = {
            row["id"]: row["data_regime"] for row in d["trades"].rows
        }
        assert regimes == {"t_pre": "pre_fix", "t_post": "post_fix"}

    def test_idempotent(self):
        d = _get_db()
        d["trades"].insert({
            "id": "t1", "market_id": "m", "token_id": "tok", "side": "BUY_YES",
            "price": 0.5, "size": 10.0, "timestamp": PRE_TS, "status": "FILLED",
            "paper": 1,
        })
        first = tag_data_regimes(CUTOFF)
        second = tag_data_regimes(CUTOFF)
        assert first["trades"] == second["trades"] == {"pre_fix": 1, "post_fix": 0}

    def test_covers_all_learning_tables(self):
        assert set(REGIME_TABLES) == {
            "trades", "frontier_decisions", "signal_calibration", "skipped_markets",
        }


class TestFullReset:
    def test_summary_shape(self):
        d = _get_db()
        _insert_override(d)
        summary = reset_learning_state(CUTOFF)
        assert summary["cutoff"] == CUTOFF
        assert summary["overrides_deactivated"] == ["KELLY_FRACTION"]
        assert set(summary["regime_counts"]) == set(REGIME_TABLES)
