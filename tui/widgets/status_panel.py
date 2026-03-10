"""Home tab — connection health, wallet, positions, uptime, bot toggle."""

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Button
from textual.reactive import reactive

from tui.messages import BotStatusUpdate, BotToggle, ConnectionUpdate, WalletUpdate
from tui.state import ConnectionStatus


class StatusPanel(Vertical):
    """Home tab showing connection health, wallet info, and bot start/stop."""

    DEFAULT_CSS = """
    StatusPanel {
        height: 1fr;
        padding: 1 2;
        background: #0a0a0a;
    }
    StatusPanel .section-title {
        text-style: bold;
        color: #00ff41;
        margin: 1 0 0 0;
    }
    StatusPanel .conn-row {
        height: 1;
        margin: 0 0 0 2;
        color: #00cc33;
    }
    StatusPanel .healthy {
        color: #00ff41;
    }
    StatusPanel .unhealthy {
        color: #ff0040;
    }
    StatusPanel .wallet-info {
        margin: 0 0 0 2;
    }
    StatusPanel .kv-line {
        height: 1;
        margin: 0 0 0 2;
        color: #00cc33;
    }
    StatusPanel #bot-toggle-row {
        height: 3;
        margin: 1 0 0 2;
    }
    StatusPanel #bot-toggle {
        min-width: 20;
    }
    StatusPanel #bot-toggle.bot-stopped {
        background: #002200;
        color: #00ff41;
        border: tall #00ff41;
    }
    StatusPanel #bot-toggle.bot-running {
        background: #330000;
        color: #ff0040;
        border: tall #ff0040;
    }
    StatusPanel #bot-status-line {
        height: 1;
        margin: 0 0 0 2;
        color: #00cc33;
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
        icon = "[#00ff41]\u25cf[/#00ff41]" if status.healthy else "[#ff0040]\u25cf[/#ff0040]"
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
            gas_str = "[#00ff41]OK[/#00ff41]" if self._has_gas else "[#ff0040]LOW[/#ff0040]"
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
                status_line.update("[#00ff41]● RUNNING[/#00ff41]  Pipeline loop active — press [b]s[/b] or click to stop")
            else:
                btn.label = "Start Bot"
                btn.variant = "success"
                btn.remove_class("bot-running")
                btn.add_class("bot-stopped")
                status_line.update("[#ff0040]● STOPPED[/#ff0040]   Press [b]s[/b] or click Start to begin")
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
