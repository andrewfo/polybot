"""Crypto resolution source signal provider.

Fetches data from CoinGecko (free, no API key) and computes probability
using log-normal price models. Supports both terminal distribution
(price at expiry) and barrier option (price touches target at any point).
Returns the mathematical result directly — no cheap LLM adjustment.
The frontier model receives the raw data and can make its own adjustments.
"""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from core import db, fetch_with_retry
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes (crypto moves fast)

# Coin-level data cache to avoid CoinGecko rate limits (429s).
# Multiple markets about the same coin share the same API responses.
# Key: coin_id, Value: (price_data, chart_data, deribit_iv, timestamp)
_coin_data_cache: dict[str, tuple[dict | None, list | None, float | None, float]] = {}
COIN_DATA_CACHE_TTL = 300  # 5 minutes

COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
DERIBIT_TICKER_URL = "https://www.deribit.com/api/v2/public/ticker"

# CoinGecko ID → Deribit currency code (Deribit only lists major coins)
_COINGECKO_TO_DERIBIT: dict[str, str] = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

USER_AGENT = "polymarket-bot/1.0 (signal research)"

# Top 50 ticker → CoinGecko ID whitelist (avoids LLM hallucination for common coins)
TICKER_TO_COINGECKO: dict[str, str] = {
    "btc": "bitcoin", "bitcoin": "bitcoin",
    "eth": "ethereum", "ethereum": "ethereum",
    "sol": "solana", "solana": "solana",
    "ada": "cardano", "cardano": "cardano",
    "xrp": "ripple", "ripple": "ripple",
    "doge": "dogecoin", "dogecoin": "dogecoin",
    "dot": "polkadot", "polkadot": "polkadot",
    "avax": "avalanche-2", "avalanche": "avalanche-2",
    "link": "chainlink", "chainlink": "chainlink",
    "matic": "matic-network", "polygon": "matic-network",
    "ltc": "litecoin", "litecoin": "litecoin",
    "uni": "uniswap", "uniswap": "uniswap",
    "atom": "cosmos", "cosmos": "cosmos",
    "near": "near",
    "apt": "aptos", "aptos": "aptos",
    "arb": "arbitrum", "arbitrum": "arbitrum",
    "op": "optimism", "optimism": "optimism",
    "sui": "sui",
    "sei": "sei-network",
    "tia": "celestia", "celestia": "celestia",
    "jup": "jupiter-exchange-solana", "jupiter": "jupiter-exchange-solana",
    "bonk": "bonk",
    "pepe": "pepe",
    "shib": "shiba-inu", "shiba": "shiba-inu",
    "ton": "the-open-network", "toncoin": "the-open-network",
    "trx": "tron", "tron": "tron",
    "xlm": "stellar", "stellar": "stellar",
    "hbar": "hedera-hashgraph", "hedera": "hedera-hashgraph",
    "fil": "filecoin", "filecoin": "filecoin",
    "rndr": "render-token", "render": "render-token",
    "inj": "injective-protocol", "injective": "injective-protocol",
    "stx": "blockstack", "stacks": "blockstack",
    "kas": "kaspa", "kaspa": "kaspa",
    "mnt": "mantle", "mantle": "mantle",
    "beam": "beam-2",
    "bnb": "binancecoin", "binance coin": "binancecoin",
    "vet": "vechain", "vechain": "vechain",
    "algo": "algorand", "algorand": "algorand",
    "ftm": "fantom", "fantom": "fantom",
    "aave": "aave",
    "mkr": "maker", "maker": "maker",
    "crv": "curve-dao-token", "curve": "curve-dao-token",
    "ldo": "lido-dao", "lido": "lido-dao",
    "snx": "havven", "synthetix": "havven",
    "sand": "the-sandbox", "sandbox": "the-sandbox",
    "mana": "decentraland", "decentraland": "decentraland",
    "grt": "the-graph",
    "wif": "dogwifcoin", "dogwifhat": "dogwifcoin",
}


