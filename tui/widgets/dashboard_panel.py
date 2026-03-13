"""Dashboard tab — bot control, health, wallet, and LLM costs in one panel."""

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Button, DataTable
from textual.reactive import reactive

from tui.messages import (
    BotProcessUpdate,
    BotStatusUpdate,
    BotToggle,
    ConnectionUpdate,
    CostUpdate,
    ExecutionUpdate,
    WalletUpdate,
)
from tui.state import ConnectionStatus


class DashboardPanel(Vertical):
    """Combined Home + Costs panel: bot control, health, wallet, LLM spend."""

    DEFAULT_CSS = """
    DashboardPanel {
        height: 1fr;
        padding: 1 2;
        background: #0a1628;
    }
    DashboardPanel .section-title {
        text-style: bold;
        color: #e0e8f0;
        margin: 1 0 0 0;
    }
    DashboardPanel .conn-row {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    DashboardPanel .kv-line {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    DashboardPanel #bot-toggle-row {
        height: 3;
        margin: 1 0 0 2;
    }
    DashboardPanel #bot-toggle {
        min-width: 20;
    }
    DashboardPanel #bot-toggle.bot-stopped {
        background: #1a3a2a;
        color: #44aa66;
        border: tall #44aa66;
    }
    DashboardPanel #bot-toggle.bot-running {
        background: #3a1a1a;
        color: #cc4444;
        border: tall #cc4444;
    }
    DashboardPanel #bot-status-line {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    DashboardPanel .process-box {
        height: auto;
        margin: 0 0 0 2;
        padding: 1 2;
        border: solid #2a3a5a;
        background: #0d1f3c;
    }
    DashboardPanel #process-phase {
        height: 1;
        text-style: bold;
        color: #e0e8f0;
    }
    DashboardPanel #process-detail {
        height: 1;
        color: #8899aa;
    }
    DashboardPanel #process-cycle {
        height: 1;
        color: #667788;
    }
    DashboardPanel .cost-summary {
        margin: 0 0 0 2;
        height: 1;
        color: #8899aa;
    }
    DashboardPanel DataTable {
        height: auto;
        max-height: 10;
        margin: 0 0 0 0;
    }
    """

    bot_running: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self._connections: dict[str, ConnectionStatus] = {
            "Polymarket API": ConnectionStatus(name="Polymarket API", healthy=False, last_check=None, error="Not checked"),
            "Polygon RPC": ConnectionStatus(name="Polygon RPC", healthy=False, last_check=None, error="Not checked"),
            "OpenRouter": ConnectionStatus(name="OpenRouter", healthy=False, last_check=None, error="Not checked"),
        }
        self._wallet_address = "---"
        self._usdc = 0.0
        self._matic = 0.0
        self._has_gas = False
        self._positions_count = 0
        self._started_at = datetime.now(timezone.utc)

    def compose(self) -> ComposeResult:
        # Bot control section
        yield Static("BOT CONTROL", classes="section-title")
        yield Static(id="bot-status-line")
        with Horizontal(id="bot-toggle-row"):
            yield Button("Start Bot", id="bot-toggle", variant="success", classes="bot-stopped")

        yield Static("CURRENT PROCESS", classes="section-title")
        with Vertical(classes="process-box"):
            yield Static("Idle", id="process-phase")
            yield Static("Bot is stopped. Press s to start.", id="process-detail")
            yield Static("", id="process-cycle")

        # Health + wallet in compact form
        yield Static("CONNECTIONS", classes="section-title")
        yield Static(id="conn-polymarket", classes="conn-row")
        yield Static(id="conn-polygon", classes="conn-row")
        yield Static(id="conn-openrouter", classes="conn-row")

        yield Static("WALLET", classes="section-title")
        yield Static(id="wallet-address", classes="kv-line")
        yield Static(id="wallet-usdc", classes="kv-line")
        yield Static(id="wallet-matic", classes="kv-line")
        yield Static(id="wallet-gas", classes="kv-line")
        yield Static(id="positions-count", classes="kv-line")

        # Trading summary
        yield Static("TRADING", classes="section-title")
        yield Static("No trades yet", id="trading-summary", classes="kv-line")
        yield DataTable(id="trades-table")

        # LLM costs summary
        yield Static("LLM COSTS", classes="section-title")
        yield Static("Today: $0.0000    Month: $0.0000    Calls: 0", id="cost-summary-line", classes="kv-line")
        yield DataTable(id="model-table")
        yield DataTable(id="task-table")

    def _format_conn(self, status: ConnectionStatus) -> str:
        icon = "[#44aa66]\u25cf[/#44aa66]" if status.healthy else "[#cc4444]\u25cf[/#cc4444]"
        check_str = status.last_check.strftime("%H:%M:%S") if status.last_check else "never"
        err = f"  ({status.error})" if status.error and not status.healthy else ""
        return f"{icon} {status.name:<20} last check: {check_str}{err}"

    def _refresh_display(self) -> None:
        conn_ids = {
            "Polymarket API": "conn-polymarket",
            "Polygon RPC": "conn-polygon",
            "OpenRouter": "conn-openrouter",
        }
        for name, widget_id in conn_ids.items():
            status = self._connections.get(name)
            if status:
                try:
                    self.query_one(f"#{widget_id}", Static).update(self._format_conn(status))
                except Exception:
                    pass

        try:
            self.query_one("#wallet-address", Static).update(f"Address:        {self._wallet_address}")
            self.query_one("#wallet-usdc", Static).update(f"USDC Balance:   ${self._usdc:,.2f}")
            self.query_one("#wallet-matic", Static).update(f"MATIC Balance:  {self._matic:.4f}")
            gas_str = "[#44aa66]OK[/#44aa66]" if self._has_gas else "[#cc4444]LOW[/#cc4444]"
            self.query_one("#wallet-gas", Static).update(f"Gas Status:     {gas_str}")
            self.query_one("#positions-count", Static).update(f"Open Positions: {self._positions_count}")
        except Exception:
            pass

        self._refresh_uptime()

    def _refresh_uptime(self) -> None:
        pass  # Uptime removed in favour of compact layout

    def on_mount(self) -> None:
        # Init trades table
        trades_table = self.query_one("#trades-table", DataTable)
        trades_table.add_columns("Status", "Side", "Price", "Size", "Market")
        trades_table.cursor_type = "row"

        # Init cost tables
        model_table = self.query_one("#model-table", DataTable)
        model_table.add_columns("Model", "Calls", "Input Tok", "Output Tok", "Cost")
        model_table.cursor_type = "row"

        task_table = self.query_one("#task-table", DataTable)
        task_table.add_columns("Task", "Calls", "Cost")
        task_table.cursor_type = "row"

        self._refresh_display()
        self._refresh_bot_toggle()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "bot-toggle":
            self.post_message(BotToggle(running=not self.bot_running))

    def on_bot_status_update(self, event: BotStatusUpdate) -> None:
        self.bot_running = event.running
        self._refresh_bot_toggle()

    def _refresh_bot_toggle(self) -> None:
        try:
            btn = self.query_one("#bot-toggle", Button)
            status_line = self.query_one("#bot-status-line", Static)
            if self.bot_running:
                btn.label = "Stop Bot"
                btn.variant = "error"
                btn.remove_class("bot-stopped")
                btn.add_class("bot-running")
                status_line.update("[#44aa66]\u25cf RUNNING[/#44aa66]  Pipeline loop active \u2014 press [b]s[/b] or click to stop")
            else:
                btn.label = "Start Bot"
                btn.variant = "success"
                btn.remove_class("bot-running")
                btn.add_class("bot-stopped")
                status_line.update("[#cc4444]\u25cf STOPPED[/#cc4444]   Press [b]s[/b] or click Start to begin")
                try:
                    self.query_one("#process-phase", Static).update("Idle")
                    self.query_one("#process-detail", Static).update("Bot is stopped. Press s to start.")
                    self.query_one("#process-cycle", Static).update("")
                except Exception:
                    pass
        except Exception:
            pass

    def on_connection_update(self, event: ConnectionUpdate) -> None:
        self._connections[event.status.name] = event.status
        self._refresh_display()

    def on_wallet_update(self, event: WalletUpdate) -> None:
        self._usdc = event.usdc
        self._matic = event.matic
        self._has_gas = event.has_gas
        self._positions_count = event.positions_count
        if event.address:
            self._wallet_address = event.address
        self._refresh_display()

    def on_bot_process_update(self, event: BotProcessUpdate) -> None:
        try:
            phase_label = self.query_one("#process-phase", Static)
            detail_label = self.query_one("#process-detail", Static)
            cycle_label = self.query_one("#process-cycle", Static)

            phase_icons = {
                "idle": "\u23f8 Idle",
                "filtering": "\u2699 Filtering Markets...",
                "aggregating": "\u26a1 Aggregating Signals...",
                "waiting": "\u23f3 Waiting for next cycle...",
            }
            phase_label.update(phase_icons.get(event.phase, event.phase))
            detail_label.update(event.detail)
            if event.cycle > 0:
                cycle_label.update(f"Cycle #{event.cycle}")
            else:
                cycle_label.update("")
        except Exception:
            pass

    def on_execution_update(self, event: ExecutionUpdate) -> None:
        """Add executed trade to the trades table."""
        try:
            trades_table = self.query_one("#trades-table", DataTable)
            status_icons = {
                "filled": "[#44aa66]\u2714 FILLED[/#44aa66]",
                "pending": "[#ccaa44]\u23f3 PENDING[/#ccaa44]",
                "blocked": "[#cc8844]BLOCKED[/#cc8844]",
                "error": "[#cc4444]ERROR[/#cc4444]",
            }
            status_str = status_icons.get(event.status, event.status)
            mode = "[#667788]paper[/#667788]" if event.paper else "[#4488cc]live[/#4488cc]"

            price_str = f"{event.price:.4f}" if event.price > 0 else "---"
            size_str = f"${event.price * event.size:.2f}" if event.price > 0 and event.size > 0 else "---"
            reason = event.reason[:40] if event.reason else ""

            trades_table.add_row(
                f"{status_str} {mode}",
                reason if event.status in ("blocked", "error") else "BUY",
                price_str,
                size_str,
                reason if event.status in ("blocked", "error") else (event.trade_id or "")[:12],
            )

            # Update summary line
            summary = self.query_one("#trading-summary", Static)
            summary.update(f"Trades this session: {trades_table.row_count}")
        except Exception:
            pass

    def on_cost_update(self, event: CostUpdate) -> None:
        try:
            self.query_one("#cost-summary-line", Static).update(
                f"Today: ${event.daily:.4f}    Month: ${event.monthly:.4f}    Calls: {event.total_calls}"
            )
        except Exception:
            pass

        # Model breakdown
        try:
            model_table = self.query_one("#model-table", DataTable)
            model_table.clear()
            for model, calls, inp, outp, cost in event.model_breakdown:
                if len(model) > 36:
                    model = model[:33] + "..."
                model_table.add_row(model, str(calls), str(inp), str(outp), f"${cost:.4f}")
        except Exception:
            pass

        # Task breakdown
        try:
            task_table = self.query_one("#task-table", DataTable)
            task_table.clear()
            for task, calls, cost in event.task_breakdown:
                task_table.add_row(task, str(calls), f"${cost:.4f}")
        except Exception:
            pass
