"""Home tab — connection health, wallet, positions, uptime."""

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, DataTable
from textual.reactive import reactive

from tui.messages import ConnectionUpdate, WalletUpdate
from tui.state import ConnectionStatus


class StatusPanel(Vertical):
    """Home tab showing connection health, wallet info, and bot status."""

    DEFAULT_CSS = """
    StatusPanel {
        height: 1fr;
        padding: 1 2;
    }
    StatusPanel .section-title {
        text-style: bold;
        color: $text;
        margin: 1 0 0 0;
    }
    StatusPanel .conn-row {
        height: 1;
        margin: 0 0 0 2;
    }
    StatusPanel .healthy {
        color: green;
    }
    StatusPanel .unhealthy {
        color: red;
    }
    StatusPanel .wallet-info {
        margin: 0 0 0 2;
    }
    StatusPanel .kv-line {
        height: 1;
        margin: 0 0 0 2;
    }
    """

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

    def on_mount(self) -> None:
        self._refresh_display()
        self.set_interval(10, self._refresh_uptime)

    def _format_conn(self, status: ConnectionStatus) -> str:
        icon = "[green]\u25cf[/green]" if status.healthy else "[red]\u25cf[/red]"
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
            gas_str = "[green]OK[/green]" if self._has_gas else "[red]LOW[/red]"
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
