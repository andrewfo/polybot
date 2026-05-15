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

from config.settings import SLIPPAGE_BUFFER

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
