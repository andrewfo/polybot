"""OpenRouter LLM client with tiered model routing.

Routes calls to cheap or frontier models based on task type.
All calls are cost-tracked, rate-limited, and retried on failure.
Frontier tasks never silently fall back to cheap models.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config.settings import CHEAP_MODEL, FRONTIER_MODEL, OPENROUTER_BASE_URL
from core import db

logger = logging.getLogger(__name__)

# Task type → model tier routing
TASK_ROUTING: dict[str, str] = {
    # Cheap model tasks
    "summarize": "cheap",
    "classify": "cheap",
    "extract": "cheap",
    "parse": "cheap",
    "search_queries": "cheap",
    # Frontier model tasks
    "estimate_probability": "frontier",
    "trade_decision": "frontier",
    "analyze_edge": "frontier",
    "evaluate_confidence": "frontier",
}

FALLBACK_CHEAP_MODEL = "z-ai/glm-4.5-air:free"

CHEAP_TIMEOUT = 30
FRONTIER_TIMEOUT = 120
MAX_RETRIES = 3

# Rate limits (calls per minute) — override via env vars
CHEAP_RATE_LIMIT = int(os.environ.get("CHEAP_RATE_LIMIT", "60"))
FRONTIER_RATE_LIMIT = int(os.environ.get("FRONTIER_RATE_LIMIT", "5"))
RATE_WINDOW_SECONDS = 60.0


class LLMError(Exception):
    """Raised when an LLM call fails after retries."""
    pass


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    task_type: str


class LLMClient:
    """Tiered LLM client via OpenRouter.

    Usage:
        llm = LLMClient(api_key="...")

        # Cheap call for article summarization
        summary = await llm.cheap("Summarize this article: ...")

        # Frontier call for probability estimation
        estimate = await llm.frontier("Given the following evidence, estimate the probability...")

        # Auto-route based on task type
        result = await llm.call(prompt, task_type="summarize")  # routes to cheap
        result = await llm.call(prompt, task_type="estimate_probability")  # routes to frontier
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY env var or pass api_key parameter."
            )

        # Rate limiting: sliding window of call timestamps
        self._cheap_call_times: list[float] = []
        self._frontier_call_times: list[float] = []

        # Lazy-initialized session
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "polymarket-bot",
                }
            )
        return self._session

    async def _wait_for_rate_limit(self, tier: str) -> None:
        """Enforce sliding-window rate limiting."""
        if tier == "frontier":
            call_times = self._frontier_call_times
            limit = FRONTIER_RATE_LIMIT
        else:
            call_times = self._cheap_call_times
            limit = CHEAP_RATE_LIMIT

        now = time.monotonic()

        # Prune timestamps older than the window
        while call_times and (now - call_times[0]) > RATE_WINDOW_SECONDS:
            call_times.pop(0)

        # If at capacity, wait for the oldest call to expire
        if len(call_times) >= limit:
            wait_time = RATE_WINDOW_SECONDS - (now - call_times[0])
            if wait_time > 0:
                logger.info("Rate limit reached for %s tier, waiting %.1fs", tier, wait_time)
                await asyncio.sleep(wait_time)
                # Prune again after sleeping
                now = time.monotonic()
                while call_times and (now - call_times[0]) > RATE_WINDOW_SECONDS:
                    call_times.pop(0)

        # Record this call
        call_times.append(time.monotonic())

    async def _call_openrouter(
        self,
        prompt: str,
        model: str,
        system: str | None,
        timeout: int,
        task_type: str,
    ) -> LLMResponse:
        """Make a single call to OpenRouter with retry logic."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages}

        # Determine tier for rate limiting
        tier = "frontier" if model == FRONTIER_MODEL else "cheap"
        await self._wait_for_rate_limit(tier)

        session = await self._get_session()
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                async with session.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        usage = data.get("usage", {})
                        input_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)

                        # OpenRouter may include cost; default to 0 for free models
                        cost_usd = float(data.get("usage", {}).get("cost", 0.0))
                        if cost_usd == 0.0:
                            # Check top-level cost field (some OpenRouter responses)
                            cost_usd = float(data.get("cost", 0.0))

                        # Log cost to database
                        timestamp = datetime.now(timezone.utc).isoformat()
                        db.record_llm_cost(
                            timestamp=timestamp,
                            model=model,
                            task_type=task_type,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost_usd,
                        )

                        return LLMResponse(
                            text=text,
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost_usd,
                            task_type=task_type,
                        )

                    elif resp.status in (429, 500, 502, 503):
                        backoff = 2 ** attempt
                        if resp.status == 429:
                            # Respect Retry-After header; default to longer backoff for rate limits
                            retry_after = resp.headers.get("Retry-After")
                            if retry_after:
                                try:
                                    backoff = max(backoff, int(retry_after))
                                except ValueError:
                                    pass
                            backoff = max(backoff, 5)  # At least 5s for rate limits
                        logger.warning(
                            "OpenRouter %d for model %s (attempt %d/%d), retrying in %ds",
                            resp.status, model, attempt + 1, MAX_RETRIES, backoff,
                        )
                        last_error = LLMError(
                            f"OpenRouter returned {resp.status} for model {model}"
                        )
                        await asyncio.sleep(backoff)
                    else:
                        # Non-retryable error
                        body = await resp.text()
                        raise LLMError(
                            f"OpenRouter returned {resp.status} for model {model}: {body}"
                        )

            except asyncio.TimeoutError:
                backoff = 2 ** attempt
                logger.warning(
                    "Timeout calling %s (attempt %d/%d), retrying in %ds",
                    model, attempt + 1, MAX_RETRIES, backoff,
                )
                last_error = LLMError(f"Timeout calling model {model}")
                await asyncio.sleep(backoff)

            except LLMError:
                raise

            except Exception as e:
                backoff = 2 ** attempt
                logger.warning(
                    "Error calling %s (attempt %d/%d): %s, retrying in %ds",
                    model, attempt + 1, MAX_RETRIES, e, backoff,
                )
                last_error = LLMError(f"Error calling model {model}: {e}")
                await asyncio.sleep(backoff)

        raise last_error or LLMError(f"Failed to call model {model} after {MAX_RETRIES} attempts")

    async def cheap(self, prompt: str, system: str | None = None) -> str:
        """Call the cheap model. Falls back to alternative free model on failure."""
        try:
            response = await self._call_openrouter(
                prompt, CHEAP_MODEL, system, CHEAP_TIMEOUT, "cheap"
            )
            return response.text
        except LLMError as e:
            logger.warning("Cheap model %s failed: %s. Trying fallback.", CHEAP_MODEL, e)

        try:
            response = await self._call_openrouter(
                prompt, FALLBACK_CHEAP_MODEL, system, CHEAP_TIMEOUT, "cheap_fallback"
            )
            return response.text
        except LLMError as e:
            logger.critical("All cheap models failed. Primary: %s, Fallback: %s", CHEAP_MODEL, FALLBACK_CHEAP_MODEL)
            raise LLMError(f"All cheap models failed: {e}") from e

    async def frontier(self, prompt: str, system: str | None = None) -> str:
        """Call the frontier model. Never falls back to cheap — alerts and raises on failure."""
        try:
            response = await self._call_openrouter(
                prompt, FRONTIER_MODEL, system, FRONTIER_TIMEOUT, "frontier"
            )
            return response.text
        except LLMError as e:
            logger.critical(
                "FRONTIER MODEL FAILED — not falling back to cheap model. Error: %s", e
            )
            raise

    async def call(self, prompt: str, task_type: str, system: str | None = None) -> str:
        """Auto-route to cheap or frontier based on task type."""
        tier = TASK_ROUTING.get(task_type)
        if tier is None:
            raise ValueError(
                f"Unknown task_type '{task_type}'. Valid types: {list(TASK_ROUTING.keys())}"
            )

        if tier == "frontier":
            return await self.frontier(prompt, system)
        else:
            return await self.cheap(prompt, system)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | list[Any] | None:
        """Try to extract a JSON object or array from text.

        Attempts in order:
        1. Direct parse of stripped text
        2. Strip markdown code fences then parse
        3. Find first { ... } or [ ... ] substring and parse
        """
        stripped = text.strip()

        # 1. Direct parse
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Strip markdown code fences
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # 3. Find first JSON object or array in the text
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start_idx = cleaned.find(start_char)
            if start_idx == -1:
                continue
            # Find matching close by searching from the end
            end_idx = cleaned.rfind(end_char)
            if end_idx > start_idx:
                candidate = cleaned[start_idx:end_idx + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass

        return None

    async def call_json(
        self, prompt: str, task_type: str, system: str | None = None
    ) -> dict[str, Any] | list[Any]:
        """Call LLM and parse response as JSON. Retries on parse failure."""
        max_json_retries = 3
        current_prompt = prompt

        for attempt in range(max_json_retries):
            text = await self.call(current_prompt, task_type, system)

            result = self._extract_json(text)
            if result is not None:
                return result

            if attempt < max_json_retries - 1:
                logger.warning(
                    "JSON parse failed (attempt %d/%d), retrying with instruction",
                    attempt + 1, max_json_retries,
                )
                current_prompt = (
                    prompt + "\n\nIMPORTANT: Your response must be ONLY valid JSON. "
                    "No explanation, no markdown, no text before or after the JSON."
                )
            else:
                raise LLMError(
                    f"Failed to parse JSON after {max_json_retries} attempts. "
                    f"Last response: {text[:200]}"
                )

        # Should not reach here, but satisfy type checker
        raise LLMError("JSON parsing failed")  # pragma: no cover

    async def get_daily_cost(self) -> float:
        """Return total LLM spend for today (UTC)."""
        return db.get_daily_llm_cost()

    async def get_monthly_cost(self) -> float:
        """Return total LLM spend for this month (UTC)."""
        return db.get_monthly_llm_cost()

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
