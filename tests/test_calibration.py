"""Tests for signals/calibration.py — dynamic source multiplier calibration."""

from unittest.mock import MagicMock, patch

import pytest

from signals.calibration import (
    BASELINE_BRIER,
    DEFAULT_MULTIPLIERS,
    ProviderCalibration,
    get_dynamic_multipliers,
    get_multiplier_dict,
    get_provider_brier_scores,
    record_prediction,
    record_resolution,
)


# ---------------------------------------------------------------------------
# record_prediction / record_resolution
# ---------------------------------------------------------------------------

class TestRecordPrediction:
    def test_inserts_row(self) -> None:
        mock_table = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_table)
        mock_db.execute.return_value.fetchone.return_value = None  # no open prediction

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            record_prediction(
                market_id="mkt_1",
                signal_source="web_search",
                predicted_probability=0.65,
                market_question="Will BTC hit $100k?",
            )

        mock_table.insert.assert_called_once()
        row = mock_table.insert.call_args[0][0]
        assert row["market_id"] == "mkt_1"
        assert row["signal_source"] == "web_search"
        assert row["predicted_probability"] == 0.65
        assert row["actual_outcome"] is None

    def test_updates_open_prediction_instead_of_duplicating(self) -> None:
        """Re-analysis of the same market updates the open row — no duplicate
        calibration samples from 30-minute churn."""
        mock_table = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_table)
        mock_db.execute.return_value.fetchone.return_value = (42,)  # open row id

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            record_prediction("mkt_1", "onchain_flow", 0.71)

        mock_table.insert.assert_not_called()
        # Second execute call is the UPDATE
        update_sql = mock_db.execute.call_args[0][0]
        assert "UPDATE signal_calibration" in update_sql
        params = mock_db.execute.call_args[0][1]
        assert params[0] == 0.71
        assert params[2] == 42

    def test_handles_db_error(self) -> None:
        with patch("signals.calibration.db.get_db", side_effect=Exception("DB error")):
            # Should not raise
            record_prediction("mkt_1", "web_search", 0.65)


class TestRecordResolution:
    def test_updates_rows(self) -> None:
        mock_db = MagicMock()

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            record_resolution("mkt_1", 1.0)

        mock_db.execute.assert_called_once()
        sql = mock_db.execute.call_args[0][0]
        assert "UPDATE signal_calibration" in sql
        params = mock_db.execute.call_args[0][1]
        assert params[0] == 1.0  # actual_outcome
        assert params[2] == "mkt_1"  # market_id


# ---------------------------------------------------------------------------
# get_provider_brier_scores
# ---------------------------------------------------------------------------

class TestBrierScores:
    def test_computes_correctly(self) -> None:
        from datetime import datetime, timezone
        mock_db = MagicMock()
        # Simulate: web_search predicted 0.8, actual was 1.0 → brier = 0.04
        #           web_search predicted 0.6, actual was 0.0 → brier = 0.36
        #           resolution_crypto predicted 0.9, actual was 1.0 → brier = 0.01
        # Include resolved_at timestamps (recent = weight ~1.0)
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute.return_value.fetchall.return_value = [
            ("web_search", 0.8, 1.0, now),
            ("web_search", 0.6, 0.0, now),
            ("resolution_crypto", 0.9, 1.0, now),
        ]

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            scores = get_provider_brier_scores()

        assert "web_search" in scores
        assert "resolution_crypto" in scores
        # web_search: weighted mean((0.8-1)^2, (0.6-0)^2) ≈ 0.20 (weights ~1.0 for recent)
        assert abs(scores["web_search"][0] - 0.20) < 0.01
        assert scores["web_search"][1] == 2
        # resolution_crypto: (0.9-1)^2 = 0.01
        assert abs(scores["resolution_crypto"][0] - 0.01) < 0.01
        assert scores["resolution_crypto"][1] == 1

    def test_empty_data(self) -> None:
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            scores = get_provider_brier_scores()

        assert scores == {}

    def test_time_decay_weights(self) -> None:
        """Older predictions should have lower weight (exponential decay)."""
        from datetime import datetime, timezone, timedelta
        import math

        mock_db = MagicMock()
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=45)).isoformat()  # 45 days ago → weight ≈ 0.37
        recent_time = now.isoformat()  # now → weight ≈ 1.0

        # Two predictions with same brier but different ages
        mock_db.execute.return_value.fetchall.return_value = [
            ("web_search", 0.8, 1.0, recent_time),   # brier=0.04, weight≈1.0
            ("web_search", 0.2, 1.0, old_time),       # brier=0.64, weight≈0.37
        ]

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            scores = get_provider_brier_scores()

        # Weighted average should be closer to 0.04 (recent) than 0.64 (old)
        assert scores["web_search"][0] < 0.25  # Much less than unweighted mean of 0.34


