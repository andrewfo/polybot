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
    MARKET_COOLDOWN_MINUTES,
    MAX_CORRELATED_POSITIONS,
    MAX_DAILY_LOSS_PCT,
    MAX_DRAWDOWN_PCT,
    MAX_ENTRY_SPREAD_PCT,
    MAX_NEW_TRADES_PER_HOUR,
    MAX_OPEN_POSITIONS,
    PAPER_REALISTIC_PRICING,
    SLIPPAGE_BUFFER,
    STALE_ORDER_MINUTES,
    STOP_LOSS_MIN_TICKS,
    STOP_LOSS_PCT,
    STOP_LOSS_TICK_SIZE,
    TAKE_PROFIT_PCT,
    TEST_BANKROLL,
    get_effective_param,
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


def check_open_positions() -> tuple[bool, str]:
    """Check if total open positions exceeds the cap."""
    positions = db.get_open_positions()
    if len(positions) >= MAX_OPEN_POSITIONS:
        return False, f"max open positions reached ({len(positions)}/{MAX_OPEN_POSITIONS})"
    return True, ""


def check_correlated_positions(market_question: str) -> tuple[bool, str]:
    """Check if we already have too many positions on the same underlying asset.

    Extracts the primary asset (e.g., Bitcoin, Ethereum) from the market question
    and counts how many open positions reference that same asset.
    """
    ASSET_KEYWORDS: dict[str, list[str]] = {
        "bitcoin": ["bitcoin", "btc"],
        "ethereum": ["ethereum", "eth"],
        "solana": ["solana", "sol"],
        "xrp": ["xrp", "ripple"],
        "dogecoin": ["dogecoin", "doge"],
        "gold": ["gold", "xauusd"],
    }

    q_lower = market_question.lower()
    matched_asset = None
    for asset, keywords in ASSET_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            matched_asset = asset
            break

    if matched_asset is None:
        return True, ""

    positions = db.get_open_positions()
    count = 0
    keywords = ASSET_KEYWORDS[matched_asset]
    for pos in positions:
        pos_q = (pos.get("market_question") or "").lower()
        if any(kw in pos_q for kw in keywords):
            count += 1

    if count >= MAX_CORRELATED_POSITIONS:
        return False, f"max correlated positions for {matched_asset} ({count}/{MAX_CORRELATED_POSITIONS})"
    return True, ""


def check_balance(bankroll: float) -> tuple[bool, str]:
    """Check if available cash exceeds the minimum bankroll reserve."""
    from config.settings import MIN_BANKROLL_RESERVE
    balance = db.get_paper_balance(bankroll)
    available = balance.get("available_cash", bankroll)
    reserve = max(MIN_BANKROLL_RESERVE, bankroll * 0.05)
    if available <= reserve:
        return False, f"insufficient balance (${available:.2f} <= ${reserve:.2f} reserve)"
    return True, ""


def check_market_cooldown(market_id: str) -> tuple[bool, str]:
    """Block re-entry on a market within MARKET_COOLDOWN_MINUTES of its last close.

    Prevents stop-loss / take-profit churn where the bot re-opens the same
    position within minutes of the previous exit.
    """
    if not market_id or MARKET_COOLDOWN_MINUTES <= 0:
        return True, ""
    last_close = db.get_last_close_time_for_market(market_id)
    if not last_close:
        return True, ""
    try:
        closed_time = datetime.fromisoformat(last_close)
        if closed_time.tzinfo is None:
            closed_time = closed_time.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True, ""
    elapsed_min = (datetime.now(timezone.utc) - closed_time).total_seconds() / 60.0
    if elapsed_min < MARKET_COOLDOWN_MINUTES:
        remaining = MARKET_COOLDOWN_MINUTES - elapsed_min
        return False, f"market cooldown active ({remaining:.0f}m remaining since last close)"
    return True, ""


