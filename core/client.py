"""Polymarket CLOB client wrapper with retry logic and rate limiting.

Wraps py-clob-client with:
- 3 retries with exponential backoff on all API calls
- Rate limiting: max 10 requests/second via asyncio semaphore
- Full logging of every API call (method, params, status, latency)
- Clear auth failure messages pointing to .env setup
"""

import asyncio
import functools
import logging
import os
import time
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY, SELL

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
CLOB_HOST = "https://clob.polymarket.com"

# Rate limit: 10 requests per second
RATE_LIMIT_SEMAPHORE_SIZE = 10
RATE_LIMIT_PERIOD = 1.0


class ClientError(Exception):
    """Raised on CLOB client failures."""
    pass


class AuthenticationError(ClientError):
    """Raised when authentication fails."""
    pass


def _parse_fill_from_order(order: Any) -> dict[str, float] | None:
    """Extract fill_price/filled_size from a py-clob-client get_order response.

    Different client versions return slightly different field names; check the
    common ones. Returns None when the order has no fill yet or fields are
    malformed.
    """
    if not isinstance(order, dict):
        return None
    size_keys = ("size_matched", "sizeMatched", "filled_size", "filledSize", "size_filled")
    price_keys = ("avg_fill_price", "avgFillPrice", "average_price", "averagePrice", "price")

    filled_size = None
    for k in size_keys:
        v = order.get(k)
        if v is None:
            continue
        try:
            filled_size = float(v)
            break
        except (TypeError, ValueError):
            continue
    if filled_size is None or filled_size <= 0:
        return None

    fill_price = None
    for k in price_keys:
        v = order.get(k)
        if v is None:
            continue
        try:
            fill_price = float(v)
            break
        except (TypeError, ValueError):
            continue
    if fill_price is None or not (0 < fill_price < 1):
        return None

    return {"fill_price": fill_price, "filled_size": filled_size}


def _parse_fill_from_trades(trades: Any, order_id: str) -> dict[str, float] | None:
    """Aggregate fills matching ``order_id`` across a trade list.

    Multiple partial fills are size-weighted into a single average price.
    """
    if not order_id:
        return None
    if isinstance(trades, dict):
        trades = trades.get("data", trades.get("trades", []))
    if not isinstance(trades, list):
        return None

    id_keys = ("order_id", "orderID", "orderId", "maker_order_id", "takerOrderId")
    size_keys = ("size", "matched_size", "match_size")
    price_keys = ("price", "match_price", "matched_price")

    total_size = 0.0
    weighted_price = 0.0
    for t in trades:
        if not isinstance(t, dict):
            continue
        if not any(t.get(k) == order_id for k in id_keys):
            continue
        size = None
        for k in size_keys:
            v = t.get(k)
            if v is None:
                continue
            try:
                size = float(v)
                break
            except (TypeError, ValueError):
                continue
        price = None
        for k in price_keys:
            v = t.get(k)
            if v is None:
                continue
            try:
                price = float(v)
                break
            except (TypeError, ValueError):
                continue
        if size is None or price is None or size <= 0 or not (0 < price < 1):
            continue
        total_size += size
        weighted_price += size * price

    if total_size <= 0:
        return None
    return {"fill_price": weighted_price / total_size, "filled_size": total_size}


