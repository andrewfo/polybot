"""Bets tab — displays Kelly-sized trade decisions from completed aggregations."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, RichLog
from rich.text import Text

from tui.messages import BetUpdate


class BetsPanel(Vertical):
    """Shows all Kelly criterion trade decisions with sizing and reasoning."""

    DEFAULT_CSS = """
    BetsPanel {
        height: 1fr;
        background: #0a1628;
    }
    BetsPanel .bets-header {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    BetsPanel .bets-status {
        height: 1;
        padding: 0 2;
        color: #667788;
    }
    BetsPanel .bets-split {
        height: 1fr;
    }
    BetsPanel .bets-table-section {
        height: 1fr;
        min-height: 60%;
    }
    BetsPanel .bets-detail-section {
        height: auto;
        max-height: 40%;
        border-top: solid #2a3a5a;
    }
    BetsPanel .bets-detail-section .section-label {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    BetsPanel .bets-detail-section RichLog {
        height: 1fr;
        background: #0d1f3c;
        border: solid #2a3a5a;
    }
    BetsPanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._bets_count = 0
        self._skips_count = 0
        self._decisions: list[object] = []  # TradeDecision objects

    def compose(self) -> ComposeResult:
        yield Label("KELLY BET SIZING", classes="bets-header")
        yield Label("Waiting for aggregated signals...", id="bets-status", classes="bets-status")
        with Vertical(classes="bets-split"):
            with Vertical(classes="bets-table-section"):
                yield DataTable(id="bets-table")
            with Vertical(classes="bets-detail-section"):
                yield Label("BET DETAILS", classes="section-label")
                yield RichLog(
                    id="bets-detail",
                    wrap=True,
                    highlight=True,
                    markup=True,
                    max_lines=200,
                )

    def on_mount(self) -> None:
        table = self.query_one("#bets-table", DataTable)
        table.add_columns(
            "Market", "Side", "Edge", "Bet $", "EV $", "Kelly%", "Conf", "Status",
        )
        table.cursor_type = "row"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show full details when a row is selected."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._decisions):
            d = self._decisions[row_index]
            detail = self.query_one("#bets-detail", RichLog)
            detail.clear()
            self._write_decision_detail(detail, d)

    def on_bet_update(self, event: BetUpdate) -> None:
        """Handle a new Kelly trade decision."""
        d = event.decision
        self._decisions.append(d)

        table = self.query_one("#bets-table", DataTable)
        detail = self.query_one("#bets-detail", RichLog)
        status_label = self.query_one("#bets-status", Label)

        question_short = d.market_question[:45]
        if len(d.market_question) > 45:
            question_short += "..."

        if d.should_trade:
            self._bets_count += 1
            side_str = d.side.replace("BUY_", "")
            edge_str = f"{d.edge:.1%}"
            bet_str = f"${d.bet_size_usd:.2f}"
            ev_str = f"${d.expected_value:.2f}"
            kelly_str = f"{d.adjusted_fraction:.1%}"
            conf_str = f"{d.confidence:.0%}"
            status_str = "TRADE"

            table.add_row(
                question_short, side_str, edge_str, bet_str,
                ev_str, kelly_str, conf_str, status_str,
            )

            # Write to detail log
            detail.write(Text(
                f"TRADE: {d.side} | {question_short} | "
                f"edge={d.edge:.1%} bet=${d.bet_size_usd:.2f} EV=${d.expected_value:.2f}",
                style="#44aa66",
            ))
        else:
            self._skips_count += 1
            table.add_row(
                question_short, "---", f"{d.edge:.1%}", "---",
                "---", "---", f"{d.confidence:.0%}", d.skip_reason[:20],
            )

            detail.write(Text(
                f"SKIP: {question_short} | {d.skip_reason} "
                f"(edge={d.edge:.3f}, est={d.estimated_prob:.2f}, mkt={d.market_price:.2f})",
                style="#667788",
            ))

        status_label.update(
            f"Bets: {self._bets_count} trades, {self._skips_count} skipped"
        )

    def _write_decision_detail(self, detail: RichLog, d: object) -> None:
        """Write full decision details to the RichLog."""
        detail.write(Text("=" * 60, style="#2a3a5a"))
        detail.write(Text(f"Market: {d.market_question}", style="#e0e8f0 bold"))
        detail.write(Text(f"Market ID: {d.market_id}", style="#667788"))
        detail.write(Text(f"Token ID: {d.token_id}", style="#667788"))
        detail.write(Text("", style="#667788"))

        if d.should_trade:
            detail.write(Text(f"Decision: TRADE", style="#44aa66 bold"))
            detail.write(Text(f"Side: {d.side}", style="#e0e8f0"))
            detail.write(Text(f"Bet Size: ${d.bet_size_usd:.2f}", style="#e0e8f0 bold"))
            detail.write(Text(f"Expected Value: ${d.expected_value:.2f}", style="#44aa66"))
        else:
            detail.write(Text(f"Decision: SKIP — {d.skip_reason}", style="#cc4444"))

        detail.write(Text("", style="#667788"))
        detail.write(Text(f"Our Estimate: {d.estimated_prob:.2%}", style="#e0e8f0"))
        detail.write(Text(f"Market Price: {d.market_price:.2%}", style="#e0e8f0"))
        detail.write(Text(f"Edge: {d.edge:.3f} ({d.edge:.1%})", style="#e0e8f0"))
        detail.write(Text(f"Full Kelly: {d.full_kelly_fraction:.3f} ({d.full_kelly_fraction:.1%})", style="#8899aa"))
        detail.write(Text(f"Adjusted Kelly (0.25x): {d.adjusted_fraction:.3f} ({d.adjusted_fraction:.1%})", style="#8899aa"))
        detail.write(Text(f"Confidence: {d.confidence:.2f}", style="#8899aa"))
        detail.write(Text("=" * 60, style="#2a3a5a"))
