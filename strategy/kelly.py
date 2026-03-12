"""Kelly criterion bet sizing engine.

Converts probability edge into optimal bet sizes using fractional Kelly.
Takes the signal engine's AggregatedSignal output and determines how much
to bet and in which direction.
"""

import logging
from dataclasses import dataclass

from config.settings import (
    KELLY_FRACTION,
    MAX_POSITION_PCT,
    MIN_BANKROLL_RESERVE,
    MIN_CONFIDENCE_BLEND,
    MIN_EDGE_THRESHOLD,
    POLYMARKET_FEE_RATE,
)
from core import db

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    """Full audit record for every Kelly sizing decision."""

    market_id: str
    token_id: str
    market_question: str
    side: str                    # "BUY_YES" or "BUY_NO"
    estimated_prob: float        # Raw probability estimate from frontier model
    effective_prob: float        # Confidence-blended probability used for Kelly
    market_price: float          # Current market implied probability
    edge: float                  # effective_prob - market_price (or inverse)
    full_kelly_fraction: float   # What full Kelly says
    adjusted_fraction: float     # After applying KELLY_FRACTION multiplier
    bet_size_usd: float          # Dollar amount
    expected_value: float        # Expected profit per dollar risked
    confidence: float            # From signal aggregator
    should_trade: bool           # Final yes/no decision
    skip_reason: str             # If should_trade is False, why


def _get_existing_exposure(market_id: str) -> float:
    """Get total existing exposure in a market from open positions."""
    try:
        positions = db.get_open_positions()
        total = 0.0
        for pos in positions:
            if pos.get("market_id") == market_id:
                total += pos.get("avg_entry", 0.0) * pos.get("size", 0.0)
        return total
    except Exception:
        return 0.0


