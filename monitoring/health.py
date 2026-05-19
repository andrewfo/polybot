"""Health check system for the trading bot.

Runs periodic checks on all external dependencies and internal state.
Critical failures raise AutoStopError to halt the bot. Warning-level
issues are logged via Notifier for dashboard visibility.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

from core.db import get_daily_llm_cost, get_open_trades
from monitoring.notifications import Notifier

logger = logging.getLogger("polybot.health")

# Thresholds
MATIC_WARN_THRESHOLD = 0.1
MATIC_CRITICAL_THRESHOLD = 0.05
LLM_DAILY_COST_CAP = 20.0  # $20 hard cap
STALE_ORDER_THRESHOLD_MINUTES = 30
USDC_CHANGE_WARN_PCT = 0.50  # Warn if USDC balance changed > 50% since last check


@dataclass
class HealthCheckResult:
    """Result from a single health check."""
    check_name: str
    status: str  # "ok" | "warning" | "critical"
    message: str


# Track last known USDC balance for large-change detection
_last_usdc_balance: float | None = None

# Track consecutive critical failures per check — only auto-stop after N in a row.
# A single transient network blip on gamma_api / openrouter must not stop the bot.
CONSECUTIVE_CRITICAL_THRESHOLD = 3
_consecutive_critical_counts: dict[str, int] = {}


def reset_consecutive_critical_counts() -> None:
    """Reset the consecutive-critical counters. Used by tests."""
    _consecutive_critical_counts.clear()


async def _check_gamma_api() -> HealthCheckResult:
    """Check Gamma API reachability with a simple market fetch."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://gamma-api.polymarket.com/markets?limit=1&active=true",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return HealthCheckResult("gamma_api", "ok", "Gamma API reachable")
                return HealthCheckResult(
                    "gamma_api", "critical",
                    f"Gamma API returned HTTP {resp.status}",
                )
    except Exception as e:
        return HealthCheckResult("gamma_api", "critical", f"Gamma API unreachable: {e!s:.80}")


async def _check_wallet_gas() -> HealthCheckResult:
    """Check MATIC balance for gas. Warn at 0.1, critical at 0.05."""
    try:
        from core.wallet import Wallet
        wallet = await asyncio.to_thread(Wallet)
        wallet.clear_cache()
        matic = await asyncio.to_thread(wallet.get_matic_balance)
        if matic < MATIC_CRITICAL_THRESHOLD:
            return HealthCheckResult(
                "wallet_gas", "critical",
                f"MATIC balance critically low: {matic:.4f} (need > {MATIC_CRITICAL_THRESHOLD})",
            )
        if matic < MATIC_WARN_THRESHOLD:
            return HealthCheckResult(
                "wallet_gas", "warning",
                f"MATIC balance low: {matic:.4f} (warn threshold: {MATIC_WARN_THRESHOLD})",
            )
        return HealthCheckResult("wallet_gas", "ok", f"MATIC balance: {matic:.4f}")
    except Exception as e:
        return HealthCheckResult("wallet_gas", "warning", f"Could not check MATIC: {e!s:.80}")


async def _check_wallet_funds() -> HealthCheckResult:
    """Check USDC balance for unexpected large changes."""
    global _last_usdc_balance
    try:
        from core.wallet import Wallet
        wallet = await asyncio.to_thread(Wallet)
        wallet.clear_cache()
        usdc = await asyncio.to_thread(wallet.get_usdc_balance)

        if _last_usdc_balance is not None and _last_usdc_balance > 0:
            change_pct = abs(usdc - _last_usdc_balance) / _last_usdc_balance
            if change_pct >= USDC_CHANGE_WARN_PCT:
                msg = (
                    f"USDC balance changed significantly: "
                    f"${_last_usdc_balance:.2f} → ${usdc:.2f} "
                    f"({change_pct:.0%} change)"
                )
                _last_usdc_balance = usdc
                return HealthCheckResult("wallet_funds", "warning", msg)

        _last_usdc_balance = usdc
        return HealthCheckResult("wallet_funds", "ok", f"USDC balance: ${usdc:.2f}")
    except Exception as e:
        return HealthCheckResult("wallet_funds", "warning", f"Could not check USDC: {e!s:.80}")


def _check_stale_orders() -> HealthCheckResult:
    """Check for orders stuck in PENDING status > 30 minutes."""
    try:
        open_trades = get_open_trades()
        if not open_trades:
            return HealthCheckResult("stale_orders", "ok", "No pending orders")

        now = datetime.now(timezone.utc)
        stale_count = 0
        for trade in open_trades:
            placed_at = trade.get("placed_at") or trade.get("timestamp", "")
            if not placed_at:
                continue
            try:
                placed_dt = datetime.fromisoformat(placed_at.replace("Z", "+00:00"))
                age_minutes = (now - placed_dt).total_seconds() / 60
                if age_minutes > STALE_ORDER_THRESHOLD_MINUTES:
                    stale_count += 1
            except (ValueError, TypeError):
                continue

        if stale_count > 0:
            return HealthCheckResult(
                "stale_orders", "warning",
                f"{stale_count} order(s) pending > {STALE_ORDER_THRESHOLD_MINUTES} minutes",
            )
        return HealthCheckResult(
            "stale_orders", "ok",
            f"{len(open_trades)} pending order(s), none stale",
        )
    except Exception as e:
        return HealthCheckResult("stale_orders", "warning", f"Could not check orders: {e!s:.80}")


