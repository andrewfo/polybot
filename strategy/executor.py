"""Order execution and position management.

Provides PaperExecutor (instant fills, no CLOB) and TradeExecutor (live CLOB
orders). Both share risk guardrails and position management logic.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config.settings import (
    MAX_DAILY_LOSS_PCT,
    MAX_DRAWDOWN_PCT,
    MAX_NEW_TRADES_PER_HOUR,
    MAX_SIMULTANEOUS_POSITIONS,
    SLIPPAGE_BUFFER,
    STALE_ORDER_MINUTES,
    TEST_BANKROLL,
)
from core import db
from core.client import ClobClientWrapper
from strategy.kelly import TradeDecision

logger = logging.getLogger(__name__)


class AutoStopError(Exception):
    """Raised when a critical guardrail triggers and the bot must stop."""
    pass


# ---------------------------------------------------------------------------
# Risk guardrails (standalone functions)
# ---------------------------------------------------------------------------

def check_position_count() -> tuple[bool, str]:
    """Check if we're at the max simultaneous positions limit."""
    positions = db.get_open_positions()
    count = len(positions)
    if count >= MAX_SIMULTANEOUS_POSITIONS:
        return False, f"at position limit ({count}/{MAX_SIMULTANEOUS_POSITIONS})"
    return True, ""


def check_trade_rate() -> tuple[bool, str]:
    """Check if we've exceeded the hourly trade rate limit."""
    recent = db.get_recent_trade_count(hours=1)
    if recent >= MAX_NEW_TRADES_PER_HOUR:
        return False, f"trade rate limit ({recent}/{MAX_NEW_TRADES_PER_HOUR} per hour)"
    return True, ""


def check_drawdown(bankroll: float) -> tuple[bool, str]:
    """Check if total drawdown exceeds the max threshold. Raises AutoStopError."""
    total_pnl = db.get_total_pnl()
    if bankroll <= 0:
        return True, ""
    drawdown_pct = -total_pnl / bankroll if total_pnl < 0 else 0.0
    if drawdown_pct >= MAX_DRAWDOWN_PCT:
        raise AutoStopError(
            f"max drawdown exceeded: {drawdown_pct:.1%} >= {MAX_DRAWDOWN_PCT:.1%}"
        )
    return True, ""


def check_daily_loss(bankroll: float) -> tuple[bool, str]:
    """Check if daily loss exceeds the max threshold. Raises AutoStopError."""
    daily_pnl = db.get_daily_pnl()
    if bankroll <= 0:
        return True, ""
    daily_loss_pct = -daily_pnl / bankroll if daily_pnl < 0 else 0.0
    if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
        raise AutoStopError(
            f"max daily loss exceeded: {daily_loss_pct:.1%} >= {MAX_DAILY_LOSS_PCT:.1%}"
        )
    return True, ""


def check_all_guardrails(bankroll: float) -> tuple[bool, str]:
    """Run all risk guardrails. Returns (ok, reason). May raise AutoStopError."""
    # These raise AutoStopError on critical failures
    check_drawdown(bankroll)
    check_daily_loss(bankroll)

    # These return soft blocks
    ok, reason = check_position_count()
    if not ok:
        return False, reason

    ok, reason = check_trade_rate()
    if not ok:
        return False, reason

    return True, ""


# ---------------------------------------------------------------------------
# Price computation
# ---------------------------------------------------------------------------

def compute_limit_price(
    decision: TradeDecision,
    market_data: dict[str, Any],
) -> tuple[float, str]:
    """Compute limit price and select token_id based on trade side.

    Returns (limit_price, token_id).
    """
    clob_token_ids = market_data.get("clobTokenIds", [])
    best_ask = float(market_data.get("bestAsk", 0) or 0)
    best_bid = float(market_data.get("bestBid", 0) or 0)

    if decision.side == "BUY_YES":
        # Buy YES token at slightly below best ask
        price = best_ask - SLIPPAGE_BUFFER
        token_id = clob_token_ids[0] if clob_token_ids else decision.token_id
    else:
        # Buy NO token: NO price = 1 - YES price
        price = (1.0 - best_bid) - SLIPPAGE_BUFFER
        token_id = clob_token_ids[1] if len(clob_token_ids) > 1 else decision.token_id

    # Clamp to valid range
    price = max(0.01, min(0.99, price))
    return price, token_id