def calculate_kelly(
    market_id: str,
    token_id: str,
    market_question: str,
    estimated_prob: float,
    market_price: float,
    confidence: float,
    available_bankroll: float,
) -> TradeDecision:
    """Calculate Kelly-optimal bet size with safety checks.

    Parameters
    ----------
    market_id : str
        Polymarket condition ID.
    token_id : str
        CLOB token ID for the outcome we'd trade.
    market_question : str
        Human-readable market question.
    estimated_prob : float
        Our probability estimate (0-1) from signal aggregator.
    market_price : float
        Current market implied probability (0-1).
    confidence : float
        Signal aggregator confidence (0-1).
    available_bankroll : float
        Current available USDC balance.

    Returns
    -------
    TradeDecision
        Full audit record including whether to trade and why/why not.
    """
    # Confidence-blend: shrink our estimate toward the market price.
    # If confidence=1.0 we fully trust our estimate; if confidence=0.0
    # we have no information and defer to the market entirely.
    # Floor the blend weight so we never dilute more than 50% toward market.
    blend_weight = max(confidence, MIN_CONFIDENCE_BLEND)
    effective_prob = blend_weight * estimated_prob + (1.0 - blend_weight) * market_price

    # Determine side using the blended probability
    if effective_prob >= market_price:
        # BUY YES: we think YES is underpriced
        side = "BUY_YES"
        edge = effective_prob - market_price
        # Fee-adjusted odds: profit after Polymarket fee
        # Profit per share = (1 - market_price) * (1 - fee)
        net_profit = (1.0 - market_price) * (1.0 - POLYMARKET_FEE_RATE)
        b = net_profit / market_price if market_price > 0 else 0.0
        p = effective_prob
        q = 1.0 - effective_prob
    else:
        # BUY NO: we think YES is overpriced, so NO is underpriced
        side = "BUY_NO"
        edge = market_price - effective_prob
        # Fee-adjusted odds for NO side
        no_price = 1.0 - market_price
        net_profit = market_price * (1.0 - POLYMARKET_FEE_RATE)
        b = net_profit / no_price if no_price > 0 else 0.0
        p = 1.0 - effective_prob
        q = effective_prob

    # --- Safety check 1: edge below threshold ---
    if edge < MIN_EDGE_THRESHOLD:
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="edge below threshold",
        )

    # Full Kelly fraction: f* = (bp - q) / b
    full_kelly_f = (b * p - q) / b if b > 0 else 0.0

    # --- Safety check 2: no positive edge ---
    if full_kelly_f <= 0:
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="no positive edge",
        )

    # Apply fractional Kelly
    adjusted_f = full_kelly_f * KELLY_FRACTION
    bet_size = available_bankroll * adjusted_f

    # --- Safety check 3: bet too small ---
    if bet_size < 1.0:
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="bet too small (< $1)",
        )

    # --- Safety check 4: cap to MAX_POSITION_PCT ---
    max_position = available_bankroll * MAX_POSITION_PCT
    if bet_size > max_position:
        logger.info(
            "Capping bet from $%.2f to $%.2f (MAX_POSITION_PCT=%.0f%%)",
            bet_size, max_position, MAX_POSITION_PCT * 100,
        )
        bet_size = max_position

    # --- Safety check 5: maintain MIN_BANKROLL_RESERVE ---
    if available_bankroll - bet_size < MIN_BANKROLL_RESERVE:
        bet_size = available_bankroll - MIN_BANKROLL_RESERVE
        if bet_size < 1.0:
            return _skip(
                market_id, token_id, market_question, side,
                estimated_prob, effective_prob, market_price, edge, confidence,
                reason="bet too small after reserve (< $1)",
            )
        logger.info(
            "Reduced bet to $%.2f to maintain $%.0f reserve",
            bet_size, MIN_BANKROLL_RESERVE,
        )

    # --- Safety check 6: subtract existing exposure ---
    existing_exposure = _get_existing_exposure(market_id)
    if existing_exposure > 0:
        remaining_room = max_position - existing_exposure
        if remaining_room <= 0:
            return _skip(
                market_id, token_id, market_question, side,
                estimated_prob, effective_prob, market_price, edge, confidence,
                reason="existing position at max exposure",
            )
        if bet_size > remaining_room:
            logger.info(
                "Reduced bet from $%.2f to $%.2f due to existing exposure $%.2f",
                bet_size, remaining_room, existing_exposure,
            )
            bet_size = remaining_room
            if bet_size < 1.0:
                return _skip(
                    market_id, token_id, market_question, side,
                    estimated_prob, effective_prob, market_price, edge, confidence,
                    reason="bet too small after existing exposure (< $1)",
                )

    expected_value = edge * bet_size

    decision = TradeDecision(
        market_id=market_id,
        token_id=token_id,
        market_question=market_question,
        side=side,
        estimated_prob=estimated_prob,
        effective_prob=effective_prob,
        market_price=market_price,
        edge=edge,
        full_kelly_fraction=full_kelly_f,
        adjusted_fraction=adjusted_f,
        bet_size_usd=bet_size,
        expected_value=expected_value,
        confidence=confidence,
        should_trade=True,
        skip_reason="",
    )

    logger.info(
        "TRADE: %s %s | edge=%.3f eff_prob=%.3f kelly=%.3f adj=%.3f bet=$%.2f EV=$%.2f",
        side, market_question[:50], edge, effective_prob, full_kelly_f, adjusted_f,
        bet_size, expected_value,
    )
    return decision


def _skip(
    market_id: str,
    token_id: str,
    market_question: str,
    side: str,
    estimated_prob: float,
    effective_prob: float,
    market_price: float,
    edge: float,
    confidence: float,
    reason: str,
) -> TradeDecision:
    """Build a TradeDecision that says 'don't trade' with a reason."""
    logger.info(
        "SKIP: %s | %s (edge=%.3f, eff=%.3f, est=%.3f, mkt=%.3f)",
        market_question[:50], reason, edge, effective_prob, estimated_prob, market_price,
    )
    return TradeDecision(
        market_id=market_id,
        token_id=token_id,
        market_question=market_question,
        side=side,
        estimated_prob=estimated_prob,
        effective_prob=effective_prob,
        market_price=market_price,
        edge=edge,
        full_kelly_fraction=0.0,
        adjusted_fraction=0.0,
        bet_size_usd=0.0,
        expected_value=0.0,
        confidence=confidence,
        should_trade=False,
        skip_reason=reason,
    )