@dataclass
class VolEstimate:
    """Rich volatility estimate from historical price data."""

    annual_vol: float           # Standard annualized vol (Bessel-corrected)
    annual_vol_ewm: float       # Exponentially-weighted (recent emphasis)
    short_term_vol: float       # Vol from last 7 days of data
    realized_drift: float | None  # Annualized drift (mean log return * 365/interval)
    drift_stderr: float | None  # Standard error of drift estimate
    data_points: int            # Number of price observations
    avg_interval_hours: float   # Average hours between observations


def norm_cdf(x: float) -> float:
    """Normal CDF via math.erf — no scipy dependency needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def log_normal_probability(
    current_price: float,
    target_price: float,
    annual_vol: float,
    days_remaining: float,
    direction: str = "above",
    drift: float | None = None,
) -> float:
    """Compute probability of price being above/below target at expiry (terminal).

    Uses geometric Brownian motion terminal distribution. When ``drift`` is
    provided (real-world drift estimated from historical returns), it is used
    directly. Otherwise falls back to risk-neutral drift (``-0.5 * sigma^2``).

    This computes P(S_T >= target) or P(S_T < target) — the probability at
    the specific expiry time, NOT the probability of ever touching the target.
    For touch/barrier probability, use ``barrier_probability()`` instead.

    Args:
        current_price: Current asset price.
        target_price: Target price threshold.
        annual_vol: Annualized volatility (as decimal, e.g. 0.80 for 80%).
        days_remaining: Days until resolution.
        direction: "above" for P(price >= target), "below" for P(price < target).
        drift: Annualized log-return drift.  ``None`` → risk-neutral fallback.

    Returns:
        Probability between 0 and 1.
    """
    # Edge case: no time remaining — binary outcome
    if days_remaining <= 0:
        if direction == "below":
            return 1.0 if current_price < target_price else 0.0
        return 1.0 if current_price >= target_price else 0.0

    # Edge case: zero volatility — deterministic
    if annual_vol <= 0:
        if direction == "below":
            return 1.0 if current_price < target_price else 0.0
        return 1.0 if current_price >= target_price else 0.0

    # Edge case: prices must be positive
    if current_price <= 0 or target_price <= 0:
        return 0.5

    log_ratio = math.log(target_price / current_price)
    # ``drift`` is the annualized log-price drift (i.e. (mu - 0.5*sigma^2) for GBM).
    # When estimated from historical log-returns, mean(log_returns)*annualize
    # already equals (mu - 0.5*sigma^2), so we use it directly.
    effective_drift = drift if drift is not None else (-0.5 * annual_vol ** 2)
    time_years = days_remaining / 365.0
    z = (log_ratio - effective_drift * time_years) / (annual_vol * math.sqrt(time_years))

    if direction == "below":
        return norm_cdf(z)
    return 1.0 - norm_cdf(z)  # P(price >= target)


def barrier_probability(
    current_price: float,
    target_price: float,
    annual_vol: float,
    days_remaining: float,
    direction: str = "above",
    drift: float | None = None,
) -> float:
    """Probability that price touches target at ANY point before expiry.

    Uses the closed-form solution for the running maximum/minimum of
    geometric Brownian motion (reflection principle).

    For direction="above": P(max_{0<=t<=T} S_t >= target)
    For direction="below": P(min_{0<=t<=T} S_t <= target)

    This is always >= the terminal probability from log_normal_probability().
    Many Polymarket crypto markets resolve based on "Will X reach Y?" which
    is a barrier/touch event, not a terminal price comparison.

    Args:
        current_price: Current asset price.
        target_price: Target price threshold.
        annual_vol: Annualized volatility (as decimal, e.g. 0.80 for 80%).
        days_remaining: Days until resolution.
        direction: "above" for ever-touch-above, "below" for ever-touch-below.
        drift: Annualized log-return drift. None → risk-neutral fallback.

    Returns:
        Probability between 0 and 1.
    """
    # Edge cases
    if days_remaining <= 0:
        if direction == "below":
            return 1.0 if current_price <= target_price else 0.0
        return 1.0 if current_price >= target_price else 0.0

    if annual_vol <= 0 or current_price <= 0 or target_price <= 0:
        return 0.5

    # Already past the barrier
    if direction == "above" and current_price >= target_price:
        return 1.0
    if direction == "below" and current_price <= target_price:
        return 1.0

    m = drift if drift is not None else (-0.5 * annual_vol ** 2)
    sigma = annual_vol
    T = days_remaining / 365.0
    sigma_sqrt_T = sigma * math.sqrt(T)

    if direction == "above":
        # P(max S_t >= H) where H > S_0
        # b = log(H/S_0) > 0
        b = math.log(target_price / current_price)
        d1 = (m * T - b) / sigma_sqrt_T
        d2 = (-m * T - b) / sigma_sqrt_T

        # Exponential term: exp(2*m*b/sigma^2)
        exponent = 2.0 * m * b / (sigma ** 2)
    else:
        # P(min S_t <= L) where L < S_0
        # c = log(S_0/L) > 0
        c = math.log(current_price / target_price)
        d1 = (-m * T - c) / sigma_sqrt_T
        d2 = (m * T - c) / sigma_sqrt_T

        # Exponential term: exp(-2*m*c/sigma^2)
        exponent = -2.0 * m * c / (sigma ** 2)

    # Clamp exponent to avoid overflow
    if exponent > 500:
        # Very large exponent means reflection term dominates → prob ≈ 1
        return 1.0
    elif exponent < -500:
        exp_term = 0.0
    else:
        exp_term = math.exp(exponent)

    prob = norm_cdf(d1) + exp_term * norm_cdf(d2)
    return max(0.0, min(1.0, prob))


def _shrink_drift(drift: float, stderr: float | None) -> float:
    """Shrink noisy drift estimates toward zero using Bayesian shrinkage.

    When the drift estimate is statistically insignificant (low t-statistic),
    we shrink it toward our prior of zero drift. This prevents noisy momentum
    estimates from wildly swinging probability outputs.

    Uses shrinkage factor: t^2 / (t^2 + 1), which gives:
    - t=0 → keep 0% of drift (no signal)
    - t=1 → keep 50% of drift (weak signal)
    - t=2 → keep 80% of drift (moderate signal, ~95% significance)
    - t=3 → keep 90% of drift (strong signal)
    """
    if drift == 0.0:
        return 0.0

    if stderr is None or stderr <= 0:
        # No standard error available — use moderate shrinkage (50%)
        return drift * 0.5

    t = abs(drift) / stderr
    shrinkage = t ** 2 / (t ** 2 + 1.0)
    return drift * shrinkage


async def _fetch_coingecko_price(
    session: aiohttp.ClientSession, coin_id: str
) -> dict[str, Any] | None:
    """Fetch current price and 24h change from CoinGecko."""
    params = {
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }

    async def _attempt() -> dict[str, Any] | None:
        async with session.get(
            COINGECKO_PRICE_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json()
        return data.get(coin_id)

    return await fetch_with_retry(_attempt, label=f"CoinGecko price ({coin_id})")


async def _fetch_coingecko_chart(
    session: aiohttp.ClientSession, coin_id: str, days: int = 30
) -> list[list[float]] | None:
    """Fetch price history from CoinGecko market_chart endpoint."""
    url = COINGECKO_CHART_URL.format(coin_id=coin_id)
    params = {"vs_currency": "usd", "days": str(days)}

    async def _attempt() -> list[list[float]] | None:
        async with session.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json()
        return data.get("prices", [])

    return await fetch_with_retry(_attempt, label=f"CoinGecko chart ({coin_id})")


async def _fetch_deribit_iv(
    session: aiohttp.ClientSession, coin_id: str
) -> float | None:
    """Fetch implied volatility from the nearest Deribit ATM option.

    Uses the Deribit ``/public/ticker`` endpoint (no auth required) on the
    nearest-expiry at-the-money option to get ``mark_iv`` (annualized IV as a
    percentage, e.g. 65 for 65%).  Returns IV as a decimal (0.65) or ``None``
    if Deribit doesn't list this coin or the request fails.
    """
    deribit_currency = _COINGECKO_TO_DERIBIT.get(coin_id)
    if not deribit_currency:
        return None

    # Deribit perpetual index gives current price; we use the DVOL instrument
    # for overall implied vol.  Format: {CURRENCY}-DVOL
    dvol_instrument = f"{deribit_currency}VOL-USDC"
    params = {"instrument_name": dvol_instrument}

    async def _attempt() -> float | None:
        async with session.get(
            DERIBIT_TICKER_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json()
        result = data.get("result", {})
        mark_price = result.get("mark_price")
        if mark_price and mark_price > 0:
            return mark_price / 100.0
        return None

    return await fetch_with_retry(_attempt, label=f"Deribit DVOL ({coin_id})")


def _compute_volatility(prices: list[list[float]]) -> VolEstimate:
    """Compute volatility estimates from price time series.

    Uses time-aware intervals (not assuming daily spacing), Bessel's
    correction for unbiased variance, and exponential weighting for
    recent observations.

    Args:
        prices: List of [timestamp_ms, price] pairs from CoinGecko.

    Returns:
        VolEstimate with multiple volatility metrics and drift.
    """
    if len(prices) < 2:
        return VolEstimate(0.0, 0.0, 0.0, None, None, 0, 0.0)

    # Build returns with actual time intervals
    # Each entry: (log_return, interval_days, timestamp_ms)
    returns: list[tuple[float, float, float]] = []
    for i in range(1, len(prices)):
        if prices[i][1] <= 0 or prices[i - 1][1] <= 0:
            continue
        dt_ms = prices[i][0] - prices[i - 1][0]
        dt_days = dt_ms / (1000.0 * 86400.0)
        if dt_days <= 0.001:  # skip sub-minute intervals
            continue
        lr = math.log(prices[i][1] / prices[i - 1][1])
        returns.append((lr, dt_days, prices[i][0]))

    if len(returns) < 2:
        return VolEstimate(0.0, 0.0, 0.0, None, None, 0, 0.0)

    n = len(returns)
    total_time_days = sum(dt for _, dt, _ in returns)
    avg_interval_days = total_time_days / n
    avg_interval_hours = avg_interval_days * 24.0
    total_time_years = total_time_days / 365.0

    # --- Drift estimate (annualized) ---
    # Under GBM: E[log(S_{i+1}/S_i)] = m * dt_i where m = (mu - sigma^2/2)
    # MLE for m: total_log_return / total_time
    total_log_return = sum(r for r, _, _ in returns)
    drift_per_year = total_log_return / total_time_years if total_time_years > 0 else 0.0

    # --- Standard vol (time-weighted, Bessel-corrected) ---
    # Under GBM: Var(log_return_i) = sigma^2 * dt_i
    # So (log_return_i - m*dt_i)^2 / dt_i is an estimate of sigma^2
    # Average these with Bessel's correction (N-1)
    sum_sq = 0.0
    for r, dt, _ in returns:
        dt_yr = dt / 365.0
        residual = r - drift_per_year * dt_yr
        sum_sq += residual ** 2 / dt_yr

    sigma_sq = sum_sq / (n - 1) if n > 1 else sum_sq
    annual_vol = math.sqrt(max(0.0, sigma_sq))

    # --- Exponentially-weighted vol (RiskMetrics-style) ---
    # Lambda = 0.94 per day (industry standard), adjusted for actual interval
    lambda_daily = 0.94
    lambda_per_obs = lambda_daily ** avg_interval_days
    ewm_var = 0.0
    weight_sum = 0.0
    w = 1.0
    for r, dt, _ in reversed(returns):
        # Annualized squared return (zero-mean for short-term vol)
        annualized_sq = r ** 2 / (dt / 365.0)
        ewm_var += w * annualized_sq
        weight_sum += w
        w *= lambda_per_obs
    annual_vol_ewm = math.sqrt(ewm_var / weight_sum) if weight_sum > 0 else annual_vol

    # --- Short-term vol (last 7 days of data) ---
    last_ts = returns[-1][2]
    cutoff_7d = last_ts - 7.0 * 24.0 * 3600.0 * 1000.0
    short_returns = [(r, dt) for r, dt, ts in returns if ts >= cutoff_7d]
    if len(short_returns) >= 3:
        # Use zero-mean estimator for short-term (drift negligible over 7 days)
        st_sum_sq = sum(r ** 2 / (dt / 365.0) for r, dt in short_returns)
        short_term_vol = math.sqrt(st_sum_sq / len(short_returns))
    else:
        short_term_vol = annual_vol_ewm  # fall back to EWM

    # --- Drift standard error ---
    # SE(drift) ≈ sigma / sqrt(T) where T is observation period in years
    drift_stderr = annual_vol / math.sqrt(total_time_years) if total_time_years > 0 else None

    return VolEstimate(
        annual_vol=annual_vol,
        annual_vol_ewm=annual_vol_ewm,
        short_term_vol=short_term_vol,
        realized_drift=drift_per_year,
        drift_stderr=drift_stderr,
        data_points=n + 1,  # n returns = n+1 price points
        avg_interval_hours=avg_interval_hours,
    )


def _select_volatility(
    vol_est: VolEstimate,
    deribit_iv: float | None,
    days_remaining: float,
) -> tuple[float, str]:
    """Select the best volatility estimate for the given time horizon.

    Priority:
    1. Deribit IV when available (forward-looking, market-implied)
       - For very short horizons (< 14d), blend with short-term realized
    2. Short-term vol for short-dated markets (< 14d)
    3. EWM vol for medium horizons (gives more weight to recent regime)
    4. Standard historical vol for long horizons
    5. Default 80% if no data available

    Returns:
        Tuple of (volatility, source_label).
    """
    if deribit_iv is not None and deribit_iv > 0:
        if days_remaining < 14 and vol_est.short_term_vol > 0:
            # Very short horizon: blend IV with recent realized vol
            # Short-term realized captures current regime; IV captures expectations
            blended = 0.5 * deribit_iv + 0.5 * vol_est.short_term_vol
            return blended, f"blend(iv={deribit_iv:.0%},st={vol_est.short_term_vol:.0%})"
        return deribit_iv, "deribit_iv"

    # No Deribit IV available
    if days_remaining < 14 and vol_est.short_term_vol > 0:
        return vol_est.short_term_vol, "short_7d"
    elif vol_est.annual_vol_ewm > 0:
        return vol_est.annual_vol_ewm, "ewm"
    elif vol_est.annual_vol > 0:
        return vol_est.annual_vol, "historical"
    else:
        return 0.80, "default_80pct"


def _describe_trend(prices: list[list[float]]) -> str:
    """Describe the 30-day price trend in human-readable text."""
    if not prices or len(prices) < 2:
        return "Insufficient data"
    first_price = prices[0][1]
    last_price = prices[-1][1]
    if first_price == 0:
        return "Cannot compute trend (zero starting price)"
    change_pct = ((last_price - first_price) / first_price) * 100
    if change_pct > 5:
        direction = "upward"
    elif change_pct < -5:
        direction = "downward"
    else:
        direction = "sideways"
    return f"{direction} ({change_pct:+.1f}% over {len(prices)} data points)"


class CryptoResolutionProvider(SignalProvider):
    """Resolution source signal provider for crypto markets.

    Pipeline:
    1. If category != crypto -> return confidence=0 immediately
    2. Resolve CoinGecko coin_id (from kwargs or via cheap LLM mapping)
    3. Fetch current price + 90-day history + Deribit IV
    4. Compute volatility (time-weighted, EWM, short-term)
    5. Determine resolution type (barrier vs terminal)
    6. Compute probability with appropriate model
    7. Return mathematical probability directly with raw data for frontier model
    """

    name: str = "resolution_crypto"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: LLMClient,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._llm = llm
        self._on_progress = on_progress

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        """Emit a progress update if a callback is registered."""
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
        """Produce a CoinGecko + log-normal model signal for a crypto market."""
        # Gate: skip non-crypto categories
        if market_category.lower() != "crypto":
            return SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' is not crypto",
                model_used="none",
                data_points=0,
            )

        # Check cache
        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                logger.debug("Cache hit for crypto signal: %s", market_question[:60])
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(
                market_question, market_end_date, kwargs
            )
        except Exception as e:
            logger.error(
                "Crypto signal pipeline failed for '%s': %s",
                market_question[:60], e,
            )
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.0,
                reasoning=f"Pipeline error: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        # Cache result
        _signal_cache[cache_key] = (result, time.monotonic())

        # Log to DB
        self._log_signal(market_question, result)

        self._emit(market_question, "done", result.reasoning[:100])

        return result

    def _log_signal(self, market_question: str, result: SignalResult) -> None:
        """Log signal result to the signals SQLite table."""
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

    async def _resolve_coin_id(
        self, resolution_keywords: dict[str, Any], market_question: str
    ) -> str | None:
        """Resolve the CoinGecko coin ID from keywords, whitelist, or LLM."""
        coin_id = resolution_keywords.get("coin_id")
        if coin_id:
            # Check if the coin_id is actually a ticker that needs mapping
            mapped = TICKER_TO_COINGECKO.get(coin_id.lower())
            if mapped:
                return mapped
            return coin_id

        # Try to extract coin name from keywords
        coin_name = resolution_keywords.get("metric_name", "")
        if not coin_name:
            # Fall back to extracting from question
            coin_name = market_question

        # Check ticker whitelist before LLM (eliminates hallucination for common coins)
        coin_name_lower = coin_name.lower()
        for ticker, cg_id in TICKER_TO_COINGECKO.items():
            if ticker in coin_name_lower.split() or ticker in coin_name_lower:
                logger.debug("Whitelist match: '%s' → %s", coin_name[:40], cg_id)
                return cg_id

        # Check cache for a previous mapping
        cache_key = f"coingecko_map:{coin_name_lower}"
        cached = db.get_cached_market(cache_key)
        if cached and isinstance(cached.get("data"), dict):
            mapped_id = cached["data"].get("coin_id")
            if mapped_id:
                return mapped_id

        # Use cheap LLM to map coin name -> CoinGecko ID
        prompt = (
            f'What is the CoinGecko API coin ID for the cryptocurrency mentioned in this text?\n'
            f'Text: "{coin_name}"\n'
            f'Common examples: bitcoin, ethereum, solana, cardano, dogecoin, ripple (xrp), polkadot, etc.\n'
            f'Respond as JSON: {{"coin_id": "the_coingecko_id"}}'
        )
        try:
            result = await self._llm.call_json(prompt, task_type="extract")
            if isinstance(result, dict) and result.get("coin_id"):
                mapped_id = result["coin_id"]
                # Cache the mapping
                db.cache_market(
                    condition_id=cache_key,
                    data={"coin_id": mapped_id, "coin_name": coin_name},
                    category="crypto",
                )
                return mapped_id
        except Exception as e:
            logger.warning("Failed to map coin name to CoinGecko ID: %s", e)

        return None

    async def _fetch_coin_data(
        self, coin_id: str
    ) -> tuple[dict[str, Any] | None, list[list[float]] | None, float | None]:
        """Fetch price, chart, and IV data with coin-level caching.

        Multiple markets about the same coin (e.g. "Will BTC hit $100k",
        "Will BTC hit $120k") share the same CoinGecko/Deribit responses
        to avoid 429 rate limits.
        """
        now = time.monotonic()
        cached = _coin_data_cache.get(coin_id)
        if cached is not None:
            price_data, chart_data, deribit_iv, cached_at = cached
            if now - cached_at < COIN_DATA_CACHE_TTL:
                logger.debug("Coin data cache hit for %s", coin_id)
                return price_data, chart_data, deribit_iv

        async with aiohttp.ClientSession() as session:
            price_data = await _fetch_coingecko_price(session, coin_id)
            chart_data = await _fetch_coingecko_chart(session, coin_id, days=90)
            deribit_iv = await _fetch_deribit_iv(session, coin_id)

        _coin_data_cache[coin_id] = (price_data, chart_data, deribit_iv, now)
        return price_data, chart_data, deribit_iv

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        """Execute the full crypto signal pipeline.

        Returns the model probability directly without LLM adjustment.
        All raw data is included so the frontier model can make its own assessment.
        """
        resolution_keywords = kwargs.get("resolution_keywords", {})

        # Resolve coin ID
        self._emit(market_question, "coingecko", "resolving coin ID")
        coin_id = await self._resolve_coin_id(resolution_keywords, market_question)
        if not coin_id:
            return SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.0,
                reasoning="Could not determine CoinGecko coin ID",
                model_used="none",
                data_points=0,
            )

        # Fetch price data + Deribit IV (with coin-level caching to avoid 429s)
        self._emit(market_question, "coingecko", f"fetching {coin_id} data")
        price_data, chart_data, deribit_iv = await self._fetch_coin_data(coin_id)

        if price_data is None:
            return SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.0,
                reasoning=f"Failed to fetch price data for {coin_id}",
                model_used="none",
                data_points=0,
                raw_data={"coin_id": coin_id},
            )

        current_price = price_data.get("usd", 0.0)
        change_24h = price_data.get("usd_24h_change", 0.0)

        if current_price <= 0:
            return SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.0,
                reasoning=f"Invalid price for {coin_id}: {current_price}",
                model_used="none",
                data_points=0,
            )

        # Get target value and direction
        target_value = resolution_keywords.get("target_value")
        target_direction = resolution_keywords.get("target_direction", "above")

        if target_value is None:
            return SignalResult(
                source="resolution_crypto",
                probability=None,
                confidence=0.1,
                reasoning=f"No target value specified. Current {coin_id} price: ${current_price:,.2f}",
                model_used="none",
                data_points=1,
                raw_data={"current_price": current_price, "coin_id": coin_id},
            )

        target_price = float(target_value)

        # Compute rich volatility estimate from chart data
        vol_est = VolEstimate(0.0, 0.0, 0.0, None, None, 1, 0.0)
        trend_description = "No history available"

        if chart_data:
            vol_est = _compute_volatility(chart_data)
            trend_description = _describe_trend(chart_data)

        # Select best volatility for this time horizon
        # Compute days remaining first
        try:
            end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_remaining = max(0, (end_dt - now).total_seconds() / 86400)
        except (ValueError, TypeError):
            days_remaining = 30.0  # default fallback

        annual_vol, vol_source = _select_volatility(vol_est, deribit_iv, days_remaining)

        # Shrink drift to avoid noisy estimates dominating the probability
        # For short-dated markets (<7d), 90-day drift is noise — use risk-neutral (drift=0)
        realized_drift = vol_est.realized_drift
        shrunk_drift: float | None = None
        if days_remaining < 7:
            shrunk_drift = 0.0
            logger.debug("Short-dated market (<7d): defaulting drift to 0 for %s", coin_id)
        elif realized_drift is not None:
            shrunk_drift = _shrink_drift(realized_drift, vol_est.drift_stderr)

        # Determine resolution type: barrier ("will reach") vs terminal ("will be at")
        # Default to barrier for crypto markets since most are "Will X reach Y?"
        resolution_type = resolution_keywords.get("resolution_type", "barrier")

        # Compute probabilities with both models
        self._emit(
            market_question, "model",
            f"vol={annual_vol:.0%}({vol_source}), "
            f"drift={shrunk_drift or 0:.0%}, "
            f"days={days_remaining:.0f}, "
            f"type={resolution_type}",
        )

        terminal_prob = log_normal_probability(
            current_price=current_price,
            target_price=target_price,
            annual_vol=annual_vol,
            days_remaining=days_remaining,
            direction=target_direction,
            drift=shrunk_drift,
        )

        barrier_prob = barrier_probability(
            current_price=current_price,
            target_price=target_price,
            annual_vol=annual_vol,
            days_remaining=days_remaining,
            direction=target_direction,
            drift=shrunk_drift,
        )

        # Select the probability based on resolution type
        if resolution_type == "terminal":
            model_prob = terminal_prob
        elif resolution_type == "barrier":
            model_prob = barrier_prob
        else:
            # Unknown type: blend (weighted toward barrier since most crypto
            # markets are "will reach" style)
            model_prob = 0.7 * barrier_prob + 0.3 * terminal_prob

        # Distance from target
        distance_pct = ((target_price - current_price) / current_price) * 100

        # Confidence based on data quality, vol estimation, and model suitability
        confidence = 0.55  # base confidence for mathematical model
        data_points = vol_est.data_points

        if annual_vol > 0 and data_points > 10:
            confidence = 0.65
        if annual_vol > 0 and data_points > 30:
            confidence = 0.70

        # Boost for forward-looking IV (much better than historical)
        if deribit_iv is not None and deribit_iv > 0:
            confidence = min(confidence + 0.08, 0.85)

        # Reduce confidence when vol estimation is uncertain
        # (large gap between standard and EWM vol suggests regime change)
        if vol_est.annual_vol > 0 and vol_est.annual_vol_ewm > 0:
            vol_ratio = max(vol_est.annual_vol, vol_est.annual_vol_ewm) / \
                        min(vol_est.annual_vol, vol_est.annual_vol_ewm)
            if vol_ratio > 1.5:
                # Vol regime is unstable — reduce confidence
                confidence = max(confidence - 0.10, 0.40)
                logger.info(
                    "Vol regime unstable for %s (ratio=%.1f), reducing confidence",
                    coin_id, vol_ratio,
                )

        # Reduce confidence for very extreme probabilities (model might be overconfident)
        if model_prob < 0.05 or model_prob > 0.95:
            confidence = min(confidence, 0.70)

        # Reduce confidence when drift was heavily shrunk (uncertain trend)
        if realized_drift is not None and shrunk_drift is not None and realized_drift != 0:
            shrink_ratio = abs(shrunk_drift) / abs(realized_drift)
            if shrink_ratio < 0.3:
                # Heavy shrinkage = very noisy drift estimate
                confidence = max(confidence - 0.05, 0.40)

        # Build detailed reasoning
        vol_label = f"vol={annual_vol:.0%}({vol_source})"
        if vol_source == "deribit_iv":
            vol_label = f"IV={annual_vol:.0%} (hist={vol_est.annual_vol:.0%}, ewm={vol_est.annual_vol_ewm:.0%})"

        drift_label = ""
        if shrunk_drift is not None:
            drift_label = f", drift={shrunk_drift:+.0%}"
            if realized_drift is not None and abs(realized_drift - shrunk_drift) > 0.01:
                drift_label += f" (raw={realized_drift:+.0%}, shrunk)"

        model_label = "Barrier" if resolution_type == "barrier" else "Terminal"
        if resolution_type not in ("barrier", "terminal"):
            model_label = "Blended(barrier+terminal)"

        reasoning = (
            f"{model_label} model: P(YES)={model_prob:.3f} "
            f"[terminal={terminal_prob:.3f}, barrier={barrier_prob:.3f}]. "
            f"Current ${current_price:,.2f}, target ${target_price:,.2f} ({target_direction}), "
            f"{vol_label}{drift_label}, {days_remaining:.0f}d remaining. "
            f"Trend: {trend_description}"
        )

        return SignalResult(
            source="resolution_crypto",
            probability=model_prob,
            confidence=confidence,
            reasoning=reasoning,
            model_used="none",  # No LLM used — pure math
            data_points=data_points,
            raw_data={
                "coin_id": coin_id,
                "current_price": current_price,
                "target_price": target_price,
                "target_direction": target_direction,
                "direction": target_direction,
                "annualized_vol": annual_vol,
                "historical_vol": vol_est.annual_vol,
                "ewm_vol": vol_est.annual_vol_ewm,
                "short_term_vol": vol_est.short_term_vol,
                "vol_source": vol_source,
                "realized_drift": realized_drift,
                "shrunk_drift": shrunk_drift,
                "drift_stderr": vol_est.drift_stderr,
                "deribit_iv": deribit_iv,
                "days_remaining": days_remaining,
                "resolution_type": resolution_type,
                "raw_log_normal_prob": terminal_prob,
                "barrier_prob": barrier_prob,
                "terminal_prob": terminal_prob,
                "model_prob": model_prob,
                "change_24h": change_24h / 100 if change_24h else 0.0,
                "trend": trend_description,
                "distance_pct": distance_pct,
                "price_7d_ago": chart_data[-8][1] if chart_data and len(chart_data) >= 8 else (chart_data[0][1] if chart_data else None),
                "avg_interval_hours": vol_est.avg_interval_hours,
                "price_history": [
                    {"date": datetime.fromtimestamp(pt[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"), "price": round(pt[1], 2)}
                    for pt in (chart_data or [])[::max(1, len(chart_data or [1]) // 60)]
                ],
            },
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache and coin data cache."""
    _signal_cache.clear()
    _coin_data_cache.clear()