# ---------------------------------------------------------------------------
# PaperExecutor
# ---------------------------------------------------------------------------

class PaperExecutor:
    """Paper trading executor with instant fills."""

    async def execute_trade(
        self,
        decision: TradeDecision,
        market_data: dict[str, Any],
        bankroll: float,
    ) -> str | None:
        """Execute a paper trade. Returns trade_id or None if blocked."""
        # Run guardrails
        ok, reason = check_all_guardrails(bankroll)
        if not ok:
            logger.warning("Trade blocked by guardrail: %s", reason)
            return None

        limit_price, token_id = compute_limit_price(decision, market_data)
        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Record trade as immediately filled
        db.record_trade(
            trade_id=trade_id,
            market_id=decision.market_id,
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=decision.bet_size_usd / limit_price if limit_price > 0 else 0,
            status="FILLED",
            paper=True,
            order_id=f"paper-{trade_id[:8]}",
            placed_at=now,
            market_question=decision.market_question,
        )

        # Upsert position
        db.upsert_position(
            token_id=token_id,
            market_id=decision.market_id,
            market_question=decision.market_question,
            side="BUY",
            avg_entry=limit_price,
            size=decision.bet_size_usd / limit_price if limit_price > 0 else 0,
            current_price=limit_price,
            paper=True,
        )

        logger.info(
            "PAPER TRADE: %s %s @ %.4f | size=$%.2f | trade_id=%s",
            decision.side, decision.market_question[:50], limit_price,
            decision.bet_size_usd, trade_id,
        )
        return trade_id

    async def monitor_orders(self) -> None:
        """Mark any lingering PENDING paper trades as filled."""
        open_trades = db.get_open_trades()
        for trade in open_trades:
            if trade.get("paper"):
                db.update_trade_status(trade["id"], "FILLED", fill_price=trade["price"])
                logger.info("Paper trade %s auto-filled", trade["id"])

    async def manage_positions(self) -> None:
        """Update unrealized PnL for all paper positions using Gamma API prices."""
        positions = db.get_open_positions()
        if not positions:
            return

        for pos in positions:
            if not pos.get("paper"):
                continue

            # Fetch current price from Gamma
            current_price = await _fetch_gamma_price(pos["market_id"])
            if current_price is None:
                continue

            unrealized_pnl = (current_price - pos["avg_entry"]) * pos["size"]
            loss_pct = unrealized_pnl / (pos["avg_entry"] * pos["size"]) if pos["avg_entry"] * pos["size"] > 0 else 0

            db.upsert_position(
                token_id=pos["token_id"],
                market_id=pos["market_id"],
                market_question=pos.get("market_question", ""),
                side=pos["side"],
                avg_entry=pos["avg_entry"],
                size=pos["size"],
                current_price=current_price,
                paper=True,
            )

            if loss_pct < -0.20:
                logger.warning(
                    "LOSS WARNING: %s down %.1f%% (entry=%.4f current=%.4f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    loss_pct * 100, pos["avg_entry"], current_price,
                )


# ---------------------------------------------------------------------------
# TradeExecutor
# ---------------------------------------------------------------------------

