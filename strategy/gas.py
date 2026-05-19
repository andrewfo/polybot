"""Gas-cost estimation for Polygon trades.

Estimates the round-trip gas cost (entry + exit) in USD using current
Polygon gas price and MATIC/USD price. Runs after Kelly + depth sizing
to filter out trades whose expected value would be eaten by gas.

Polymarket order placement and cancellation on the CTF Exchange each
consume on the order of 200k-300k gas. A complete cycle (entry + exit)
is budgeted at GAS_UNITS_PER_TRADE_CYCLE (default 500_000 units).
"""

import logging
import time
from dataclasses import dataclass

import aiohttp

from config.settings import (
    GAS_PRICE_FALLBACK_GWEI,
    GAS_UNITS_PER_TRADE_CYCLE,
    MATIC_USD_FALLBACK,
    MIN_EV_GAS_RATIO,
)

logger = logging.getLogger(__name__)

POLYGON_RPC_URL = "https://polygon-rpc.com"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
FETCH_TIMEOUT = 10  # seconds
CACHE_TTL = 300  # 5 minutes

# Module-level cache: (gas_price_gwei, matic_usd, timestamp)
_cache: dict[str, tuple[float, float]] = {}


@dataclass
class GasAnalysis:
    """Result of gas-cost analysis for a proposed trade."""

    gas_price_gwei: float          # Current Polygon gas price
    matic_usd: float               # MATIC price in USD
    gas_units: int                 # Estimated gas units for round trip
    gas_cost_usd: float            # gas_units * gas_price * matic_usd
    expected_value_usd: float      # The trade's EV in USD (edge × bet_size)
    ev_to_gas_ratio: float         # expected_value / gas_cost
    passes_gate: bool              # True if EV clears MIN_EV_GAS_RATIO × gas_cost
    skip_reason: str               # Non-empty if passes_gate is False


def _get_cached(key: str) -> float | None:
    """Return cached value if still valid, else None."""
    if key in _cache:
        value, ts = _cache[key]
        if time.monotonic() - ts < CACHE_TTL:
            return value
    return None


def _set_cached(key: str, value: float) -> None:
    """Store a value in the module cache."""
    _cache[key] = (value, time.monotonic())


async def fetch_polygon_gas_price_gwei() -> float:
    """Fetch current Polygon gas price in gwei via public RPC.

    Falls back to GAS_PRICE_FALLBACK_GWEI on any error. Cached for 5 min.
    """
    cached = _get_cached("gas_gwei")
    if cached is not None:
        return cached

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_gasPrice",
        "params": [],
        "id": 1,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(POLYGON_RPC_URL, json=payload) as resp:
                if resp.status != 200:
                    logger.debug("Polygon RPC gasPrice status %d", resp.status)
                    return GAS_PRICE_FALLBACK_GWEI
                data = await resp.json()
                hex_wei = data.get("result")
                if not hex_wei:
                    return GAS_PRICE_FALLBACK_GWEI
                wei = int(hex_wei, 16)
                gwei = wei / 1e9
                _set_cached("gas_gwei", gwei)
                return gwei
    except Exception as e:
        logger.debug("Polygon gas price fetch failed: %s", e)
        return GAS_PRICE_FALLBACK_GWEI


async def fetch_matic_usd_price() -> float:
    """Fetch MATIC/USD price from CoinGecko.

    Falls back to MATIC_USD_FALLBACK on any error. Cached for 5 min.
    """
    cached = _get_cached("matic_usd")
    if cached is not None:
        return cached

    params = {"ids": "matic-network", "vs_currencies": "usd"}
    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(COINGECKO_PRICE_URL, params=params) as resp:
                if resp.status != 200:
                    return MATIC_USD_FALLBACK
                data = await resp.json()
                price = data.get("matic-network", {}).get("usd")
                if not price or price <= 0:
                    return MATIC_USD_FALLBACK
                _set_cached("matic_usd", float(price))
                return float(price)
    except Exception as e:
        logger.debug("MATIC/USD price fetch failed: %s", e)
        return MATIC_USD_FALLBACK


async def estimate_round_trip_gas_cost_usd() -> tuple[float, float, float]:
    """Estimate one entry+exit cycle's gas cost in USD.

    Returns (gas_cost_usd, gas_price_gwei, matic_usd).
    """
    gas_gwei = await fetch_polygon_gas_price_gwei()
    matic_usd = await fetch_matic_usd_price()
    # gas_units * gwei * 1e-9 = MATIC consumed
    matic_consumed = GAS_UNITS_PER_TRADE_CYCLE * gas_gwei * 1e-9
    gas_cost_usd = matic_consumed * matic_usd
    return gas_cost_usd, gas_gwei, matic_usd


async def analyze_gas_cost(expected_value_usd: float) -> GasAnalysis:
    """Compute the EV-to-gas ratio and decide if the trade clears the gate.

    A trade passes only if its expected_value_usd >= MIN_EV_GAS_RATIO * gas_cost.
    Default MIN_EV_GAS_RATIO is 3.0 — EV must triple the round-trip gas cost.
    """
    gas_cost_usd, gas_gwei, matic_usd = await estimate_round_trip_gas_cost_usd()

    if gas_cost_usd <= 0:
        # Pathological: treat as pass to avoid blocking trades on a bad fetch
        return GasAnalysis(
            gas_price_gwei=gas_gwei,
            matic_usd=matic_usd,
            gas_units=GAS_UNITS_PER_TRADE_CYCLE,
            gas_cost_usd=0.0,
            expected_value_usd=expected_value_usd,
            ev_to_gas_ratio=float("inf"),
            passes_gate=True,
            skip_reason="",
        )

    ratio = expected_value_usd / gas_cost_usd
    passes = ratio >= MIN_EV_GAS_RATIO
    skip_reason = ""
    if not passes:
        skip_reason = (
            f"EV ${expected_value_usd:.3f} < {MIN_EV_GAS_RATIO:.1f}× gas cost "
            f"${gas_cost_usd:.3f} (gas={gas_gwei:.1f} gwei, MATIC=${matic_usd:.3f})"
        )

    return GasAnalysis(
        gas_price_gwei=gas_gwei,
        matic_usd=matic_usd,
        gas_units=GAS_UNITS_PER_TRADE_CYCLE,
        gas_cost_usd=gas_cost_usd,
        expected_value_usd=expected_value_usd,
        ev_to_gas_ratio=ratio,
        passes_gate=passes,
        skip_reason=skip_reason,
    )