def check_entry_spread(market_data: dict[str, Any]) -> tuple[bool, str]:
    """Refuse to enter when the relative spread would exceed MAX_ENTRY_SPREAD_PCT.

    Crossing a 30%+ relative spread books an immediate unrealized loss larger
    than STOP_LOSS_PCT, guaranteeing a stop-out on the first mark.
    """
    try:
        best_ask = float(market_data.get("bestAsk", 0) or 0)
        best_bid = float(market_data.get("bestBid", 0) or 0)
    except (TypeError, ValueError):
        return True, ""
    if best_ask <= 0 or best_bid <= 0 or best_ask <= best_bid:
        return True, ""
    mid = (best_ask + best_bid) / 2.0
    if mid <= 0:
        return True, ""
    rel_spread = (best_ask - best_bid) / mid
    if rel_spread > MAX_ENTRY_SPREAD_PCT:
        return False, (
            f"entry spread too wide ({rel_spread:.1%} > {MAX_ENTRY_SPREAD_PCT:.1%}, "
            f"bid={best_bid:.3f} ask={best_ask:.3f})"
        )
    return True, ""


def check_all_guardrails(
    bankroll: float,
    market_question: str = "",
    market_id: str = "",
    market_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Run all risk guardrails. Returns (ok, reason). May raise AutoStopError."""
    # These raise AutoStopError on critical failures
    check_drawdown(bankroll)
    check_daily_loss(bankroll)

    # These return soft blocks
    ok, reason = check_open_positions()
    if not ok:
        return False, reason

    ok, reason = check_trade_rate()
    if not ok:
        return False, reason

    ok, reason = check_balance(bankroll)
    if not ok:
        return False, reason

    if market_id:
        ok, reason = check_market_cooldown(market_id)
        if not ok:
            return False, reason

    if market_question:
        ok, reason = check_correlated_positions(market_question)
        if not ok:
            return False, reason

    if market_data is not None:
        ok, reason = check_entry_spread(market_data)
        if not ok:
            return False, reason

    return True, ""


# ---------------------------------------------------------------------------
# Exit thresholds
# ---------------------------------------------------------------------------

def effective_stop_loss_pct(avg_entry: float, base_sl: float) -> float:
    """Return the effective stop-loss pct, widened on low-priced markets.

    Polymarket's 1-cent tick at sub-$0.20 prices makes a flat percentage stop
    fire on a single tick of normal book noise. The floor requires at least
    STOP_LOSS_MIN_TICKS of price movement against entry before stopping.
    """
    if avg_entry <= 0:
        return base_sl
    tick_floor = (STOP_LOSS_MIN_TICKS * STOP_LOSS_TICK_SIZE) / avg_entry
    return max(base_sl, tick_floor)


# ---------------------------------------------------------------------------
# Price computation
# ---------------------------------------------------------------------------

def compute_limit_price(
    decision: TradeDecision,
    market_data: dict[str, Any],
    depth_slippage: float | None = None,
) -> tuple[float, str]:
    """Compute limit price and select token_id based on trade side.

    When depth_slippage is provided (from DepthAnalysis), the buffer is set
    to 1.5x the measured slippage instead of the fixed SLIPPAGE_BUFFER.
    This produces tighter limits on deep books and wider limits on thin ones.

    Returns (limit_price, token_id).
    """
    from strategy.market_filter import extract_clob_token_ids
    clob_token_ids = extract_clob_token_ids(market_data)
    best_ask = float(market_data.get("bestAsk", 0) or 0)
    best_bid = float(market_data.get("bestBid", 0) or 0)

    # Use measured slippage when available, otherwise fall back to fixed buffer
    if depth_slippage is not None and depth_slippage > 0:
        buffer = min(depth_slippage * 1.5, 0.05)  # cap at 5%
    else:
        buffer = SLIPPAGE_BUFFER

    if decision.side == "BUY_YES":
        # Cross the spread: bid at or above best ask so the order actually fills
        price = best_ask + buffer
        token_id = clob_token_ids[0] if clob_token_ids else decision.token_id
    else:
        # Buy NO token: NO ask = 1 - YES bid; cross it by adding the buffer
        price = (1.0 - best_bid) + buffer
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
        ok, reason = check_all_guardrails(
            bankroll,
            market_question=decision.market_question,
            market_id=decision.market_id,
            market_data=market_data,
        )
        if not ok:
            logger.warning("Trade blocked by guardrail: %s", reason)
            return None

        limit_price, token_id = compute_limit_price(decision, market_data)
        # For paper, simulate the realistic fill at best_ask (BUY_YES) or
        # 1 - best_bid (BUY_NO). The limit price includes a spread-crossing
        # buffer that a real CLOB would not actually charge — booking it as
        # the fill silently bakes a ~spread+buffer loss into every paper
        # entry. Fall back to limit_price when book data is missing.
        best_ask = float(market_data.get("bestAsk", 0) or 0)
        best_bid = float(market_data.get("bestBid", 0) or 0)
        if decision.side == "BUY_YES" and 0 < best_ask < 1:
            fill_price = best_ask
        elif decision.side == "BUY_NO" and 0 < best_bid < 1:
            fill_price = 1.0 - best_bid
        else:
            fill_price = limit_price
        fill_price = max(0.01, min(0.99, fill_price))

        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        size = decision.bet_size_usd / fill_price if fill_price > 0 else 0

        # Record trade as immediately filled
        db.record_trade(
            trade_id=trade_id,
            market_id=decision.market_id,
            token_id=token_id,
            side=decision.side,
            price=fill_price,
            size=size,
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
            side=decision.side,
            avg_entry=fill_price,
            size=size,
            current_price=fill_price,
            paper=True,
        )

        logger.info(
            "PAPER TRADE: %s %s @ %.4f | size=$%.2f | trade_id=%s",
            decision.side, decision.market_question[:50], fill_price,
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
        """Update unrealized PnL for all paper positions using Gamma API prices.

        Closes positions that hit take-profit or stop-loss thresholds.
        """
        positions = db.get_open_positions()
        if not positions:
            return

        eff_tp = get_effective_param("TAKE_PROFIT_PCT", TAKE_PROFIT_PCT)
        eff_sl = get_effective_param("STOP_LOSS_PCT", STOP_LOSS_PCT)

        for pos in positions:
            if not pos.get("paper"):
                continue

            pos_side = pos.get("side", "BUY_YES")
            # Fetch the order book so we can value the position at the mid
            # (display mark) and evaluate TP/SL against the bid (what a real
            # sell would realize). When PAPER_REALISTIC_PRICING is off, fall
            # back to mid for both — preserves legacy behavior.
            mark_price: float | None = None
            exit_price: float | None = None
            if PAPER_REALISTIC_PRICING:
                book = await _fetch_gamma_book(pos["market_id"])
                if book is not None:
                    if pos_side == "BUY_YES":
                        mark_price = book["mid"]
                        exit_price = book["best_bid"]
                    else:
                        mark_price = 1.0 - book["mid"]
                        exit_price = 1.0 - book["best_ask"]
            if mark_price is None or exit_price is None:
                # Fallback path: bid/ask unavailable or flag off — use mid for both
                mid = await _fetch_gamma_price(pos["market_id"], side=pos_side)
                if mid is None:
                    continue
                mark_price = mid
                exit_price = mid

            cost_basis = pos["avg_entry"] * pos["size"]
            # PnL for TP/SL evaluation uses the realizable exit price.
            realizable_pnl = (exit_price - pos["avg_entry"]) * pos["size"]
            pnl_pct = realizable_pnl / cost_basis if cost_basis > 0 else 0
            dyn_sl = effective_stop_loss_pct(pos["avg_entry"], eff_sl)

            if pnl_pct >= eff_tp:
                logger.info(
                    "TAKE PROFIT: %s up %.1f%% (entry=%.4f mark=%.4f exit=%.4f, PnL=$%.2f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], mark_price, exit_price, realizable_pnl,
                )
                db.close_position(pos["token_id"], exit_price=exit_price, realized_pnl=realizable_pnl, reason="take_profit")
                continue

            if pnl_pct <= -dyn_sl:
                logger.warning(
                    "STOP LOSS: %s down %.1f%% (entry=%.4f mark=%.4f exit=%.4f, PnL=$%.2f, sl=%.1f%%)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], mark_price, exit_price, realizable_pnl, dyn_sl * 100,
                )
                db.close_position(pos["token_id"], exit_price=exit_price, realized_pnl=realizable_pnl, reason="stop_loss")
                continue

            db.upsert_position(
                token_id=pos["token_id"],
                market_id=pos["market_id"],
                market_question=pos.get("market_question", ""),
                side=pos_side,
                avg_entry=pos["avg_entry"],
                size=pos["size"],
                current_price=mark_price,
                paper=True,
            )

            if pnl_pct < -0.20:
                logger.warning(
                    "LOSS WARNING: %s down %.1f%% (entry=%.4f mark=%.4f exit=%.4f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], mark_price, exit_price,
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
        ok, reason = check_all_guardrails(
            bankroll,
            market_question=decision.market_question,
            market_id=decision.market_id,
            market_data=market_data,
        )
        if not ok:
            logger.warning("Trade blocked by guardrail: %s", reason)
            return None

        limit_price, token_id = compute_limit_price(decision, market_data)
        size = decision.bet_size_usd / limit_price if limit_price > 0 else 0
        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Place order on CLOB
        try:
            order_id = await self._client.place_limit_order(
                token_id=token_id,
                side="BUY",
                price=limit_price,
                size=size,
            )
        except Exception:
            logger.exception(
                "place_limit_order failed for %s %s @ %.4f size=%.2f — no trade recorded",
                decision.side, decision.market_question[:50], limit_price, size,
            )
            return None

        # Record trade as PENDING
        db.record_trade(
            trade_id=trade_id,
            market_id=decision.market_id,
            token_id=token_id,
            side=decision.side,
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
                # Query CLOB for actual fill price; fall back to limit on failure.
                actual_price = trade["price"]
                actual_size = trade["size"]
                try:
                    fill = await self._client.get_order_fill(order_id)
                except Exception as e:
                    logger.warning(
                        "get_order_fill(%s) errored, recording limit price: %s",
                        order_id, e,
                    )
                    fill = None
                if fill is not None:
                    actual_price = fill["fill_price"]
                    actual_size = fill["filled_size"]
                    if abs(actual_price - trade["price"]) > 0.0001:
                        logger.info(
                            "Order %s filled at %.4f (limit was %.4f) | trade %s",
                            order_id, actual_price, trade["price"], trade["id"],
                        )
                else:
                    logger.warning(
                        "Order %s: actual fill price unavailable; recording limit %.4f",
                        order_id, trade["price"],
                    )

                db.update_trade_status(trade["id"], "FILLED", fill_price=actual_price)
                db.upsert_position(
                    token_id=trade["token_id"],
                    market_id=trade["market_id"],
                    market_question=trade.get("market_question", ""),
                    side=trade["side"],
                    avg_entry=actual_price,
                    size=actual_size,
                    current_price=actual_price,
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
        """Update unrealized PnL for all live positions using Gamma API prices.

        Closes positions that hit take-profit or stop-loss thresholds
        by placing limit sell orders on the CLOB.
        """
        positions = db.get_open_positions()
        if not positions:
            return

        eff_tp = get_effective_param("TAKE_PROFIT_PCT", TAKE_PROFIT_PCT)
        eff_sl = get_effective_param("STOP_LOSS_PCT", STOP_LOSS_PCT)

        for pos in positions:
            if pos.get("paper"):
                continue

            pos_side = pos.get("side", "BUY_YES")
            current_price = await _fetch_gamma_price(pos["market_id"], side=pos_side)
            if current_price is None:
                continue

            cost_basis = pos["avg_entry"] * pos["size"]
            unrealized_pnl = (current_price - pos["avg_entry"]) * pos["size"]
            pnl_pct = unrealized_pnl / cost_basis if cost_basis > 0 else 0
            dyn_sl = effective_stop_loss_pct(pos["avg_entry"], eff_sl)

            # Take-profit: place sell order
            if pnl_pct >= eff_tp:
                logger.info(
                    "TAKE PROFIT: %s up %.1f%% (entry=%.4f current=%.4f, PnL=$%.2f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], current_price, unrealized_pnl,
                )
                try:
                    sell_price = max(0.01, min(0.99, current_price - SLIPPAGE_BUFFER))
                    await self._client.place_limit_order(
                        token_id=pos["token_id"],
                        side="SELL",
                        price=sell_price,
                        size=pos["size"],
                    )
                    db.close_position(pos["token_id"], exit_price=current_price, realized_pnl=unrealized_pnl, reason="take_profit")
                except Exception as e:
                    logger.error("Failed to close position for take-profit: %s", e)
                continue

            # Stop-loss: place sell order on tick-aware threshold
            if pnl_pct <= -dyn_sl:
                logger.warning(
                    "STOP LOSS: %s down %.1f%% (entry=%.4f current=%.4f, PnL=$%.2f, sl=%.1f%%)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], current_price, unrealized_pnl, dyn_sl * 100,
                )
                try:
                    sell_price = max(0.01, min(0.99, current_price - SLIPPAGE_BUFFER))
                    await self._client.place_limit_order(
                        token_id=pos["token_id"],
                        side="SELL",
                        price=sell_price,
                        size=pos["size"],
                    )
                    db.close_position(pos["token_id"], exit_price=current_price, realized_pnl=unrealized_pnl, reason="stop_loss")
                except Exception as e:
                    logger.error("Failed to close position for stop-loss: %s", e)
                continue

            db.upsert_position(
                token_id=pos["token_id"],
                market_id=pos["market_id"],
                market_question=pos.get("market_question", ""),
                side=pos_side,
                avg_entry=pos["avg_entry"],
                size=pos["size"],
                current_price=current_price,
                paper=False,
            )

            if pnl_pct < -0.20:
                logger.warning(
                    "LOSS WARNING: %s down %.1f%% (entry=%.4f current=%.4f)",
                    pos.get("market_question", pos["token_id"])[:50],
                    pnl_pct * 100, pos["avg_entry"], current_price,
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"


async def _fetch_gamma_market(condition_id: str) -> dict[str, Any] | None:
    """Fetch the raw Gamma market dict for a condition_id, or None.

    Single network round-trip; callers parse the fields they need. Uses the
    cached Gamma numeric ID since Gamma's ?id= param requires the numeric ID,
    not the condition_id (0x...).
    """
    from core.db import get_gamma_id_for_condition

    gamma_id = get_gamma_id_for_condition(condition_id)
    if not gamma_id:
        logger.debug("No cached Gamma ID for %s, cannot fetch market", condition_id[:20])
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GAMMA_API_URL,
                params={"id": gamma_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if isinstance(data, list) and data:
                    return data[0]
                if isinstance(data, dict):
                    return data
                return None
    except Exception as e:
        logger.debug("Failed to fetch Gamma market for %s: %s", condition_id, e)
        return None


def _parse_bid_ask(market: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract (best_bid, best_ask) floats from a Gamma market dict, or (None, None)."""
    best_bid_raw = market.get("bestBid")
    best_ask_raw = market.get("bestAsk")
    try:
        best_bid = float(best_bid_raw) if best_bid_raw is not None else None
    except (TypeError, ValueError):
        best_bid = None
    try:
        best_ask = float(best_ask_raw) if best_ask_raw is not None else None
    except (TypeError, ValueError):
        best_ask = None
    return best_bid, best_ask


async def _fetch_gamma_book(condition_id: str) -> dict[str, float] | None:
    """Return {'best_bid', 'best_ask', 'mid'} for a market, or None.

    Returns None when bid or ask is missing or out of valid (0,1) range; callers
    that can tolerate a single-side price should use ``_fetch_gamma_price``
    instead, which falls back to outcomePrices.
    """
    market = await _fetch_gamma_market(condition_id)
    if market is None:
        return None
    best_bid, best_ask = _parse_bid_ask(market)
    if (
        best_bid is None
        or best_ask is None
        or not (0 < best_bid <= best_ask < 1)
    ):
        return None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": (best_bid + best_ask) / 2.0,
    }


async def _fetch_gamma_price(condition_id: str, side: str = "BUY_YES") -> float | None:
    """Fetch mark-to-market price from Gamma API for a market.

    Returns the bid/ask midpoint of the relevant token. Mid is the standard
    mark — using the bid would book the whole round-trip spread as an
    unrealized loss the instant we fill, instantly tripping STOP_LOSS_PCT on
    any wide-spread market. Realization (actual sell) still happens at the bid.
    Falls back to outcomePrices (last/mid) when bid/ask are missing.
    """
    market = await _fetch_gamma_market(condition_id)
    if market is None:
        return None

    best_bid, best_ask = _parse_bid_ask(market)
    if (
        best_bid is not None
        and best_ask is not None
        and 0 < best_bid <= best_ask < 1
    ):
        yes_mid = (best_bid + best_ask) / 2.0
        return yes_mid if side == "BUY_YES" else 1.0 - yes_mid

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

    if not prices:
        return None
    try:
        yes_price = float(prices[0])
    except (TypeError, ValueError):
        return None
    if side == "BUY_NO":
        return 1.0 - yes_price
    return yes_price
