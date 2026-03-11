"""Technical analysis signal provider for crypto markets.

Computes RSI, MACD, Bollinger Bands, and moving average crossovers from
CoinGecko price history. Combines indicators into a directional probability
estimate via cheap LLM interpretation.
"""

import logging
import math
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

# Cache: market_question -> (SignalResult, timestamp)
_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes

COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
USER_AGENT = "polymarket-bot/1.0 (signal research)"


async def _fetch_chart(
    session: aiohttp.ClientSession, coin_id: str, days: int = 90
) -> list[list[float]] | None:
    """Fetch price history from CoinGecko."""
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


def compute_rsi(prices: list[float], period: int = 14) -> float | None:
    """Compute RSI (Relative Strength Index) from price series.

    Returns RSI value between 0 and 100, or None if insufficient data.
    """
    if len(prices) < period + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    if len(gains) < period:
        return None

    # Initial averages (SMA)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages (Wilder's EMA)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, float] | None:
    """Compute MACD line, signal line, and histogram.

    Returns dict with macd_line, signal_line, histogram, or None if insufficient data.
    """
    if len(prices) < slow + signal_period:
        return None

    def ema(data: list[float], period: int) -> list[float]:
        multiplier = 2.0 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * multiplier + result[-1] * (1 - multiplier))
        return result

    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)

    # MACD line = fast EMA - slow EMA
    macd_line_values = [f - s for f, s in zip(fast_ema, slow_ema)]

    # Signal line = EMA of MACD line
    signal_line_values = ema(macd_line_values, signal_period)

    macd_val = macd_line_values[-1]
    signal_val = signal_line_values[-1]
    histogram = macd_val - signal_val

    # MACD crossover direction (recent)
    prev_macd = macd_line_values[-2] if len(macd_line_values) > 1 else macd_val
    prev_signal = signal_line_values[-2] if len(signal_line_values) > 1 else signal_val
    crossover = "bullish" if prev_macd <= prev_signal and macd_val > signal_val else (
        "bearish" if prev_macd >= prev_signal and macd_val < signal_val else "none"
    )

    return {
        "macd_line": macd_val,
        "signal_line": signal_val,
        "histogram": histogram,
        "crossover": crossover,
    }


def compute_bollinger_bands(
    prices: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, float] | None:
    """Compute Bollinger Bands.

    Returns dict with upper, middle, lower, pct_b (price position within bands).
    """
    if len(prices) < period:
        return None

    recent = prices[-period:]
    middle = sum(recent) / len(recent)
    variance = sum((p - middle) ** 2 for p in recent) / len(recent)
    std = math.sqrt(variance)

    upper = middle + num_std * std
    lower = middle - num_std * std

    current_price = prices[-1]
    band_width = upper - lower
    pct_b = (current_price - lower) / band_width if band_width > 0 else 0.5

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "band_width": band_width,
        "pct_b": pct_b,
        "current_price": current_price,
    }


def compute_moving_averages(prices: list[float]) -> dict[str, Any] | None:
    """Compute SMA crossover signals (20/50 day)."""
    if len(prices) < 50:
        return None

    sma_20 = sum(prices[-20:]) / 20
    sma_50 = sum(prices[-50:]) / 50

    # Previous day SMAs for crossover detection
    if len(prices) >= 51:
        prev_sma_20 = sum(prices[-21:-1]) / 20
        prev_sma_50 = sum(prices[-51:-1]) / 50
        crossover = "golden" if prev_sma_20 <= prev_sma_50 and sma_20 > sma_50 else (
            "death" if prev_sma_20 >= prev_sma_50 and sma_20 < sma_50 else "none"
        )
    else:
        crossover = "none"

    trend = "bullish" if sma_20 > sma_50 else "bearish"

    return {
        "sma_20": sma_20,
        "sma_50": sma_50,
        "trend": trend,
        "crossover": crossover,
        "spread_pct": ((sma_20 - sma_50) / sma_50) * 100 if sma_50 != 0 else 0.0,
    }


def _summarize_indicators(
    rsi: float | None,
    macd: dict[str, Any] | None,
    bollinger: dict[str, float] | None,
    ma: dict[str, Any] | None,
) -> str:
    """Build a text summary of all technical indicators."""
    lines: list[str] = []

    if rsi is not None:
        if rsi > 70:
            condition = "OVERBOUGHT"
        elif rsi < 30:
            condition = "OVERSOLD"
        else:
            condition = "NEUTRAL"
        lines.append(f"RSI(14): {rsi:.1f} ({condition})")

    if macd is not None:
        lines.append(
            f"MACD: line={macd['macd_line']:.2f}, signal={macd['signal_line']:.2f}, "
            f"histogram={macd['histogram']:.2f}, crossover={macd['crossover']}"
        )

    if bollinger is not None:
        lines.append(
            f"Bollinger Bands(20,2): upper=${bollinger['upper']:,.2f}, "
            f"middle=${bollinger['middle']:,.2f}, lower=${bollinger['lower']:,.2f}, "
            f"%B={bollinger['pct_b']:.2f}"
        )

    if ma is not None:
        lines.append(
            f"Moving Averages: SMA20=${ma['sma_20']:,.2f}, SMA50=${ma['sma_50']:,.2f}, "
            f"trend={ma['trend']}, crossover={ma['crossover']}, spread={ma['spread_pct']:+.1f}%"
        )

    return "\n".join(lines) if lines else "No indicators computed"