# ---------------------------------------------------------------------------
# get_dynamic_multipliers
# ---------------------------------------------------------------------------

class TestDynamicMultipliers:
    def test_defaults_when_insufficient_data(self) -> None:
        """With fewer than MIN_CALIBRATION_SAMPLES, use defaults."""
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "web_search": (0.15, 5),           # Only 5 samples (< 20 minimum)
            "resolution_crypto": (0.10, 3),
        }):
            result = get_dynamic_multipliers()

        for source, cal in result.items():
            assert cal.is_default is True
            assert cal.multiplier == DEFAULT_MULTIPLIERS[source]

    def test_dynamic_when_sufficient_data(self) -> None:
        """With enough samples, compute dynamic multipliers."""
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "web_search": (0.20, 30),
            "resolution_crypto": (0.10, 25),
            "prediction_markets": (0.15, 22),
        }):
            result = get_dynamic_multipliers()

        # avg_brier = (0.20 + 0.10 + 0.15) / 3 = 0.15
        # resolution_crypto ratio = 0.15 / 0.10 = 1.5 → mult = 2.5 * 1.5 = 3.75
        # prediction_markets ratio = 0.15 / 0.15 = 1.0 → mult = 1.8 * 1.0 = 1.8
        # web_search is benched (default 0) and brier 0.20 misses the
        # earn-back threshold (< 0.20) → stays at 0
        ws = result["web_search"]
        assert ws.multiplier == 0.0

        rc = result["resolution_crypto"]
        assert rc.is_default is False
        assert abs(rc.multiplier - 3.75) < 0.01

        pm = result["prediction_markets"]
        assert pm.is_default is False
        assert abs(pm.multiplier - 1.8) < 0.01

    def test_clamping_prevents_extreme_multipliers(self) -> None:
        """Multiplier ratio clamped to [0.5, 2.0]."""
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "web_search": (0.30, 25),           # Very bad
            "resolution_crypto": (0.001, 25),   # Near-perfect
            "prediction_markets": (0.15, 25),
        }):
            result = get_dynamic_multipliers()

        # resolution_crypto: ratio = avg/0.001 → capped at 2.0
        rc = result["resolution_crypto"]
        assert rc.multiplier == 2.0 * DEFAULT_MULTIPLIERS["resolution_crypto"]

        # web_search: benched and brier 0.30 → no earn-back, stays at 0
        ws = result["web_search"]
        assert ws.multiplier == 0.0

    def test_mixed_sufficient_and_insufficient(self) -> None:
        """Some providers have enough data, others don't."""
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "web_search": (0.20, 30),           # Sufficient (but benched)
            "resolution_crypto": (0.10, 5),     # Insufficient (< 20)
            "prediction_markets": (0.15, 25),   # Sufficient
        }):
            result = get_dynamic_multipliers()

        # resolution_crypto should use default
        assert result["resolution_crypto"].is_default is True
        assert result["resolution_crypto"].multiplier == DEFAULT_MULTIPLIERS["resolution_crypto"]

        # prediction_markets should be dynamic; benched web_search stays at 0
        assert result["web_search"].multiplier == 0.0
        assert result["prediction_markets"].is_default is False