class ClobClientWrapper:
    """Async wrapper around Polymarket's CLOB client.

    All methods use retry logic and rate limiting. The underlying
    py-clob-client is synchronous, so calls are run in the default
    executor to avoid blocking the event loop.
    """

    def __init__(
        self,
        private_key: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        chain_id: int = 137,  # Polygon mainnet
    ) -> None:
        self._private_key = private_key or os.environ.get("PRIVATE_KEY", "")
        self._api_key = api_key or os.environ.get("POLYMARKET_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("POLYMARKET_API_SECRET", "")
        self._api_passphrase = api_passphrase or os.environ.get("POLYMARKET_API_PASSPHRASE", "")

        if not self._private_key:
            raise AuthenticationError(
                "PRIVATE_KEY is required. Add it to your .env file. "
                "Run `python scripts/setup_wallet.py` for setup help."
            )

        # Normalize private key
        if not self._private_key.startswith("0x"):
            self._private_key = "0x" + self._private_key

        # Initialize the CLOB client
        try:
            self._client = ClobClient(
                CLOB_HOST,
                key=self._private_key,
                chain_id=chain_id,
            )

            # Set API credentials if available
            if self._api_key and self._api_secret and self._api_passphrase:
                self._client.set_api_creds(
                    self._client.create_or_derive_api_creds()
                )
                logger.info("CLOB client initialized with API credentials")
            else:
                logger.warning(
                    "CLOB client initialized without API credentials. "
                    "Some operations (placing orders) will fail. "
                    "Set POLYMARKET_API_KEY, POLYMARKET_API_SECRET, "
                    "POLYMARKET_API_PASSPHRASE in .env"
                )
        except Exception as e:
            raise AuthenticationError(
                f"Failed to initialize CLOB client: {e}. "
                "Check your PRIVATE_KEY and API credentials in .env. "
                "Run `python scripts/setup_wallet.py` for help."
            ) from e

        # Rate limiting semaphore
        self._semaphore = asyncio.Semaphore(RATE_LIMIT_SEMAPHORE_SIZE)
        self._last_request_time: float = 0.0

    async def _rate_limited_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute a synchronous CLOB client call with rate limiting and retry."""
        method_name = getattr(func, "__name__", str(func))

        for attempt in range(MAX_RETRIES):
            async with self._semaphore:
                # Enforce minimum spacing between requests
                now = time.monotonic()
                elapsed = now - self._last_request_time
                if elapsed < (RATE_LIMIT_PERIOD / RATE_LIMIT_SEMAPHORE_SIZE):
                    await asyncio.sleep(
                        (RATE_LIMIT_PERIOD / RATE_LIMIT_SEMAPHORE_SIZE) - elapsed
                    )

                start = time.monotonic()
                try:
                    # Run sync call in executor to not block event loop
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, functools.partial(func, *args, **kwargs)
                    )
                    latency = time.monotonic() - start
                    self._last_request_time = time.monotonic()

                    logger.debug(
                        "CLOB API call: %s | args=%s | status=OK | latency=%.3fs",
                        method_name, args, latency,
                    )
                    return result

                except Exception as e:
                    latency = time.monotonic() - start
                    self._last_request_time = time.monotonic()

                    error_str = str(e).lower()
                    if "401" in error_str or "403" in error_str or "auth" in error_str:
                        logger.error(
                            "Authentication failed for %s: %s. "
                            "Check your API credentials in .env.",
                            method_name, e,
                        )
                        raise AuthenticationError(
                            f"Authentication failed: {e}. "
                            "Check POLYMARKET_API_KEY, POLYMARKET_API_SECRET, "
                            "POLYMARKET_API_PASSPHRASE in .env. "
                            "Run `python scripts/setup_wallet.py` for help."
                        ) from e

                    if attempt < MAX_RETRIES - 1:
                        backoff = 2 ** attempt
                        logger.warning(
                            "CLOB API call failed: %s | error=%s | "
                            "latency=%.3fs | attempt=%d/%d | retry_in=%ds",
                            method_name, e, latency, attempt + 1, MAX_RETRIES, backoff,
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.error(
                            "CLOB API call failed after %d attempts: %s | error=%s",
                            MAX_RETRIES, method_name, e,
                        )
                        raise ClientError(
                            f"CLOB API call {method_name} failed after "
                            f"{MAX_RETRIES} attempts: {e}"
                        ) from e

        # Should not reach here
        raise ClientError(f"CLOB API call {method_name} failed")  # pragma: no cover

    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> str:
        """Place a limit order. Returns order ID.

        Args:
            token_id: The token to trade
            side: "BUY" or "SELL"
            price: Limit price (0-1 range for binary markets)
            size: Number of shares
        """
        clob_side = BUY if side.upper() == "BUY" else SELL

        order_args = OrderArgs(
            price=price,
            size=size,
            side=clob_side,
            token_id=token_id,
        )

        result = await self._rate_limited_call(
            self._client.create_and_post_order, order_args
        )

        if isinstance(result, dict):
            order_id = result.get("orderID", result.get("id", ""))
        else:
            order_id = str(result)

        logger.info(
            "Placed %s order: token=%s price=%.4f size=%.2f order_id=%s",
            side, token_id, price, size, order_id,
        )
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        try:
            await self._rate_limited_call(self._client.cancel, order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except ClientError as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return False

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all open orders with details."""
        result = await self._rate_limited_call(self._client.get_orders)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("data", result.get("orders", []))
        return []

    async def get_order_fill(self, order_id: str) -> dict[str, float] | None:
        """Return actual fill details for an order, or None if not yet filled.

        Returns {'fill_price': float, 'filled_size': float}. Prefers the
        per-order endpoint when available; falls back to scanning recent
        trades. Returns None when fill data cannot be obtained — callers
        should fall back to the recorded limit price.
        """
        # Strategy 1: per-order endpoint (py-clob-client.get_order)
        order_data: Any = None
        try:
            get_order = getattr(self._client, "get_order", None)
            if get_order is not None:
                order_data = await self._rate_limited_call(get_order, order_id)
        except (ClientError, AttributeError) as e:
            logger.debug("get_order(%s) failed: %s", order_id, e)
            order_data = None

        fill = _parse_fill_from_order(order_data) if order_data else None
        if fill is not None:
            return fill

        # Strategy 2: scan trade history for this order's fills
        try:
            get_trades = getattr(self._client, "get_trades", None)
            if get_trades is None:
                return None
            trades = await self._rate_limited_call(get_trades)
        except (ClientError, AttributeError) as e:
            logger.debug("get_trades fallback failed for order %s: %s", order_id, e)
            return None

        return _parse_fill_from_trades(trades, order_id)

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get current token positions."""
        # py-clob-client doesn't have a direct positions method,
        # but we can get balances via the API
        try:
            result = await self._rate_limited_call(
                self._client.get_balances
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("data", result.get("balances", []))
            return []
        except (ClientError, AttributeError):
            # get_balances may not be available in all versions
            logger.warning("get_positions: get_balances not available, returning empty")
            return []
