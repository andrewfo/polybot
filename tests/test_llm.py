"""Tests for core.llm — LLM client with tiered model routing."""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import sqlite_utils

from core.llm import (
    FALLBACK_CHEAP_MODEL,
    TASK_ROUTING,
    LLMClient,
    LLMError,
    LLMResponse,
)
from config.settings import CHEAP_MODEL, FRONTIER_MODEL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_db(tmp_path: Path):
    """Redirect all DB operations to a temporary SQLite file."""
    test_db_path = tmp_path / "test.db"
    test_db = sqlite_utils.Database(str(test_db_path))
    test_db["llm_costs"].create({
        "id": int,
        "timestamp": str,
        "model": str,
        "task_type": str,
        "input_tokens": int,
        "output_tokens": int,
        "cost_usd": float,
    }, pk="id", if_not_exists=True)

    with patch("core.db.get_db", return_value=test_db):
        yield test_db


@pytest_asyncio.fixture
async def llm():
    """Create an LLMClient with a dummy API key."""
    client = LLMClient(api_key="test-key-123")
    yield client
    await client.close()


def make_openrouter_response(
    content: str = "Hello",
    model: str = CHEAP_MODEL,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    cost: float = 0.0,
) -> dict:
    """Build a mock OpenRouter JSON response."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "model": model,
        "cost": cost,
    }


class MockResponse:
    """Mock aiohttp response that supports async context manager."""

    def __init__(self, status: int = 200, json_data: dict | None = None, text_data: str = ""):
        self.status = status
        self._json_data = json_data or {}
        self._text_data = text_data

    async def json(self) -> dict:
        return self._json_data

    async def text(self) -> str:
        return self._text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def make_mock_session(responses: list[MockResponse]) -> MagicMock:
    """Create a mock session whose .post() returns responses in order."""
    session = MagicMock()
    session.closed = False
    call_count = 0

    def post_side_effect(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    session.post = MagicMock(side_effect=post_side_effect)

    async def close():
        session.closed = True

    session.close = close
    return session


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cheap_routes_to_cheap_model(llm: LLMClient) -> None:
    """cheap() should call the CHEAP_MODEL."""
    resp = MockResponse(200, make_openrouter_response(content="summary", model=CHEAP_MODEL))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.cheap("Summarize this")

    assert result == "summary"
    call_args = session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["model"] == CHEAP_MODEL


@pytest.mark.asyncio
async def test_frontier_routes_to_frontier_model(llm: LLMClient) -> None:
    """frontier() should call the FRONTIER_MODEL."""
    resp = MockResponse(200, make_openrouter_response(content="0.75", model=FRONTIER_MODEL))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.frontier("Estimate probability")

    assert result == "0.75"
    call_args = session.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["model"] == FRONTIER_MODEL


@pytest.mark.asyncio
async def test_task_routing_cheap(llm: LLMClient) -> None:
    """call() with a cheap task type should route to cheap model."""
    resp = MockResponse(200, make_openrouter_response(content="classified"))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.call("classify this", task_type="summarize")

    assert result == "classified"
    payload = session.post.call_args.kwargs.get("json") or session.post.call_args[1].get("json")
    assert payload["model"] == CHEAP_MODEL


@pytest.mark.asyncio
async def test_task_routing_frontier(llm: LLMClient) -> None:
    """call() with a frontier task type should route to frontier model."""
    resp = MockResponse(200, make_openrouter_response(content="0.82", model=FRONTIER_MODEL))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.call("estimate this", task_type="estimate_probability")

    assert result == "0.82"
    payload = session.post.call_args.kwargs.get("json") or session.post.call_args[1].get("json")
    assert payload["model"] == FRONTIER_MODEL


@pytest.mark.asyncio
async def test_unknown_task_type_raises(llm: LLMClient) -> None:
    """call() with unknown task_type should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown task_type"):
        await llm.call("test", task_type="nonexistent")


# ---------------------------------------------------------------------------
# Retry tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_429(llm: LLMClient) -> None:
    """Should retry on 429 and succeed on second attempt."""
    responses = [
        MockResponse(429),
        MockResponse(200, make_openrouter_response(content="ok")),
    ]
    session = make_mock_session(responses)

    with patch.object(llm, "_get_session", return_value=session), \
         patch("core.llm.asyncio.sleep", new_callable=AsyncMock):
        result = await llm.cheap("test")

    assert result == "ok"
    assert session.post.call_count == 2


