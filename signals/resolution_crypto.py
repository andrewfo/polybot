"""Crypto resolution source signal provider.

Fetches data from CoinGecko (free, no API key) and computes a log-normal
price model probability. Returns the mathematical result directly — no
cheap LLM adjustment. The frontier model receives the raw data and can
make its own adjustments.
"""

import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes (crypto moves fast)

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
    """Compute probability of price reaching target using log-normal model.

    Uses geometric Brownian motion. When ``drift`` is provided (real-world
    drift estimated from historical returns), it is used directly.  Otherwise
    falls back to risk-neutral drift (``-0.5 * σ²``).

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
    # ``drift`` is the annualized log-price drift (i.e. (μ − ½σ²) for GBM).
    # When estimated from historical log-returns, mean(log_returns)*365 already
    # equals (μ − ½σ²), so we use it directly.  Risk-neutral fallback: μ=0.
    effective_drift = drift if drift is not None else (-0.5 * annual_vol ** 2)
    time_years = days_remaining / 365.0
    z = (log_ratio - effective_drift * time_years) / (annual_vol * math.sqrt(time_years))

    if direction == "below":
        return norm_cdf(z)
    return 1.0 - norm_cdf(z)  # P(price >= target)


async def _fetch_coingecko_price(
    session: aiohttp.ClientSession, coin_id: str
) -> dict[str, Any] | None:
    """Fetch current price and 24h change from CoinGecko."""
    params = {
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    try:
        async with session.get(
            COINGECKO_PRICE_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("CoinGecko price returned %d for %s", resp.status, coin_id)
                return None
            data = await resp.json()
        return data.get(coin_id)
    except Exception as e:
        logger.warning("Error fetching CoinGecko price for %s: %s", coin_id, e)
        return None


async def _fetch_coingecko_chart(
    session: aiohttp.ClientSession, coin_id: str, days: int = 30
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
    instrument = f"{deribit_currency}_USDC-PERPETUAL"
    dvol_instrument = f"{deribit_currency}VOL-USDC"
    params = {"instrument_name": dvol_instrument}
    try:
        async with session.get(
            DERIBIT_TICKER_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.debug("Deribit DVOL returned %d for %s", resp.status, coin_id)
                return None
            data = await resp.json()
        result = data.get("result", {})
        mark_price = result.get("mark_price")
        if mark_price and mark_price > 0:
            # DVOL index value IS the annualized IV percentage
            return mark_price / 100.0
    except Exception as e:
        logger.debug("Deribit DVOL fetch failed for %s: %s", coin_id, e)

    # Fallback: try the nearest listed option's mark_iv field
    # (requires knowing the current strike — skip for simplicity)
    return None


def _compute_volatility(prices: list[list[float]]) -> tuple[float, float | None]:
    """Compute annualized volatility and realized drift from daily price data.

    Args:
        prices: List of [timestamp_ms, price] pairs from CoinGecko.

    Returns:
        Tuple of (annualized_volatility, annualized_drift).
        Drift is the mean daily log-return × 365 — already equals (μ − ½σ²)
        for GBM, so it can be passed directly to ``log_normal_probability``.
        Returns ``None`` for drift when insufficient data.
    """
    if len(prices) < 2:
        return 0.0, None

    # Extract just prices
    price_values = [p[1] for p in prices if p[1] > 0]
    if len(price_values) < 2:
        return 0.0, None

    # Compute daily log returns
    log_returns: list[float] = []
    for i in range(1, len(price_values)):
        log_returns.append(math.log(price_values[i] / price_values[i - 1]))

    if not log_returns:
        return 0.0, None

    # Standard deviation of log returns
    mean_ret = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / len(log_returns)
    daily_vol = math.sqrt(variance)

    # Annualize (crypto trades 365 days/year)
    annual_vol = daily_vol * math.sqrt(365)
    annual_drift = mean_ret * 365

    return annual_vol, annual_drift


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
    3. Fetch current price + 30-day history
    4. Compute log-normal model probability (no LLM cost)
    5. Return mathematical probability directly with raw data for frontier model
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
        """Resolve the CoinGecko coin ID from keywords or via LLM."""
        coin_id = resolution_keywords.get("coin_id")
        if coin_id:
            return coin_id

        # Try to extract coin name from keywords
        coin_name = resolution_keywords.get("metric_name", "")
        if not coin_name:
            # Fall back to extracting from question
            coin_name = market_question

        # Check cache for a previous mapping
        cache_key = f"coingecko_map:{coin_name.lower()}"
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

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        """Execute the full crypto signal pipeline.

        Returns the log-normal model probability directly without LLM adjustment.
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

        # Fetch price data + Deribit IV in parallel
        self._emit(market_question, "coingecko", f"fetching {coin_id} data")
        async with aiohttp.ClientSession() as session:
            price_data = await _fetch_coingecko_price(session, coin_id)
            chart_data = await _fetch_coingecko_chart(session, coin_id, days=90)
            deribit_iv = await _fetch_deribit_iv(session, coin_id)

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

        # Compute volatility and realized drift from chart data
        historical_vol = 0.0
        realized_drift: float | None = None
        trend_description = "No history available"
        data_points = 1  # at least the current price

        if chart_data:
            historical_vol, realized_drift = _compute_volatility(chart_data)
            trend_description = _describe_trend(chart_data)
            data_points = len(chart_data)

        # Choose best volatility estimate: prefer Deribit IV (forward-looking)
        vol_source = "historical"
        annual_vol = historical_vol
        if deribit_iv is not None and deribit_iv > 0:
            annual_vol = deribit_iv
            vol_source = "deribit_iv"
            logger.info(
                "Using Deribit IV %.0f%% for %s (historical %.0f%%)",
                deribit_iv * 100, coin_id, historical_vol * 100,
            )

        # Compute days remaining
        try:
            end_dt = datetime.fromisoformat(market_end_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_remaining = max(0, (end_dt - now).total_seconds() / 86400)
        except (ValueError, TypeError):
            days_remaining = 30.0  # default fallback

        # Log-normal model probability — this IS the signal, no LLM needed
        self._emit(
            market_question, "model",
            f"vol={annual_vol:.0%}({vol_source}), drift={realized_drift or 0:.0%}, days={days_remaining:.0f}",
        )
        model_prob = log_normal_probability(
            current_price=current_price,
            target_price=target_price,
            annual_vol=annual_vol,
            days_remaining=days_remaining,
            direction=target_direction,
            drift=realized_drift,
        )

        # Distance from target
        distance_pct = ((target_price - current_price) / current_price) * 100

        # Confidence based on data quality
        confidence = 0.6  # base confidence for mathematical model
        if annual_vol > 0 and data_points > 10:
            confidence = 0.7
        if annual_vol > 0 and data_points > 20:
            confidence = 0.75
        # Boost confidence when using forward-looking IV
        if vol_source == "deribit_iv":
            confidence = min(confidence + 0.05, 0.85)

        vol_label = f"vol={annual_vol:.0%}"
        if vol_source == "deribit_iv":
            vol_label = f"IV={annual_vol:.0%} (hist={historical_vol:.0%})"

        drift_label = ""
        if realized_drift is not None:
            drift_label = f", drift={realized_drift:+.0%}"

        reasoning = (
            f"Log-normal model: P(YES)={model_prob:.2f}. "
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
                "historical_vol": historical_vol,
                "vol_source": vol_source,
                "realized_drift": realized_drift,
                "deribit_iv": deribit_iv,
                "days_remaining": days_remaining,
                "raw_log_normal_prob": model_prob,
                "change_24h": change_24h / 100 if change_24h else 0.0,
                "trend": trend_description,
                "distance_pct": distance_pct,
            },
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
