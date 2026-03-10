"""Markets tab — browse active markets from Gamma API."""

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import DataTable, Button, Static
from textual.reactive import reactive

from tui.messages import MarketsUpdate


class MarketsPanel(Vertical):
    """Active markets browser with sortable DataTable."""

    DEFAULT_CSS = """
    MarketsPanel {
        height: 1fr;
    }
    MarketsPanel .controls {
        height: 3;
        dock: top;
        padding: 0 1;
    }
    MarketsPanel .controls Button {
        min-width: 12;
        margin: 0 1;
    }
    MarketsPanel .controls Static {
        margin: 0 1;
        content-align: center middle;
    }
    MarketsPanel DataTable {
        height: 1fr;
    }
    """

    sort_field: reactive[str] = reactive("volume24hr")
    limit: reactive[int] = reactive(20)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="controls"):
            yield Button("Volume", id="sort-volume", variant="primary")
            yield Button("Liquidity", id="sort-liquidity")
            yield Button("Newest", id="sort-start")
            yield Static(" | ", classes="sep")
            yield Button("20", id="limit-20", variant="primary")
            yield Button("50", id="limit-50")
            yield Button("100", id="limit-100")
            yield Static(" | ", classes="sep")
            yield Button("Refresh (r)", id="refresh-markets")
        yield DataTable(id="markets-table")

    def on_mount(self) -> None:
        table = self.query_one("#markets-table", DataTable)
        table.add_columns("#", "YES", "NO", "Liquidity", "Vol 24H", "Expires", "Question")
        table.cursor_type = "row"

    def on_markets_update(self, event: MarketsUpdate) -> None:
        table = self.query_one("#markets-table", DataTable)
        table.clear()
        for i, m in enumerate(event.markets):
            question = m.get("question", "???")
            if len(question) > 50:
                question = question[:47] + "..."

            liq = float(m.get("liquidity") or 0)
            vol = float(m.get("volume24hr") or 0)
            end_raw = m.get("endDate", "")
            end_str = end_raw[:10] if end_raw else "---"

            prices_raw = m.get("outcomePrices", "[]")
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            except json.JSONDecodeError:
                prices = []
            yes_p = f"{float(prices[0]):.1%}" if len(prices) > 0 else "---"
            no_p = f"{float(prices[1]):.1%}" if len(prices) > 1 else "---"

            table.add_row(
                str(i + 1),
                yes_p,
                no_p,
                f"${liq:,.0f}",
                f"${vol:,.0f}",
                end_str,
                question,
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        # Sort buttons
        sort_map = {
            "sort-volume": "volume24hr",
            "sort-liquidity": "liquidity",
            "sort-start": "startDate",
        }
        if btn_id in sort_map:
            self.sort_field = sort_map[btn_id]
            for btn in self.query(".controls Button"):
                if btn.id and btn.id.startswith("sort-"):
                    btn.variant = "primary" if btn.id == btn_id else "default"
            self.app.refresh_markets()
            return

        # Limit buttons
        limit_map = {"limit-20": 20, "limit-50": 50, "limit-100": 100}
        if btn_id in limit_map:
            self.limit = limit_map[btn_id]
            for btn in self.query(".controls Button"):
                if btn.id and btn.id.startswith("limit-"):
                    btn.variant = "primary" if btn.id == btn_id else "default"
            self.app.refresh_markets()
            return

        if btn_id == "refresh-markets":
            self.app.refresh_markets()