class TestBenchedEarnBack:
    """Benched sources (default weight 0) regain weight only via earn-back."""

    def test_earns_back_with_good_brier_and_samples(self) -> None:
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "onchain_flow": (0.12, 35),         # Beats 0.20 over 30+ samples
            "resolution_crypto": (0.10, 25),
            "prediction_markets": (0.15, 25),
        }):
            result = get_dynamic_multipliers()

        of = result["onchain_flow"]
        assert of.is_default is False
        assert of.multiplier > 0
        # ratio = avg_brier / 0.12 clamped to [0.5, 2.0], base weight 1.0
        assert 0.5 <= of.multiplier <= 2.0

    def test_no_earn_back_below_sample_minimum(self) -> None:
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "onchain_flow": (0.10, 29),         # Great brier, one sample short
            "resolution_crypto": (0.10, 25),
            "prediction_markets": (0.15, 25),
        }):
            result = get_dynamic_multipliers()

        assert result["onchain_flow"].multiplier == 0.0
        assert result["onchain_flow"].is_default is True

    def test_no_earn_back_with_weak_brier(self) -> None:
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "onchain_flow": (0.24, 100),        # Plenty of samples, still noise
            "resolution_crypto": (0.10, 25),
            "prediction_markets": (0.15, 25),
        }):
            result = get_dynamic_multipliers()

        assert result["onchain_flow"].multiplier == 0.0

    def test_benched_with_no_data_stays_at_zero(self) -> None:
        with patch("signals.calibration.get_provider_brier_scores", return_value={}):
            result = get_dynamic_multipliers()

        assert result["onchain_flow"].multiplier == 0.0
        assert result["web_search"].multiplier == 0.0


class TestGetMultiplierDict:
    def test_returns_simple_dict(self) -> None:
        with patch("signals.calibration.get_provider_brier_scores", return_value={}):
            result = get_multiplier_dict()

        assert isinstance(result, dict)
        assert "resolution_crypto" in result
        assert "web_search" in result
        assert "prediction_markets" in result


# ---------------------------------------------------------------------------
# Calibration integration: condition_id fix (Change 1)
# ---------------------------------------------------------------------------

class TestCalibrationConditionId:
    def test_record_prediction_uses_condition_id(self) -> None:
        """Verify record_prediction stores condition_id, not question text."""
        mock_table = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_table)
        mock_db.execute.return_value.fetchone.return_value = None  # no open prediction

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            record_prediction(
                market_id="0x1234abcd",
                signal_source="web_search",
                predicted_probability=0.65,
                market_question="Will BTC hit $100k?",
            )

        row = mock_table.insert.call_args[0][0]
        assert row["market_id"] == "0x1234abcd"  # condition_id, not question

    def test_record_resolution_matches_condition_id(self) -> None:
        """Verify record_resolution updates by condition_id."""
        mock_db = MagicMock()

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            record_resolution("0x1234abcd", 1.0)

        params = mock_db.execute.call_args[0][1]
        assert params[2] == "0x1234abcd"  # market_id matches condition_id


# ---------------------------------------------------------------------------
# Pre-fix data exclusion (Phase 3, profitability fix plan)
# ---------------------------------------------------------------------------

class TestBrierScoresCutoff:
    def test_pre_fix_predictions_excluded(self) -> None:
        """Predictions made before LEARNING_DATA_CUTOFF must not count toward
        Brier scores (they came from old signal code and churn duplicates)."""
        from datetime import datetime, timezone
        import core.db as db_mod

        d = db_mod.get_db()
        now = datetime.now(timezone.utc).isoformat()
        pre_fix_ts = "2026-05-18T12:00:00+00:00"
        for ts in (pre_fix_ts, now):
            d["signal_calibration"].insert({
                "market_id": f"mkt_{ts[:10]}", "signal_source": "onchain_flow",
                "predicted_probability": 0.8, "actual_outcome": 1.0,
                "market_question": "q", "timestamp": ts, "resolved_at": now,
            })

        scores = get_provider_brier_scores()

        # Only the post-cutoff prediction counts
        assert scores["onchain_flow"][1] == 1