@pytest.mark.asyncio
async def test_retry_on_500(llm: LLMClient) -> None:
    """Should retry on 500 and succeed on third attempt."""
    responses = [
        MockResponse(500),
        MockResponse(500),
        MockResponse(200, make_openrouter_response(content="recovered")),
    ]
    session = make_mock_session(responses)

    with patch.object(llm, "_get_session", return_value=session), \
         patch("core.llm.asyncio.sleep", new_callable=AsyncMock):
        result = await llm.cheap("test")

    assert result == "recovered"
    assert session.post.call_count == 3


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_frontier_no_fallback(llm: LLMClient) -> None:
    """Frontier failure should raise LLMError, never call cheap model."""
    # All 3 retries fail
    responses = [MockResponse(500), MockResponse(500), MockResponse(500)]
    session = make_mock_session(responses)

    with patch.object(llm, "_get_session", return_value=session), \
         patch("core.llm.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LLMError):
            await llm.frontier("test")

    # Verify all calls used the frontier model, none used cheap
    for call in session.post.call_args_list:
        payload = call.kwargs.get("json") or call[1].get("json")
        assert payload["model"] == FRONTIER_MODEL


@pytest.mark.asyncio
async def test_cheap_fallback_to_llama(llm: LLMClient) -> None:
    """When cheap model fails, should try fallback Llama model."""
    # Primary cheap fails 3 times, then fallback succeeds
    fail_resp = MockResponse(500)
    success_resp = MockResponse(200, make_openrouter_response(
        content="fallback result", model=FALLBACK_CHEAP_MODEL
    ))
    responses = [fail_resp, fail_resp, fail_resp, success_resp]
    session = make_mock_session(responses)

    with patch.object(llm, "_get_session", return_value=session), \
         patch("core.llm.asyncio.sleep", new_callable=AsyncMock):
        result = await llm.cheap("test")

    assert result == "fallback result"
    # First 3 calls should be primary, 4th should be fallback
    calls = session.post.call_args_list
    assert len(calls) == 4
    last_payload = calls[3].kwargs.get("json") or calls[3][1].get("json")
    assert last_payload["model"] == FALLBACK_CHEAP_MODEL


# ---------------------------------------------------------------------------
# Cost tracking tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_tracking(_patch_db, llm: LLMClient) -> None:
    """Every successful call should insert a row in llm_costs."""
    resp = MockResponse(200, make_openrouter_response(
        content="tracked", prompt_tokens=15, completion_tokens=25, cost=0.001
    ))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        await llm.cheap("test tracking")

    rows = list(_patch_db["llm_costs"].rows)
    assert len(rows) == 1
    assert rows[0]["model"] == CHEAP_MODEL
    assert rows[0]["input_tokens"] == 15
    assert rows[0]["output_tokens"] == 25
    assert rows[0]["cost_usd"] == 0.001


@pytest.mark.asyncio
async def test_get_daily_cost(_patch_db, llm: LLMClient) -> None:
    """get_daily_cost() should sum today's costs."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    _patch_db["llm_costs"].insert({"timestamp": today, "model": "m", "task_type": "t",
                                    "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.50})
    _patch_db["llm_costs"].insert({"timestamp": today, "model": "m", "task_type": "t",
                                    "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.25})
    # Yesterday's cost should not be included
    _patch_db["llm_costs"].insert({"timestamp": "2020-01-01T00:00:00", "model": "m",
                                    "task_type": "t", "input_tokens": 1, "output_tokens": 1,
                                    "cost_usd": 9.99})

    cost = await llm.get_daily_cost()
    assert abs(cost - 0.75) < 0.001


@pytest.mark.asyncio
async def test_get_monthly_cost(_patch_db, llm: LLMClient) -> None:
    """get_monthly_cost() should sum this month's costs."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    _patch_db["llm_costs"].insert({"timestamp": f"{month}-01T00:00:00", "model": "m",
                                    "task_type": "t", "input_tokens": 1, "output_tokens": 1,
                                    "cost_usd": 1.00})
    _patch_db["llm_costs"].insert({"timestamp": f"{month}-15T12:00:00", "model": "m",
                                    "task_type": "t", "input_tokens": 1, "output_tokens": 1,
                                    "cost_usd": 2.00})
    # Different month
    _patch_db["llm_costs"].insert({"timestamp": "2020-06-15T00:00:00", "model": "m",
                                    "task_type": "t", "input_tokens": 1, "output_tokens": 1,
                                    "cost_usd": 99.0})

    cost = await llm.get_monthly_cost()
    assert abs(cost - 3.00) < 0.001


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_json_valid(llm: LLMClient) -> None:
    """call_json() should parse valid JSON responses."""
    data = {"probability": 0.72, "confidence": "high"}
    resp = MockResponse(200, make_openrouter_response(content=json.dumps(data)))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.call_json("test", task_type="summarize")

    assert result == data


@pytest.mark.asyncio
async def test_call_json_strips_code_fences(llm: LLMClient) -> None:
    """call_json() should strip markdown code fences."""
    data = {"key": "value"}
    fenced = f"```json\n{json.dumps(data)}\n```"
    resp = MockResponse(200, make_openrouter_response(content=fenced))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.call_json("test", task_type="classify")

    assert result == data


@pytest.mark.asyncio
async def test_call_json_retry_on_invalid(llm: LLMClient) -> None:
    """call_json() should retry with JSON instruction on parse failure."""
    bad_resp = MockResponse(200, make_openrouter_response(content="not json at all"))
    good_resp = MockResponse(200, make_openrouter_response(content='{"valid": true}'))
    session = make_mock_session([bad_resp, good_resp])

    with patch.object(llm, "_get_session", return_value=session):
        result = await llm.call_json("test", task_type="extract")

    assert result == {"valid": True}
    assert session.post.call_count == 2


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------

def test_missing_api_key_raises() -> None:
    """LLMClient should raise ValueError if no API key is provided."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="OpenRouter API key required"):
            LLMClient()


# ---------------------------------------------------------------------------
# Rate limiting test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limiting(llm: LLMClient) -> None:
    """Rate limiter should delay when call limit is reached."""
    sleep_called = False
    original_sleep = asyncio.sleep

    async def mock_sleep(duration):
        nonlocal sleep_called
        if duration > 0:
            sleep_called = True
        # Don't actually sleep

    # Fill up the cheap call times to trigger rate limiting
    import time
    now = time.monotonic()
    from core.llm import CHEAP_RATE_LIMIT
    llm._cheap_call_times = [now - i for i in range(CHEAP_RATE_LIMIT)]  # fill to limit

    resp = MockResponse(200, make_openrouter_response(content="delayed"))
    session = make_mock_session([resp])

    with patch.object(llm, "_get_session", return_value=session), \
         patch("core.llm.asyncio.sleep", side_effect=mock_sleep), \
         patch("core.llm.time.monotonic", return_value=now):
        await llm.cheap("test rate limit")

    assert sleep_called, "Rate limiter should have triggered a sleep"
