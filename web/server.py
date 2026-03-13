"""FastAPI backend for the Polymarket bot web dashboard.

Serves REST endpoints that proxy to existing backend functions,
plus WebSocket push updates for real-time state changes.
"""

import asyncio
import collections
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import (
    AGGREGATION_INTERVAL_MINUTES,
    CHEAP_MODEL,
    DEPTH_ANALYSIS_ENABLED,
    DISCOVERY_INTERVAL_MINUTES,
    KELLY_FRACTION,
    MAX_ACCEPTABLE_SLIPPAGE,
    MAX_DAILY_LOSS_PCT,
    MAX_DIVERGENCE_ANY_CONFIDENCE,
    MAX_DIVERGENCE_LOW_CONFIDENCE,
    MAX_DRAWDOWN_PCT,
    MAX_POSITION_PCT,
    MAX_SIMULTANEOUS_POSITIONS,
    MAX_SPREAD,
    MIN_BANKROLL_RESERVE,
    MIN_CONFIDENCE_BLEND,
    MIN_DEPTH_USD,
    MIN_EDGE_THRESHOLD,
    MIN_MARKET_LIQUIDITY,
    PAPER_TRADING,
    POLYMARKET_FEE_RATE,
    POSITION_CHECK_INTERVAL_MINUTES,
    TEST_BANKROLL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BotEngine — manages 3 worker tasks on the FastAPI event loop
# ---------------------------------------------------------------------------

class BotEngine:
    """Manages discovery, aggregation, and position-monitor worker tasks."""

    def __init__(self, ws_manager: "WSManager") -> None:
        self.running: bool = False
        self.phase: str = "idle"
        self.cycle_count: int = 0
        self.filtered_market_cache: list[dict[str, Any]] = []
        self.aggregated_ids: set[str] = set()
        self.analysis_entries: dict[str, dict[str, Any]] = {}

        self._ws = ws_manager
        self._tasks: list[asyncio.Task[None]] = []
        self._llm: Any = None
        self._aggregator: Any = None
        self._executor: Any = None

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.phase = "idle"
        self.cycle_count = 0
        self.aggregated_ids.clear()

        # Init shared resources (lazy imports to avoid circular deps)
        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        from strategy.executor import PaperExecutor, TradeExecutor

        self._llm = LLMClient()
        self._aggregator = SignalAggregator(self._llm)
        if PAPER_TRADING:
            self._executor = PaperExecutor()
        else:
            from core.client import ClobClientWrapper
            self._executor = TradeExecutor(ClobClientWrapper())

        self._tasks = [
            asyncio.create_task(self._discovery_loop()),
            asyncio.create_task(self._aggregation_loop()),
            asyncio.create_task(self._position_loop()),
        ]
        logger.info("BotEngine started — 3 workers launched (paper=%s)", PAPER_TRADING)
        await self._broadcast({"type": "bot_status", "running": True, "phase": "idle"})

    async def stop(self) -> None:
        self.running = False
        self.phase = "idle"
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Close LLM session to avoid unclosed connector warnings
        if self._llm is not None:
            await self._llm.close()
            self._llm = None
        self._aggregator = None
        self._executor = None
        logger.info("BotEngine stopped — all workers cancelled")
        await self._broadcast({"type": "bot_status", "running": False, "phase": "idle"})

    # ------------------------------------------------------------------
    # Worker 1: Discovery Loop
    # ------------------------------------------------------------------

    async def _discovery_loop(self) -> None:
        """Run immediately on start, then every DISCOVERY_INTERVAL_MINUTES."""
        while self.running:
            try:
                self.phase = "filtering"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "filtering"})

                # Calibration check
                from signals.calibration import check_and_record_resolutions
                resolved = await check_and_record_resolutions()
                if resolved > 0:
                    logger.info("Calibration: resolved %d markets", resolved)

                # Full filter pipeline
                from strategy.market_filter import (
                    batch_categorize_markets,
                    discover_markets,
                    extract_resolution_params,
                    filter_computable_markets,
                    filter_markets,
                    pre_screen_crypto_edge,
                    rank_candidates,
                )
                raw = await discover_markets()
                filtered = await filter_markets(raw)
                await batch_categorize_markets(filtered, self._llm)

                # Keep only crypto
                crypto = [m for m in filtered if m.get("_category") == "crypto"]

                # Extract resolution params for each
                for m in crypto:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    params = await extract_resolution_params(
                        m["question"], "crypto", self._llm, condition_id=cid
                    )
                    if params:
                        m["_resolution_params"] = params

                # Gate: only keep markets our math can actually model
                crypto = filter_computable_markets(crypto)

                # Pre-screen with CoinGecko math
                crypto = await pre_screen_crypto_edge(crypto)

                # Rank
                ranked = rank_candidates(crypto)
                self.filtered_market_cache = ranked

                logger.info("Discovery complete: %d raw → %d ranked", len(raw), len(ranked))
                self.phase = "waiting"
                await self._broadcast({
                    "type": "discovery_complete",
                    "discovered": len(raw),
                    "filtered": len(ranked),
                })
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Discovery loop error")
                self.phase = "waiting"

            await self._cancellable_sleep(DISCOVERY_INTERVAL_MINUTES * 60)

    # ------------------------------------------------------------------
    # Worker 2: Aggregation Loop
    # ------------------------------------------------------------------

    async def _aggregation_loop(self) -> None:
        """Wait for first discovery, then run every AGGREGATION_INTERVAL_MINUTES."""
        # Wait for first discovery cycle to populate cache
        while self.running and not self.filtered_market_cache:
            await self._cancellable_sleep(10)

        while self.running:
            try:
                self.phase = "aggregating"
                self.cycle_count += 1
                await self._broadcast({
                    "type": "bot_status", "running": True,
                    "phase": "aggregating", "cycle_count": self.cycle_count,
                })

                # Select candidates (dedup by conditionId)
                batch_size = 40
                candidates = [
                    m for m in self.filtered_market_cache
                    if m.get("conditionId", m.get("condition_id", "")) not in self.aggregated_ids
                ][:batch_size]

                if not candidates:
                    logger.info("Aggregation cycle %d: no new candidates", self.cycle_count)
                    self.phase = "waiting"
                    await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
                    await self._cancellable_sleep(AGGREGATION_INTERVAL_MINUTES * 60)
                    continue

                logger.info("Aggregation cycle %d: processing %d candidates", self.cycle_count, len(candidates))

                # Init analysis entries
                for m in candidates:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    self.analysis_entries[cid] = {
                        "question": m.get("question", ""),
                        "status": "waiting",
                        "market_data": m,
                    }

                for i, m in enumerate(candidates):
                    if not self.running:
                        break
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    self.analysis_entries[cid]["status"] = "processing"

                    await self._broadcast({
                        "type": "batch_update",
                        "current_index": i,
                        "total": len(candidates),
                        "condition_id": cid,
                        "status": "processing",
                    })

                    try:
                        await self._process_candidate(m, cid)
                    except Exception:
                        logger.exception("Aggregation failed for %s", cid)
                        self.analysis_entries[cid]["status"] = "error"

                self.phase = "waiting"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Aggregation loop error")
                self.phase = "waiting"

            await self._cancellable_sleep(AGGREGATION_INTERVAL_MINUTES * 60)

    @staticmethod
    def _parse_market_price(m: dict[str, Any]) -> float:
        """Extract YES outcome price from Gamma market data.

        Tries outcomePrices first, then bestBid/bestAsk midpoint, then 0.
        Never silently defaults to 0.5.
        """
        prices = m.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, TypeError):
                prices = []
        if prices and len(prices) >= 1:
            try:
                p = float(prices[0])
                if 0 < p < 1:
                    return p
            except (ValueError, TypeError):
                pass
        # Fallback: midpoint of bestBid/bestAsk
        bid = m.get("bestBid")
        ask = m.get("bestAsk")
        if bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2
                if 0 < mid < 1:
                    return mid
            except (ValueError, TypeError):
                pass
        # Fallback: bestAsk alone (YES price ≈ bestAsk)
        if ask is not None:
            try:
                a = float(ask)
                if 0 < a < 1:
                    return a
            except (ValueError, TypeError):
                pass
        return 0.0  # Caller should skip if 0

    async def _process_candidate(self, m: dict[str, Any], cid: str) -> None:
        """Run aggregation + Kelly + depth + execution for a single market."""
        from strategy.depth import analyze_depth
        from strategy.kelly import calculate_kelly

        # Parse market price — never default to 0.5
        market_price = self._parse_market_price(m)
        if market_price <= 0:
            self.analysis_entries[cid].update({
                "status": "skipped",
                "skip_reason": "Could not determine market price from Gamma data",
            })
            return

        # Run aggregation
        result = await self._aggregator.aggregate(
            market_question=m["question"],
            market_category=m.get("_category", "crypto"),
            market_end_date=m.get("endDate", ""),
            market_price=market_price,
            condition_id=cid,
            resolution_params=m.get("_resolution_params"),
        )

        self.aggregated_ids.add(cid)

        if result is None or result.skipped:
            self.analysis_entries[cid].update({
                "status": "skipped",
                "skip_reason": result.skip_reason if result else "no usable signals",
            })
            return

        # Run Kelly sizing
        try:
            from core.wallet import Wallet
            w = await asyncio.to_thread(Wallet)
            bankroll = await asyncio.to_thread(w.get_usdc_balance)
        except Exception:
            bankroll = TEST_BANKROLL

        tokens = m.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        token_id = tokens[0] if tokens else ""

        decision = calculate_kelly(
            market_id=cid,
            token_id=token_id,
            market_question=m["question"],
            estimated_prob=result.final_probability,
            market_price=market_price,
            confidence=result.confidence,
            available_bankroll=bankroll,
        )

        # Depth analysis (always run for UI visibility, adjust trade if needed)
        depth_data: dict[str, Any] = {}
        if DEPTH_ANALYSIS_ENABLED and token_id:
            try:
                depth = await analyze_depth(
                    token_id=decision.token_id,
                    side=decision.side,
                    bet_size_usd=max(decision.bet_size_usd, 1.0),
                )
                depth_data = {
                    "total_depth_usd": depth.total_depth_usd,
                    "slippage": depth.slippage,
                    "best_price": depth.best_price,
                    "avg_fill_price": depth.avg_fill_price,
                    "max_fillable_usd": depth.max_fillable_usd,
                    "levels": depth.levels,
                    "skip_reason": depth.skip_reason,
                }
                if decision.should_trade:
                    if depth.skip_reason:
                        decision.should_trade = False
                        decision.skip_reason = depth.skip_reason
                    else:
                        decision.bet_size_usd = depth.adjusted_bet_usd
                        decision.depth_total_usd = depth.total_depth_usd
                        decision.depth_slippage = depth.slippage
                        decision.depth_adjusted = depth.adjusted_bet_usd < decision.bet_size_usd
                        depth_data["adjusted_bet_usd"] = depth.adjusted_bet_usd
                        depth_data["was_adjusted"] = depth.adjusted_bet_usd < decision.bet_size_usd
            except Exception as e:
                logger.debug("Depth analysis failed for %s: %s", cid, e)

        # Execute trade if decision says go
        exec_data: dict[str, Any] = {}
        if decision.should_trade:
            trade_id = await self._executor.execute_trade(decision, m, bankroll)
            exec_data = {
                "status": "filled" if trade_id else "blocked",
                "trade_id": trade_id,
                "price": decision.market_price,
                "size": decision.bet_size_usd,
                "paper": PAPER_TRADING,
            }

        # Build market metadata for frontend
        end_date = m.get("endDate", "")
        days_remaining = None
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_remaining = max(0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
            except Exception:
                pass

        market_meta = {
            "liquidity": m.get("liquidityNum"),
            "volume_24h": m.get("volume24hr"),
            "spread": m.get("spread"),
            "best_bid": m.get("bestBid"),
            "best_ask": m.get("bestAsk"),
            "end_date": end_date,
            "days_remaining": round(days_remaining, 1) if days_remaining is not None else None,
            "resolution_type": (m.get("_resolution_params") or {}).get("resolution_type", "unknown"),
            "resolution_params": m.get("_resolution_params"),
            "model_edge": m.get("_model_edge"),
            "time_score": m.get("_time_score"),
            "total_score": m.get("_total_score"),
        }

        # Compute effective weights for each signal
        from signals.aggregator import SIGNAL_WEIGHT_MULTIPLIERS, _compute_effective_weight
        import math as _math

        signal_probs = [
            s.probability for s in result.individual_signals
            if s.probability is not None and s.confidence > 0
        ]
        signals_stdev = 0.0
        if len(signal_probs) >= 2:
            mean_p = sum(signal_probs) / len(signal_probs)
            signals_stdev = _math.sqrt(sum((p - mean_p) ** 2 for p in signal_probs) / len(signal_probs))

        # Store full analysis entry — include all raw signal data
        self.analysis_entries[cid].update({
            "status": "done",
            "decision": "TRADE" if decision.should_trade else "SKIP",
            "edge": decision.edge,
            "market_meta": market_meta,
            "aggregation": {
                "final_probability": result.final_probability,
                "preliminary_probability": result.preliminary_probability,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "signals_agreement": result.signals_agreement,
                "market_efficiency": result.market_efficiency,
                "market_price": market_price,
                "total_data_points": result.total_data_points,
                "signals_stdev": round(signals_stdev, 4),
                "signal_weight_multipliers": {k: round(v, 2) for k, v in SIGNAL_WEIGHT_MULTIPLIERS.items()},
                "signals": [
                    {
                        "source": s.source,
                        "probability": s.probability,
                        "confidence": s.confidence,
                        "reasoning": s.reasoning,
                        "model_used": s.model_used,
                        "data_points": s.data_points,
                        "raw_data": s.raw_data,
                        "effective_weight": round(_compute_effective_weight(s), 3) if s.probability is not None and s.confidence > 0 else 0,
                        "base_multiplier": SIGNAL_WEIGHT_MULTIPLIERS.get(s.source, 1.0),
                        "usable": s.probability is not None and s.confidence > 0,
                    }
                    for s in (result.all_signals if result.all_signals else result.individual_signals)
                ],
            },
            "kelly": {
                "side": decision.side,
                "edge": decision.edge,
                "estimated_prob": decision.estimated_prob,
                "market_price": decision.market_price,
                "bankroll": bankroll,
                "effective_prob": decision.effective_prob,
                "confidence": decision.confidence,
                "raw_kelly": decision.full_kelly_fraction,
                "fractional_kelly": decision.adjusted_fraction,
                "bet_size": decision.bet_size_usd,
                "expected_value": decision.expected_value,
                "should_trade": decision.should_trade,
                "skip_reason": decision.skip_reason,
                "fee_rate": POLYMARKET_FEE_RATE,
                "kelly_fraction_multiplier": KELLY_FRACTION,
                "min_edge_threshold": MIN_EDGE_THRESHOLD,
                "max_position_pct": MAX_POSITION_PCT,
                "min_bankroll_reserve": MIN_BANKROLL_RESERVE,
                "confidence_blend_floor": MIN_CONFIDENCE_BLEND,
            },
            "thresholds": {
                "min_edge": MIN_EDGE_THRESHOLD,
                "min_confidence": 0.25,
                "max_spread": MAX_SPREAD,
                "min_liquidity": MIN_MARKET_LIQUIDITY,
                "max_slippage": MAX_ACCEPTABLE_SLIPPAGE,
                "min_depth_usd": MIN_DEPTH_USD,
                "max_drawdown": MAX_DRAWDOWN_PCT,
                "max_daily_loss": MAX_DAILY_LOSS_PCT,
                "max_positions": MAX_SIMULTANEOUS_POSITIONS,
                "max_divergence_low_conf": MAX_DIVERGENCE_LOW_CONFIDENCE,
                "max_divergence_any_conf": MAX_DIVERGENCE_ANY_CONFIDENCE,
                "kelly_fraction": KELLY_FRACTION,
                "fee_rate": POLYMARKET_FEE_RATE,
                "confidence_blend_floor": MIN_CONFIDENCE_BLEND,
            },
            "depth": depth_data,
            "execution": exec_data,
        })

        # Extract price_history from crypto signal raw_data for the chart
        for s in (result.all_signals if result.all_signals else result.individual_signals):
            if s.source == "resolution_crypto" and s.raw_data.get("price_history"):
                self.analysis_entries[cid]["price_history"] = s.raw_data["price_history"]
                break

    # ------------------------------------------------------------------
    # Worker 3: Position Monitor Loop
    # ------------------------------------------------------------------

    async def _position_loop(self) -> None:
        """Monitor orders and manage positions every POSITION_CHECK_INTERVAL_MINUTES."""
        while self.running:
            try:
                self.phase = "monitoring"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "monitoring"})
                await self._executor.monitor_orders()
                await self._executor.manage_positions()
                self.phase = "waiting"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Position monitor error")
                self.phase = "waiting"

            await self._cancellable_sleep(POSITION_CHECK_INTERVAL_MINUTES * 60)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cancellable_sleep(self, seconds: float) -> None:
        """Sleep in 5s chunks, checking self.running for responsive cancellation."""
        elapsed = 0.0
        while elapsed < seconds and self.running:
            await asyncio.sleep(min(5.0, seconds - elapsed))
            elapsed += 5.0

    async def _broadcast(self, data: dict[str, Any]) -> None:
        await self._ws.broadcast(data)


