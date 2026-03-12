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

    Confidence=0.80 → effective_prob = 0.80*0.55 + 0.20*0.40 = 0.52
    Edge = 0.52 - 0.40 = 0.12
    Fee-adjusted odds: b = (1-0.40)*0.98/0.40 = 0.588/0.40 = 1.47
    Kelly: f* = (1.47*0.52 - 0.48) / 1.47 = (0.7644 - 0.48) / 1.47 ≈ 0.19347
    Adjusted: 0.19347 * 0.25 ≈ 0.04837
    Bet: 1000 * 0.04837 ≈ $48.37
    """

    def test_should_trade(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.should_trade is True

    def test_side_is_buy_yes(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.side == "BUY_YES"

    def test_effective_prob_blended(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        # effective = 0.80*0.55 + 0.20*0.40 = 0.52
        assert abs(d.effective_prob - 0.52) < 1e-9

    def test_edge_uses_effective_prob(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, confidence=0.80)
        # edge = effective_prob - market = 0.52 - 0.40 = 0.12
        assert abs(d.edge - 0.12) < 1e-9

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
        # effective = 0.80*0.50 + 0.20*0.70 = 0.54
        assert abs(d.effective_prob - 0.54) < 1e-9

    def test_edge_calculation(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70, confidence=0.80)
        # edge = market - effective = 0.70 - 0.54 = 0.16
        assert abs(d.edge - 0.16) < 1e-9


# ---------------------------------------------------------------------------
# Confidence blending behavior
# ---------------------------------------------------------------------------

class TestConfidenceBlending:
    """Confidence directly affects bet sizing via probability blending."""

    def test_high_confidence_bigger_bet(self) -> None:
        d_high = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.95)
        d_low = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.50)
        assert d_high.bet_size_usd > d_low.bet_size_usd

    def test_perfect_confidence_uses_raw_estimate(self) -> None:
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=1.0)
        assert abs(d.effective_prob - 0.60) < 1e-9

    def test_zero_confidence_uses_blend_floor(self) -> None:
        d = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.0)
        # With MIN_CONFIDENCE_BLEND=0.50: effective = 0.50*0.60 + 0.50*0.40 = 0.50
        # Blend floor prevents full collapse to market price
        assert abs(d.effective_prob - 0.50) < 1e-9
        assert d.should_trade is True  # 10% edge after blend floor

    def test_low_confidence_with_blend_floor(self) -> None:
        """Low confidence uses blend floor — small edge can still pass threshold."""
        d = _kelly(estimated_prob=0.56, market_price=0.50, confidence=0.30)
        # With MIN_CONFIDENCE_BLEND=0.50: effective = 0.50*0.56 + 0.50*0.50 = 0.53
        # edge = 0.53 - 0.50 = 0.03 = MIN_EDGE_THRESHOLD → passes
        assert abs(d.effective_prob - 0.53) < 1e-9
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
    """Edge present but below MIN_EDGE_THRESHOLD (0.05)."""

    def test_skip_small_raw_edge(self) -> None:
        # Raw edge = 0.03, confidence=0.8, effective = 0.8*0.53 + 0.2*0.50 = 0.524
        # Effective edge = 0.024 < 0.05
        d = _kelly(estimated_prob=0.53, market_price=0.50)
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason

    def test_trade_with_strong_edge_and_confidence(self) -> None:
        # Raw edge = 0.10, confidence=0.8, effective = 0.8*0.60 + 0.2*0.50 = 0.58
        # Effective edge = 0.08 > 0.05
        d = _kelly(estimated_prob=0.60, market_price=0.50)
        assert d.should_trade is True


class TestBetCappedByMaxPositionPct:
    """Bet should be capped at MAX_POSITION_PCT (10%) of bankroll."""

    def test_cap_applied(self) -> None:
        d = _kelly(estimated_prob=0.95, market_price=0.20, available_bankroll=1000.0)
        assert d.bet_size_usd <= 1000.0 * 0.10 + 1e-9
        assert d.should_trade is True


class TestBetReducedForReserve:
    """Bet reduced to maintain MIN_BANKROLL_RESERVE ($20)."""

    def test_reserve_maintained(self) -> None:
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=30.0)
        assert d.bet_size_usd <= 30.0 - 20.0 + 1e-9
        assert d.should_trade is True

    def test_skip_when_reserve_eats_bet(self) -> None:
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=20.50)
        assert d.should_trade is False
        assert "reserve" in d.skip_reason or "small" in d.skip_reason


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
        assert d.should_trade is True
        assert d.side == "BUY_YES"

    def test_market_price_near_one(self) -> None:
        d = _kelly(estimated_prob=0.85, market_price=0.98, confidence=0.9)
        assert d.side == "BUY_NO"
        assert d.edge > 0

    def test_db_error_returns_zero_exposure(self) -> None:
        """If DB call fails, existing exposure defaults to 0."""
        with patch("strategy.kelly.db") as mock_db:
            mock_db.get_open_positions.side_effect = Exception("DB error")
            exposure = _get_existing_exposure("mkt_1")
            assert exposure == 0.0
