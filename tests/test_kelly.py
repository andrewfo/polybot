"""Tests for strategy/kelly.py — Kelly criterion bet sizing."""

from unittest.mock import patch

import pytest

from strategy.kelly import TradeDecision, calculate_kelly, _get_existing_exposure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kelly(
    estimated_prob: float = 0.55,
    market_price: float = 0.40,
    available_bankroll: float = 1000.0,
    confidence: float = 0.8,
    market_id: str = "mkt_1",
    token_id: str = "tok_1",
    market_question: str = "Will BTC hit $100k?",
) -> TradeDecision:
    """Shortcut to call calculate_kelly with sensible defaults."""
    with patch("strategy.kelly.db") as mock_db:
        mock_db.get_open_positions.return_value = []
        return calculate_kelly(
            market_id=market_id,
            token_id=token_id,
            market_question=market_question,
            estimated_prob=estimated_prob,
            market_price=market_price,
            confidence=confidence,
            available_bankroll=available_bankroll,
        )


# ---------------------------------------------------------------------------
# Core Kelly formula tests — now with confidence blending + fee adjustment
# ---------------------------------------------------------------------------

class TestPositiveEdgeBuyYes:
    """Market at 0.40, estimate 0.55 → BUY YES with positive edge.

    With sublinear blending: confidence=0.80 → blend_weight = 0.80^0.75 ≈ 0.8409
    effective_prob = 0.8409*0.55 + 0.1591*0.40 ≈ 0.5261
    Edge = 0.5261 - 0.40 = 0.1261
    """

    def test_should_trade(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.should_trade is True

    def test_side_is_buy_yes(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.side == "BUY_YES"

    def test_effective_prob_blended(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        # Sublinear: blend = 0.80^0.75 ≈ 0.8409
        blend = 0.80 ** 0.75
        expected = blend * 0.55 + (1 - blend) * 0.40
        assert abs(d.effective_prob - expected) < 1e-4

    def test_edge_uses_effective_prob(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        blend = 0.80 ** 0.75
        expected_eff = blend * 0.55 + (1 - blend) * 0.40
        assert abs(d.edge - (expected_eff - 0.40)) < 1e-4

    def test_raw_estimate_preserved(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        assert abs(d.estimated_prob - 0.55) < 1e-9

    def test_kelly_fraction_positive(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        assert d.full_kelly_fraction > 0
        assert d.adjusted_fraction > 0

    def test_bet_size_smaller_than_no_blend(self) -> None:
        """Confidence blending should produce smaller bets than raw estimate."""
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        # With blending + fees, bet should be smaller than the old $62.50
        assert d.bet_size_usd < 62.50
        assert d.bet_size_usd > 0


class TestPositiveEdgeBuyNo:
    """Market at 0.70, estimate 0.50 → BUY NO."""

    def test_should_trade(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        assert d.should_trade is True

    def test_side_is_buy_no(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        assert d.side == "BUY_NO"

    def test_effective_prob_blended(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70, confidence=0.80)
        # Sublinear: blend = 0.80^0.75
        blend = 0.80 ** 0.75
        expected = blend * 0.50 + (1 - blend) * 0.70
        assert abs(d.effective_prob - expected) < 1e-4

    def test_edge_calculation(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70, confidence=0.80)
        blend = 0.80 ** 0.75
        expected_eff = blend * 0.50 + (1 - blend) * 0.70
        assert abs(d.edge - (0.70 - expected_eff)) < 1e-4


# ---------------------------------------------------------------------------
# Confidence blending behavior
# ---------------------------------------------------------------------------

class TestConfidenceBlending:
    """Confidence directly affects bet sizing via sublinear probability blending."""

    def test_high_confidence_bigger_bet(self) -> None:
        d_high = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.95)
        d_low = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.50)
        assert d_high.bet_size_usd > d_low.bet_size_usd

    def test_perfect_confidence_uses_raw_estimate(self) -> None:
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=1.0)
        assert abs(d.effective_prob - 0.60) < 1e-9

    def test_zero_confidence_uses_blend_floor(self) -> None:
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.0)
        # With MIN_CONFIDENCE_BLEND=0.25: blend = max(0^0.75, 0.25) = 0.25
        # effective = 0.25*0.60 + 0.75*0.40 = 0.15 + 0.30 = 0.45
        assert abs(d.effective_prob - 0.45) < 1e-4
        assert d.should_trade is True  # 5% edge after blend floor

    def test_low_confidence_preserves_edge(self) -> None:
        """Low confidence with sublinear blending preserves more edge than linear."""
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.30)
        # Sublinear: blend = 0.30^0.75 ≈ 0.398
        # effective = 0.398*0.60 + 0.602*0.40 ≈ 0.480
        # edge = 0.480 - 0.40 = 0.08
        assert d.edge > 0.05  # meaningful edge preserved
        assert d.should_trade is True

    def test_sublinear_blending_curve(self) -> None:
        """Verify sublinear blending at key confidence points."""
        # conf=0.25 → blend = 0.25^0.75 ≈ 0.354
        d = _kelly(estimated_prob=0.70, market_price=0.50, confidence=0.25)
        blend = 0.25 ** 0.75
        expected = blend * 0.70 + (1 - blend) * 0.50
        assert abs(d.effective_prob - expected) < 1e-3

        # conf=0.50 → blend = 0.50^0.75 ≈ 0.594
        d = _kelly(estimated_prob=0.70, market_price=0.50, confidence=0.50)
        blend = 0.50 ** 0.75
        expected = blend * 0.70 + (1 - blend) * 0.50
        assert abs(d.effective_prob - expected) < 1e-3

    def test_blending_preserves_edge(self) -> None:
        """With new blending, a 20% raw edge at conf=0.30 should survive."""
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.30)
        # Old linear with floor=0.50: effective=0.50, edge=0.10 → gets gutted
        # New sublinear: blend=0.30^0.75≈0.398, effective≈0.48, edge≈0.08
        assert d.edge >= 0.05  # Edge survives
        assert d.should_trade is True


# ---------------------------------------------------------------------------
# Fee adjustment
# ---------------------------------------------------------------------------

class TestFeeAdjustment:
    """Polymarket's 2% profit fee reduces effective odds."""

    def test_fee_reduces_kelly_fraction(self) -> None:
        """With fees, Kelly fraction should be smaller than without."""
        d = _kelly(estimated_prob=0.70, market_price=0.40, confidence=1.0)
        # Without fees: b = 0.60/0.40 = 1.5
        # With 2% fee: b = 0.60*0.98/0.40 = 1.47
        # This slightly reduces Kelly fraction
        # f* = (1.47*0.70 - 0.30) / 1.47 = (1.029 - 0.30) / 1.47 ≈ 0.4959
        # Without fee: f* = (1.5*0.70 - 0.30) / 1.5 = 0.50
        assert d.full_kelly_fraction < 0.50


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

class TestZeroEdge:
    """Market = estimate → should not trade."""

    def test_skip(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.50)
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason


class TestEdgeBelowThreshold:
    """Edge present but below MIN_EDGE_THRESHOLD (0.02)."""

    def test_skip_tiny_edge(self) -> None:
        # Very small edge: estimate=0.51, market=0.50
        # With sublinear blending, edge is tiny → skip
        d = _kelly(estimated_prob=0.51, market_price=0.50)
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason

    def test_trade_with_strong_edge_and_confidence(self) -> None:
        # Raw edge = 0.10, confidence=0.8
        # Sublinear: blend = 0.80^0.75 ≈ 0.84
        # effective ≈ 0.84*0.60 + 0.16*0.50 ≈ 0.584
        # edge ≈ 0.084 > 0.02
        d = _kelly(estimated_prob=0.60, market_price=0.50)
        assert d.should_trade is True


class TestBetCappedByMaxPositionPct:
    """Bet should be capped at MAX_POSITION_PCT (10%) of bankroll."""

    def test_cap_applied(self) -> None:
        d = _kelly(estimated_prob=0.95, market_price=0.20, available_bankroll=1000.0)
        assert d.bet_size_usd <= 1000.0 * 0.10 + 1e-9
        assert d.should_trade is True


class TestBetReducedForReserve:
    """Bet reduced to maintain dynamic bankroll reserve (max($20, 5%))."""

    def test_reserve_maintained(self) -> None:
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=30.0)
        # Dynamic reserve = max(20, 30*0.05) = 20
        assert d.bet_size_usd <= 30.0 - 20.0 + 1e-9
        assert d.should_trade is True

    def test_skip_when_reserve_eats_bet(self) -> None:
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=20.50)
        assert d.should_trade is False
        assert "reserve" in d.skip_reason or "small" in d.skip_reason

    def test_dynamic_reserve_scales_with_bankroll(self) -> None:
        """Large bankroll uses 5% reserve instead of flat $20."""
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=10000.0)
        # Dynamic reserve = max(20, 10000*0.05) = 500
        # Max position = 10000 * 0.10 = 1000
        # bet capped at 1000 (max position), but bankroll - 1000 = 9000 > 500
        assert d.should_trade is True


