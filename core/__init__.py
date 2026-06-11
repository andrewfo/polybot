"""Core utilities shared across the bot."""

import asyncio
import logging
import os
import random
import time
from typing import TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_API_RETRIES = 3
# 429 responses need much longer waits than transient errors — CoinGecko's
# free tier typically clears within a minute, so 2-8s retries just burn
# attempts while still rate-limited.
RATE_LIMIT_BACKOFF_BASE = 30.0
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
            if getattr(exc, "status", None) == 429:
                backoff = RATE_LIMIT_BACKOFF_BASE * attempt + random.uniform(0, 5)
            else:
                backoff = min(2 ** attempt, 30) + random.uniform(0, 1)
            log_fn = logger.info if is_conn_err else logger.warning
            log_fn(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt, max_attempts, exc, backoff,
            )
            await asyncio.sleep(backoff)
    logger.error("%s failed after %d attempts: %s", label, max_attempts, last_err)
    return None


# ---------------------------------------------------------------------------
# CoinGecko global throttle
# ---------------------------------------------------------------------------
# The free tier allows roughly 10 requests/min per IP. Five modules hit
# CoinGecko (resolution_crypto, onchain_flow, learning, gas, health) and an
# aggregation cycle over multiple coins easily bursts past the limit, causing
# 429 storms. Every CoinGecko request must await coingecko_throttle() first
# so calls are spaced globally regardless of which module makes them.

COINGECKO_MIN_INTERVAL = float(os.environ.get("COINGECKO_MIN_INTERVAL", "6.0"))

_coingecko_last_call = 0.0
# asyncio primitives bind to the event loop they're first awaited on, so a
# single module-level Lock breaks under per-test event loops — key by loop.
_throttle_locks: dict[int, asyncio.Lock] = {}


def _get_throttle_lock() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _throttle_locks.get(loop_id)
    if lock is None:
        lock = asyncio.Lock()
        _throttle_locks[loop_id] = lock
    return lock


async def coingecko_throttle() -> None:
    """Wait until the next CoinGecko request slot is available.

    Serializes all CoinGecko calls process-wide with a minimum interval of
    COINGECKO_MIN_INTERVAL seconds (default 6s ≈ 10 req/min).
    """
    global _coingecko_last_call
    if COINGECKO_MIN_INTERVAL <= 0:
        return
    async with _get_throttle_lock():
        wait = COINGECKO_MIN_INTERVAL - (time.monotonic() - _coingecko_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _coingecko_last_call = time.monotonic()