class TechnicalAnalysisProvider(SignalProvider):
    """Technical analysis signal provider for crypto markets.

    Computes RSI, MACD, Bollinger Bands, and MA crossovers from CoinGecko
    90-day price data. Combines via cheap LLM into a probability estimate.
    """

    name: str = "technical_analysis"

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
        if market_category.lower() != "crypto":
            return SignalResult(
                source="technical_analysis",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' not supported for technical analysis",
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
            result = await self._run_pipeline(market_question, market_end_date, kwargs)
        except Exception as e:
            logger.error("Technical analysis failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="technical_analysis",
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

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
        kwargs: dict[str, Any],
    ) -> SignalResult:
        resolution_keywords = kwargs.get("resolution_keywords", {})
        coin_id = resolution_keywords.get("coin_id")

        if not coin_id:
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
                source="technical_analysis", probability=None, confidence=0.0,
                reasoning="Could not determine coin ID",
                model_used="none", data_points=0,
            )

        self._emit(market_question, "ta_fetch", f"fetching {coin_id} 90d chart")
        async with aiohttp.ClientSession() as session:
            chart_data = await _fetch_chart(session, coin_id, days=90)

        if not chart_data or len(chart_data) < 30:
            return SignalResult(
                source="technical_analysis", probability=None, confidence=0.0,
                reasoning=f"Insufficient chart data for {coin_id} ({len(chart_data) if chart_data else 0} points)",
                model_used="none", data_points=0,
            )

        # Extract daily closing prices
        prices = [p[1] for p in chart_data if p[1] > 0]
        if len(prices) < 30:
            return SignalResult(
                source="technical_analysis", probability=None, confidence=0.0,
                reasoning="Insufficient valid price points",
                model_used="none", data_points=0,
            )

        # Compute all indicators
        self._emit(market_question, "ta_compute", "computing RSI, MACD, BB, MA")
        rsi = compute_rsi(prices)
        macd = compute_macd(prices)
        bollinger = compute_bollinger_bands(prices)
        ma = compute_moving_averages(prices)

        indicator_count = sum(1 for x in [rsi, macd, bollinger, ma] if x is not None)
        if indicator_count == 0:
            return SignalResult(
                source="technical_analysis", probability=None, confidence=0.0,
                reasoning="No technical indicators could be computed",
                model_used="none", data_points=len(prices),
            )

        summary = _summarize_indicators(rsi, macd, bollinger, ma)

        # Price change stats
        current_price = prices[-1]
        price_7d_ago = prices[-7] if len(prices) >= 7 else prices[0]
        price_30d_ago = prices[-30] if len(prices) >= 30 else prices[0]
        change_7d = ((current_price - price_7d_ago) / price_7d_ago * 100) if price_7d_ago > 0 else 0
        change_30d = ((current_price - price_30d_ago) / price_30d_ago * 100) if price_30d_ago > 0 else 0

        # Cheap LLM interprets all indicators
        self._emit(market_question, "ta_interpret", f"{indicator_count} indicators")
        from signals.temporal import format_date_context_line
        date_ctx = format_date_context_line(market_end_date)
        interpret_prompt = (
            f'Market question: "{market_question}"\n'
            f"{date_ctx}\n\n"
            f"Technical analysis for {coin_id}:\n"
            f"Current price: ${current_price:,.2f}\n"
            f"7-day change: {change_7d:+.1f}%\n"
            f"30-day change: {change_30d:+.1f}%\n\n"
            f"Technical indicators:\n{summary}\n\n"
            f"Based on these technical indicators, estimate the probability of YES.\n"
            f"Consider:\n"
            f"- Overbought/oversold conditions (RSI)\n"
            f"- Momentum direction and strength (MACD)\n"
            f"- Volatility squeeze or expansion (Bollinger Bands)\n"
            f"- Trend direction (moving average crossovers)\n\n"
            f"Note: Technical analysis has limited predictive power for longer timeframes.\n"
            f"Adjust confidence downward for markets resolving > 30 days out.\n\n"
            f'Respond as JSON: {{"probability": 0.XX, "confidence": 0.XX, "reasoning": "..."}}'
        )

        raw_data = {
            "coin_id": coin_id,
            "current_price": current_price,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "rsi": rsi,
            "macd": macd,
            "bollinger_pct_b": bollinger["pct_b"] if bollinger else None,
            "ma_trend": ma["trend"] if ma else None,
            "ma_crossover": ma["crossover"] if ma else None,
            "indicator_count": indicator_count,
        }

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
                    source="technical_analysis",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning,
                    model_used="cheap",
                    data_points=len(prices),
                    raw_data=raw_data,
                )
        except Exception as e:
            logger.error("Failed to interpret TA: %s", e)

        return SignalResult(
            source="technical_analysis",
            probability=None,
            confidence=0.0,
            reasoning="Failed to interpret technical indicators",
            model_used="none",
            data_points=len(prices),
            raw_data=raw_data,
        )


def clear_signal_cache() -> None:
    """Clear the in-memory signal cache."""
    _signal_cache.clear()
