"""Tests for monitoring/health.py — health check system."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.health import (
    MATIC_CRITICAL_THRESHOLD,
    MATIC_WARN_THRESHOLD,
    LLM_DAILY_COST_CAP,
    STALE_ORDER_THRESHOLD_MINUTES,
    HealthCheckResult,
    _check_coingecko,
    _check_cost_runaway,
    _check_gamma_api,
    _check_openrouter,
    _check_stale_orders,
    _check_wallet_funds,
    _check_wallet_gas,
    run_health_checks,
)


# ---------------------------------------------------------------------------
# Helpers for mocking aiohttp
# ---------------------------------------------------------------------------

def _mock_aiohttp_session(status: int = 200, exc: Exception | None = None):
    """Create a mock aiohttp.ClientSession that returns given status or raises."""
    mock_resp = MagicMock()
    mock_resp.status = status

    @asynccontextmanager
    async def _get(*args, **kwargs):
        if exc:
            raise exc
        yield mock_resp

    mock_session = MagicMock()
    mock_session.get = _get

    @asynccontextmanager
    async def _session_ctx(*args, **kwargs):
        yield mock_session

    mock_cls = MagicMock(return_value=mock_session)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # Make ClientSession() work as both context manager and direct constructor
    class FakeSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *a):
            pass

    return FakeSession, mock_session


def _mock_aiohttp_error(exc: Exception):
    """Create a mock aiohttp session that raises on .get()."""
    @asynccontextmanager
    async def _get(*args, **kwargs):
        raise exc
        yield  # noqa: unreachable — needed to make it a generator

    mock_session = MagicMock()
    mock_session.get = _get

    class FakeSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *a):
            pass

    return FakeSession


# ---------------------------------------------------------------------------
# Individual check tests
# ---------------------------------------------------------------------------


class TestCheckStaleOrders:
    """Tests for _check_stale_orders."""

    def test_no_pending_orders(self):
        with patch("monitoring.health.get_open_trades", return_value=[]):
            result = _check_stale_orders()
        assert result.status == "ok"
        assert result.check_name == "stale_orders"

    def test_fresh_orders(self):
        now = datetime.now(timezone.utc).isoformat()
        trades = [{"id": "t1", "placed_at": now, "status": "PENDING"}]
        with patch("monitoring.health.get_open_trades", return_value=trades):
            result = _check_stale_orders()
        assert result.status == "ok"
        assert "none stale" in result.message

    def test_stale_orders_warning(self):
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        trades = [{"id": "t1", "placed_at": old_time, "status": "PENDING"}]
        with patch("monitoring.health.get_open_trades", return_value=trades):
            result = _check_stale_orders()
        assert result.status == "warning"
        assert "1 order(s) pending" in result.message

    def test_uses_timestamp_fallback(self):
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        trades = [{"id": "t1", "timestamp": old_time, "status": "PENDING"}]
        with patch("monitoring.health.get_open_trades", return_value=trades):
            result = _check_stale_orders()
        assert result.status == "warning"


class TestCheckCostRunaway:
    """Tests for _check_cost_runaway."""

    def test_cost_ok(self):
        with patch("monitoring.health.get_daily_llm_cost", return_value=5.0):
            result = _check_cost_runaway()
        assert result.status == "ok"

    def test_cost_warning(self):
        with patch("monitoring.health.get_daily_llm_cost", return_value=17.0):
            result = _check_cost_runaway()
        assert result.status == "warning"
        assert "approaching" in result.message

    def test_cost_critical(self):
        with patch("monitoring.health.get_daily_llm_cost", return_value=25.0):
            result = _check_cost_runaway()
        assert result.status == "critical"
        assert "exceeds" in result.message


class TestCheckGammaApi:
    """Tests for _check_gamma_api."""

    @pytest.mark.asyncio
    async def test_gamma_ok(self):
        fake_session_cls, _ = _mock_aiohttp_session(status=200)
        with patch("monitoring.health.aiohttp.ClientSession", fake_session_cls):
            result = await _check_gamma_api()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_gamma_down(self):
        fake_session_cls, _ = _mock_aiohttp_session(status=503)
        with patch("monitoring.health.aiohttp.ClientSession", fake_session_cls):
            result = await _check_gamma_api()
        assert result.status == "critical"

    @pytest.mark.asyncio
    async def test_gamma_exception(self):
        fake_cls = _mock_aiohttp_error(Exception("connection refused"))
        with patch("monitoring.health.aiohttp.ClientSession", fake_cls):
            result = await _check_gamma_api()
        assert result.status == "critical"


class TestCheckOpenRouter:
    """Tests for _check_openrouter."""

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = await _check_openrouter()
        assert result.status == "critical"
        assert "not set" in result.message

    @pytest.mark.asyncio
    async def test_openrouter_ok(self):
        fake_session_cls, _ = _mock_aiohttp_session(status=200)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch("monitoring.health.aiohttp.ClientSession", fake_session_cls):
                result = await _check_openrouter()
        assert result.status == "ok"


class TestCheckCoinGecko:
    """Tests for _check_coingecko."""

    @pytest.mark.asyncio
    async def test_coingecko_ok(self):
        fake_session_cls, _ = _mock_aiohttp_session(status=200)
        with patch("monitoring.health.aiohttp.ClientSession", fake_session_cls):
            result = await _check_coingecko()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_coingecko_down(self):
        fake_session_cls, _ = _mock_aiohttp_session(status=429)
        with patch("monitoring.health.aiohttp.ClientSession", fake_session_cls):
            result = await _check_coingecko()
        assert result.status == "warning"


class TestCheckWalletGas:
    """Tests for _check_wallet_gas."""

    @pytest.mark.asyncio
    async def test_gas_ok(self):
        mock_wallet = MagicMock()
        mock_wallet.get_matic_balance.return_value = 1.5
        mock_wallet.clear_cache.return_value = None

        async def fake_to_thread(fn, *args):
            if fn is MagicMock:
                return mock_wallet
            return fn(*args)

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 1.5]):
            result = await _check_wallet_gas()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_gas_warning(self):
        mock_wallet = MagicMock()
        mock_wallet.get_matic_balance.return_value = 0.08
        mock_wallet.clear_cache.return_value = None

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 0.08]):
            result = await _check_wallet_gas()
        assert result.status == "warning"

    @pytest.mark.asyncio
    async def test_gas_critical(self):
        mock_wallet = MagicMock()
        mock_wallet.get_matic_balance.return_value = 0.03
        mock_wallet.clear_cache.return_value = None

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 0.03]):
            result = await _check_wallet_gas()
        assert result.status == "critical"


class TestCheckWalletFunds:
    """Tests for _check_wallet_funds."""

    @pytest.mark.asyncio
    async def test_funds_ok_first_check(self):
        """First check should always be ok (no previous balance to compare)."""
        import monitoring.health as h
        h._last_usdc_balance = None

        mock_wallet = MagicMock()
        mock_wallet.get_usdc_balance.return_value = 500.0
        mock_wallet.clear_cache.return_value = None

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 500.0]):
            result = await _check_wallet_funds()
        assert result.status == "ok"
        assert h._last_usdc_balance == 500.0

    @pytest.mark.asyncio
    async def test_funds_large_change_warning(self):
        """Should warn if balance changed > 50%."""
        import monitoring.health as h
        h._last_usdc_balance = 1000.0

        mock_wallet = MagicMock()
        mock_wallet.get_usdc_balance.return_value = 400.0
        mock_wallet.clear_cache.return_value = None

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 400.0]):
            result = await _check_wallet_funds()
        assert result.status == "warning"
        assert "changed significantly" in result.message

    @pytest.mark.asyncio
    async def test_funds_small_change_ok(self):
        """Small balance changes should be ok."""
        import monitoring.health as h
        h._last_usdc_balance = 1000.0

        mock_wallet = MagicMock()
        mock_wallet.get_usdc_balance.return_value = 980.0
        mock_wallet.clear_cache.return_value = None

        with patch("monitoring.health.asyncio.to_thread", side_effect=[mock_wallet, 980.0]):
            result = await _check_wallet_funds()
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Integration: run_health_checks
# ---------------------------------------------------------------------------


class TestRunHealthChecks:
    """Tests for run_health_checks() orchestration."""

    @pytest.mark.asyncio
    async def test_all_ok_returns_results(self):
        """When all checks pass, returns results without raising."""
        ok_result = HealthCheckResult("test", "ok", "all good")

        with (
            patch("monitoring.health._check_gamma_api", return_value=ok_result),
            patch("monitoring.health._check_openrouter", return_value=ok_result),
            patch("monitoring.health._check_coingecko", return_value=ok_result),
            patch("monitoring.health._check_wallet_gas", return_value=ok_result),
            patch("monitoring.health._check_wallet_funds", return_value=ok_result),
            patch("monitoring.health._check_stale_orders", return_value=ok_result),
            patch("monitoring.health._check_cost_runaway", return_value=ok_result),
        ):
            results = await run_health_checks()

        assert len(results) == 7
        assert all(r.status == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_warning_logged_not_raised(self):
        """Warning results should be logged but not raise AutoStopError."""
        ok_result = HealthCheckResult("test", "ok", "all good")
        warn_result = HealthCheckResult("stale_orders", "warning", "1 stale order")

        with (
            patch("monitoring.health._check_gamma_api", return_value=ok_result),
            patch("monitoring.health._check_openrouter", return_value=ok_result),
            patch("monitoring.health._check_coingecko", return_value=ok_result),
            patch("monitoring.health._check_wallet_gas", return_value=ok_result),
            patch("monitoring.health._check_wallet_funds", return_value=ok_result),
            patch("monitoring.health._check_stale_orders", return_value=warn_result),
            patch("monitoring.health._check_cost_runaway", return_value=ok_result),
        ):
            results = await run_health_checks()

        assert any(r.status == "warning" for r in results)

    @pytest.mark.asyncio
    async def test_critical_raises_auto_stop(self):
        """Critical results should raise AutoStopError."""
        from strategy.executor import AutoStopError

        ok_result = HealthCheckResult("test", "ok", "all good")
        crit_result = HealthCheckResult("cost_runaway", "critical", "cost too high")

        with (
            patch("monitoring.health._check_gamma_api", return_value=ok_result),
            patch("monitoring.health._check_openrouter", return_value=ok_result),
            patch("monitoring.health._check_coingecko", return_value=ok_result),
            patch("monitoring.health._check_wallet_gas", return_value=ok_result),
            patch("monitoring.health._check_wallet_funds", return_value=ok_result),
            patch("monitoring.health._check_stale_orders", return_value=ok_result),
            patch("monitoring.health._check_cost_runaway", return_value=crit_result),
        ):
            with pytest.raises(AutoStopError, match="cost_runaway"):
                await run_health_checks()

    @pytest.mark.asyncio
    async def test_multiple_criticals_in_message(self):
        """All critical failures should be listed in the AutoStopError message."""
        from strategy.executor import AutoStopError

        ok_result = HealthCheckResult("test", "ok", "all good")
        crit1 = HealthCheckResult("gamma_api", "critical", "api down")
        crit2 = HealthCheckResult("cost_runaway", "critical", "cost too high")

        with (
            patch("monitoring.health._check_gamma_api", return_value=crit1),
            patch("monitoring.health._check_openrouter", return_value=ok_result),
            patch("monitoring.health._check_coingecko", return_value=ok_result),
            patch("monitoring.health._check_wallet_gas", return_value=ok_result),
            patch("monitoring.health._check_wallet_funds", return_value=ok_result),
            patch("monitoring.health._check_stale_orders", return_value=ok_result),
            patch("monitoring.health._check_cost_runaway", return_value=crit2),
        ):
            with pytest.raises(AutoStopError) as exc_info:
                await run_health_checks()
            assert "gamma_api" in str(exc_info.value)
            assert "cost_runaway" in str(exc_info.value)


class TestHealthCheckResult:
    """Tests for the HealthCheckResult dataclass."""

    def test_dataclass_fields(self):
        r = HealthCheckResult("test_check", "ok", "Everything fine")
        assert r.check_name == "test_check"
        assert r.status == "ok"
        assert r.message == "Everything fine"

    def test_status_values(self):
        for status in ("ok", "warning", "critical"):
            r = HealthCheckResult("test", status, "msg")
            assert r.status == status