class TradeExecutor:
    """Live trading executor using CLOB API."""

    def __init__(self, client: ClobClientWrapper) -> None:
        self._client = client

    async def execute_trade(
        self,
        decision: TradeDecision,
        market_data: dict[str, Any],
        bankroll: float,
    ) -> str | None:
        """Execute a live trade. Returns trade_id or None if blocked."""
        ok, reason = check_all_guardrails(bankroll)
        if not ok:
            logger.warning("Trade blocked by guardrail: %s", reason)
            return None

        limit_price, token_id = compute_limit_price(decision, market_data)
        size = decision.bet_size_usd / limit_price if limit_price > 0 else 0
        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Place order on CLOB
        order_id = await self._client.place_limit_order(
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=size,
        )

        # Record trade as PENDING
        db.record_trade(
            trade_id=trade_id,
            market_id=decision.market_id,
            token_id=token_id,
            side="BUY",
            price=limit_price,
            size=size,
            status="PENDING",
            paper=False,
            order_id=order_id,
            placed_at=now,
            market_question=decision.market_question,
        )

        logger.info(
            "LIVE TRADE: %s %s @ %.4f | size=%.2f | order_id=%s | trade_id=%s",
            decision.side, decision.market_question[:50], limit_price,
            size, order_id, trade_id,
        )
        return trade_id

    async def monitor_orders(self) -> None:
        """Check CLOB open orders vs DB pending trades. Expire stale orders."""
        open_trades = db.get_open_trades()
        if not open_trades:
            return

        # Get open orders from CLOB
        try:
            clob_orders = await self._client.get_open_orders()
        except Exception as e:
            logger.error("Failed to fetch open orders: %s", e)
            return

        clob_order_ids = {
            o.get("id", o.get("orderID", "")) for o in clob_orders
        }
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(minutes=STALE_ORDER_MINUTES)

        for trade in open_trades:
            if trade.get("paper"):
                continue

            order_id = trade.get("order_id", "")

            # If order is no longer on CLOB, it was filled
            if order_id and order_id not in clob_order_ids:
                db.update_trade_status(trade["id"], "FILLED", fill_price=trade["price"])
                # Upsert position
                db.upsert_position(
                    token_id=trade["token_id"],
                    market_id=trade["market_id"],
                    market_question=trade.get("market_question", ""),
                    side=trade["side"],
                    avg_entry=trade["price"],
                    size=trade["size"],
                    current_price=trade["price"],
                    paper=False,
                )
                logger.info("Order %s filled (trade %s)", order_id, trade["id"])
                continue

            # Check for stale orders
            placed_at = trade.get("placed_at")
            if placed_at:
                try:
                    placed_time = datetime.fromisoformat(placed_at)
                    if placed_time.tzinfo is None:
                        placed_time = placed_time.replace(tzinfo=timezone.utc)
                    if placed_time < stale_cutoff:
                        # Cancel and expire
                        if order_id:
                            await self._client.cancel_order(order_id)
                        db.update_trade_status(trade["id"], "EXPIRED")
                        logger.info(
                            "Expired stale order %s (trade %s, placed %s)",
                            order_id, trade["id"], placed_at,
                        )
                except (ValueError, TypeError):
                    pass

    async def manage_positions(self) -> None:
        """Update unrealized PnL for all live positions using Gamma API prices."""
        positions = db.get_open_positions()
        if not positions:
            return

        for pos in positions:
            if pos.get("paper"):
                continue

            current_price = await _fetch_gamma_price(pos["market_id"])
            if current_price is None:
                continue

            unrealized_pnl = (current_price - pos["avg_entry"]) * pos["size"]
            loss_pct = unrealized_pnl / (pos["avg_entry"] * pos["size"]) if pos["avg_entry"] * pos["size"] > 0 else 0

            db.upsert_position(
                token_id=pos["token_id"],
                market_id=pos["market_id"],
                market_question=pos.get("market_question", ""),
                side=pos["side"],
                avg_entry=pos["avg_entry"],
                size=pos["size"],
                current_price=current_price,
                paper=False,
            )

            if loss_pct < -0.20:
                logger.warning(
                    "LOSS WARNING: %s down %.1f%% (entry=%.4f current=%.4f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    loss_pct * 100, pos["avg_entry"], current_price,
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"


async def _fetch_gamma_price(condition_id: str) -> float | None:
    """Fetch current YES price from Gamma API for a market."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GAMMA_API_URL,
                params={"id": condition_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if isinstance(data, list) and data:
                    market = data[0]
                elif isinstance(data, dict):
                    market = data
                else:
                    return None

                outcome_prices = market.get("outcomePrices", "")
                if isinstance(outcome_prices, str):
                    import json
                    try:
                        prices = json.loads(outcome_prices)
                    except (json.JSONDecodeError, TypeError):
                        return None
                elif isinstance(outcome_prices, list):
                    prices = outcome_prices
                else:
                    return None

                return float(prices[0]) if prices else None
    except Exception as e:
        logger.debug("Failed to fetch Gamma price for %s: %s", condition_id, e)
        return None
