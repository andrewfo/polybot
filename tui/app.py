"""Main TUI application — Textual App with workers, keybindings, message routing."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
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
    CommandResult,
    ConnectionUpdate,
    CostUpdate,
    LogMessage,
    MarketsUpdate,
    PipelineComplete,
    PipelineStageUpdate,
    WalletUpdate,
)
from tui.state import ConnectionStatus, PipelineProgress
from tui.widgets.command_bar import CommandBar
from tui.widgets.costs_panel import CostsPanel
from tui.widgets.log_panel import LogPanel
from tui.widgets.markets_panel import MarketsPanel
from tui.widgets.pipeline_panel import PipelinePanel
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
        Binding("5", "switch_tab('logs')", "Logs", show=True),
        Binding("f", "run_pipeline", "Run Filter"),
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
            with TabPane("Logs", id="logs"):
                yield LogPanel()
        yield CommandBar()
        yield Footer()

    def on_mount(self) -> None:
        """Attach log handler and kick off background workers."""
        # Attach TUI log handler to root logger
        handler = TUILogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        logger.info("TUI Dashboard starting...")

        # Start background loops
        self._start_health_loop()
        self.refresh_markets()
        self.refresh_costs()

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
                        "model": "z-ai/glm-4.5-air:free",
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
        """Execute the full filter pipeline with progress updates."""
        from core.client import ClobClientWrapper
        from core.llm import LLMClient
        from strategy.market_filter import (
            categorize_market,
            discover_markets,
            extract_resolution_params,
            filter_markets,
            rank_candidates,
        )

        pipeline_start = datetime.now(timezone.utc)

        try:
            client = ClobClientWrapper()
            async with LLMClient() as llm:
                # Stage 0: Discover
                self._post_stage("discover", 0, started_at=pipeline_start)
                markets = await discover_markets(client)

                # Stage 1: Filter
                self._post_stage("filter", 1, total=len(markets), started_at=pipeline_start)
                filtered = await filter_markets(markets, client)

                # Stage 2: Categorize (per-item progress)
                self._post_stage("categorize", 2, processed=0, total=len(filtered), started_at=pipeline_start)
                for i, m in enumerate(filtered):
                    m["_category"] = await categorize_market(m, llm)
                    self._post_stage("categorize", 2, processed=i + 1, total=len(filtered), started_at=pipeline_start)

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

    def on_command_result(self, event: CommandResult) -> None:
        """Route command results to the log panel and switch to logs tab."""
        try:
            self.query_one(LogPanel).on_command_result(event)
        except Exception:
            pass
        tc = self.query_one(TabbedContent)
        tc.active = "logs"