# ---------------------------------------------------------------------------
# Log buffer
# ---------------------------------------------------------------------------

class LogBuffer(logging.Handler):
    """In-memory log handler backed by a deque."""

    def __init__(self, maxlen: int = 500) -> None:
        super().__init__()
        self.records: collections.deque[dict[str, str]] = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append({
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": self.format(record),
        })

    def get_logs(self, level: str | None = None, limit: int = 100) -> list[dict[str, str]]:
        logs = list(self.records)
        if level and level != "ALL":
            logs = [r for r in logs if r["level"] == level]
        return logs[-limit:]


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------

class WSManager:
    """Manages WebSocket connections for push updates."""

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.discard(ws)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    ws_manager = WSManager()
    engine = BotEngine(ws_manager)
    log_buffer = LogBuffer()

    # Attach log buffer to root logger
    log_buffer.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(log_buffer)

    # Shared aiohttp session
    _session_holder: dict[str, aiohttp.ClientSession | None] = {"session": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _session_holder["session"] = aiohttp.ClientSession()
        logger.info("Web dashboard started on http://127.0.0.1:%s", os.environ.get("WEB_PORT", "8080"))
        yield
        # Stop bot workers on shutdown
        if engine.running:
            await engine.stop()
        if _session_holder["session"]:
            await _session_holder["session"].close()

    app = FastAPI(title="Polymarket Bot", lifespan=lifespan)

    # CORS for dev mode (Vite on :5173)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _session() -> aiohttp.ClientSession:
        s = _session_holder["session"]
        assert s is not None, "Session not initialized"
        return s

    # -----------------------------------------------------------------------
    # Dashboard endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        services: list[dict[str, Any]] = []

        # 1. Polymarket API (Gamma)
        try:
            t0 = time.monotonic()
            async with _session().get(
                "https://gamma-api.polymarket.com/markets?limit=1&active=true",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                latency = round((time.monotonic() - t0) * 1000)
                services.append({
                    "name": "Polymarket API",
                    "healthy": resp.status == 200,
                    "latency_ms": latency,
                    "error": None if resp.status == 200 else f"HTTP {resp.status}",
                })
        except Exception as e:
            services.append({"name": "Polymarket API", "healthy": False, "latency_ms": None, "error": str(e)[:80]})

        # 2. Polygon RPC
        try:
            from core.wallet import Wallet
            t0 = time.monotonic()
            wallet = await asyncio.to_thread(Wallet)
            await asyncio.to_thread(wallet.get_usdc_balance)
            latency = round((time.monotonic() - t0) * 1000)
            services.append({"name": "Polygon RPC", "healthy": True, "latency_ms": latency, "error": None})
        except Exception as e:
            services.append({"name": "Polygon RPC", "healthy": False, "latency_ms": None, "error": str(e)[:80]})

        # 3. OpenRouter
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            services.append({"name": "OpenRouter", "healthy": False, "latency_ms": None, "error": "OPENROUTER_API_KEY not set"})
        else:
            try:
                t0 = time.monotonic()
                async with _session().get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    latency = round((time.monotonic() - t0) * 1000)
                    services.append({
                        "name": "OpenRouter",
                        "healthy": resp.status == 200,
                        "latency_ms": latency,
                        "error": None if resp.status == 200 else f"HTTP {resp.status}",
                    })
            except Exception as e:
                services.append({"name": "OpenRouter", "healthy": False, "latency_ms": None, "error": str(e)[:80]})

        return {"services": services}

    @app.get("/api/wallet")
    async def wallet():
        try:
            from core.wallet import Wallet
            w = await asyncio.to_thread(Wallet)
            usdc = await asyncio.to_thread(w.get_usdc_balance)
            matic = await asyncio.to_thread(w.get_matic_balance)
            has_gas = await asyncio.to_thread(w.has_sufficient_gas)
            from core.db import get_open_positions
            positions = get_open_positions()
            return {
                "address": w.address,
                "usdc": usdc,
                "matic": matic,
                "has_gas": has_gas,
                "positions_count": len(positions),
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/positions")
    async def positions():
        try:
            from core.db import get_open_positions
            return get_open_positions()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/costs")
    async def costs():
        try:
            from core.db import get_db, get_daily_llm_cost, get_monthly_llm_cost
            daily = get_daily_llm_cost()
            monthly = get_monthly_llm_cost()
            db = get_db()

            model_breakdown: list[dict[str, Any]] = []
            try:
                rows = list(db.execute(
                    "SELECT model, COUNT(*) as calls, SUM(input_tokens) as inp, "
                    "SUM(output_tokens) as outp, SUM(cost_usd) as cost "
                    "FROM llm_costs GROUP BY model ORDER BY cost DESC"
                ).fetchall())
                for r in rows:
                    model_breakdown.append({"model": r[0], "calls": r[1], "input_tokens": r[2], "output_tokens": r[3], "cost": r[4]})
            except Exception:
                pass

            task_breakdown: list[dict[str, Any]] = []
            try:
                rows = list(db.execute(
                    "SELECT task_type, COUNT(*) as calls, SUM(cost_usd) as cost "
                    "FROM llm_costs GROUP BY task_type ORDER BY cost DESC"
                ).fetchall())
                for r in rows:
                    task_breakdown.append({"task_type": r[0], "calls": r[1], "cost": r[2]})
            except Exception:
                pass

            total_calls = 0
            try:
                total = db.execute("SELECT COUNT(*) FROM llm_costs").fetchone()
                if total:
                    total_calls = total[0]
            except Exception:
                pass

            return {
                "daily": daily,
                "monthly": monthly,
                "total_calls": total_calls,
                "model_breakdown": model_breakdown,
                "task_breakdown": task_breakdown,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/pnl")
    async def pnl():
        try:
            from core.db import get_db, get_daily_pnl, get_total_pnl
            db = get_db()
            daily = get_daily_pnl()
            total = get_total_pnl()

            # Bankroll snapshots for the chart
            snapshots: list[dict[str, Any]] = []
            try:
                rows = list(db.execute(
                    "SELECT timestamp, total_value, available_cash, "
                    "unrealized_pnl, realized_pnl_today, realized_pnl_total "
                    "FROM bankroll ORDER BY timestamp ASC LIMIT 500"
                ).fetchall())
                for r in rows:
                    snapshots.append({
                        "timestamp": r[0],
                        "total_value": r[1],
                        "available_cash": r[2],
                        "unrealized_pnl": r[3],
                        "realized_pnl_today": r[4],
                        "realized_pnl_total": r[5],
                    })
            except Exception:
                pass

            # Win rate from closed trades
            trade_count = 0
            win_count = 0
            try:
                rows = list(db.execute(
                    "SELECT pnl FROM trades WHERE pnl IS NOT NULL"
                ).fetchall())
                trade_count = len(rows)
                win_count = sum(1 for r in rows if r[0] > 0)
            except Exception:
                pass

            win_rate = win_count / trade_count if trade_count > 0 else 0.0

            return {
                "snapshots": snapshots,
                "daily_pnl": daily,
                "total_pnl": total,
                "trade_count": trade_count,
                "win_rate": win_rate,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/bot/status")
    async def bot_status():
        return {
            "running": engine.running,
            "phase": engine.phase,
            "cycle_count": engine.cycle_count,
            "paper_trading": PAPER_TRADING,
        }

    # -----------------------------------------------------------------------
    # Markets endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/markets")
    async def markets(
        sort: str = Query("volume24hr"),
        limit: int = Query(20, ge=1, le=200),
    ):
        url = (
            f"https://gamma-api.polymarket.com/markets"
            f"?closed=false&active=true&limit={limit}&order={sort}&ascending=false"
        )
        try:
            async with _session().get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                return JSONResponse(status_code=resp.status, content={"error": f"Gamma API returned {resp.status}"})
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": str(e)[:200]})

    # -----------------------------------------------------------------------
    # Analysis endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/analysis")
    async def analysis_list():
        entries: list[dict[str, Any]] = []
        for cid, entry in engine.analysis_entries.items():
            entries.append({
                "condition_id": cid,
                "question": entry.get("question", ""),
                "status": entry.get("status", "unknown"),
                "decision": entry.get("decision"),
                "edge": entry.get("edge"),
            })
        return entries

    @app.get("/api/analysis/{condition_id}")
    async def analysis_detail(condition_id: str):
        entry = engine.analysis_entries.get(condition_id)
        if entry is None:
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return entry

    @app.get("/api/trades")
    async def trades():
        try:
            from core.db import get_open_trades
            return get_open_trades()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    # -----------------------------------------------------------------------
    # Logs endpoint
    # -----------------------------------------------------------------------

    @app.get("/api/logs")
    async def logs(
        level: str = Query("ALL"),
        limit: int = Query(100, ge=1, le=500),
    ):
        return log_buffer.get_logs(level=level, limit=limit)

    # -----------------------------------------------------------------------
    # Bot control endpoints
    # -----------------------------------------------------------------------

    @app.post("/api/bot/start")
    async def bot_start():
        if engine.running:
            return {"status": "already_running"}
        await engine.start()
        return {"status": "started"}

    @app.post("/api/bot/stop")
    async def bot_stop():
        if not engine.running:
            return {"status": "already_stopped"}
        await engine.stop()
        return {"status": "stopped"}

    # -----------------------------------------------------------------------
    # Command endpoints
    # -----------------------------------------------------------------------

    @app.post("/api/commands/aggregate")
    async def cmd_aggregate(request: Request):
        body = await request.json()
        question = body.get("question", "")
        market_price = float(body.get("market_price", 0.5))
        if not question:
            return JSONResponse(status_code=400, content={"error": "question required"})

        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        async with LLMClient() as llm:
            agg = SignalAggregator(llm)
            result = await agg.aggregate(
                market_question=question,
                market_category="crypto",
                market_end_date="",
                market_price=market_price,
            )
        if result is None:
            return {"status": "skipped", "reason": "no usable signals"}

        # Store under a synthetic key
        key = f"manual_{hash(question) & 0xFFFFFFFF:08x}"
        engine.analysis_entries[key] = {
            "question": question,
            "status": "done",
            "decision": "N/A",
            "edge": result.final_probability - market_price,
            "aggregation": {
                "final_probability": result.final_probability,
                "preliminary_probability": result.preliminary_probability,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "signals_agreement": result.signals_agreement,
                "market_efficiency": result.market_efficiency,
                "market_price": market_price,
                "total_data_points": result.total_data_points,
                "signals": [
                    {
                        "source": s.source,
                        "probability": s.probability,
                        "confidence": s.confidence,
                        "reasoning": s.reasoning,
                        "model_used": s.model_used,
                        "data_points": s.data_points,
                        "raw_data": s.raw_data,
                    }
                    for s in result.individual_signals
                ],
            },
        }
        return {"status": "done", "condition_id": key, "probability": result.final_probability}

    @app.post("/api/commands/signal-test")
    async def cmd_signal_test(request: Request):
        body = await request.json()
        question = body.get("question", "")
        if not question:
            return JSONResponse(status_code=400, content={"error": "question required"})

        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        async with LLMClient() as llm:
            agg = SignalAggregator(llm)
            # Run individual signal providers without frontier model
            results: list[dict[str, Any]] = []
            for provider in agg._providers:
                try:
                    signal = await provider.get_signal(question, "crypto", "")
                    results.append({
                        "source": signal.source,
                        "probability": signal.probability,
                        "confidence": signal.confidence,
                        "reasoning": signal.reasoning,
                    })
                except Exception as e:
                    results.append({
                        "source": provider.__class__.__name__,
                        "probability": None,
                        "confidence": 0,
                        "reasoning": f"Error: {e!s}",
                    })
        return {"question": question, "signals": results}

    # -----------------------------------------------------------------------
    # WebSocket
    # -----------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # Keep alive, no-op
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    # -----------------------------------------------------------------------
    # Static files (production — serve built React app)
    # -----------------------------------------------------------------------

    dist_dir = Path(__file__).parent.parent / "frontend" / "dist"
    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
