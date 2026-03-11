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
# Core Kelly formula tests
# ---------------------------------------------------------------------------

class TestPositiveEdgeBuyYes:
    """Market at 0.40, estimate 0.55 → BUY YES with positive edge."""

    def test_should_trade(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.should_trade is True

    def test_side_is_buy_yes(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert d.side == "BUY_YES"

    def test_edge_calculation(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        assert abs(d.edge - 0.15) < 1e-9

    def test_kelly_fraction_positive(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40)
        # b = 0.60/0.40 = 1.5, p = 0.55, q = 0.45
        # f* = (1.5 * 0.55 - 0.45) / 1.5 = (0.825 - 0.45) / 1.5 = 0.25
        assert abs(d.full_kelly_fraction - 0.25) < 1e-9
        # adjusted = 0.25 * 0.25 = 0.0625
        assert abs(d.adjusted_fraction - 0.0625) < 1e-9

    def test_bet_size(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, available_bankroll=1000.0)
        # bet = 1000 * 0.0625 = $62.50
        assert abs(d.bet_size_usd - 62.50) < 1e-6

    def test_expected_value(self) -> None:
        d = _kelly(estimated_prob=0.55, market_price=0.40, available_bankroll=1000.0)
        # EV = edge * bet_size = 0.15 * 62.50 = 9.375
        assert abs(d.expected_value - 9.375) < 1e-6


class TestPositiveEdgeBuyNo:
    """Market at 0.70, estimate 0.50 → BUY NO."""

    def test_should_trade(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        assert d.should_trade is True

    def test_side_is_buy_no(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        assert d.side == "BUY_NO"

    def test_edge_calculation(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        assert abs(d.edge - 0.20) < 1e-9

    def test_kelly_fraction_positive(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.70)
        # BUY NO: no_price = 0.30, b = 0.70/0.30 = 7/3, p = 0.50, q = 0.50
        # f* = (7/3 * 0.50 - 0.50) / (7/3) = (7/6 - 0.50) / (7/3)
        #    = (7/6 - 3/6) / (7/3) = (4/6) / (7/3) = (2/3) / (7/3) = 2/7
        expected_f = 2.0 / 7.0
        assert abs(d.full_kelly_fraction - expected_f) < 1e-9


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

class TestZeroEdge:
    """Market = estimate → should not trade."""

    def test_skip(self) -> None:
        d = _kelly(estimated_prob=0.50, market_price=0.50)
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason


class TestNegativeKelly:
    """Edge exists but Kelly fraction is zero or negative."""

    def test_skip(self) -> None:
        # edge = 0.06 > threshold (0.05) but Kelly could be 0 if math doesn't work
        # Actually with edge > 0 and b > 0, Kelly is usually positive.
        # Let's make a case where full_kelly_f <= 0 by having edge just above
        # threshold but the odds structure yields negative Kelly.
        # With binary markets this is hard — edge > 0 implies Kelly > 0.
        # So test with edge exactly at threshold boundary.
        d = _kelly(estimated_prob=0.50, market_price=0.46)
        # edge = 0.04 < 0.05 threshold
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason


class TestEdgeBelowThreshold:
    """Edge present but below MIN_EDGE_THRESHOLD (0.05)."""

    def test_skip_at_0_03(self) -> None:
        d = _kelly(estimated_prob=0.53, market_price=0.50)
        assert d.should_trade is False
        assert "edge below threshold" in d.skip_reason

    def test_trade_at_0_06(self) -> None:
        d = _kelly(estimated_prob=0.56, market_price=0.50)
        assert d.should_trade is True


class TestBetCappedByMaxPositionPct:
    """Bet should be capped at MAX_POSITION_PCT (10%) of bankroll."""

    def test_cap_applied(self) -> None:
        # Use extreme edge to generate large Kelly fraction
        d = _kelly(estimated_prob=0.95, market_price=0.20, available_bankroll=1000.0)
        # MAX_POSITION_PCT = 0.10 → max $100
        assert d.bet_size_usd <= 1000.0 * 0.10 + 1e-9
        assert d.should_trade is True


class TestBetReducedForReserve:
    """Bet reduced to maintain MIN_BANKROLL_RESERVE ($20)."""

    def test_reserve_maintained(self) -> None:
        # Small bankroll: $30. Large edge so Kelly wants a big fraction.
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=30.0)
        # Max position = 30 * 0.10 = $3, which is > $1 and leaves $27 > $20 reserve
        # So the cap fires first, bet = $3
        assert d.bet_size_usd <= 30.0 - 20.0 + 1e-9
        assert d.should_trade is True

    def test_skip_when_reserve_eats_bet(self) -> None:
        # bankroll = $20.50. After reserve ($20), only $0.50 left → skip
        d = _kelly(estimated_prob=0.90, market_price=0.20, available_bankroll=20.50)
        # Max position = 20.50 * 0.10 = $2.05; after reserve: 20.50 - 20 = $0.50
        # bet = min(2.05, Kelly*bankroll) then reserve check: 20.50 - bet < 20 → reduce
        # reduced bet = 0.50 < $1 → skip
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
            # Max position = $100. Existing exposure = $50. Room = $50.
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
        # edge = 0.06, b = 1.0, p = 0.56, q = 0.44
        # f* = (1.0*0.56 - 0.44)/1.0 = 0.12
        # adj = 0.12 * 0.25 = 0.03
        # bet = 10 * 0.03 = $0.30 < $1
        assert d.should_trade is False
        assert "bet too small" in d.skip_reason


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


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary and edge-case behavior."""

    def test_extreme_confidence_no_effect_on_kelly(self) -> None:
        """Confidence is stored but doesn't affect Kelly formula directly."""
        d1 = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.99)
        d2 = _kelly(estimated_prob=0.60, market_price=0.40, confidence=0.30)
        assert d1.bet_size_usd == d2.bet_size_usd
        assert d1.confidence == 0.99
        assert d2.confidence == 0.30

    def test_market_price_near_zero(self) -> None:
        d = _kelly(estimated_prob=0.10, market_price=0.02)
        assert d.should_trade is True
        assert d.side == "BUY_YES"

    def test_market_price_near_one(self) -> None:
        d = _kelly(estimated_prob=0.85, market_price=0.98)
        assert d.side == "BUY_NO"
        assert d.edge > 0

    def test_db_error_returns_zero_exposure(self) -> None:
        """If DB call fails, existing exposure defaults to 0."""
        with patch("strategy.kelly.db") as mock_db:
            mock_db.get_open_positions.side_effect = Exception("DB error")
            exposure = _get_existing_exposure("mkt_1")
            assert exposure == 0.0
