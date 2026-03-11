"""Signals tab — live view of the news signal pipeline activity."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import DataTable, RichLog, Static, Label
from rich.text import Text

from tui.messages import AggregationResult, DrillDownRequest, SignalUpdate

MAX_ACTIVITY_LINES = 500

STAGE_ICONS = {
    "queries": "\u2692",    # hammers — generating search queries
    "fetch": "\u2b07",      # download — fetching articles
    "dedup": "\u2702",      # scissors — deduplicating
    "summarize": "\u270e",  # pencil — summarizing
    "estimate": "\u2696",   # scales — estimating probability
    "done": "\u2714",       # checkmark — finished
    "error": "\u2718",      # X — error
    "cache": "\u29d7",      # hourglass — cache hit
    "fred": "\U0001f3e6",   # bank — FRED data fetch
    "coingecko": "\U0001f98e",  # lizard — CoinGecko data fetch
    "model": "\U0001f4ca",  # bar chart — log-normal model
    "adjust": "\U0001f504",  # arrows — LLM adjustment
    "interpret": "\U0001f9e0",  # brain — LLM interpretation
    "polling": "\U0001f4ca",  # bar chart — polling data
    # Aggregator stages
    "collecting": "\U0001f4e1",  # satellite — collecting signals
    "filtering": "\U0001f50d",   # magnifying glass — filtering signals
    "preliminary": "\u2696",     # scales — computing preliminary estimate
    "frontier": "\U0001f680",    # rocket — frontier model call
    "frontier_done": "\u2705",   # green check — frontier model done
    "skip": "\u23ed",            # skip — market skipped
}


class SignalsPanel(Vertical):
    """Live signal pipeline activity and results table."""

    DEFAULT_CSS = """
    SignalsPanel {
        height: 1fr;
        background: #0a1628;
    }
    SignalsPanel .signals-header {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    SignalsPanel .signals-status {
        height: 1;
        padding: 0 2;
        color: #667788;
    }
    SignalsPanel .signals-split {
        height: 1fr;
    }
    SignalsPanel .activity-section {
        height: 1fr;
        max-height: 50%;
        border-bottom: solid #2a3a5a;
    }
    SignalsPanel .activity-section .section-label {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    SignalsPanel .activity-section RichLog {
        height: 1fr;
        background: #0d1f3c;
        border: solid #2a3a5a;
    }
    SignalsPanel .results-section {
        height: 1fr;
    }
    SignalsPanel .results-section .section-label {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    SignalsPanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._signals_count = 0
        self._active_market: str = ""
        self._aggregation_store: dict[str, tuple[dict, object]] = {}  # question -> (market_data, AggregatedSignal)
        self._result_rows: list[tuple[str, str]] = []  # (question, source) per table row

    def compose(self) -> ComposeResult:
        yield Label("SIGNAL ENGINE", classes="signals-header")
        yield Label("Waiting for signal pipeline...", id="signals-status", classes="signals-status")
        with Vertical(classes="signals-split"):
            with Vertical(classes="activity-section"):
                yield Label("LIVE ACTIVITY", classes="section-label")
                yield RichLog(
                    id="signal-activity",
                    wrap=True,
                    highlight=True,
                    markup=True,
                    max_lines=MAX_ACTIVITY_LINES,
                )
            with Vertical(classes="results-section"):
                yield Label("SIGNAL RESULTS", classes="section-label")
                yield DataTable(id="signal-results")

    def on_mount(self) -> None:
        table = self.query_one("#signal-results", DataTable)
        table.add_columns("Market", "Source", "Prob", "Conf", "Points", "Reasoning")
        table.cursor_type = "row"

    def on_aggregation_result(self, event: AggregationResult) -> None:
        """Store aggregation data for drill-down lookup."""
        self._aggregation_store[event.market_question] = (
            event.market_data, event.aggregation,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drill down into a signal result row."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._result_rows):
            question, source = self._result_rows[row_index]
            stored = self._aggregation_store.get(question)
            if stored:
                market_data, aggregation = stored
                self.post_message(DrillDownRequest(
                    market_data=market_data,
                    aggregation=aggregation,
                ))
            else:
                # No aggregation data — show basic drill-down with question as market data
                self.post_message(DrillDownRequest(
                    market_data={"question": question},
                ))

    def on_signal_update(self, event: SignalUpdate) -> None:
        """Handle a signal pipeline update."""
        activity = self.query_one("#signal-activity", RichLog)
        status = self.query_one("#signals-status", Label)

        icon = STAGE_ICONS.get(event.stage, "\u2022")
        question_short = event.market_question[:55]
        if len(event.market_question) > 55:
            question_short += "..."

        source_prefix = f"[{event.source}] " if event.source else ""

        # Color by stage
        if event.stage == "done":
            if event.probability is not None:
                prob_str = f"{event.probability:.0%}"
                conf_str = f"{event.confidence:.0%}"
                color = "#e0e8f0"
                line = f"{icon} {source_prefix}{question_short}  =>  P={prob_str} C={conf_str} ({event.data_points} pts)"
            else:
                color = "#ccaa44"
                line = f"{icon} {source_prefix}{question_short}  =>  insufficient data ({event.data_points} pts)"
        elif event.stage == "error":
            color = "#cc4444"
            line = f"{icon} {source_prefix}{question_short}  {event.detail}"
        elif event.stage == "cache":
            color = "#667788"
            line = f"{icon} {source_prefix}{question_short}  cache hit"
        else:
            color = "#8899aa"
            detail_str = f"  {event.detail}" if event.detail else ""
            line = f"{icon} {source_prefix}[{event.stage}] {question_short}{detail_str}"

        activity.write(Text(line, style=color))

        # Update status line
        if event.done:
            self._signals_count += 1
            status.update(f"Signals computed: {self._signals_count}")
            self._add_result_row(event)
        elif event.stage == "cache":
            self._signals_count += 1
            status.update(f"Signals computed: {self._signals_count} (cache hit)")
            self._add_result_row(event)
        else:
            self._active_market = question_short
            status.update(f"Processing: {question_short}")

    def _add_result_row(self, event: SignalUpdate) -> None:
        """Add a completed signal to the results table."""
        table = self.query_one("#signal-results", DataTable)

        question_short = event.market_question[:40]
        if len(event.market_question) > 40:
            question_short += "..."

        source_str = event.source if event.source else "news"
        prob_str = f"{event.probability:.0%}" if event.probability is not None else "---"
        conf_str = f"{event.confidence:.0%}"
        points_str = str(event.data_points)
        reasoning = event.detail[:60] if event.detail else ""

        table.add_row(question_short, source_str, prob_str, conf_str, points_str, reasoning)
        self._result_rows.append((event.market_question, source_str))
