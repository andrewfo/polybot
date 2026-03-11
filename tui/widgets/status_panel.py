"""Home tab — connection health, wallet, positions, uptime, bot toggle, process status."""

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Button
from textual.reactive import reactive

from tui.messages import BotProcessUpdate, BotStatusUpdate, BotToggle, ConnectionUpdate, WalletUpdate
from tui.state import ConnectionStatus


class StatusPanel(Vertical):
    """Home tab showing connection health, wallet info, bot start/stop, and current process."""

    DEFAULT_CSS = """
    StatusPanel {
        height: 1fr;
        padding: 1 2;
        background: #0a1628;
    }
    StatusPanel .section-title {
        text-style: bold;
        color: #e0e8f0;
        margin: 1 0 0 0;
    }
    StatusPanel .conn-row {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    StatusPanel .wallet-info {
        margin: 0 0 0 2;
    }
    StatusPanel .kv-line {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    StatusPanel #bot-toggle-row {
        height: 3;
        margin: 1 0 0 2;
    }
    StatusPanel #bot-toggle {
        min-width: 20;
    }
    StatusPanel #bot-toggle.bot-stopped {
        background: #1a3a2a;
        color: #44aa66;
        border: tall #44aa66;
    }
    StatusPanel #bot-toggle.bot-running {
        background: #3a1a1a;
        color: #cc4444;
        border: tall #cc4444;
    }
    StatusPanel #bot-status-line {
        height: 1;
        margin: 0 0 0 2;
        color: #8899aa;
    }
    StatusPanel .process-box {
        height: auto;
        margin: 0 0 0 2;
        padding: 1 2;
        border: solid #2a3a5a;
        background: #0d1f3c;
    }
    StatusPanel #process-phase {
        height: 1;
        text-style: bold;
        color: #e0e8f0;
    }
    StatusPanel #process-detail {
        height: 1;
        color: #8899aa;
    }
    StatusPanel #process-cycle {
        height: 1;
        color: #667788;
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
        yield Static("BOT CONTROL", classes="section-title")
        yield Static(id="bot-status-line")
        with Horizontal(id="bot-toggle-row"):
            yield Button("Start Bot", id="bot-toggle", variant="success", classes="bot-stopped")

        yield Static("CURRENT PROCESS", classes="section-title")
        with Vertical(classes="process-box"):
            yield Static("Idle", id="process-phase")
            yield Static("Bot is stopped. Press s to start.", id="process-detail")
            yield Static("", id="process-cycle")

        yield Static("CONNECTIONS", classes="section-title")
        yield Static(id="conn-polymarket", classes="conn-row")
        yield Static(id="conn-polygon", classes="conn-row")
        yield Static(id="conn-openrouter", classes="conn-row")

        yield Static("WALLET", classes="section-title")
        yield Static(id="wallet-address", classes="kv-line")
        yield Static(id="wallet-usdc", classes="kv-line")
        yield Static(id="wallet-matic", classes="kv-line")
        yield Static(id="wallet-gas", classes="kv-line")

        yield Static("POSITIONS", classes="section-title")
        yield Static(id="positions-count", classes="kv-line")

        yield Static("BOT INFO", classes="section-title")
        yield Static(id="bot-mode", classes="kv-line")
        yield Static(id="bot-uptime", classes="kv-line")

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
            self.query_one("#bot-mode", Static).update(f"Mode:           Paper Trading")
        except Exception:
            pass

        self._refresh_uptime()

    def _refresh_uptime(self) -> None:
        try:
            elapsed = datetime.now(timezone.utc) - self._started_at
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            self.query_one("#bot-uptime", Static).update(f"Uptime:         {hours}h {minutes}m {seconds}s")
        except Exception:
            pass

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
                # Reset process display
                try:
                    self.query_one("#process-phase", Static).update("Idle")
                    self.query_one("#process-detail", Static).update("Bot is stopped. Press s to start.")
                    self.query_one("#process-cycle", Static).update("")
                except Exception:
                    pass
        except Exception:
            pass

    def on_mount(self) -> None:
        self._refresh_display()
        self._refresh_bot_toggle()
        self.set_interval(10, self._refresh_uptime)

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
        """Update the current process display on the home tab."""
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