async def _check_openrouter() -> HealthCheckResult:
    """Check OpenRouter API reachability via /models endpoint."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return HealthCheckResult(
            "openrouter", "critical", "OPENROUTER_API_KEY not set",
        )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return HealthCheckResult("openrouter", "ok", "OpenRouter reachable")
                return HealthCheckResult(
                    "openrouter", "critical",
                    f"OpenRouter returned HTTP {resp.status}",
                )
    except Exception as e:
        return HealthCheckResult("openrouter", "critical", f"OpenRouter unreachable: {e!s:.80}")


async def _check_coingecko() -> HealthCheckResult:
    """Check CoinGecko API reachability via /api/v3/ping."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.coingecko.com/api/v3/ping",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return HealthCheckResult("coingecko", "ok", "CoinGecko reachable")
                return HealthCheckResult(
                    "coingecko", "warning",
                    f"CoinGecko returned HTTP {resp.status}",
                )
    except Exception as e:
        return HealthCheckResult("coingecko", "warning", f"CoinGecko unreachable: {e!s:.80}")


def _check_cost_runaway() -> HealthCheckResult:
    """Check if daily LLM cost exceeds the $20 hard cap."""
    try:
        daily_cost = get_daily_llm_cost()
        if daily_cost >= LLM_DAILY_COST_CAP:
            return HealthCheckResult(
                "cost_runaway", "critical",
                f"Daily LLM cost ${daily_cost:.2f} exceeds ${LLM_DAILY_COST_CAP:.0f} cap",
            )
        if daily_cost >= LLM_DAILY_COST_CAP * 0.8:
            return HealthCheckResult(
                "cost_runaway", "warning",
                f"Daily LLM cost ${daily_cost:.2f} approaching ${LLM_DAILY_COST_CAP:.0f} cap",
            )
        return HealthCheckResult(
            "cost_runaway", "ok",
            f"Daily LLM cost: ${daily_cost:.2f} / ${LLM_DAILY_COST_CAP:.0f}",
        )
    except Exception as e:
        return HealthCheckResult("cost_runaway", "warning", f"Could not check LLM costs: {e!s:.80}")


async def run_health_checks() -> list[HealthCheckResult]:
    """Run all health checks. Logs warnings/criticals via Notifier.

    Returns list of HealthCheckResult. Raises AutoStopError on any critical failure.
    """
    from strategy.executor import AutoStopError

    notifier = Notifier()

    # Run network checks concurrently, sync checks inline
    from config.settings import PAPER_TRADING
    checks = [
        _check_gamma_api(),
        _check_openrouter(),
        _check_coingecko(),
    ]
    if not PAPER_TRADING:
        checks.append(_check_wallet_gas())
        checks.append(_check_wallet_funds())
    network_results = await asyncio.gather(*checks, return_exceptions=True)

    results: list[HealthCheckResult] = []
    for r in network_results:
        if isinstance(r, Exception):
            results.append(HealthCheckResult("unknown", "warning", f"Check failed: {r!s:.80}"))
        else:
            results.append(r)

    # Sync checks
    results.append(_check_stale_orders())
    results.append(_check_cost_runaway())

    # Process results: log warnings and criticals
    critical_failures: list[str] = []
    for result in results:
        if result.status == "warning":
            await notifier.send(
                f"Health warning [{result.check_name}]: {result.message}",
                level="warning",
            )
            _consecutive_critical_counts.pop(result.check_name, None)
        elif result.status == "critical":
            count = _consecutive_critical_counts.get(result.check_name, 0) + 1
            _consecutive_critical_counts[result.check_name] = count
            await notifier.send(
                f"Health CRITICAL [{result.check_name}] ({count}/{CONSECUTIVE_CRITICAL_THRESHOLD}): {result.message}",
                level="critical",
            )
            if count >= CONSECUTIVE_CRITICAL_THRESHOLD:
                critical_failures.append(f"{result.check_name}: {result.message}")
        else:  # ok
            _consecutive_critical_counts.pop(result.check_name, None)

    ok_count = sum(1 for r in results if r.status == "ok")
    warn_count = sum(1 for r in results if r.status == "warning")
    crit_count = sum(1 for r in results if r.status == "critical")
    logger.info(
        "Health checks complete: %d ok, %d warnings, %d critical",
        ok_count, warn_count, crit_count,
    )

    if critical_failures:
        raise AutoStopError(
            f"Critical health check failure(s) for {CONSECUTIVE_CRITICAL_THRESHOLD} consecutive cycles: "
            + "; ".join(critical_failures)
        )

    return results
