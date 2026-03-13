"""Main TUI application — Textual App with workers, keybindings, message routing."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from config.settings import CHEAP_MODEL
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Header, Footer, TabbedContent, TabPane

# Ensure project root is on sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tui.log_handler import TUILogHandler
from tui.messages import (
    AggregationResult,
    AnalysisSelectionChanged,
    BatchUpdate,
    BetUpdate,
    BotProcessUpdate,
    BotStatusUpdate,
    BotToggle,
    CommandResult,
    ConnectionUpdate,
    CostUpdate,
    DrillDownRequest,
    ExecutionUpdate,
    LogMessage,
    MarketsUpdate,
    PipelineComplete,
    PipelineStageUpdate,
    SignalUpdate,
    WalletUpdate,
)
from tui.state import ConnectionStatus, PipelineProgress
from tui.widgets.command_bar import CommandBar
from tui.widgets.dashboard_panel import DashboardPanel
from tui.widgets.log_panel import LogPanel
from tui.widgets.markets_panel import MarketsPanel
from tui.widgets.analysis_list_panel import AnalysisListPanel
from tui.widgets.analysis_detail_panel import AnalysisDetailPanel

logger = logging.getLogger(__name__)

# Max markets to aggregate per filter cycle
BATCH_SIZE = 40


class TUIApp(App):
    """Polymarket Bot — Real-Time TUI Dashboard."""

    TITLE = "Polymarket Bot"
    SUB_TITLE = "Signal-Based Trading Dashboard"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("1", "switch_tab('dashboard')", "Dashboard", show=True),
        Binding("2", "switch_tab('markets')", "Markets", show=True),
        Binding("3", "switch_tab('analysis')", "Analysis", show=True),
        Binding("4", "switch_tab('logs')", "Logs", show=True),
        Binding("s", "toggle_bot", "Start/Stop"),
        Binding("a", "run_aggregate_default", "Aggregate"),
        Binding("r", "refresh", "Refresh"),
        Binding("colon", "toggle_command_bar", "Command", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPanel()
            with TabPane("Markets", id="markets"):
                yield MarketsPanel()
            with TabPane("Analysis", id="analysis"):
                with Horizontal(classes="analysis-split"):
                    yield AnalysisListPanel(classes="analysis-list")
                    yield AnalysisDetailPanel(classes="analysis-detail")
            with TabPane("Logs", id="logs"):
                yield LogPanel()
        yield CommandBar()
        yield Footer()

    _bot_running: bool = False

    # Track already-aggregated markets to avoid duplicates across cycles
    _aggregated_ids: set[str] = set()
    _cycle_count: int = 0

    def on_mount(self) -> None:
        """Attach log handler and kick off background workers."""
        self._aggregated_ids = set()
        self._cycle_count = 0

        # Attach TUI log handler to root logger
        handler = TUILogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        logger.info("TUI Dashboard starting...")

        # Start health loop (always runs) and initial data fetch
        self._start_health_loop()
        self.refresh_markets()
        self.refresh_costs()
        # Pipeline loop does NOT start until bot is started

    # -----------------------------------------------------------------
    # Bot start/stop
    # -----------------------------------------------------------------

    def action_toggle_bot(self) -> None:
        self._set_bot_running(not self._bot_running)

    def on_bot_toggle(self, event: BotToggle) -> None:
        self._set_bot_running(event.running)

    def _set_bot_running(self, running: bool) -> None:
        if running == self._bot_running:
            return
        self._bot_running = running
        self.post_message(BotStatusUpdate(running))
        if running:
            logger.info("Bot STARTED — pipeline loop active")
            self._aggregated_ids = set()
            self._cycle_count = 0
            self._start_health_loop()
            self._start_pipeline_loop()
            self.post_message(BotProcessUpdate("filtering", "Starting first filter cycle..."))
        else:
            logger.info("Bot STOPPED — all workers cancelled")
            self.workers.cancel_group(self, "pipeline-loop")
            self.workers.cancel_group(self, "pipeline")
            self.workers.cancel_group(self, "health-loop")
            self.workers.cancel_group(self, "health-check")
            self.workers.cancel_group(self, "markets")
            self.workers.cancel_group(self, "costs")
            from core.db import clear_pipeline_cache
            clear_pipeline_cache()
            self.post_message(BotProcessUpdate("idle", "Bot is stopped. Press s to start."))

    # -----------------------------------------------------------------
    # Tab switching
    # -----------------------------------------------------------------

    def action_switch_tab(self, tab_id: str) -> None:
        tc = self.query_one(TabbedContent)
        tc.active = tab_id

    # -----------------------------------------------------------------
    # Command bar
    # -----------------------------------------------------------------

    def action_toggle_command_bar(self) -> None:
        self.query_one(CommandBar).toggle()

    # -----------------------------------------------------------------
    # Action shortcuts
    # -----------------------------------------------------------------

    def action_run_aggregate_default(self) -> None:
        self.run_aggregate()

    def action_refresh(self) -> None:
        self.run_health_check()
        self.refresh_markets()
        self.refresh_costs()

    # -----------------------------------------------------------------
    # Health check worker
    # -----------------------------------------------------------------

    def _start_health_loop(self) -> None:
        self.run_worker(self._health_check_loop(), exclusive=True, group="health-loop")

    async def _health_check_loop(self) -> None:
        """Periodic health checks every 5 minutes."""
        while True:
            await self._do_health_check()
            await asyncio.sleep(300)

    def run_health_check(self) -> None:
        self.run_worker(self._do_health_check(), exclusive=True, group="health-check")

    async def _do_health_check(self) -> None:
        """Check all three connections and post updates."""
        now = datetime.now(timezone.utc)

        # 1. Polymarket API — hit Gamma API (lightweight, no auth needed)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://gamma-api.polymarket.com/markets?limit=1&active=true",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        self.post_message(ConnectionUpdate(
                            ConnectionStatus("Polymarket API", True, now)
                        ))
                    else:
                        self.post_message(ConnectionUpdate(
                            ConnectionStatus("Polymarket API", False, now, f"HTTP {resp.status}")
                        ))
        except Exception as e:
            self.post_message(ConnectionUpdate(
                ConnectionStatus("Polymarket API", False, now, str(e)[:60])
            ))

        # 2. Polygon RPC — try wallet balance check
        try:
            from core.wallet import Wallet
            wallet = await asyncio.to_thread(Wallet)
            usdc = await asyncio.to_thread(wallet.get_usdc_balance)
            matic = await asyncio.to_thread(wallet.get_matic_balance)
            has_gas = await asyncio.to_thread(wallet.has_sufficient_gas)
            self.post_message(ConnectionUpdate(
                ConnectionStatus("Polygon RPC", True, now)
            ))

            # Also send wallet update
            from core.db import get_open_positions
            positions = get_open_positions()
            self.post_message(WalletUpdate(
                usdc=usdc,
                matic=matic,
                has_gas=has_gas,
                positions_count=len(positions),
                address=wallet.address,
            ))
        except Exception as e:
            self.post_message(ConnectionUpdate(
                ConnectionStatus("Polygon RPC", False, now, str(e)[:60])
            ))

        # 3. OpenRouter — cheap LLM ping
        try:
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY not set")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": CHEAP_MODEL,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        self.post_message(ConnectionUpdate(
                            ConnectionStatus("OpenRouter", True, now)
                        ))
                    else:
                        self.post_message(ConnectionUpdate(
                            ConnectionStatus("OpenRouter", False, now, f"HTTP {resp.status}")
                        ))
        except Exception as e:
            self.post_message(ConnectionUpdate(
                ConnectionStatus("OpenRouter", False, now, str(e)[:60])
            ))

    # -----------------------------------------------------------------
    # Markets worker
    # -----------------------------------------------------------------

    def refresh_markets(self) -> None:
        self.run_worker(self._fetch_markets(), exclusive=True, group="markets")

    async def _fetch_markets(self) -> None:
        """Fetch active markets from Gamma API."""
        markets_panel = self.query_one(MarketsPanel)
        sort_field = markets_panel.sort_field
        limit = markets_panel.limit

        url = (
            f"https://gamma-api.polymarket.com/markets"
            f"?closed=false&active=true&limit={limit}&order={sort_field}&ascending=false"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        self.post_message(MarketsUpdate(markets))
                    else:
                        logger.error("Gamma API returned %d", resp.status)
        except Exception as e:
            logger.error("Failed to fetch markets: %s", e)

    # -----------------------------------------------------------------
    # Costs worker
    # -----------------------------------------------------------------

    def refresh_costs(self) -> None:
        self.run_worker(self._fetch_costs(), exclusive=True, group="costs")

    async def _fetch_costs(self) -> None:
        """Load LLM cost data from SQLite."""
        try:
            from core.db import get_db, get_daily_llm_cost, get_monthly_llm_cost

            daily = get_daily_llm_cost()
            monthly = get_monthly_llm_cost()
            db = get_db()

            # Model breakdown
            model_rows: list[tuple[str, int, int, int, float]] = []
            try:
                rows = list(db.execute(
                    "SELECT model, COUNT(*) as calls, SUM(input_tokens) as inp, "
                    "SUM(output_tokens) as outp, SUM(cost_usd) as cost "
                    "FROM llm_costs GROUP BY model ORDER BY cost DESC"
                ).fetchall())
                for row in rows:
                    model_rows.append((row[0], row[1], row[2], row[3], row[4]))
            except Exception:
                pass

            # Task breakdown
            task_rows: list[tuple[str, int, float]] = []
            try:
                rows = list(db.execute(
                    "SELECT task_type, COUNT(*) as calls, SUM(cost_usd) as cost "
                    "FROM llm_costs GROUP BY task_type ORDER BY cost DESC"
                ).fetchall())
                for row in rows:
                    task_rows.append((row[0], row[1], row[2]))
            except Exception:
                pass

            # Total calls
            total_calls = 0
            try:
                total = db.execute("SELECT COUNT(*) FROM llm_costs").fetchone()
                if total:
                    total_calls = total[0]
            except Exception:
                pass

            self.post_message(CostUpdate(
                daily=daily,
                monthly=monthly,
                model_breakdown=model_rows,
                task_breakdown=task_rows,
                total_calls=total_calls,
            ))
        except Exception as e:
            logger.error("Failed to load cost data: %s", e)

    # -----------------------------------------------------------------
    # Pipeline loop — filter → aggregate top 20 → repeat
    # -----------------------------------------------------------------

    def _start_pipeline_loop(self) -> None:
        self.run_worker(self._pipeline_loop(), exclusive=True, group="pipeline-loop")

    async def _pipeline_loop(self) -> None:
        """Continuous loop: filter markets → aggregate top 20 → repeat while bot is running."""
        await asyncio.sleep(3)  # Brief startup delay

        while self._bot_running:
            self._cycle_count += 1
            cycle = self._cycle_count

            try:
                # Check for resolved markets to update calibration data
                try:
                    from signals.calibration import check_and_record_resolutions
                    resolved = await check_and_record_resolutions()
                    if resolved > 0:
                        logger.info("Calibration: recorded %d new resolutions", resolved)
                except Exception as e:
                    logger.debug("Resolution check failed: %s", e)

                # Phase 1: Filter
                self.post_message(BotProcessUpdate("filtering", "Discovering and filtering markets...", cycle))
                ranked = await self._run_filter_stages()

                if not self._bot_running:
                    break

                if not ranked:
                    logger.warning("No markets survived filtering. Waiting 60s before retry.")
                    self.post_message(BotProcessUpdate("waiting", "No markets found. Retrying in 60s...", cycle))
                    for _ in range(12):
                        if not self._bot_running:
                            break
                        await asyncio.sleep(5)
                    continue

                # Phase 2: Select top 20, skip already-aggregated
                batch = []
                for m in ranked:
                    cond_id = m.get("conditionId", m.get("condition_id", ""))
                    if cond_id and cond_id in self._aggregated_ids:
                        continue
                    batch.append(m)
                    if len(batch) >= BATCH_SIZE:
                        break

                if not batch:
                    logger.info("All top markets already aggregated. Clearing history and re-filtering.")
                    self._aggregated_ids.clear()
                    self.post_message(BotProcessUpdate("waiting", "All markets processed. Clearing and re-filtering...", cycle))
                    await asyncio.sleep(10)
                    continue

                logger.info("Cycle %d: Aggregating %d markets (skipped %d already processed)",
                            cycle, len(batch), len(ranked) - len(batch))

                # Post batch to Analysis tab
                statuses: dict[str, str] = {}
                for m in batch:
                    cond_id = m.get("conditionId", m.get("condition_id", ""))
                    statuses[cond_id] = "waiting"
                self.post_message(BatchUpdate(markets=batch, current_index=-1, statuses=dict(statuses)))

                # Post pipeline complete to Markets tab progress bar
                self.post_message(PipelineComplete(
                    results=ranked,
                    discovered=len(ranked),
                    filtered=len(batch),
                ))

                # Phase 3: Aggregate each market in the batch
                self.post_message(BotProcessUpdate("aggregating", f"Starting batch of {len(batch)} markets...", cycle))
                await self._aggregate_batch(batch, statuses, cycle)

                if not self._bot_running:
                    break

                # Refresh costs and markets after a full cycle
                self.refresh_costs()
                self.refresh_markets()

                # Brief pause before next cycle
                self.post_message(BotProcessUpdate("waiting", "Cycle complete. Starting next filter cycle...", cycle))
                for _ in range(4):  # 20s pause
                    if not self._bot_running:
                        break
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error("Pipeline loop error: %s", e, exc_info=True)
                self.post_message(BotProcessUpdate("waiting", f"Error: {str(e)[:80]}. Retrying in 30s...", cycle))
                for _ in range(6):
                    if not self._bot_running:
                        break
                    await asyncio.sleep(5)

    async def _run_filter_stages(self) -> list[dict[str, Any]]:
        """Execute filter stages 0-4 and return ranked markets."""
        from core.llm import LLMClient
        from strategy.market_filter import (
            batch_categorize_markets,
            discover_markets,
            extract_resolution_params,
            filter_markets,
            pre_screen_crypto_edge,
            rank_candidates,
        )

        pipeline_start = datetime.now(timezone.utc)

        async with LLMClient() as llm:
            # Stage 0: Discover
            self._post_stage("discover", 0, started_at=pipeline_start)
            markets = await discover_markets()

            # Stage 1: Filter
            self._post_stage("filter", 1, total=len(markets), started_at=pipeline_start)
            filtered = await filter_markets(markets)

            # Stage 2: Categorize (batched to avoid rate limits)
            self._post_stage("categorize", 2, processed=0, total=len(filtered), started_at=pipeline_start)
            await batch_categorize_markets(filtered, llm)
            self._post_stage("categorize", 2, processed=len(filtered), total=len(filtered), started_at=pipeline_start)

            # Category gate: keep only crypto markets
            filtered = [m for m in filtered if m.get("_category") == "crypto"]

            # Stage 3: Extract resolution params
            self._post_stage("extract", 3, processed=0, total=len(filtered), started_at=pipeline_start)
            for i, m in enumerate(filtered):
                cat = m.get("_category", "")
                if cat == "crypto":
                    params = await extract_resolution_params(
                        m.get("question", ""), cat, llm,
                        condition_id=m.get("condition_id", ""),
                    )
                    if params:
                        m["_resolution_params"] = params
                self._post_stage("extract", 3, processed=i + 1, total=len(filtered), started_at=pipeline_start)

            # Stage 4: Pre-screen with CoinGecko math (free, fast)
            self._post_stage("prescreen", 4, processed=0, total=len(filtered), started_at=pipeline_start)
            filtered = await pre_screen_crypto_edge(filtered)
            self._post_stage("prescreen", 4, processed=len(filtered), total=len(filtered), started_at=pipeline_start)

            # Stage 5: Rank by edge potential
            self._post_stage("rank", 5, started_at=pipeline_start)
            ranked = rank_candidates(filtered)

        return ranked

    def _post_stage(
        self,
        stage: str,
        index: int,
        processed: int = 0,
        total: int = 0,
        started_at: datetime | None = None,
    ) -> None:
        self.post_message(PipelineStageUpdate(PipelineProgress(
            running=True,
            current_stage=stage,
            stage_index=index,
            total_stages=6,
            items_processed=processed,
            items_total=total,
            started_at=started_at or datetime.now(timezone.utc),
            stage_started_at=datetime.now(timezone.utc),
        )))

    async def _aggregate_batch(
        self,
        batch: list[dict[str, Any]],
        statuses: dict[str, str],
        cycle: int,
    ) -> None:
        """Aggregate signals for each market in the batch."""
        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        from signals.prediction_markets import PredictionMarketsSignalProvider
        from signals.resolution_crypto import CryptoResolutionProvider
        from signals.web_search import WebSearchSignalProvider

        async with LLMClient() as llm:
            for i, mkt in enumerate(batch):
                if not self._bot_running:
                    break

                cond_id = mkt.get("conditionId", mkt.get("condition_id", ""))
                question = mkt.get("question", "")
                category = mkt.get("_category", "")
                end_date = mkt.get("endDate", "2026-12-31")

                question_short = question[:60] + "..." if len(question) > 60 else question
                self.post_message(BotProcessUpdate(
                    "aggregating", f"Market {i + 1}/{len(batch)}: {question_short}", cycle
                ))

                # Update batch status
                statuses[cond_id] = "processing"
                self.post_message(BatchUpdate(markets=batch, current_index=i, statuses=dict(statuses)))

                # Get market price from tokens list (normalized market format)
                market_price = 0.50
                for tok in mkt.get("tokens", []):
                    if tok.get("outcome", "").upper() == "YES":
                        try:
                            market_price = float(tok.get("price", 0.50))
                        except (TypeError, ValueError):
                            pass
                        break

                # Build resolution kwargs
                resolution_kwargs: dict = {}
                res_params = mkt.get("_resolution_params")
                if res_params:
                    resolution_kwargs["resolution_keywords"] = res_params

                def _agg_progress(mkt_question: str, stage: str, detail: str = "") -> None:
                    self.post_message(SignalUpdate(
                        market_question=mkt_question,
                        stage=stage,
                        detail=detail,
                        source="aggregator",
                    ))

                providers = [
                    CryptoResolutionProvider(llm=llm),
                    WebSearchSignalProvider(llm=llm),
                    PredictionMarketsSignalProvider(llm=llm),
                ]

                aggregator = SignalAggregator(
                    llm=llm,
                    providers=providers,
                    on_progress=_agg_progress,
                )

                try:
                    agg_result = await aggregator.aggregate(
                        market_question=question,
                        market_category=category,
                        market_end_date=end_date,
                        market_price=market_price,
                        condition_id=cond_id,
                        **resolution_kwargs,
                    )

                    # Post aggregation result for Analysis tab
                    self.post_message(AggregationResult(
                        market_data=mkt,
                        aggregation=agg_result,
                        market_question=question,
                    ))

                    if agg_result is not None:
                        statuses[cond_id] = "done"
                        self.post_message(SignalUpdate(
                            market_question=question,
                            stage="done",
                            detail=agg_result.reasoning[:100],
                            probability=agg_result.final_probability,
                            confidence=agg_result.confidence,
                            data_points=agg_result.total_data_points,
                            done=True,
                            source="aggregator",
                        ))
                        logger.info(
                            "Aggregated: %s => P=%.2f C=%.2f (%s)",
                            question[:50], agg_result.final_probability,
                            agg_result.confidence, agg_result.market_efficiency,
                        )

                        # Kelly bet sizing + depth analysis
                        decision = await self._run_kelly(mkt, cond_id, question, agg_result, market_price)

                        # Execute trade if Kelly says go
                        if decision and decision.should_trade:
                            await self._run_execution(decision, mkt, cond_id)
                    else:
                        statuses[cond_id] = "skipped"
                        self.post_message(SignalUpdate(
                            market_question=question,
                            stage="skip",
                            detail="Market skipped by aggregator",
                            source="aggregator",
                            done=True,
                        ))
                except Exception as e:
                    statuses[cond_id] = "error"
                    logger.warning("Aggregation failed for '%s': %s", question[:50], e)
                    self.post_message(SignalUpdate(
                        market_question=question,
                        stage="error",
                        detail=str(e)[:100],
                        source="aggregator",
                        done=True,
                    ))

                # Mark as aggregated regardless of outcome
                if cond_id:
                    self._aggregated_ids.add(cond_id)

                # Update batch display
                self.post_message(BatchUpdate(markets=batch, current_index=i, statuses=dict(statuses)))

    # -----------------------------------------------------------------
    # Kelly bet sizing helper
    # -----------------------------------------------------------------

    async def _run_kelly(
        self,
        mkt: dict[str, Any],
        cond_id: str,
        question: str,
        agg_result: Any,
        market_price: float,
    ) -> Any:
        """Run Kelly criterion sizing with depth analysis. Returns TradeDecision or None."""
        from config.settings import DEPTH_ANALYSIS_ENABLED, TEST_BANKROLL
        from strategy.kelly import calculate_kelly

        try:
            # Use TEST_BANKROLL as placeholder; swap for real wallet balance when live
            available_bankroll = TEST_BANKROLL

            # Get YES and NO token IDs from market data
            yes_token_id = ""
            no_token_id = ""
            for tok in mkt.get("tokens", []):
                outcome = tok.get("outcome", "").upper()
                if outcome == "YES":
                    yes_token_id = tok.get("token_id", "")
                elif outcome == "NO":
                    no_token_id = tok.get("token_id", "")

            decision = calculate_kelly(
                market_id=cond_id,
                token_id=yes_token_id,
                market_question=question,
                estimated_prob=agg_result.final_probability,
                market_price=market_price,
                confidence=agg_result.confidence,
                available_bankroll=available_bankroll,
            )

            # Depth analysis: check order book before finalizing
            if decision.should_trade and DEPTH_ANALYSIS_ENABLED:
                try:
                    from strategy.depth import analyze_depth

                    # Pick the right token for the side we're buying
                    depth_token = yes_token_id if decision.side == "BUY_YES" else no_token_id
                    if depth_token:
                        depth = await analyze_depth(
                            token_id=depth_token,
                            side=decision.side,
                            bet_size_usd=decision.bet_size_usd,
                        )
                        decision.depth_total_usd = depth.total_depth_usd
                        decision.depth_slippage = depth.slippage

                        if depth.skip_reason:
                            decision.should_trade = False
                            decision.skip_reason = f"depth: {depth.skip_reason}"
                            decision.depth_adjusted = True
                            logger.info(
                                "Depth skip for '%s': %s",
                                question[:50], depth.skip_reason,
                            )
                        elif depth.adjusted_bet_usd < decision.bet_size_usd:
                            old_bet = decision.bet_size_usd
                            decision.bet_size_usd = depth.adjusted_bet_usd
                            decision.expected_value = decision.edge * decision.bet_size_usd
                            decision.depth_adjusted = True
                            logger.info(
                                "Depth adjusted '%s': $%.2f → $%.2f (slippage %.1%%)",
                                question[:50], old_bet, decision.bet_size_usd,
                                depth.slippage * 100,
                            )
                except Exception as e:
                    logger.warning("Depth analysis failed for '%s': %s", question[:50], e)
                    # Continue without depth adjustment — don't block the trade

            self.post_message(BetUpdate(
                decision=decision, market_data=mkt, aggregation=agg_result,
            ))
            return decision
        except Exception as e:
            logger.warning("Kelly sizing failed for '%s': %s", question[:50], e)
            return None

    # -----------------------------------------------------------------
    # Order execution helper
    # -----------------------------------------------------------------

    async def _run_execution(
        self,
        decision: Any,
        mkt: dict[str, Any],
        cond_id: str,
    ) -> None:
        """Execute a trade via PaperExecutor or TradeExecutor."""
        from config.settings import PAPER_TRADING, TEST_BANKROLL
        from strategy.executor import AutoStopError, PaperExecutor

        try:
            if PAPER_TRADING:
                executor = PaperExecutor()
            else:
                from core.client import ClobClientWrapper
                from strategy.executor import TradeExecutor
                client = ClobClientWrapper()
                executor = TradeExecutor(client)

            bankroll = TEST_BANKROLL
            trade_id = await executor.execute_trade(decision, mkt, bankroll)

            if trade_id:
                # Compute fill details
                from strategy.executor import compute_limit_price
                price, _token = compute_limit_price(decision, mkt)
                size = decision.bet_size_usd / price if price > 0 else 0

                status = "filled" if PAPER_TRADING else "pending"
                self.post_message(ExecutionUpdate(
                    condition_id=cond_id,
                    trade_id=trade_id,
                    status=status,
                    price=price,
                    size=size,
                    paper=PAPER_TRADING,
                ))
                logger.info(
                    "Executed %s: %s %s @ %.4f ($%.2f)",
                    "PAPER" if PAPER_TRADING else "LIVE",
                    decision.side, decision.market_question[:40],
                    price, decision.bet_size_usd,
                )
            else:
                self.post_message(ExecutionUpdate(
                    condition_id=cond_id,
                    trade_id=None,
                    status="blocked",
                    paper=PAPER_TRADING,
                    reason="Blocked by risk guardrail",
                ))
        except AutoStopError as e:
            logger.critical("AutoStop triggered: %s", e)
            self.post_message(ExecutionUpdate(
                condition_id=cond_id,
                trade_id=None,
                status="error",
                paper=PAPER_TRADING,
                reason=f"AUTO-STOP: {e}",
            ))
            # Stop the bot on critical guardrail failure
            self._bot_running = False
            self.post_message(BotStatusUpdate(running=False))
        except Exception as e:
            logger.error("Execution failed: %s", e, exc_info=True)
            self.post_message(ExecutionUpdate(
                condition_id=cond_id,
                trade_id=None,
                status="error",
                paper=PAPER_TRADING,
                reason=str(e)[:100],
            ))

    # -----------------------------------------------------------------
    # Command bar workers
    # -----------------------------------------------------------------

    def run_categorize(self, question: str) -> None:
        self.run_worker(self._do_categorize(question), group="cmd")

    async def _do_categorize(self, question: str) -> None:
        """Categorize a market question via cheap LLM."""
        try:
            from core.llm import LLMClient
            from strategy.market_filter import categorize_market

            market = {"condition_id": "", "question": question}
            async with LLMClient() as llm:
                category = await categorize_market(market, llm)

            self.post_message(CommandResult(
                command=f"categorize {question}",
                success=True,
                output=f"Question: {question}\nCategory: {category.upper()}",
            ))
            self.refresh_costs()
        except Exception as e:
            self.post_message(CommandResult(
                command=f"categorize {question}",
                success=False,
                output=f"Error: {e}",
            ))

    def run_llm_test(self, prompt: str) -> None:
        self.run_worker(self._do_llm_test(prompt), group="cmd")

    async def _do_llm_test(self, prompt: str) -> None:
        """Send a prompt to the cheap model."""
        try:
            from core.llm import LLMClient

            async with LLMClient() as llm:
                response = await llm.call(prompt, task_type="summarize")

            self.post_message(CommandResult(
                command=f"llm-test {prompt[:50]}",
                success=True,
                output=f"Prompt: {prompt}\nResponse: {response}",
            ))
            self.refresh_costs()
        except Exception as e:
            self.post_message(CommandResult(
                command=f"llm-test {prompt[:50]}",
                success=False,
                output=f"Error: {e}",
            ))

    # -----------------------------------------------------------------
    # Signal test worker
    # -----------------------------------------------------------------

    DEFAULT_SIGNAL_TEST_QUESTION = "Will Bitcoin reach $150,000 by end of 2026?"

    def run_signal_test(self, question: str = "") -> None:
        q = question or self.DEFAULT_SIGNAL_TEST_QUESTION
        self.run_worker(self._do_signal_test(q), group="signal-test")
        # Switch to analysis tab
        tc = self.query_one(TabbedContent)
        tc.active = "analysis"

    async def _do_signal_test(self, question: str) -> None:
        """Run all signal providers on a question and emit live updates."""
        from core.llm import LLMClient
        from signals.prediction_markets import PredictionMarketsSignalProvider
        from signals.resolution_crypto import CryptoResolutionProvider
        from signals.web_search import WebSearchSignalProvider
        from strategy.market_filter import categorize_market, extract_resolution_params

        # Add manual entry to analysis list
        try:
            self.query_one(AnalysisListPanel).add_manual_entry(question, "manual-st")
        except Exception:
            pass

        def _make_progress_cb(source_name: str):
            def on_progress(mkt_question: str, stage: str, detail: str = "") -> None:
                self.post_message(SignalUpdate(
                    market_question=mkt_question,
                    stage=stage,
                    detail=detail,
                    source=source_name,
                ))
            return on_progress

        try:
            async with LLMClient() as llm:
                # Auto-detect category
                market = {"condition_id": "", "question": question}
                category = await categorize_market(market, llm)

                # Extract resolution params for crypto
                resolution_kwargs: dict = {}
                if category == "crypto":
                    params = await extract_resolution_params(question, category, llm)
                    if params:
                        resolution_kwargs["resolution_keywords"] = params

                # Create all 3 providers with source-tagged progress callbacks
                crypto_provider = CryptoResolutionProvider(llm=llm, on_progress=_make_progress_cb("crypto"))
                web_search_provider = WebSearchSignalProvider(llm=llm, on_progress=_make_progress_cb("web_search"))
                prediction_markets_provider = PredictionMarketsSignalProvider(llm=llm, on_progress=_make_progress_cb("prediction_markets"))

                # Run all providers in parallel
                results = await asyncio.gather(
                    crypto_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    web_search_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    prediction_markets_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    return_exceptions=True,
                )

                provider_names = [
                    "resolution_crypto", "web_search",
                    "prediction_markets",
                ]
                output_lines = [f"Question: {question}", f"Category: {category}"]

                for name, result in zip(provider_names, results):
                    if isinstance(result, Exception):
                        logger.warning("Signal provider %s failed: %s", name, result)
                        continue
                    # Skip providers that returned confidence=0 (category mismatch)
                    if result.confidence <= 0:
                        continue
                    self.post_message(SignalUpdate(
                        market_question=question,
                        stage="done",
                        detail=result.reasoning[:100] if result.reasoning else "",
                        probability=result.probability,
                        confidence=result.confidence,
                        data_points=result.data_points,
                        done=True,
                        source=name,
                    ))
                    output_lines.append(
                        f"\n[{name}] P={result.probability} C={result.confidence} "
                        f"({result.data_points} pts): {result.reasoning}"
                    )

            self.post_message(CommandResult(
                command="signal-test",
                success=True,
                output="\n".join(output_lines),
            ))
            self.refresh_costs()
        except Exception as e:
            self.post_message(SignalUpdate(
                market_question=question,
                stage="error",
                detail=str(e)[:100],
                done=True,
            ))
            self.post_message(CommandResult(
                command="signal-test",
                success=False,
                output=f"Error: {e}",
            ))

    # -----------------------------------------------------------------
    # Aggregate worker — full signal pipeline with frontier model
    # -----------------------------------------------------------------

    def run_aggregate(self, question: str = "", market_price: str = "0.50") -> None:
        q = question or self.DEFAULT_SIGNAL_TEST_QUESTION
        try:
            price = float(market_price)
        except ValueError:
            price = 0.50
        self.run_worker(self._do_aggregate(q, price), group="aggregate")
        # Switch to analysis tab
        tc = self.query_one(TabbedContent)
        tc.active = "analysis"

    async def _do_aggregate(self, question: str, market_price: float) -> None:
        """Run full aggregation pipeline: all signals + frontier model."""
        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        from signals.prediction_markets import PredictionMarketsSignalProvider
        from signals.resolution_crypto import CryptoResolutionProvider
        from signals.web_search import WebSearchSignalProvider
        from strategy.market_filter import categorize_market, extract_resolution_params

        # Add manual entry to analysis list
        try:
            self.query_one(AnalysisListPanel).add_manual_entry(question)
        except Exception:
            pass

        def _make_progress_cb(source_name: str):
            def on_progress(mkt_question: str, stage: str, detail: str = "") -> None:
                self.post_message(SignalUpdate(
                    market_question=mkt_question,
                    stage=stage,
                    detail=detail,
                    source=source_name,
                ))
            return on_progress

        try:
            async with LLMClient() as llm:
                # Auto-detect category
                market = {"condition_id": "", "question": question}
                category = await categorize_market(market, llm)
                self.post_message(SignalUpdate(
                    market_question=question,
                    stage="collecting",
                    detail=f"category={category}",
                    source="aggregator",
                ))

                # Extract resolution params for crypto
                resolution_kwargs: dict = {}
                if category == "crypto":
                    params = await extract_resolution_params(question, category, llm)
                    if params:
                        resolution_kwargs["resolution_keywords"] = params

                # Build all 3 providers with progress callbacks
                providers = [
                    CryptoResolutionProvider(llm=llm, on_progress=_make_progress_cb("crypto")),
                    WebSearchSignalProvider(llm=llm, on_progress=_make_progress_cb("web_search")),
                    PredictionMarketsSignalProvider(llm=llm, on_progress=_make_progress_cb("prediction_markets")),
                ]

                aggregator = SignalAggregator(
                    llm=llm,
                    providers=providers,
                    on_progress=_make_progress_cb("aggregator"),
                )

                result = await aggregator.aggregate(
                    market_question=question,
                    market_category=category,
                    market_end_date="2026-12-31",
                    market_price=market_price,
                    **resolution_kwargs,
                )

                # Post aggregation result
                agg_market = {"condition_id": "manual", "question": question, "_category": category}
                self.post_message(AggregationResult(
                    market_data=agg_market,
                    aggregation=result,
                    market_question=question,
                ))

                if result is None:
                    self.post_message(SignalUpdate(
                        market_question=question,
                        stage="skip",
                        detail="Market skipped (insufficient signals or low confidence)",
                        source="aggregator",
                        done=True,
                    ))
                    self.post_message(CommandResult(
                        command="aggregate",
                        success=True,
                        output=f"Question: {question}\nResult: SKIPPED (insufficient signals or low frontier confidence)",
                    ))
                else:
                    self.post_message(SignalUpdate(
                        market_question=question,
                        stage="done",
                        detail=result.reasoning[:100],
                        probability=result.final_probability,
                        confidence=result.confidence,
                        data_points=result.total_data_points,
                        done=True,
                        source="aggregator",
                    ))
                    output_lines = [
                        f"Question: {question}",
                        f"Category: {category}",
                        f"Market price: {market_price}",
                        f"",
                        f"FINAL PROBABILITY: {result.final_probability:.2%}",
                        f"Confidence: {result.confidence:.2%}",
                        f"Preliminary estimate: {result.preliminary_probability:.2%}",
                        f"Signals agreement: {result.signals_agreement}",
                        f"Market assessment: {result.market_efficiency}",
                        f"Reasoning: {result.reasoning}",
                        f"",
                        f"Individual signals ({len(result.individual_signals)}):",
                    ]
                    for sig in result.individual_signals:
                        output_lines.append(
                            f"  [{sig.source}] P={sig.probability} C={sig.confidence} "
                            f"({sig.data_points} pts): {sig.reasoning[:80]}"
                        )

                    # Kelly bet sizing for manual aggregate
                    try:
                        agg_market = {"condition_id": "manual", "question": question, "_category": category}
                        decision = await self._run_kelly(agg_market, "manual", question, result, market_price)

                        output_lines.append("")
                        if decision and decision.should_trade:
                            depth_info = ""
                            if decision.depth_adjusted:
                                depth_info = f" [depth-adjusted, slippage={decision.depth_slippage:.1%}]"
                            elif decision.depth_total_usd > 0:
                                depth_info = f" [depth=${decision.depth_total_usd:.0f}, slippage={decision.depth_slippage:.1%}]"
                            output_lines.append(
                                f"KELLY: {decision.side} | bet=${decision.bet_size_usd:.2f} "
                                f"edge={decision.edge:.1%} EV=${decision.expected_value:.2f} "
                                f"(kelly={decision.adjusted_fraction:.1%}){depth_info}"
                            )
                        elif decision:
                            output_lines.append(
                                f"KELLY: SKIP — {decision.skip_reason} "
                                f"(edge={decision.edge:.3f})"
                            )
                    except Exception as e:
                        logger.warning("Kelly sizing failed: %s", e)
                        output_lines.append(f"\nKELLY: Error — {e}")

                    self.post_message(CommandResult(
                        command="aggregate",
                        success=True,
                        output="\n".join(output_lines),
                    ))

            self.refresh_costs()
        except Exception as e:
            logger.error("Aggregate failed: %s", e, exc_info=True)
            self.post_message(SignalUpdate(
                market_question=question,
                stage="error",
                detail=str(e)[:100],
                done=True,
                source="aggregator",
            ))
            self.post_message(CommandResult(
                command="aggregate",
                success=False,
                output=f"Error: {e}",
            ))

    # -----------------------------------------------------------------
    # Message routing — forward app-level messages to widgets
    # -----------------------------------------------------------------

    def on_log_message(self, event: LogMessage) -> None:
        try:
            self.query_one(LogPanel).on_log_message(event)
        except Exception:
            pass

    def on_connection_update(self, event: ConnectionUpdate) -> None:
        try:
            self.query_one(DashboardPanel).on_connection_update(event)
        except Exception:
            pass

    def on_wallet_update(self, event: WalletUpdate) -> None:
        try:
            self.query_one(DashboardPanel).on_wallet_update(event)
        except Exception:
            pass

    def on_markets_update(self, event: MarketsUpdate) -> None:
        try:
            self.query_one(MarketsPanel).on_markets_update(event)
        except Exception:
            pass

    def on_cost_update(self, event: CostUpdate) -> None:
        try:
            self.query_one(DashboardPanel).on_cost_update(event)
        except Exception:
            pass

    def on_pipeline_stage_update(self, event: PipelineStageUpdate) -> None:
        try:
            self.query_one(MarketsPanel).on_pipeline_stage_update(event)
        except Exception:
            pass

    def on_pipeline_complete(self, event: PipelineComplete) -> None:
        try:
            self.query_one(MarketsPanel).on_pipeline_complete(event)
        except Exception:
            pass

    def on_signal_update(self, event: SignalUpdate) -> None:
        try:
            self.query_one(AnalysisListPanel).on_signal_update(event)
        except Exception:
            pass

    def on_bot_status_update(self, event: BotStatusUpdate) -> None:
        try:
            self.query_one(DashboardPanel).on_bot_status_update(event)
        except Exception:
            pass

    def on_aggregation_result(self, event: AggregationResult) -> None:
        try:
            self.query_one(AnalysisListPanel).on_aggregation_result(event)
        except Exception:
            pass

    def on_drill_down_request(self, event: DrillDownRequest) -> None:
        """Switch to analysis tab and show the market detail."""
        from tui.widgets.analysis_list_panel import AnalysisEntry
        # Create a temporary entry for drill-down from Markets tab
        entry = AnalysisEntry(
            condition_id=event.market_data.get("conditionId", event.market_data.get("condition_id", "drilldown")),
            market_data=event.market_data,
            status="done" if event.aggregation else "waiting",
            aggregation=event.aggregation,
        )
        tc = self.query_one(TabbedContent)
        tc.active = "analysis"
        try:
            self.query_one(AnalysisDetailPanel).show_entry(entry)
        except Exception:
            pass

    def on_command_result(self, event: CommandResult) -> None:
        try:
            self.query_one(LogPanel).on_command_result(event)
        except Exception:
            pass
        tc = self.query_one(TabbedContent)
        tc.active = "logs"

    def on_bot_process_update(self, event: BotProcessUpdate) -> None:
        try:
            self.query_one(DashboardPanel).on_bot_process_update(event)
        except Exception:
            pass

    def on_batch_update(self, event: BatchUpdate) -> None:
        try:
            self.query_one(AnalysisListPanel).on_batch_update(event)
        except Exception:
            pass

    def on_bet_update(self, event: BetUpdate) -> None:
        try:
            self.query_one(AnalysisListPanel).on_bet_update(event)
        except Exception:
            pass

    def on_analysis_selection_changed(self, event: AnalysisSelectionChanged) -> None:
        """Show detail for the selected analysis entry."""
        try:
            self.query_one(AnalysisDetailPanel).show_entry(event.entry)
        except Exception:
            pass