class TestExistingPositionReducesSizing:
    """Existing position in same market reduces available sizing."""

    def test_reduced_by_exposure(self) -> None:
        with patch("strategy.kelly.db") as mock_db:
            mock_db.get_open_positions.return_value = [
                {
                    "market_id": "mkt_1",
                    "avg_entry": 0.40,
                    "size": 125.0,  # exposure = 0.40 * 125 = $50
                }
            ]
            d = calculate_kelly(
                market_id="mkt_1",
                token_id="tok_1",
                market_question="Will BTC hit $100k?",
                estimated_prob=0.95,
                market_price=0.20,
                confidence=0.8,
                available_bankroll=1000.0,
            )
            assert d.bet_size_usd <= 50.0 + 1e-9
            assert d.should_trade is True

    def test_skip_when_fully_exposed(self) -> None:
        with patch("strategy.kelly.db") as mock_db:
            mock_db.get_open_positions.return_value = [
                {
                    "market_id": "mkt_1",
                    "avg_entry": 0.50,
                    "size": 200.0,  # exposure = $100 = max position
                }
            ]
            d = calculate_kelly(
                market_id="mkt_1",
                token_id="tok_1",
                market_question="Will BTC hit $100k?",
                estimated_prob=0.95,
                market_price=0.20,
                confidence=0.8,
                available_bankroll=1000.0,
            )
            assert d.should_trade is False
            assert "existing position" in d.skip_reason


