"""Order book depth analysis for slippage-aware bet sizing.

Fetches the CLOB order book directly via public HTTP endpoint (no auth)
and computes expected slippage for a given order size. Used to adjust
Kelly-optimal bets downward when liquidity is insufficient.
"""

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from config.settings import MAX_ACCEPTABLE_SLIPPAGE, MIN_DEPTH_USD

logger = logging.getLogger(__name__)

CLOB_BOOK_URL = "https://clob.polymarket.com/book"
BOOK_FETCH_TIMEOUT = 10  # seconds


@dataclass
class DepthAnalysis:
    """Result of order book depth analysis for a single side."""

    token_id: str
    side: str                      # "BUY_YES" or "BUY_NO"
    best_price: float              # Best available ask price
    total_depth_usd: float         # Total USD available on the ask side
    avg_fill_price: float          # Weighted avg price to fill bet_size_usd
    slippage: float                # (avg_fill - best_price) / best_price
    max_fillable_usd: float        # Max bet that can be filled at acceptable slippage
    levels: int                    # Number of price levels on this side
    adjusted_bet_usd: float        # Recommended bet after depth adjustment
    skip_reason: str               # Non-empty if depth is insufficient


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""

    price: float
    size: float  # number of shares


async def fetch_order_book(token_id: str) -> list[OrderBookLevel]:
    """Fetch ask-side order book for a token from the CLOB API.

    Returns sorted list of ask levels (lowest price first).
    The CLOB book endpoint is public — no auth required.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=BOOK_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                CLOB_BOOK_URL, params={"token_id": token_id}
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "CLOB book fetch failed: status=%d token=%s",
                        resp.status, token_id[:20],
                    )
                    return []
                data = await resp.json()
    except Exception as e:
        logger.warning("CLOB book fetch error for %s: %s", token_id[:20], e)
        return []

    asks = data.get("asks", [])
    levels: list[OrderBookLevel] = []
    for ask in asks:
        try:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price > 0 and size > 0:
                levels.append(OrderBookLevel(price=price, size=size))
        except (ValueError, TypeError):
            continue

    # Sort by price ascending (cheapest first)
    levels.sort(key=lambda lv: lv.price)
    return levels


def compute_slippage(
    levels: list[OrderBookLevel],
    bet_size_usd: float,
) -> tuple[float, float, float]:
    """Walk ask levels to compute average fill price and slippage.

    Parameters
    ----------
    levels : list[OrderBookLevel]
        Ask levels sorted by price ascending.
    bet_size_usd : float
        Desired bet in USD.

    Returns
    -------
    tuple of (avg_fill_price, slippage, max_fillable_usd)
        avg_fill_price: weighted average price across consumed levels
        slippage: (avg_fill - best_price) / best_price (0 if single level)
        max_fillable_usd: total USD available across all levels
    """
    if not levels:
        return 0.0, 1.0, 0.0

    best_price = levels[0].price
    total_cost = 0.0
    total_shares = 0.0
    remaining_usd = bet_size_usd

    for level in levels:
        level_usd = level.price * level.size  # total cost to consume this level
        if level_usd >= remaining_usd:
            # Partial fill of this level
            shares_here = remaining_usd / level.price
            total_cost += remaining_usd
            total_shares += shares_here
            remaining_usd = 0.0
            break
        else:
            # Consume entire level
            total_cost += level_usd
            total_shares += level.size
            remaining_usd -= level_usd

    # Max fillable across entire book
    max_fillable = sum(lv.price * lv.size for lv in levels)

    if total_shares == 0:
        return 0.0, 1.0, max_fillable

    avg_fill = total_cost / total_shares
    slippage = (avg_fill - best_price) / best_price if best_price > 0 else 0.0

    return avg_fill, slippage, max_fillable


def find_max_fillable_at_slippage(
    levels: list[OrderBookLevel],
    max_slippage: float,
) -> float:
    """Find the maximum USD bet that stays within a slippage threshold.

    Uses binary search over bet sizes to find the largest bet where
    slippage <= max_slippage.
    """
    if not levels:
        return 0.0

    max_total = sum(lv.price * lv.size for lv in levels)
    if max_total <= 0:
        return 0.0

    # Check if even the full book is within slippage
    _, full_slippage, _ = compute_slippage(levels, max_total)
    if full_slippage <= max_slippage:
        return max_total

    # Binary search
    lo, hi = 0.0, max_total
    for _ in range(50):  # converge to $0.01 precision
        mid = (lo + hi) / 2.0
        _, slip, _ = compute_slippage(levels, mid)
        if slip <= max_slippage:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.01:
            break

    return lo


async def analyze_depth(
    token_id: str,
    side: str,
    bet_size_usd: float,
) -> DepthAnalysis:
    """Fetch order book and analyze depth for a proposed trade.

    Parameters
    ----------
    token_id : str
        The CLOB token ID to check (YES token for BUY_YES, NO token for BUY_NO).
    side : str
        "BUY_YES" or "BUY_NO".
    bet_size_usd : float
        Kelly-optimal bet size in USD.

    Returns
    -------
    DepthAnalysis
        Full depth analysis with adjusted bet recommendation.
    """
    levels = await fetch_order_book(token_id)

    if not levels:
        return DepthAnalysis(
            token_id=token_id,
            side=side,
            best_price=0.0,
            total_depth_usd=0.0,
            avg_fill_price=0.0,
            slippage=1.0,
            max_fillable_usd=0.0,
            levels=0,
            adjusted_bet_usd=0.0,
            skip_reason="no order book data",
        )

    best_price = levels[0].price
    total_depth_usd = sum(lv.price * lv.size for lv in levels)
    avg_fill, slippage, max_fillable = compute_slippage(levels, bet_size_usd)

    # Determine adjusted bet
    skip_reason = ""
    adjusted_bet = bet_size_usd

    if total_depth_usd < MIN_DEPTH_USD:
        logger.info(
            "Depth skip: insufficient depth $%.0f < $%.0f",
            total_depth_usd, MIN_DEPTH_USD,
        )
        skip_reason = "insufficient depth"
        adjusted_bet = 0.0
    elif slippage > MAX_ACCEPTABLE_SLIPPAGE:
        # Find the max bet that keeps slippage acceptable
        max_at_slippage = find_max_fillable_at_slippage(levels, MAX_ACCEPTABLE_SLIPPAGE)
        if max_at_slippage < 1.0:
            logger.info(
                "Depth skip: slippage too high %.1f%% even for minimum bet", slippage * 100,
            )
            skip_reason = "slippage too high"
            adjusted_bet = 0.0
        else:
            adjusted_bet = min(bet_size_usd, max_at_slippage)
            logger.info(
                "Depth adjustment: $%.2f → $%.2f (slippage %.1f%% → within %.1f%%)",
                bet_size_usd, adjusted_bet, slippage * 100, MAX_ACCEPTABLE_SLIPPAGE * 100,
            )
    else:
        adjusted_bet = bet_size_usd

    return DepthAnalysis(
        token_id=token_id,
        side=side,
        best_price=best_price,
        total_depth_usd=total_depth_usd,
        avg_fill_price=avg_fill,
        slippage=slippage,
        max_fillable_usd=max_fillable,
        levels=len(levels),
        adjusted_bet_usd=adjusted_bet,
        skip_reason=skip_reason,
    )
