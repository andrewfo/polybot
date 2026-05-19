"""FastAPI backend for the Polymarket bot web dashboard.

Serves REST endpoints that proxy to existing backend functions,
plus WebSocket push updates for real-time state changes.
"""

import asyncio
import collections
import json
import logging
import math
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
    GAS_ANALYSIS_ENABLED,
    KELLY_FRACTION,
    MAX_ACCEPTABLE_SLIPPAGE,
    MAX_DAILY_LOSS_PCT,
    MAX_DIVERGENCE_ANY_CONFIDENCE,
    MAX_DIVERGENCE_LOW_CONFIDENCE,
    MAX_DRAWDOWN_PCT,
    MAX_POSITION_PCT,
    MAX_SPREAD,
    MIN_BANKROLL_RESERVE,
    MIN_CONFIDENCE_BLEND,
    MIN_DEPTH_USD,
    MIN_EDGE_THRESHOLD,
    MIN_EV_GAS_RATIO,
    MIN_MARKET_LIQUIDITY,
    PAPER_TRADING,
    POLYMARKET_FEE_RATE,
    POSITION_CHECK_INTERVAL_MINUTES,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TEST_BANKROLL,
    get_effective_param,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level engine ref — lets Telegram bot check engine state
# ---------------------------------------------------------------------------
_engine_ref: "BotEngine | None" = None

# ---------------------------------------------------------------------------
# Cached Wallet singleton — avoid re-initializing on every API call
# ---------------------------------------------------------------------------
_wallet_instance = None


async def _get_wallet():
    global _wallet_instance
    if _wallet_instance is None:
        from core.wallet import Wallet
        _wallet_instance = await asyncio.to_thread(Wallet)
    return _wallet_instance


# ---------------------------------------------------------------------------
# BotEngine — manages 3 worker tasks on the FastAPI event loop
# ---------------------------------------------------------------------------

