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
from textual.widgets import Header, Footer, TabbedContent, TabPane
from textual.worker import Worker, get_current_worker

# Ensure project root is on sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tui.log_handler import TUILogHandler
from tui.messages import (
    AggregationResult,
    BotStatusUpdate,
    BotToggle,
    CommandResult,
    ConnectionUpdate,
    CostUpdate,
    DrillDownRequest,
    LogMessage,
    MarketsUpdate,
    PipelineComplete,
    PipelineStageUpdate,
    SignalUpdate,
    WalletUpdate,
)
from tui.state import ConnectionStatus, PipelineProgress
from tui.widgets.command_bar import CommandBar
from tui.widgets.costs_panel import CostsPanel
from tui.widgets.log_panel import LogPanel
from tui.widgets.markets_panel import MarketsPanel
from tui.widgets.pipeline_panel import PipelinePanel
from tui.widgets.signals_panel import SignalsPanel
from tui.widgets.status_panel import StatusPanel

logger = logging.getLogger(__name__)


class TUIApp(App):
    """Polymarket Bot — Real-Time TUI Dashboard."""

    TITLE = "Polymarket Bot"
    SUB_TITLE = "Signal-Based Trading Dashboard"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("1", "switch_tab('home')", "Home", show=True),
        Binding("2", "switch_tab('markets')", "Markets", show=True),
        Binding("3", "switch_tab('filter')", "Filter", show=True),
        Binding("4", "switch_tab('costs')", "Costs", show=True),
        Binding("5", "switch_tab('signals')", "Signals", show=True),
        Binding("6", "switch_tab('logs')", "Logs", show=True),
        Binding("s", "toggle_bot", "Start/Stop"),
        Binding("f", "run_pipeline", "Run Filter"),
        Binding("a", "run_aggregate_default", "Aggregate"),
        Binding("r", "refresh", "Refresh"),
        Binding("colon", "toggle_command_bar", "Command", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="home"):
            with TabPane("Home", id="home"):
                yield StatusPanel()
            with TabPane("Markets", id="markets"):
                yield MarketsPanel()
            with TabPane("Filter", id="filter"):
                yield PipelinePanel()
            with TabPane("Costs", id="costs"):
                yield CostsPanel()
            with TabPane("Signals", id="signals"):
                yield SignalsPanel()
            with TabPane("Logs", id="logs"):
                yield LogPanel()
        yield CommandBar()
        yield Footer()

    _bot_running: bool = False

    def on_mount(self) -> None:
        """Attach log handler and kick off background workers."""
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
            self._start_health_loop()
            self._start_pipeline_loop()
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

    def action_run_pipeline(self) -> None:
        self.run_filter_pipeline()

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
    # Pipeline auto-repeat loop
    # -----------------------------------------------------------------

    PIPELINE_INTERVAL_SECONDS = int(os.environ.get("PIPELINE_INTERVAL_SECONDS", "600"))  # 10 min default

    def _start_pipeline_loop(self) -> None:
        self.run_worker(self._pipeline_loop(), exclusive=True, group="pipeline-loop")

    async def _pipeline_loop(self) -> None:
        """Run the filter pipeline on a recurring interval while bot is running."""
        await asyncio.sleep(5)
        while self._bot_running:
            logger.info("Auto-pipeline: starting scheduled run (interval=%ds)", self.PIPELINE_INTERVAL_SECONDS)
            try:
                await self._run_filter_pipeline()
            except Exception as e:
                logger.error("Auto-pipeline failed: %s", e, exc_info=True)
            # Sleep in short increments so cancellation is responsive
            for _ in range(self.PIPELINE_INTERVAL_SECONDS // 5):
                if not self._bot_running:
                    break
                await asyncio.sleep(5)

    # -----------------------------------------------------------------
    # Pipeline worker
    # -----------------------------------------------------------------

    def run_filter_pipeline(self) -> None:
        self.run_worker(self._run_filter_pipeline(), exclusive=True, group="pipeline")

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
            total_stages=5,
            items_processed=processed,
            items_total=total,
            started_at=started_at or datetime.now(timezone.utc),
            stage_started_at=datetime.now(timezone.utc),
        )))

    async def _run_filter_pipeline(self) -> None:
        """Execute the full filter pipeline with progress updates.

        Stages 0-4: Market discovery, filtering, categorization, extraction, ranking.
        Stage 5 (if bot is running): Signal aggregation on top-ranked markets.
        """
        from core.llm import LLMClient
        from strategy.market_filter import (
            batch_categorize_markets,
            categorize_market,
            discover_markets,
            extract_resolution_params,
            filter_markets,
            rank_candidates,
        )

        pipeline_start = datetime.now(timezone.utc)

        try:
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

                # Stage 3: Extract resolution params
                self._post_stage("extract", 3, processed=0, total=len(filtered), started_at=pipeline_start)
                for i, m in enumerate(filtered):
                    cat = m.get("_category", "")
                    if cat in ("economics", "crypto"):
                        params = await extract_resolution_params(
                            m.get("question", ""), cat, llm,
                            condition_id=m.get("condition_id", ""),
                        )
                        if params:
                            m["_resolution_params"] = params
                    self._post_stage("extract", 3, processed=i + 1, total=len(filtered), started_at=pipeline_start)

                # Stage 4: Rank
                self._post_stage("rank", 4, started_at=pipeline_start)
                ranked = rank_candidates(filtered)

                self.post_message(PipelineComplete(
                    results=ranked,
                    discovered=len(markets),
                    filtered=len(filtered),
                ))

                # Stage 5: Signal aggregation (only when bot is running)
                if self._bot_running and ranked:
                    from signals.aggregator import SignalAggregator
                    from signals.news import NewsSignalProvider
                    from signals.polling import PollingSignalProvider
                    from signals.resolution_crypto import CryptoResolutionProvider
                    from signals.resolution_econ import EconomicsResolutionProvider

                    def _agg_progress(mkt_question: str, stage: str, detail: str = "") -> None:
                        self.post_message(SignalUpdate(
                            market_question=mkt_question,
                            stage=stage,
                            detail=detail,
                            source="aggregator",
                        ))

                    top_markets = ranked[:5]  # Analyze top 5
                    logger.info("Running signal aggregation on %d top markets", len(top_markets))

                    for i, mkt in enumerate(top_markets):
                        if not self._bot_running:
                            break

                        question = mkt.get("question", "")
                        category = mkt.get("_category", "")
                        end_date = mkt.get("endDate", "2026-12-31")

                        # Get market price from outcome prices
                        outcome_prices = mkt.get("outcomePrices", "[]")
                        if isinstance(outcome_prices, str):
                            import json as _json
                            try:
                                prices = _json.loads(outcome_prices)
                            except (ValueError, TypeError):
                                prices = []
                        else:
                            prices = outcome_prices
                        market_price = float(prices[0]) if prices else 0.50

                        # Build resolution kwargs
                        resolution_kwargs: dict = {}
                        res_params = mkt.get("_resolution_params")
                        if res_params:
                            resolution_kwargs["resolution_keywords"] = res_params

                        providers = [
                            NewsSignalProvider(llm=llm),
                            PollingSignalProvider(llm=llm),
                            EconomicsResolutionProvider(llm=llm),
                            CryptoResolutionProvider(llm=llm),
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
                                **resolution_kwargs,
                            )

                            # Post aggregation result for drill-down storage
                            self.post_message(AggregationResult(
                                market_data=mkt,
                                aggregation=agg_result,
                                market_question=question,
                            ))

                            if agg_result is not None:
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
                            else:
                                self.post_message(SignalUpdate(
                                    market_question=question,
                                    stage="skip",
                                    detail="Market skipped by aggregator",
                                    source="aggregator",
                                    done=True,
                                ))
                        except Exception as e:
                            logger.warning("Aggregation failed for '%s': %s", question[:50], e)
                            self.post_message(SignalUpdate(
                                market_question=question,
                                stage="error",
                                detail=str(e)[:100],
                                source="aggregator",
                                done=True,
                            ))

                # Refresh costs after pipeline (it made LLM calls)
                self.refresh_costs()

        except Exception as e:
            logger.error("Pipeline failed: %s", e, exc_info=True)
            self.post_message(PipelineStageUpdate(PipelineProgress(running=False)))
            self.post_message(CommandResult(
                command="pipeline",
                success=False,
                output=f"Pipeline failed: {e}",
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
        # Switch to signals tab
        tc = self.query_one(TabbedContent)
        tc.active = "signals"

    async def _do_signal_test(self, question: str) -> None:
        """Run all signal providers on a question and emit live updates."""
        from core.llm import LLMClient
        from signals.news import NewsSignalProvider
        from signals.polling import PollingSignalProvider
        from signals.resolution_crypto import CryptoResolutionProvider
        from signals.resolution_econ import EconomicsResolutionProvider
        from strategy.market_filter import categorize_market, extract_resolution_params

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

                # Extract resolution params for econ/crypto
                resolution_kwargs: dict = {}
                if category in ("economics", "crypto"):
                    params = await extract_resolution_params(question, category, llm)
                    if params:
                        resolution_kwargs["resolution_keywords"] = params

                # Create all 4 providers with source-tagged progress callbacks
                news_provider = NewsSignalProvider(llm=llm, on_progress=_make_progress_cb("news"))
                polling_provider = PollingSignalProvider(llm=llm, on_progress=_make_progress_cb("polling"))
                econ_provider = EconomicsResolutionProvider(llm=llm, on_progress=_make_progress_cb("econ"))
                crypto_provider = CryptoResolutionProvider(llm=llm, on_progress=_make_progress_cb("crypto"))

                # Run all providers in parallel
                results = await asyncio.gather(
                    news_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    polling_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    econ_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    crypto_provider.get_signal(question, category, "2026-12-31", **resolution_kwargs),
                    return_exceptions=True,
                )

                provider_names = ["news", "polling", "resolution_econ", "resolution_crypto"]
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
        tc = self.query_one(TabbedContent)
        tc.active = "signals"

    async def _do_aggregate(self, question: str, market_price: float) -> None:
        """Run full aggregation pipeline: all signals + frontier model."""
        from core.llm import LLMClient
        from signals.aggregator import SignalAggregator
        from signals.news import NewsSignalProvider
        from signals.polling import PollingSignalProvider
        from signals.resolution_crypto import CryptoResolutionProvider
        from signals.resolution_econ import EconomicsResolutionProvider
        from strategy.market_filter import categorize_market, extract_resolution_params

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

                # Extract resolution params for econ/crypto
                resolution_kwargs: dict = {}
                if category in ("economics", "crypto"):
                    params = await extract_resolution_params(question, category, llm)
                    if params:
                        resolution_kwargs["resolution_keywords"] = params

                # Build providers with progress callbacks
                providers = [
                    NewsSignalProvider(llm=llm, on_progress=_make_progress_cb("news")),
                    PollingSignalProvider(llm=llm, on_progress=_make_progress_cb("polling")),
                    EconomicsResolutionProvider(llm=llm, on_progress=_make_progress_cb("econ")),
                    CryptoResolutionProvider(llm=llm, on_progress=_make_progress_cb("crypto")),
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

                # Post aggregation result for drill-down storage
                agg_market = {"condition_id": "", "question": question, "_category": category}
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
        """Route log messages to the log panel."""
        try:
            self.query_one(LogPanel).on_log_message(event)
        except Exception:
            pass

    def on_connection_update(self, event: ConnectionUpdate) -> None:
        """Route connection updates to the status panel."""
        try:
            self.query_one(StatusPanel).on_connection_update(event)
        except Exception:
            pass

    def on_wallet_update(self, event: WalletUpdate) -> None:
        """Route wallet updates to the status panel."""
        try:
            self.query_one(StatusPanel).on_wallet_update(event)
        except Exception:
            pass

    def on_markets_update(self, event: MarketsUpdate) -> None:
        """Route markets data to the markets panel."""
        try:
            self.query_one(MarketsPanel).on_markets_update(event)
        except Exception:
            pass

    def on_cost_update(self, event: CostUpdate) -> None:
        """Route cost data to the costs panel."""
        try:
            self.query_one(CostsPanel).on_cost_update(event)
        except Exception:
            pass

    def on_pipeline_stage_update(self, event: PipelineStageUpdate) -> None:
        """Route pipeline progress to the pipeline panel."""
        try:
            self.query_one(PipelinePanel).on_pipeline_stage_update(event)
        except Exception:
            pass

    def on_pipeline_complete(self, event: PipelineComplete) -> None:
        """Route pipeline completion to the pipeline panel."""
        try:
            self.query_one(PipelinePanel).on_pipeline_complete(event)
        except Exception:
            pass

    def on_signal_update(self, event: SignalUpdate) -> None:
        """Route signal updates to the signals panel."""
        try:
            self.query_one(SignalsPanel).on_signal_update(event)
        except Exception:
            pass

    def on_bot_status_update(self, event: BotStatusUpdate) -> None:
        """Route bot status to the status panel."""
        try:
            self.query_one(StatusPanel).on_bot_status_update(event)
        except Exception:
            pass

    def on_aggregation_result(self, event: AggregationResult) -> None:
        """Route aggregation results to the signals panel for drill-down storage."""
        try:
            self.query_one(SignalsPanel).on_aggregation_result(event)
        except Exception:
            pass

    def on_drill_down_request(self, event: DrillDownRequest) -> None:
        """Push a detail screen when a row is selected."""
        from tui.widgets.detail_screen import MarketDetailScreen
        self.push_screen(MarketDetailScreen(
            market_data=event.market_data,
            aggregation=event.aggregation,
        ))

    def on_command_result(self, event: CommandResult) -> None:
        """Route command results to the log panel and switch to logs tab."""
        try:
            self.query_one(LogPanel).on_command_result(event)
        except Exception:
            pass
        tc = self.query_one(TabbedContent)
        tc.active = "logs"
