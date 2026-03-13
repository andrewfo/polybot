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
        mock_db = MagicMock()
        # Simulate: web_search predicted 0.8, actual was 1.0 → brier = 0.04
        #           web_search predicted 0.6, actual was 0.0 → brier = 0.36
        #           resolution_crypto predicted 0.9, actual was 1.0 → brier = 0.01
        mock_db.execute.return_value.fetchall.return_value = [
            ("web_search", 0.8, 1.0),
            ("web_search", 0.6, 0.0),
            ("resolution_crypto", 0.9, 1.0),
        ]

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            scores = get_provider_brier_scores()

        assert "web_search" in scores
        assert "resolution_crypto" in scores
        # web_search: mean((0.8-1)^2, (0.6-0)^2) = mean(0.04, 0.36) = 0.20
        assert abs(scores["web_search"][0] - 0.20) < 1e-6
        assert scores["web_search"][1] == 2
        # resolution_crypto: (0.9-1)^2 = 0.01
        assert abs(scores["resolution_crypto"][0] - 0.01) < 1e-6
        assert scores["resolution_crypto"][1] == 1

    def test_empty_data(self) -> None:
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []

        with patch("signals.calibration.db.get_db", return_value=mock_db):
            scores = get_provider_brier_scores()

        assert scores == {}


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
        # web_search ratio = 0.15 / 0.20 = 0.75 → mult = 1.5 * 0.75 = 1.125
        # resolution_crypto ratio = 0.15 / 0.10 = 1.5 → mult = 2.0 * 1.5 = 3.0
        # prediction_markets ratio = 0.15 / 0.15 = 1.0 → mult = 1.8 * 1.0 = 1.8
        ws = result["web_search"]
        assert ws.is_default is False
        assert abs(ws.multiplier - 1.125) < 0.01

        rc = result["resolution_crypto"]
        assert rc.is_default is False
        assert abs(rc.multiplier - 3.0) < 0.01

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

        # web_search: ratio = avg/0.30 → could be < 0.5
        ws = result["web_search"]
        assert ws.multiplier >= 0.5 * DEFAULT_MULTIPLIERS["web_search"]

    def test_mixed_sufficient_and_insufficient(self) -> None:
        """Some providers have enough data, others don't."""
        with patch("signals.calibration.get_provider_brier_scores", return_value={
            "web_search": (0.20, 30),           # Sufficient
            "resolution_crypto": (0.10, 5),     # Insufficient (< 20)
            "prediction_markets": (0.15, 25),   # Sufficient
        }):
            result = get_dynamic_multipliers()

        # resolution_crypto should use default
        assert result["resolution_crypto"].is_default is True
        assert result["resolution_crypto"].multiplier == DEFAULT_MULTIPLIERS["resolution_crypto"]

        # web_search and prediction_markets should be dynamic
        # avg_brier computed only from sufficient providers: (0.20 + 0.15) / 2 = 0.175
        assert result["web_search"].is_default is False
        assert result["prediction_markets"].is_default is False


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
