"""Kelly criterion bet sizing engine.

Converts probability edge into optimal bet sizes using fractional Kelly.
Takes the signal engine's AggregatedSignal output and determines how much
to bet and in which direction.
"""

import logging
from dataclasses import dataclass

from config.settings import (
    KELLY_FRACTION,
    MAX_GAS_DRAG_PCT,
    MAX_POSITION_PCT,
    MIN_BANKROLL_RESERVE,
    MIN_BET_USD,
    MIN_CONFIDENCE_BLEND,
    MIN_EDGE_THRESHOLD,
    POLYMARKET_FEE_RATE,
    get_effective_param,
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
    # Depth analysis fields (populated after Kelly by depth adjustment)
    depth_total_usd: float = 0.0      # Total ask-side depth in USD
    depth_slippage: float = 0.0       # Expected slippage at original bet size
    depth_adjusted: bool = False       # True if bet was reduced due to depth
    # Gas analysis fields (populated after Kelly+depth by gas adjustment)
    gas_cost_usd: float = 0.0          # Estimated round-trip gas cost in USD
    ev_to_gas_ratio: float = 0.0       # expected_value / gas_cost_usd
    gas_blocked: bool = False          # True if trade was blocked by gas gate


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
    gas_cost_usd: float = 0.0,
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
    # Resolve effective parameters (DB overrides if active, else defaults)
    eff_kelly_fraction = get_effective_param("KELLY_FRACTION", KELLY_FRACTION)
    eff_min_edge = get_effective_param("MIN_EDGE_THRESHOLD", MIN_EDGE_THRESHOLD)
    eff_min_conf_blend = get_effective_param("MIN_CONFIDENCE_BLEND", MIN_CONFIDENCE_BLEND)

    # Confidence-blend: shrink our estimate toward the market price.
    # Sublinear scaling: confidence^0.75 provides a natural shrinkage curve
    # that preserves more edge at low confidence than linear blending.
    # conf=0.25 → 0.35 blend, conf=0.50 → 0.59, conf=0.80 → 0.85
    blend_weight = max(confidence ** 0.75, eff_min_conf_blend)
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

    # --- Safety check 0: reject lottery tickets (extreme low-prob bets) ---
    # Market prices below 0.04 or above 0.96 are long-shot bets where
    # model estimates are unreliable and the edge is often illusory.
    if market_price < 0.04 or market_price > 0.96:
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="lottery ticket (market price too extreme)",
        )

    # --- Safety check 1: edge below threshold ---
    if edge < eff_min_edge:
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
    adjusted_f = full_kelly_f * eff_kelly_fraction
    bet_size = available_bankroll * adjusted_f

    # --- Safety check 3: bet below configured minimum or gas-adaptive floor ---
    # Floor adapts to gas conditions: at high gas, a bigger bet is needed for
    # gas to consume <= MAX_GAS_DRAG_PCT of the notional.
    gas_adaptive_floor = (
        gas_cost_usd / MAX_GAS_DRAG_PCT if gas_cost_usd > 0 and MAX_GAS_DRAG_PCT > 0 else 0.0
    )
    effective_floor = max(MIN_BET_USD, gas_adaptive_floor)
    if bet_size < effective_floor:
        logger.info(
            "Skip: bet too small (below floor) bet=$%.2f floor=$%.2f "
            "MIN_BET_USD=$%.2f gas_floor=$%.2f",
            bet_size, effective_floor, MIN_BET_USD, gas_adaptive_floor,
        )
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="bet too small (below floor)",
            full_kelly_f=full_kelly_f,
        )

    # --- Safety check 4: cap to MAX_POSITION_PCT ---
    max_position = available_bankroll * MAX_POSITION_PCT
    if bet_size > max_position:
        logger.info(
            "Capping bet from $%.2f to $%.2f (MAX_POSITION_PCT=%.0f%%)",
            bet_size, max_position, MAX_POSITION_PCT * 100,
        )
        bet_size = max_position

    # --- Safety check 5: maintain dynamic bankroll reserve ---
    # Scales with portfolio size: max(MIN_BANKROLL_RESERVE, bankroll * 5%)
    effective_reserve = max(MIN_BANKROLL_RESERVE, available_bankroll * 0.05)
    if available_bankroll - bet_size < effective_reserve:
        bet_size = available_bankroll - effective_reserve
        if bet_size < effective_floor:
            return _skip(
                market_id, token_id, market_question, side,
                estimated_prob, effective_prob, market_price, edge, confidence,
                reason="bet too small after reserve",
                full_kelly_f=full_kelly_f,
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
                full_kelly_f=full_kelly_f,
            )
        if bet_size > remaining_room:
            logger.info(
                "Reduced bet from $%.2f to $%.2f due to existing exposure $%.2f",
                bet_size, remaining_room, existing_exposure,
            )
            bet_size = remaining_room
            if bet_size < effective_floor:
                return _skip(
                    market_id, token_id, market_question, side,
                    estimated_prob, effective_prob, market_price, edge, confidence,
                    reason="bet too small after existing exposure",
                    full_kelly_f=full_kelly_f,
                )

    # --- Safety check 7: edge survives gas drag ---
    # Subtract per-unit gas drag from edge; bet only if the net edge still
    # clears the threshold. This makes gas a true cost, not a post-hoc gate.
    gas_drag_pct = gas_cost_usd / bet_size if bet_size > 0 else 0.0
    net_edge = edge - gas_drag_pct
    if net_edge < eff_min_edge:
        logger.info(
            "Skip: net edge below threshold after gas | edge=%.3f gas_drag=%.3f "
            "net=%.3f min=%.3f gas=$%.3f bet=$%.2f",
            edge, gas_drag_pct, net_edge, eff_min_edge, gas_cost_usd, bet_size,
        )
        return _skip(
            market_id, token_id, market_question, side,
            estimated_prob, effective_prob, market_price, edge, confidence,
            reason="net edge below threshold after gas",
            full_kelly_f=full_kelly_f,
        )

    expected_value = net_edge * bet_size

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
        gas_cost_usd=gas_cost_usd,
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
    full_kelly_f: float = 0.0,
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
        full_kelly_fraction=full_kelly_f,
        adjusted_fraction=full_kelly_f * get_effective_param("KELLY_FRACTION", KELLY_FRACTION) if full_kelly_f > 0 else 0.0,
        bet_size_usd=0.0,
        expected_value=0.0,
        confidence=confidence,
        should_trade=False,
        skip_reason=reason,
    )
