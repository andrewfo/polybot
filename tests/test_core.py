"""Tests for core utilities: fetch_with_retry backoff and the CoinGecko throttle."""

import asyncio
import time
from types import SimpleNamespace

import aiohttp
import pytest

import core
from core import coingecko_throttle, fetch_with_retry

# aiohttp's ClientResponseError.__str__ dereferences request_info.real_url
_FAKE_REQUEST_INFO = SimpleNamespace(real_url="https://api.test.example")


def _raise_status(status: int) -> None:
    raise aiohttp.ClientResponseError(
        request_info=_FAKE_REQUEST_INFO, history=(), status=status,
        message=f"HTTP {status}",
    )


@pytest.mark.asyncio
async def test_fetch_with_retry_429_uses_long_backoff(monkeypatch):
    """429 responses back off in 30s steps, not the 2-8s transient schedule."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(core, "RATE_LIMIT_BACKOFF_BASE", 30.0)

    async def factory():
        _raise_status(429)

    result = await fetch_with_retry(factory, label="test 429")
    assert result is None
    assert len(sleeps) == 2  # 3 attempts → 2 backoffs
    assert sleeps[0] >= 30.0
    assert sleeps[1] >= 60.0


@pytest.mark.asyncio
async def test_fetch_with_retry_non_429_keeps_short_backoff(monkeypatch):
    """Other HTTP errors keep the fast exponential schedule."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def factory():
        _raise_status(500)

    result = await fetch_with_retry(factory, label="test 500")
    assert result is None
    assert len(sleeps) == 2
    assert all(s < 10.0 for s in sleeps)


@pytest.mark.asyncio
async def test_coingecko_throttle_spaces_calls(monkeypatch):
    """Consecutive throttled calls are spaced by COINGECKO_MIN_INTERVAL."""
    monkeypatch.setattr(core, "COINGECKO_MIN_INTERVAL", 0.05)
    monkeypatch.setattr(core, "_coingecko_last_call", 0.0)

    start = time.monotonic()
    await coingecko_throttle()  # first call goes through immediately
    await coingecko_throttle()
    await coingecko_throttle()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.10


@pytest.mark.asyncio
async def test_coingecko_throttle_disabled_is_noop():
    """Interval of 0 (set by conftest for the suite) makes the throttle free."""
    start = time.monotonic()
    for _ in range(5):
        await coingecko_throttle()
    assert time.monotonic() - start < 0.05
