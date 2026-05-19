"""Core utilities shared across the bot."""

import asyncio
import logging
import random
from typing import TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_API_RETRIES = 3
# Connection-class errors (DNS/TLS/handshake/socket) often resolve on retry —
# e.g. "Cannot connect to host ...:443 ssl:default [Multiple exceptions]" from
# aiohttp when IPv6 + IPv4 race and both transiently fail. Give these extras.
CONNECTION_RETRIES = 6
_TRANSIENT_CONN_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientOSError,
    aiohttp.ServerTimeoutError,
    asyncio.TimeoutError,
)


async def fetch_with_retry(
    coro_factory,
    *,
    retries: int = MAX_API_RETRIES,
    label: str = "API call",
) -> T | None:
    """Retry an async callable with exponential backoff + jitter.

    ``coro_factory`` must be a zero-arg callable that returns a fresh coroutine
    each time (e.g. ``lambda: session.get(url)``).  Returns ``None`` after all
    retries are exhausted (callers already handle ``None``).

    Transient connection errors (DNS/TLS/socket) get extra attempts since they
    typically clear within a few seconds.
    """
    last_err: Exception | None = None
    attempt = 0
    max_attempts = retries
    while attempt < max_attempts:
        try:
            return await coro_factory()
        except Exception as exc:
            last_err = exc
            is_conn_err = isinstance(exc, _TRANSIENT_CONN_ERRORS)
            # Bump cap once we've identified a connection-class failure.
            if is_conn_err and max_attempts < CONNECTION_RETRIES:
                max_attempts = CONNECTION_RETRIES
            attempt += 1
            if attempt >= max_attempts:
                break
            backoff = min(2 ** attempt, 30) + random.uniform(0, 1)
            log_fn = logger.info if is_conn_err else logger.warning
            log_fn(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, max_attempts, exc, backoff,
            )
            await asyncio.sleep(backoff)
    logger.error("%s failed after %d attempts: %s", label, max_attempts, last_err)
    return None
