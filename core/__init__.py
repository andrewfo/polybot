"""Core utilities shared across the bot."""

import asyncio
import logging
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_API_RETRIES = 3


async def fetch_with_retry(
    coro_factory,
    *,
    retries: int = MAX_API_RETRIES,
    label: str = "API call",
) -> T | None:
    """Retry an async callable up to *retries* times with exponential backoff.

    ``coro_factory`` must be a zero-arg callable that returns a fresh coroutine
    each time (e.g. ``lambda: session.get(url)``).  Returns ``None`` after all
    retries are exhausted (callers already handle ``None``).
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_err = exc
            backoff = 2 ** attempt
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %ds",
                label, attempt + 1, retries, exc, backoff,
            )
            await asyncio.sleep(backoff)
    logger.error("%s failed after %d attempts: %s", label, retries, last_err)
    return None