class TestVerySmallBankroll:
    """Very small bankroll → bet_size < $1 → skip."""

    def test_skip(self) -> None:
        d = _kelly(estimated_prob=0.56, market_price=0.50, available_bankroll=10.0)
        assert d.should_trade is False
        assert "bet too small" in d.skip_reason or "edge below" in d.skip_reason


# ---------------------------------------------------------------------------
# TradeDecision dataclass audit completeness
# ---------------------------------------------------------------------------

class TestTradeDecisionAudit:
    """TradeDecision contains full audit info for every decision."""

    def test_trade_has_all_fields(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.market_id == "mkt_1"
        assert d.token_id == "tok_1"
        assert d.market_question == "Will BTC hit $100k?"
        assert d.side == "BUY_YES"
        assert d.estimated_prob == 0.55
        assert 0 < d.effective_prob <= 0.55  # blended toward market
        assert d.market_price == 0.40
        assert d.edge > 0
        assert d.full_kelly_fraction > 0
        assert d.adjusted_fraction > 0
        assert d.bet_size_usd > 0
        assert d.expected_value > 0
        assert d.confidence == 0.8
        assert d.should_trade is True
        assert d.skip_reason == ""

    def test_skip_has_all_fields(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.50)
        assert d.market_id == "mkt_1"
        assert d.token_id == "tok_1"
        assert d.should_trade is False
        assert d.skip_reason != ""
        assert d.bet_size_usd == 0.0
        assert d.expected_value == 0.0
        assert d.effective_prob is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary and edge-case behavior."""

    def test_market_price_near_zero(self) -> None:
        d = _kelly(estimated_prob=0.10, market_price=0.02, confidence=0.9)
        # Extreme prices are now rejected as lottery tickets
        assert d.should_trade is False
        assert "lottery ticket" in d.skip_reason

    def test_market_price_near_one(self) -> None:
        d = _kelly(estimated_prob=0.85, market_price=0.98, confidence=0.9)
        # Extreme prices are now rejected as lottery tickets
        assert d.should_trade is False
        assert "lottery ticket" in d.skip_reason

    def test_db_error_returns_zero_exposure(self) -> None:
        """If DB call fails, existing exposure defaults to 0."""
        with patch("strategy.kelly.db") as mock_db:
            mock_db.get_open_positions.side_effect = Exception("DB error")
            exposure = _get_existing_exposure("mkt_1")
            assert exposure == 0.0