class BotEngine:
    """Manages discovery, aggregation, and position-monitor worker tasks."""

    def __init__(self, ws_manager: "WSManager") -> None:
        self.running: bool = False
        self.paused: bool = False
        self.phase: str = "idle"
        self.cycle_count: int = 0
        self.filtered_market_cache: list[dict[str, Any]] = []
        self.aggregated_ids: dict[str, dict[str, Any]] = {}  # cid → {"timestamp": float, "market_price": float}
        self.analysis_entries: dict[str, dict[str, Any]] = {}

        self._ws = ws_manager
        self._tasks: list[asyncio.Task[None]] = []
        self._llm: Any = None
        self._aggregator: Any = None
        self._executor: Any = None

        # Cycle timing tracking
        self._started_at: float | None = None
        self._discovery_last_run: float | None = None
        self._aggregation_last_run: float | None = None
        self._position_last_run: float | None = None

        # Session statistics
        self._session_markets_analyzed: int = 0
        self._session_trades_executed: int = 0
        self._session_signals_collected: int = 0
        self._session_markets_skipped: int = 0
        self._session_markets_discovered: int = 0
        self._last_discovery_found: int = 0
        self._last_discovery_ranked: int = 0
        self._last_batch_size: int = 0

        # Pipeline activity feed (most recent events)
        self._activity_feed: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)

        # Health check results (updated by position monitor loop)
        self._last_health_results: list[Any] = []

        # Consecutive failure counters per worker (auto-stop at 3)
        self._discovery_failures: int = 0
        self._aggregation_failures: int = 0
        self._position_failures: int = 0

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    async def pause(self) -> None:
        if not self.running or self.paused:
            return
        self.paused = True
        self.phase = "paused"
        logger.info("BotEngine paused — discovery/aggregation suspended, position monitor active")
        self._push_activity("system", "Bot paused", "No new trades; position monitoring continues")
        await self._broadcast({"type": "bot_status", "running": True, "paused": True, "phase": "paused"})

    async def resume(self) -> None:
        if not self.running or not self.paused:
            return
        self.paused = False
        self.phase = "waiting"
        logger.info("BotEngine resumed — full pipeline active")
        self._push_activity("system", "Bot resumed", "Discovery and aggregation restarted")
        await self._broadcast({"type": "bot_status", "running": True, "paused": False, "phase": "waiting"})

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.paused = False
        self.phase = "idle"
        self.cycle_count = 0
        self._started_at = time.time()
        self._session_markets_analyzed = 0
        self._session_trades_executed = 0
        self._session_signals_collected = 0
        self._session_markets_skipped = 0
        self._session_markets_discovered = 0
        self._activity_feed.clear()

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
        self.paused = False
        self.phase = "idle"
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        # Gather all tasks except the caller (which is still running)
        others = [t for t in self._tasks if t is not current]
        await asyncio.gather(*others, return_exceptions=True)
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
            if self.paused:
                await self._cancellable_sleep(10)
                continue
            try:
                self.phase = "filtering"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "filtering"})

                # Calibration check + skipped market resolution tracking
                from signals.calibration import check_and_record_resolutions
                resolved = await check_and_record_resolutions()
                if resolved > 0:
                    logger.info("Calibration: resolved %d markets", resolved)
                from monitoring.learning import update_skipped_resolutions
                skip_resolved = await update_skipped_resolutions()
                if skip_resolved > 0:
                    logger.info("Skip tracking: resolved %d skipped markets", skip_resolved)

                # Full filter pipeline
                from strategy.market_filter import (
                    batch_categorize_markets,
                    classify_market_types,
                    discover_markets,
                    extract_resolution_params,
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

                # Classify markets as price_target or event (both kept)
                crypto = classify_market_types(crypto)

                # Pre-screen with CoinGecko math
                crypto = await pre_screen_crypto_edge(crypto)

                # Rank
                ranked = rank_candidates(crypto)
                self.filtered_market_cache = ranked

                logger.info("Discovery complete: %d raw → %d ranked", len(raw), len(ranked))
                self._discovery_last_run = time.time()
                self._last_discovery_found = len(raw)
                self._last_discovery_ranked = len(ranked)
                self._session_markets_discovered += len(raw)
                self._discovery_failures = 0  # Reset on success
                self._push_activity(
                    "discovery",
                    f"Discovery complete: {len(ranked)} crypto markets ranked",
                    f"From {len(raw)} raw markets, {len(crypto)} crypto, {len(ranked)} passed filters",
                )
                self.phase = "waiting"
                await self._broadcast({
                    "type": "discovery_complete",
                    "discovered": len(raw),
                    "filtered": len(ranked),
                })

                # If 0 ranked markets, force-refresh cache and rediscover fresh markets
                if len(ranked) == 0 and self.running:
                    logger.warning("Discovery found 0 ranked markets — force-refreshing cache and retrying")
                    self._push_activity(
                        "discovery",
                        "0 ranked markets — rediscovering with fresh data",
                        "Clearing market cache to fetch new markets from Gamma",
                    )
                    await self._cancellable_sleep(30)  # Brief pause before retry
                    if self.running:
                        self.phase = "filtering"
                        await self._broadcast({"type": "bot_status", "running": True, "phase": "filtering"})
                        raw = await discover_markets(force_refresh=True)
                        filtered = await filter_markets(raw)
                        await batch_categorize_markets(filtered, self._llm)
                        crypto = [m for m in filtered if m.get("_category") == "crypto"]
                        for m in crypto:
                            cid = m.get("conditionId", m.get("condition_id", ""))
                            params = await extract_resolution_params(
                                m["question"], "crypto", self._llm, condition_id=cid
                            )
                            if params:
                                m["_resolution_params"] = params
                        crypto = classify_market_types(crypto)
                        crypto = await pre_screen_crypto_edge(crypto)
                        ranked = rank_candidates(crypto)
                        self.filtered_market_cache = ranked
                        self._last_discovery_found = len(raw)
                        self._last_discovery_ranked = len(ranked)
                        self._session_markets_discovered += len(raw)
                        logger.info("Rediscovery complete: %d raw → %d ranked", len(raw), len(ranked))
                        self._push_activity(
                            "discovery",
                            f"Rediscovery: {len(ranked)} crypto markets ranked",
                            f"From {len(raw)} fresh raw markets after cache bust",
                        )
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
                self._discovery_failures += 1
                self.phase = "waiting"
                self._push_activity("error", "Discovery loop error", f"Consecutive failures: {self._discovery_failures}")
                if self._discovery_failures >= 3:
                    logger.critical("Discovery worker: 3 consecutive failures — auto-stopping bot")
                    self._push_activity("error", "Auto-stop: discovery worker failed 3 times", "")
                    await self.stop()
                    return

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
            if self.paused:
                await self._cancellable_sleep(10)
                continue
            try:
                self.phase = "aggregating"
                self.cycle_count += 1
                await self._broadcast({
                    "type": "bot_status", "running": True,
                    "phase": "aggregating", "cycle_count": self.cycle_count,
                })

                # Select candidates (dedup by conditionId, allow re-analysis on price move)
                batch_size = 20
                now_ts = time.time()
                reanalysis_hours = AGGREGATION_INTERVAL_MINUTES / 60

                # Skip markets with open positions (already have a bet)
                from core.db import get_open_positions
                open_pos = get_open_positions()
                open_position_ids = {p["market_id"] for p in open_pos}
                if open_position_ids:
                    logger.info("Skipping %d markets with open positions", len(open_position_ids))

                candidates = []
                for m in self.filtered_market_cache:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    if not cid:
                        continue
                    if cid in open_position_ids:
                        continue
                    prev = self.aggregated_ids.get(cid)
                    if prev is None:
                        candidates.append(m)
                    else:
                        # Re-analyze if: price moved >5%, interval elapsed, or <7d to expiry
                        cur_price = self._parse_market_price(m)
                        price_moved = abs(cur_price - prev["market_price"]) > 0.05 if cur_price > 0 else False
                        hours_elapsed = (now_ts - prev["timestamp"]) / 3600
                        days_to_expiry = None
                        end_date = m.get("endDate", "")
                        if end_date:
                            try:
                                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                                days_to_expiry = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400
                            except Exception:
                                pass
                        if price_moved or hours_elapsed >= reanalysis_hours or (days_to_expiry is not None and days_to_expiry < 7):
                            candidates.append(m)
                    if len(candidates) >= batch_size:
                        break

                if not candidates:
                    logger.info("Aggregation cycle %d: no new candidates", self.cycle_count)
                    self.phase = "waiting"
                    await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
                    await self._cancellable_sleep(AGGREGATION_INTERVAL_MINUTES * 60)
                    continue

                logger.info("Aggregation cycle %d: processing %d candidates", self.cycle_count, len(candidates))
                self._last_batch_size = len(candidates)

                # Init analysis entries
                for m in candidates:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    self.analysis_entries[cid] = {
                        "question": m.get("question", ""),
                        "status": "waiting",
                        "market_data": m,
                    }

                # Parallel aggregation with semaphore (3 concurrent, respects frontier rate limit)
                sem = asyncio.Semaphore(3)

                async def _process_with_sem(m_item: dict[str, Any], idx: int) -> None:
                    if not self.running:
                        return
                    c = m_item.get("conditionId", m_item.get("condition_id", ""))
                    self.analysis_entries[c]["status"] = "processing"
                    await self._broadcast({
                        "type": "batch_update",
                        "current_index": idx,
                        "total": len(candidates),
                        "condition_id": c,
                        "status": "processing",
                    })
                    async with sem:
                        try:
                            await self._process_candidate(m_item, c)
                        except Exception:
                            logger.exception("Aggregation failed for %s", c)
                            self.analysis_entries[c]["status"] = "error"

                await asyncio.gather(
                    *(_process_with_sem(m, i) for i, m in enumerate(candidates)),
                    return_exceptions=True,
                )

                self._aggregation_last_run = time.time()

                # Count session stats from this batch
                for _cid, entry in self.analysis_entries.items():
                    if entry.get("status") == "done":
                        self._session_markets_analyzed += 1
                        if entry.get("kelly", {}).get("should_trade"):
                            self._session_trades_executed += 1
                            self._push_activity(
                                "trade",
                                f"Trade executed: {entry.get('question', _cid)[:60]}",
                                f"Edge: {entry.get('kelly', {}).get('edge', 0):.1%}, "
                                f"Size: ${entry.get('kelly', {}).get('bet_size', 0):.2f}",
                            )
                        else:
                            self._session_markets_skipped += 1
                    elif entry.get("status") == "skipped":
                        self._session_markets_skipped += 1

                self._aggregation_failures = 0  # Reset on success
                self._push_activity(
                    "aggregation",
                    f"Aggregation cycle {self.cycle_count} complete",
                    f"{len(candidates)} candidates processed",
                )

                # Run learning cycle after each aggregation batch
                try:
                    from monitoring.learning import run_learning_cycle
                    self.phase = "learning"
                    await self._broadcast({"type": "bot_status", "running": True, "phase": "learning"})
                    learning_report = await run_learning_cycle()
                    if learning_report.recommendations:
                        logger.info(
                            "Learning: %d recommendations generated",
                            len(learning_report.recommendations),
                        )
                except Exception:
                    logger.exception("Learning cycle error (non-fatal)")

                self.phase = "waiting"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Aggregation loop error")
                self._aggregation_failures += 1
                self.phase = "waiting"
                self._push_activity("error", "Aggregation loop error", f"Consecutive failures: {self._aggregation_failures}")
                if self._aggregation_failures >= 3:
                    logger.critical("Aggregation worker: 3 consecutive failures — auto-stopping bot")
                    self._push_activity("error", "Auto-stop: aggregation worker failed 3 times", "")
                    await self.stop()
                    return

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
            resolution_keywords=m.get("_resolution_params"),
            market_type=m.get("_market_type", "price_target"),
        )

        self.aggregated_ids[cid] = {"timestamp": time.time(), "market_price": market_price}

        # Build market metadata for frontend (always, even if skipped)
        end_date = m.get("endDate", "")
        days_remaining = None
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_remaining = max(0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
            except Exception:
                pass

        market_meta = {
            "liquidity": m.get("liquidity", m.get("liquidityNum")),
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
            "total_score": m.get("_score"),
        }

        # Read effective params so learning overrides are reflected in the UI
        eff_kelly_fraction = get_effective_param("KELLY_FRACTION", KELLY_FRACTION)
        eff_min_edge = get_effective_param("MIN_EDGE_THRESHOLD", MIN_EDGE_THRESHOLD)
        eff_min_conf_blend = get_effective_param("MIN_CONFIDENCE_BLEND", MIN_CONFIDENCE_BLEND)
        eff_take_profit = get_effective_param("TAKE_PROFIT_PCT", TAKE_PROFIT_PCT)
        eff_stop_loss = get_effective_param("STOP_LOSS_PCT", STOP_LOSS_PCT)

        skip_thresholds = {
            "min_edge": eff_min_edge,
            "min_confidence": 0.25,
            "max_spread": MAX_SPREAD,
            "min_liquidity": MIN_MARKET_LIQUIDITY,
            "max_slippage": MAX_ACCEPTABLE_SLIPPAGE,
            "min_depth_usd": MIN_DEPTH_USD,
            "max_drawdown": MAX_DRAWDOWN_PCT,
            "max_daily_loss": MAX_DAILY_LOSS_PCT,
            "max_divergence_low_conf": MAX_DIVERGENCE_LOW_CONFIDENCE,
            "max_divergence_any_conf": MAX_DIVERGENCE_ANY_CONFIDENCE,
            "kelly_fraction": eff_kelly_fraction,
            "fee_rate": POLYMARKET_FEE_RATE,
            "confidence_blend_floor": eff_min_conf_blend,
            "take_profit_pct": eff_take_profit,
            "stop_loss_pct": eff_stop_loss,
        }

        if result is None or result.skipped:
            # Record skipped market for audit trail
            try:
                from core.db import record_skipped_market
                record_skipped_market(
                    market_id=cid,
                    skip_reason=result.skip_reason if result else "no usable signals",
                    market_price=market_price,
                    estimated_prob=result.final_probability if result else 0.0,
                    confidence=result.confidence if result else 0.0,
                )
            except Exception:
                pass

            # Still populate structured data so UI shows market info + signals
            from signals.aggregator import SIGNAL_WEIGHT_MULTIPLIERS, _compute_effective_weight

            skip_signals = []
            if result is not None:
                for s in (result.all_signals if result.all_signals else result.individual_signals):
                    skip_signals.append({
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
                    })

            self.analysis_entries[cid].update({
                "status": "skipped",
                "decision": "SKIP",
                "edge": 0,
                "skip_reason": result.skip_reason if result else "no usable signals",
                "market_meta": market_meta,
                "aggregation": {
                    "final_probability": result.final_probability if result else 0,
                    "preliminary_probability": result.preliminary_probability if result else 0,
                    "confidence": result.confidence if result else 0,
                    "reasoning": result.reasoning if result else "",
                    "signals_agreement": result.signals_agreement if result else "--",
                    "market_efficiency": result.market_efficiency if result else "--",
                    "market_price": market_price,
                    "total_data_points": result.total_data_points if result else 0,
                    "signals_stdev": 0,
                    "signals": skip_signals,
                },
                "kelly": {
                    "edge": 0,
                    "effective_prob": 0,
                    "bet_size": 0,
                    "should_trade": False,
                    "skip_reason": result.skip_reason if result else "no usable signals",
                    "market_price": market_price,
                },
                "thresholds": skip_thresholds,
                "depth": {},
                "execution": {},
            })

            # Extract price_history from crypto signal if available
            if result is not None:
                for s in (result.all_signals if result.all_signals else result.individual_signals):
                    if s.source == "resolution_crypto" and s.raw_data and s.raw_data.get("price_history"):
                        self.analysis_entries[cid]["price_history"] = s.raw_data["price_history"]
                        break
            return

        # Run Kelly sizing — use available cash (not static TEST_BANKROLL)
        if PAPER_TRADING:
            from core.db import get_paper_balance
            paper_bal = get_paper_balance(TEST_BANKROLL)
            bankroll = max(paper_bal["available_cash"], 0)
        else:
            try:
                w = await _get_wallet()
                bankroll = await asyncio.to_thread(w.get_usdc_balance)
            except Exception:
                bankroll = TEST_BANKROLL

        from strategy.market_filter import extract_clob_token_ids
        token_ids = extract_clob_token_ids(m)
        token_id = token_ids[0] if token_ids else ""
        # Normalize so compute_limit_price() and executor can find token IDs
        m["clobTokenIds"] = token_ids

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
                        original_bet = decision.bet_size_usd
                        decision.bet_size_usd = depth.adjusted_bet_usd
                        decision.depth_total_usd = depth.total_depth_usd
                        decision.depth_slippage = depth.slippage
                        decision.depth_adjusted = depth.adjusted_bet_usd < original_bet
                        depth_data["adjusted_bet_usd"] = depth.adjusted_bet_usd
                        depth_data["was_adjusted"] = depth.adjusted_bet_usd < original_bet
            except Exception as e:
                logger.debug("Depth analysis failed for %s: %s", cid, e)

        # Gas-cost analysis — block trades whose EV doesn't clear gas overhead
        gas_data: dict[str, Any] = {}
        if GAS_ANALYSIS_ENABLED:
            try:
                from strategy.gas import analyze_gas_cost
                # Re-derive EV against the final (possibly depth-adjusted) bet size
                ev_for_gas = decision.edge * decision.bet_size_usd
                gas = await analyze_gas_cost(expected_value_usd=ev_for_gas)
                gas_data = {
                    "gas_price_gwei": gas.gas_price_gwei,
                    "matic_usd": gas.matic_usd,
                    "gas_units": gas.gas_units,
                    "gas_cost_usd": gas.gas_cost_usd,
                    "expected_value_usd": gas.expected_value_usd,
                    "ev_to_gas_ratio": gas.ev_to_gas_ratio,
                    "min_ev_gas_ratio": MIN_EV_GAS_RATIO,
                    "passes_gate": gas.passes_gate,
                    "skip_reason": gas.skip_reason,
                }
                decision.gas_cost_usd = gas.gas_cost_usd
                decision.ev_to_gas_ratio = gas.ev_to_gas_ratio
                if decision.should_trade and not gas.passes_gate:
                    decision.should_trade = False
                    decision.skip_reason = gas.skip_reason
                    decision.gas_blocked = True
                    logger.info(
                        "GAS SKIP: %s | %s",
                        decision.market_question[:50], gas.skip_reason,
                    )
            except Exception as e:
                logger.debug("Gas analysis failed for %s: %s", cid, e)

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
                "kelly_fraction_multiplier": eff_kelly_fraction,
                "min_edge_threshold": eff_min_edge,
                "max_position_pct": MAX_POSITION_PCT,
                "min_bankroll_reserve": MIN_BANKROLL_RESERVE,
                "confidence_blend_floor": eff_min_conf_blend,
            },
            "thresholds": skip_thresholds,
            "depth": depth_data,
            "gas": gas_data,
            "execution": exec_data,
        })

        # Record frontier decision for audit trail (Phase 6)
        try:
            from core.db import record_frontier_decision, record_skipped_market
            record_frontier_decision(
                market_id=cid,
                estimated_prob=decision.estimated_prob,
                effective_prob=decision.effective_prob,
                market_price=decision.market_price,
                edge=decision.edge,
                kelly_fraction=decision.adjusted_fraction,
                bet_size_usd=decision.bet_size_usd,
                confidence=decision.confidence,
                should_trade=decision.should_trade,
                skip_reason=decision.skip_reason,
            )
            if not decision.should_trade:
                record_skipped_market(
                    market_id=cid,
                    skip_reason=decision.skip_reason,
                    market_price=decision.market_price,
                    estimated_prob=decision.estimated_prob,
                    confidence=decision.confidence,
                )
        except Exception as e:
            logger.debug("Failed to record frontier decision: %s", e)

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

                # Run health checks
                try:
                    from monitoring.health import run_health_checks
                    self._last_health_results = await run_health_checks()
                    self._push_activity("health", "Health checks passed", f"{len(self._last_health_results)} checks ok")
                except Exception as health_err:
                    from strategy.executor import AutoStopError
                    if isinstance(health_err, AutoStopError):
                        logger.critical("Health check triggered auto-stop: %s", health_err)
                        self._push_activity("error", "Auto-stop triggered", str(health_err))
                        await self.stop()
                        return
                    logger.exception("Health check error (non-fatal)")

                # Snapshot bankroll if > 1 hour since last
                try:
                    from monitoring.pnl import snapshot_bankroll
                    await snapshot_bankroll()
                except Exception:
                    logger.debug("Bankroll snapshot failed (non-fatal)")

                self._position_last_run = time.time()
                self._position_failures = 0  # Reset on success
                self._push_activity("monitor", "Position check complete", "Orders monitored, positions managed")
                self.phase = "waiting"
                await self._broadcast({"type": "bot_status", "running": True, "phase": "waiting"})
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Position monitor error")
                self._position_failures += 1
                self.phase = "waiting"
                self._push_activity("error", "Position monitor error", f"Consecutive failures: {self._position_failures}")
                if self._position_failures >= 3:
                    logger.critical("Position monitor: 3 consecutive failures — auto-stopping bot")
                    self._push_activity("error", "Auto-stop: position monitor failed 3 times", "")
                    await self.stop()
                    return

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

    def _push_activity(self, event_type: str, message: str, detail: str = "") -> None:
        """Push a pipeline activity event to the feed."""
        self._activity_feed.appendleft({
            "type": event_type,
            "message": message,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


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

    global _engine_ref
    ws_manager = WSManager()
    engine = BotEngine(ws_manager)
    _engine_ref = engine
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

        # Start Telegram bot if configured
        telegram_app = None
        try:
            from monitoring.telegram import create_telegram_app
            telegram_app = create_telegram_app()
            if telegram_app is not None:
                await telegram_app.initialize()
                await telegram_app.start()
                await telegram_app.updater.start_polling(drop_pending_updates=True)
                logger.info("Telegram bot started (polling)")
        except Exception as e:
            logger.warning("Telegram bot failed to start: %s", e)

        yield

        # Stop Telegram bot
        if telegram_app is not None:
            try:
                await telegram_app.updater.stop()
                await telegram_app.stop()
                await telegram_app.shutdown()
            except Exception:
                pass
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
            t0 = time.monotonic()
            wallet = await _get_wallet()
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

        # Include bot health check results if available
        health_checks: list[dict[str, str]] = []
        for hc in engine._last_health_results:
            health_checks.append({
                "check_name": hc.check_name,
                "status": hc.status,
                "message": hc.message,
            })

        return {"services": services, "health_checks": health_checks}

    @app.get("/api/wallet")
    async def wallet():
        try:
            w = await _get_wallet()
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

    @app.get("/api/paper-balance")
    async def paper_balance():
        try:
            from core.db import get_paper_balance
            return get_paper_balance(TEST_BANKROLL)
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

            total_cost = 0.0
            try:
                row = db.execute("SELECT SUM(cost_usd) FROM llm_costs").fetchone()
                if row and row[0] is not None:
                    total_cost = float(row[0])
            except Exception:
                pass

            return {
                "daily": daily,
                "monthly": monthly,
                "total_calls": total_calls,
                "total_cost": total_cost,
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
            "paused": engine.paused,
            "phase": engine.phase,
            "cycle_count": engine.cycle_count,
            "paper_trading": PAPER_TRADING,
        }

    @app.get("/api/bot/cycles")
    async def bot_cycles():
        """Cycle timing, session stats, and pipeline activity feed."""
        now = time.time()

        def _next_run(last: float | None, interval_min: float) -> dict[str, Any]:
            if last is None or not engine.running:
                return {"last_run": None, "next_run": None, "seconds_remaining": None, "interval_minutes": interval_min}
            next_ts = last + interval_min * 60
            remaining = max(0, next_ts - now)
            return {
                "last_run": datetime.fromtimestamp(last, tz=timezone.utc).isoformat(),
                "next_run": datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat(),
                "seconds_remaining": round(remaining),
                "interval_minutes": interval_min,
            }

        return {
            "discovery": {
                **_next_run(engine._discovery_last_run, DISCOVERY_INTERVAL_MINUTES),
                "markets_found": engine._last_discovery_found,
                "markets_ranked": engine._last_discovery_ranked,
            },
            "aggregation": {
                **_next_run(engine._aggregation_last_run, AGGREGATION_INTERVAL_MINUTES),
                "batch_size": engine._last_batch_size,
            },
            "position_monitor": _next_run(engine._position_last_run, POSITION_CHECK_INTERVAL_MINUTES),
            "uptime_seconds": round(now - engine._started_at) if engine._started_at and engine.running else None,
            "session_stats": {
                "markets_discovered": engine._session_markets_discovered,
                "markets_analyzed": engine._session_markets_analyzed,
                "trades_executed": engine._session_trades_executed,
                "markets_skipped": engine._session_markets_skipped,
            },
            "activity_feed": list(engine._activity_feed),
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
    async def trades(limit: int = 200):
        try:
            from core.db import get_all_trades
            return get_all_trades(limit=min(limit, 500))
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/trades/{trade_id}")
    async def trade_detail(trade_id: str):
        try:
            from core.db import get_trade_with_context
            result = get_trade_with_context(trade_id)
            if result is None:
                return JSONResponse(status_code=404, content={"error": "Trade not found"})
            return result
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    # -----------------------------------------------------------------------
    # Learning / continuous improvement endpoints
    # -----------------------------------------------------------------------

    def _sanitize_json(obj: Any) -> Any:
        """Replace inf/NaN floats with JSON-safe values (null)."""
        if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
            return None
        if isinstance(obj, dict):
            return {k: _sanitize_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize_json(v) for v in obj]
        return obj

    @app.get("/api/learning/report")
    async def learning_report():
        """Get the latest learning report with all analyses and recommendations."""
        try:
            from monitoring.learning import get_latest_report
            report = get_latest_report()
            if report is None:
                return {"status": "no_data", "message": "No learning reports yet. Run a learning cycle first."}
            import dataclasses
            return _sanitize_json(dataclasses.asdict(report))
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/learning/history")
    async def learning_history(limit: int = Query(20, ge=1, le=100)):
        """Get trend data from recent learning reports."""
        try:
            from monitoring.learning import get_report_history
            return get_report_history(limit=limit)
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.post("/api/learning/run")
    async def learning_run():
        """Trigger a learning cycle manually (doesn't require bot to be running)."""
        try:
            from monitoring.learning import run_learning_cycle
            report = await run_learning_cycle()
            import dataclasses
            return _sanitize_json({
                "status": "complete",
                "recommendations": len(report.recommendations),
                "data_sufficiency": report.data_sufficiency,
                "report": dataclasses.asdict(report),
            })
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/learning/recommendations")
    async def learning_recommendations():
        """Get just the parameter recommendations from the latest report."""
        try:
            from monitoring.learning import get_latest_report
            report = get_latest_report()
            if report is None:
                return []
            import dataclasses
            return [dataclasses.asdict(r) for r in report.recommendations]
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/learning/calibration")
    async def learning_calibration():
        """Get calibration curve data for the frontier model."""
        try:
            from monitoring.learning import analyze_frontier_bias
            bias = analyze_frontier_bias()

            # Transform calibration_curve: backend {bin_center, predicted_mean, actual_mean, count}
            # → frontend {bucket, avg_estimated, avg_actual, bias, count}
            calibration_curve = []
            for entry in bias.calibration_curve:
                predicted = entry.get("predicted_mean", 0)
                actual = entry.get("actual_mean", 0)
                calibration_curve.append({
                    "bucket": f"{entry.get('bin_center', 0):.1f}",
                    "count": entry.get("count", 0),
                    "avg_estimated": predicted,
                    "avg_actual": actual,
                    "bias": round(predicted - actual, 4),
                })

            # Transform bias bands: backend dict[str, float] → frontend dict[str, {count, mean_bias}]
            bias_by_confidence: dict[str, Any] = {}
            for band, mean_bias_val in bias.bias_by_confidence_band.items():
                bias_by_confidence[band] = {"count": bias.sample_count, "mean_bias": mean_bias_val}
            bias_by_price: dict[str, Any] = {}
            for band, mean_bias_val in bias.bias_by_price_band.items():
                bias_by_price[band] = {"count": bias.sample_count, "mean_bias": mean_bias_val}

            return {
                "mean_bias": bias.mean_bias,
                "abs_mean_error": bias.abs_mean_error,
                "sample_count": bias.sample_count,
                "calibration_curve": calibration_curve,
                "bias_by_confidence": bias_by_confidence,
                "bias_by_price": bias_by_price,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/learning/skip-analysis")
    async def learning_skip_analysis():
        """Get retrospective analysis of skipped markets."""
        try:
            from monitoring.learning import analyze_skipped_markets
            report = analyze_skipped_markets()

            # Transform SkipRetroReport → SkipAnalysis shape expected by frontend
            top_missed_reasons: dict[str, int] = {}
            for reason, info in report.by_skip_reason.items():
                if isinstance(info, dict):
                    top_missed_reasons[reason] = info.get("would_have_profited", info.get("count", 0))
                else:
                    top_missed_reasons[reason] = int(info)

            avg_missed_edge = 0.0
            if report.resolved_skipped > 0 and report.missed_profit_estimate > 0:
                avg_missed_edge = report.missed_profit_estimate / max(report.would_have_profited, 1)

            recommendation = ""
            if report.would_have_profited > 0 and report.resolved_skipped > 0:
                miss_rate = report.would_have_profited / report.resolved_skipped
                if miss_rate > 0.3:
                    recommendation = (
                        f"Missing {miss_rate:.0%} of resolved skips — consider loosening "
                        f"edge or confidence thresholds."
                    )
                elif miss_rate > 0.1:
                    recommendation = "Some missed opportunities, but skip filters are mostly working."
                else:
                    recommendation = "Skip filters are well-calibrated — very few missed opportunities."

            return {
                "total_skipped": report.total_skipped,
                "resolved_count": report.resolved_skipped,
                "missed_opportunities": report.would_have_profited,
                "avg_missed_edge": round(avg_missed_edge, 4),
                "top_missed_reasons": top_missed_reasons,
                "recommendation": recommendation,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/learning/overrides")
    async def learning_overrides():
        """Return all active parameter overrides."""
        try:
            from core.db import get_db
            d = get_db()
            if "parameter_overrides" not in d.table_names():
                return []
            return list(d["parameter_overrides"].rows_where("active = 1"))
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.post("/api/learning/overrides/{parameter}/revert")
    async def learning_override_revert(parameter: str):
        """Revert (deactivate) a parameter override."""
        try:
            from monitoring.learning import revert_override
            success = revert_override(parameter)
            if success:
                return {"status": "reverted", "parameter": parameter}
            return JSONResponse(status_code=404, content={"error": f"No active override for {parameter}"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.post("/api/learning/overrides/{parameter}/set")
    async def learning_override_set(parameter: str, request: Request):
        """Manually set a parameter override value."""
        try:
            body = await request.json()
            value = float(body.get("value", 0))
            reason = str(body.get("reason", "manual override"))

            from monitoring.learning import PARAMETER_LIMITS
            if parameter not in PARAMETER_LIMITS:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Unknown parameter: {parameter}. "
                             f"Valid: {list(PARAMETER_LIMITS.keys())}"},
                )

            floor, ceiling = PARAMETER_LIMITS[parameter]
            value = max(floor, min(ceiling, value))

            from core.db import get_db
            from config.settings import get_effective_param
            d = get_db()
            now_str = datetime.now(timezone.utc).isoformat()

            # Get current default
            import config.settings as _s
            default = getattr(_s, parameter, value)

            # Deactivate previous
            try:
                existing = d["parameter_overrides"].get(parameter)
                if existing and existing["active"] == 1:
                    d["parameter_overrides"].update(parameter, {"active": 0})
            except Exception:
                pass

            d["parameter_overrides"].upsert({
                "parameter": parameter,
                "original_value": default,
                "current_value": round(value, 4),
                "applied_at": now_str,
                "source_report_ts": "",
                "confidence": 1.0,
                "sample_count": 0,
                "reason": reason[:500],
                "active": 1,
            }, pk="parameter")

            return {"status": "set", "parameter": parameter, "value": round(value, 4)}
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

    @app.post("/api/bot/pause")
    async def bot_pause():
        if not engine.running:
            return {"status": "not_running"}
        if engine.paused:
            return {"status": "already_paused"}
        await engine.pause()
        return {"status": "paused"}

    @app.post("/api/bot/resume")
    async def bot_resume():
        if not engine.running:
            return {"status": "not_running"}
        if not engine.paused:
            return {"status": "not_paused"}
        await engine.resume()
        return {"status": "resumed"}

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
    # Database explorer endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/db/tables")
    async def db_tables():
        """List all database tables with row counts."""
        try:
            from core.db import get_db
            db = get_db()
            tables = []
            for name in db.table_names():
                count = db.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
                columns = [{"name": col.name, "type": col.type} for col in db[name].columns]
                tables.append({"name": name, "row_count": count, "columns": columns})
            return tables
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

    @app.get("/api/db/tables/{table_name}")
    async def db_table_rows(
        table_name: str,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        order_by: str = Query("rowid"),
        desc: bool = Query(True),
    ):
        """Fetch rows from a specific table with pagination."""
        try:
            from core.db import get_db
            db = get_db()
            if table_name not in db.table_names():
                return JSONResponse(status_code=404, content={"error": f"Table '{table_name}' not found"})
            direction = "DESC" if desc else "ASC"
            # Validate order_by is a real column or rowid
            valid_cols = {col.name for col in db[table_name].columns} | {"rowid"}
            if order_by not in valid_cols:
                order_by = "rowid"
            total = db.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM [{table_name}] ORDER BY [{order_by}] {direction} LIMIT ? OFFSET ?",
                [limit, offset],
            ).fetchall()
            columns = [col.name for col in db[table_name].columns]
            data = [{columns[i]: row[i] for i in range(len(columns))} for row in rows]
            return {"table": table_name, "total": total, "offset": offset, "limit": limit, "rows": data}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)[:200]})

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
