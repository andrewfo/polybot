"""Monte Carlo simulation signal provider.

Runs geometric Brownian motion simulations for crypto price targets using
CoinGecko historical data, and bootstrapped change simulations for economics
indicators using FRED data. Pure math — cheap LLM only interprets results.
"""

import logging
import math
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from config.settings import FRED_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Simulation parameters
NUM_SIMULATIONS = 5000
SEED = None  # Set to int for reproducibility in tests

# FRED indicator → series mapping (same as resolution_econ)
INDICATOR_SERIES: dict[str, list[str]] = {
    "rate": ["FEDFUNDS", "DFF"],
    "inflation": ["CPIAUCSL"],
    "employment": ["UNRATE"],
    "gdp": ["GDP"],
}
DEFAULT_SERIES = ["DGS10"]


def _norm_cdf(x: float) -> float:
    """Normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _run_gbm_simulation(
    current_price: float,
    target_price: float,
    annual_vol: float,
    days_remaining: float,
    direction: str,
    n_sims: int = NUM_SIMULATIONS,
) -> dict[str, Any]:
    """Run Monte Carlo GBM simulation and return statistics.

    Returns dict with: mc_probability, mean_final, median_final, std_final,
    pct_5, pct_25, pct_75, pct_95, n_simulations.
    """
    if days_remaining <= 0 or annual_vol <= 0 or current_price <= 0:
        # Degenerate case — return deterministic
        hit = (current_price >= target_price) if direction == "above" else (current_price < target_price)
        return {
            "mc_probability": 1.0 if hit else 0.0,
            "mean_final": current_price,
            "median_final": current_price,
            "std_final": 0.0,
            "pct_5": current_price,
            "pct_25": current_price,
            "pct_75": current_price,
            "pct_95": current_price,
            "n_simulations": 0,
        }

    rng = random.Random(SEED)
    dt = days_remaining / 365.0
    drift = -0.5 * annual_vol ** 2 * dt  # risk-neutral drift over full period
    diffusion = annual_vol * math.sqrt(dt)

    finals: list[float] = []
    hits = 0

    for _ in range(n_sims):
        z = rng.gauss(0, 1)
        log_return = drift + diffusion * z
        final_price = current_price * math.exp(log_return)
        finals.append(final_price)
        if direction == "below":
            if final_price < target_price:
                hits += 1
        else:
            if final_price >= target_price:
                hits += 1

    finals.sort()
    n = len(finals)
    mc_prob = hits / n_sims

    return {
        "mc_probability": mc_prob,
        "mean_final": sum(finals) / n,
        "median_final": finals[n // 2],
        "std_final": math.sqrt(sum((f - sum(finals) / n) ** 2 for f in finals) / n),
        "pct_5": finals[int(n * 0.05)],
        "pct_25": finals[int(n * 0.25)],
        "pct_75": finals[int(n * 0.75)],
        "pct_95": finals[int(n * 0.95)],
        "n_simulations": n_sims,
    }


def _run_bootstrap_simulation(
    current_value: float,
    target_value: float,
    historical_changes: list[float],
    direction: str,
    n_sims: int = NUM_SIMULATIONS,
) -> dict[str, Any]:
    """Bootstrap simulation for economics indicators.

    Samples from historical period-over-period changes to simulate
    forward paths. Works for any FRED time series.
    """
    if not historical_changes or current_value == 0:
        return {
            "mc_probability": 0.5,
            "mean_final": current_value,
            "median_final": current_value,
            "std_final": 0.0,
            "pct_5": current_value,
            "pct_95": current_value,
            "n_simulations": 0,
        }

    rng = random.Random(SEED)
    finals: list[float] = []
    hits = 0

    for _ in range(n_sims):
        # Sample a random historical change and apply it
        change = rng.choice(historical_changes)
        final_value = current_value + change
        finals.append(final_value)

        if direction in ("below", "cut"):
            if final_value < target_value:
                hits += 1
        else:  # above, hike, other
            if final_value >= target_value:
                hits += 1

    finals.sort()
    n = len(finals)
    mc_prob = hits / n_sims

    return {
        "mc_probability": mc_prob,
        "mean_final": sum(finals) / n,
        "median_final": finals[n // 2],
        "std_final": math.sqrt(sum((f - sum(finals) / n) ** 2 for f in finals) / n),
        "pct_5": finals[int(n * 0.05)],
        "pct_95": finals[int(n * 0.95)],
        "n_simulations": n_sims,
    }


async def _fetch_coingecko_chart(
    session: aiohttp.ClientSession, coin_id: str, days: int = 90
) -> list[list[float]] | None:
    """Fetch price history from CoinGecko market_chart endpoint."""
    url = COINGECKO_CHART_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "days": str(days)}
    try:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("CoinGecko chart returned %d for %s", resp.status, coin_id)
                return None
            data = await resp.json()
        return data.get("prices", [])
    except Exception as e:
        logger.warning("Error fetching CoinGecko chart for %s: %s", coin_id, e)
        return None


async def _fetch_coingecko_price(
    session: aiohttp.ClientSession, coin_id: str
) -> float | None:
    """Fetch current price from CoinGecko."""
    params = {"ids": coin_id, "vs_currencies": "usd"}
    try:
        async with session.get(
            COINGECKO_PRICE_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        coin_data = data.get(coin_id, {})
        return coin_data.get("usd")
    except Exception as e:
        logger.warning("Error fetching CoinGecko price for %s: %s", coin_id, e)
        return None


async def _fetch_fred_series(
    session: aiohttp.ClientSession, series_id: str, limit: int = 60
) -> list[dict[str, str]]:
    """Fetch observations from FRED (more history for bootstrap)."""
    params = {
        "series_id": series_id,
        "file_type": "json",
        "api_key": FRED_API_KEY,
        "sort_order": "desc",
        "limit": str(limit),
    }
    try:
        async with session.get(
            FRED_OBS_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        observations = data.get("observations", [])
        return [obs for obs in observations if obs.get("value", ".") != "."]
    except Exception as e:
        logger.warning("Error fetching FRED series %s: %s", series_id, e)
        return []


def _compute_volatility(prices: list[list[float]]) -> float:
    """Compute annualized volatility from price history."""
    if len(prices) < 2:
        return 0.0
    price_values = [p[1] for p in prices if p[1] > 0]
    if len(price_values) < 2:
        return 0.0
    log_returns: list[float] = []
    for i in range(1, len(price_values)):
        log_returns.append(math.log(price_values[i] / price_values[i - 1]))
    if not log_returns:
        return 0.0
    mean_ret = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / len(log_returns)
    return math.sqrt(variance) * math.sqrt(365)


def _extract_fred_changes(observations: list[dict[str, str]]) -> list[float]:
    """Extract period-over-period changes from FRED observations."""
    values: list[float] = []
    for obs in observations:
        try:
            values.append(float(obs["value"]))
        except (KeyError, ValueError):
            continue
    if len(values) < 2:
        return []
    # Changes between consecutive periods (newest first, so reverse for chronological)
    values.reverse()
    return [values[i] - values[i - 1] for i in range(1, len(values))]


class MonteCarloProvider(SignalProvider):
    """Monte Carlo simulation signal provider.

    For crypto: runs GBM simulations using historical volatility from CoinGecko.
    For economics: bootstraps from historical FRED period-over-period changes.
    Returns probability distribution statistics + cheap LLM interpretation.
    """

    name: str = "monte_carlo"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: LLMClient,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._llm = llm
        self._on_progress = on_progress

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        if self._on_progress:
            try:
                self._on_progress(question, stage, detail)
            except Exception:
                pass

    async def get_signal(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        **kwargs: Any,
    ) -> SignalResult:
        category = market_category.lower()
        if category not in ("crypto", "economics"):
            return SignalResult(
                source="monte_carlo",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' not supported for Monte Carlo",
                model_used="none",
                data_points=0,
            )

        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                self._emit(market_question, "cache")
                return cached_result

        try:
            if category == "crypto":
                result = await self._run_crypto_mc(market_question, market_end_date, kwargs)
            else:
                result = await self._run_econ_mc(market_question, market_end_date, kwargs)
        except Exception as e:
            logger.error("Monte Carlo pipeline failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="monte_carlo",
                probability=None,
                confidence=0.0,
                reasoning=f"Pipeline error: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        _signal_cache[cache_key] = (result, time.monotonic())
        self._log_signal(market_question, result)
        self._emit(market_question, "done", result.reasoning[:100])
        return result

    def _log_signal(self, market_question: str, result: SignalResult) -> None:
        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source=result.source,
                probability=result.probability if result.probability is not None else -1.0,
                confidence=result.confidence,
                reasoning=result.reasoning[:1000],
                model_used=result.model_used,
            )
        except Exception as e:
            logger.warning("Failed to log signal to DB: %s", e)

    async def _run_crypto_mc(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        resolution_keywords = kwargs.get("resolution_keywords", {})
        coin_id = resolution_keywords.get("coin_id")
        target_value = resolution_keywords.get("target_value")
        target_direction = resolution_keywords.get("target_direction", "above")

        if not coin_id:
            # Try LLM extraction
            prompt = (
                f'What is the CoinGecko API coin ID for the cryptocurrency in: "{market_question}"?\n'
                f'Respond as JSON: {{"coin_id": "the_id"}}'
            )
            try:
                result = await self._llm.call_json(prompt, task_type="extract")
                if isinstance(result, dict):
                    coin_id = result.get("coin_id")
            except Exception:
                pass

        if not coin_id:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning="Could not determine coin ID for MC simulation",
                model_used="none", data_points=0,
            )

        if target_value is None:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning="No target value for MC simulation",
                model_used="none", data_points=0,
            )

        target_price = float(target_value)

        self._emit(market_question, "mc_fetch", f"fetching {coin_id} 90d history")
        async with aiohttp.ClientSession() as session:
            chart_data = await _fetch_coingecko_chart(session, coin_id, days=90)
            current_price = await _fetch_coingecko_price(session, coin_id)

        if not chart_data or current_price is None or current_price <= 0:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning=f"Insufficient price data for {coin_id}",
                model_used="none", data_points=0,
            )

        annual_vol = _compute_volatility(chart_data)
        if annual_vol <= 0:
            annual_vol = 0.80  # Default crypto volatility

        # Days remaining
        try:
            end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            days_remaining = max(0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
        except (ValueError, TypeError):
            days_remaining = 30.0

        self._emit(market_question, "mc_simulate", f"{NUM_SIMULATIONS} GBM paths")
        mc_stats = _run_gbm_simulation(
            current_price=current_price,
            target_price=target_price,
            annual_vol=annual_vol,
            days_remaining=days_remaining,
            direction=target_direction,
            n_sims=NUM_SIMULATIONS,
        )

        # Cheap LLM interprets MC results
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)
        interpret_prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n\n"
            f"Monte Carlo simulation results ({NUM_SIMULATIONS} paths, geometric Brownian motion):\n"
            f"- Current price: ${current_price:,.2f}\n"
            f"- Target price: ${target_price:,.2f} ({target_direction})\n"
            f"- Annualized volatility: {annual_vol:.0%}\n"
            f"- Days remaining: {days_remaining:.0f}\n"
            f"- MC probability of YES: {mc_stats['mc_probability']:.3f}\n"
            f"- Mean simulated final price: ${mc_stats['mean_final']:,.2f}\n"
            f"- Median simulated final price: ${mc_stats['median_final']:,.2f}\n"
            f"- 5th percentile: ${mc_stats['pct_5']:,.2f}\n"
            f"- 25th percentile: ${mc_stats['pct_25']:,.2f}\n"
            f"- 75th percentile: ${mc_stats['pct_75']:,.2f}\n"
            f"- 95th percentile: ${mc_stats['pct_95']:,.2f}\n\n"
            f"Based on this Monte Carlo simulation, provide your probability estimate.\n"
            f"The simulation assumes a random walk with historical volatility.\n"
            f"Adjust slightly if you believe momentum or mean-reversion effects apply.\n\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        try:
            result = await self._llm.call_json(interpret_prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))
                if prob is not None:
                    prob = max(0.0, min(1.0, float(prob)))
                conf = max(0.0, min(1.0, conf))
                return SignalResult(
                    source="monte_carlo",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(chart_data),
                    raw_data={
                        "simulation_type": "gbm",
                        "coin_id": coin_id,
                        "current_price": current_price,
                        "target_price": target_price,
                        "annual_vol": annual_vol,
                        "days_remaining": days_remaining,
                        "mc_probability": mc_stats["mc_probability"],
                        "mean_final": mc_stats["mean_final"],
                        "median_final": mc_stats["median_final"],
                        "pct_5": mc_stats["pct_5"],
                        "pct_95": mc_stats["pct_95"],
                        "n_simulations": mc_stats["n_simulations"],
                    },
                )
        except Exception as e:
            logger.error("Failed to interpret MC results: %s", e)

        # Fall back to raw MC probability
        return SignalResult(
            source="monte_carlo",
            probability=mc_stats["mc_probability"],
            confidence=0.4,
            reasoning=f"Raw Monte Carlo: {mc_stats['mc_probability']:.1%} from {NUM_SIMULATIONS} GBM paths",
            model_used="none",
            data_points=len(chart_data),
            raw_data=mc_stats,
        )

    async def _run_econ_mc(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        resolution_keywords = kwargs.get("resolution_keywords", {})
        indicator_type = resolution_keywords.get("indicator_type", "other")
        target_value = resolution_keywords.get("target_value")
        target_direction = resolution_keywords.get("target_direction", "above")

        series_ids = INDICATOR_SERIES.get(indicator_type, DEFAULT_SERIES)
        primary_series = series_ids[0]

        self._emit(market_question, "mc_fetch", f"fetching {primary_series} history (60 obs)")
        async with aiohttp.ClientSession() as session:
            observations = await _fetch_fred_series(session, primary_series, limit=60)

        if len(observations) < 3:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning=f"Insufficient FRED data for {primary_series}",
                model_used="none", data_points=0,
            )

        # Extract values and compute changes
        values: list[float] = []
        for obs in observations:
            try:
                values.append(float(obs["value"]))
            except (KeyError, ValueError):
                continue

        if len(values) < 3:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning="Insufficient numeric data from FRED",
                model_used="none", data_points=0,
            )

        current_value = values[0]  # newest first
        historical_changes = _extract_fred_changes(observations)

        if not historical_changes:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning="Could not compute historical changes",
                model_used="none", data_points=0,
            )

        if target_value is None:
            return SignalResult(
                source="monte_carlo", probability=None, confidence=0.0,
                reasoning="No target value for economics MC simulation",
                model_used="none", data_points=len(values),
                raw_data={"current_value": current_value, "series": primary_series},
            )

        target = float(target_value)

        self._emit(market_question, "mc_simulate", f"bootstrapping {NUM_SIMULATIONS} paths from {len(historical_changes)} changes")
        mc_stats = _run_bootstrap_simulation(
            current_value=current_value,
            target_value=target,
            historical_changes=historical_changes,
            direction=target_direction,
            n_sims=NUM_SIMULATIONS,
        )

        # Cheap LLM interprets
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)
        interpret_prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n\n"
            f"Monte Carlo bootstrap simulation ({NUM_SIMULATIONS} paths):\n"
            f"- FRED series: {primary_series}\n"
            f"- Current value: {current_value}\n"
            f"- Target value: {target} ({target_direction})\n"
            f"- Historical changes sampled: {len(historical_changes)} periods\n"
            f"- MC probability of YES: {mc_stats['mc_probability']:.3f}\n"
            f"- Mean simulated final: {mc_stats['mean_final']:.4f}\n"
            f"- Median simulated final: {mc_stats['median_final']:.4f}\n"
            f"- 5th percentile: {mc_stats['pct_5']:.4f}\n"
            f"- 95th percentile: {mc_stats['pct_95']:.4f}\n\n"
            f"The simulation bootstraps from actual historical period-over-period changes.\n"
            f"Provide your probability estimate based on these results.\n\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        try:
            result = await self._llm.call_json(interpret_prompt, task_type="classify")
            if isinstance(result, dict):
                prob = result.get("probability")
                conf = float(result.get("confidence", 0.0))
                reasoning = str(result.get("reasoning", ""))
                if prob is not None:
                    prob = max(0.0, min(1.0, float(prob)))
                conf = max(0.0, min(1.0, conf))
                return SignalResult(
                    source="monte_carlo",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(values),
                    raw_data={
                        "simulation_type": "bootstrap",
                        "series": primary_series,
                        "current_value": current_value,
                        "target_value": target,
                        "n_historical_changes": len(historical_changes),
                        "mc_probability": mc_stats["mc_probability"],
                        "mean_final": mc_stats["mean_final"],
                        "pct_5": mc_stats["pct_5"],
                        "pct_95": mc_stats["pct_95"],
                        "n_simulations": mc_stats["n_simulations"],
                    },
                )
        except Exception as e:
            logger.error("Failed to interpret econ MC results: %s", e)

        return SignalResult(
            source="monte_carlo",
            probability=mc_stats["mc_probability"],
            confidence=0.3,
            reasoning=f"Raw bootstrap MC: {mc_stats['mc_probability']:.1%} from {NUM_SIMULATIONS} paths",
            model_used="none",
            data_points=len(values),
            raw_data=mc_stats,
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
