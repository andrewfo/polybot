"""Analysis list panel — left pane DataTable showing markets with aggregation status."""

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label

from tui.messages import (
    AggregationResult,
    AnalysisSelectionChanged,
    BatchUpdate,
    BetUpdate,
    SignalUpdate,
)


@dataclass
class AnalysisEntry:
    """State for one market in the Analysis tab."""
    condition_id: str
    market_data: dict[str, Any]
    status: str = "waiting"  # waiting | processing | done | skipped | error
    aggregation: Any = None  # AggregatedSignal | None
    decision: Any = None     # TradeDecision | None


STATUS_ICONS = {
    "waiting": ("\u23f3", "#667788"),      # hourglass, dim
    "processing": ("\u26a1", "#4488cc"),   # lightning, blue
    "done": ("\u2714", "#44aa66"),         # check, green
    "skipped": ("\u23ed", "#8899aa"),      # skip, muted
    "error": ("\u2718", "#cc4444"),        # X, red
}


class AnalysisListPanel(Vertical):
    """Left-pane list of markets with status, decision, edge, and question."""

    DEFAULT_CSS = """
    AnalysisListPanel {
        height: 1fr;
        background: #0a1628;
    }
    AnalysisListPanel .analysis-header {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: #e0e8f0;
    }
    AnalysisListPanel .analysis-status {
        height: 1;
        padding: 0 1;
        color: #667788;
    }
    AnalysisListPanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._entries: OrderedDict[str, AnalysisEntry] = OrderedDict()
        self._row_keys: list[str] = []  # conditionId per table row
        self._trade_count = 0
        self._skip_count = 0

    def compose(self) -> ComposeResult:
        yield Label("ANALYSIS", classes="analysis-header")
        yield Label("Waiting for signals...", id="analysis-status", classes="analysis-status")
        yield DataTable(id="analysis-table")

    def on_mount(self) -> None:
        table = self.query_one("#analysis-table", DataTable)
        table.add_columns("#", "St", "Decision", "Edge", "Question")
        table.cursor_type = "row"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Notify the detail panel of the selection."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._row_keys):
            cid = self._row_keys[row_index]
            entry = self._entries.get(cid)
            if entry:
                self.post_message(AnalysisSelectionChanged(entry=entry))

    def on_batch_update(self, event: BatchUpdate) -> None:
        """Populate waiting markets from the pipeline batch."""
        for m in event.markets:
            cid = m.get("conditionId", m.get("condition_id", ""))
            if not cid:
                continue
            status = event.statuses.get(cid, "waiting")
            if cid in self._entries:
                self._entries[cid].status = status
                self._entries[cid].market_data = m
            else:
                self._entries[cid] = AnalysisEntry(
                    condition_id=cid,
                    market_data=m,
                    status=status,
                )
        self._rebuild_table()

    def on_aggregation_result(self, event: AggregationResult) -> None:
        """Store aggregation data when it arrives."""
        cid = event.market_data.get("conditionId", event.market_data.get("condition_id", ""))
        if cid and cid in self._entries:
            self._entries[cid].aggregation = event.aggregation
            if event.aggregation is None:
                self._entries[cid].status = "skipped"
            else:
                self._entries[cid].status = "done"
            self._rebuild_table()

    def on_bet_update(self, event: BetUpdate) -> None:
        """Attach Kelly decision to the entry."""
        if not event.market_data:
            return
        cid = event.market_data.get("conditionId", event.market_data.get("condition_id", ""))
        if not cid:
            # Manual aggregate uses "manual" as condition_id
            cid = getattr(event.decision, "market_id", "")
        if cid and cid in self._entries:
            self._entries[cid].decision = event.decision
            self._entries[cid].aggregation = event.aggregation
            self._entries[cid].status = "done"
        elif cid:
            # Manual aggregate — create entry
            mkt = event.market_data or {}
            self._entries[cid] = AnalysisEntry(
                condition_id=cid,
                market_data=mkt,
                status="done",
                aggregation=event.aggregation,
                decision=event.decision,
            )
        self._rebuild_table()
        self._update_counts()

    def on_signal_update(self, event: SignalUpdate) -> None:
        """Update status based on signal stage updates (for manual tests)."""
        # For manual signal-test/aggregate, entries may not have conditionId
        # We match by question in that case
        if event.stage == "error":
            for entry in self._entries.values():
                q = entry.market_data.get("question", "")
                if q and q == event.market_question:
                    entry.status = "error"
                    self._rebuild_table()
                    break

    def add_manual_entry(
        self,
        question: str,
        condition_id: str = "manual",
        market_data: dict[str, Any] | None = None,
    ) -> None:
        """Add a manual aggregate/signal-test entry."""
        mkt = market_data or {"question": question, "condition_id": condition_id}
        if condition_id not in self._entries:
            self._entries[condition_id] = AnalysisEntry(
                condition_id=condition_id,
                market_data=mkt,
                status="processing",
            )
            self._rebuild_table()

    def _update_counts(self) -> None:
        self._trade_count = 0
        self._skip_count = 0
        for entry in self._entries.values():
            if entry.decision:
                if entry.decision.should_trade:
                    self._trade_count += 1
                else:
                    self._skip_count += 1
        try:
            status = self.query_one("#analysis-status", Label)
            total = len(self._entries)
            done = sum(1 for e in self._entries.values() if e.status in ("done", "skipped", "error"))
            status.update(
                f"{done}/{total} analyzed  |  {self._trade_count} trades, {self._skip_count} skips"
            )
        except Exception:
            pass

    def _rebuild_table(self) -> None:
        """Rebuild the DataTable from current entries."""
        table = self.query_one("#analysis-table", DataTable)
        table.clear()
        self._row_keys = []

        for i, (cid, entry) in enumerate(self._entries.items()):
            self._row_keys.append(cid)

            icon, color = STATUS_ICONS.get(entry.status, ("\u2022", "#8899aa"))
            status_text = Text(icon, style=color)

            # Decision column
            if entry.decision:
                if entry.decision.should_trade:
                    dec_text = Text("TRADE", style="#44aa66 bold")
                else:
                    dec_text = Text("SKIP", style="#cc4444")
            elif entry.status == "skipped":
                dec_text = Text("SKIP", style="#8899aa")
            elif entry.status == "error":
                dec_text = Text("ERR", style="#cc4444")
            else:
                dec_text = Text("---", style="#667788")

            # Edge column
            if entry.decision and entry.decision.edge:
                edge_val = entry.decision.edge
                edge_color = "#44aa66" if edge_val > 0 else "#cc4444"
                edge_str = Text(f"{edge_val:+.1%}", style=edge_color)
            else:
                edge_str = Text("---", style="#667788")

            # Question (truncated)
            question = entry.market_data.get("question", "???")
            if len(question) > 45:
                question = question[:42] + "..."

            table.add_row(str(i + 1), status_text, dec_text, edge_str, question)

        self._update_counts()
