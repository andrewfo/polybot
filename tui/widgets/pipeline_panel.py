"""In Progress tab — shows top 20 filtered markets and aggregation progress."""

import json
from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static, ProgressBar, Label

from tui.messages import BatchUpdate, DrillDownRequest, PipelineStageUpdate, PipelineComplete

STAGE_NAMES = ["discover", "filter", "categorize", "extract", "rank"]
STAGE_WEIGHTS = [0.10, 0.20, 0.35, 0.15, 0.20]  # Weighted progress per stage

STATUS_DISPLAY = {
    "waiting": "[#667788]\u23f3 Waiting[/#667788]",
    "processing": "[#4488cc]\u26a1 Processing[/#4488cc]",
    "done": "[#44aa66]\u2714 Done[/#44aa66]",
    "skipped": "[#8899aa]\u23ed Skipped[/#8899aa]",
    "error": "[#cc4444]\u2718 Error[/#cc4444]",
}


class PipelinePanel(Vertical):
    """In Progress panel showing current batch and aggregation status."""

    DEFAULT_CSS = """
    PipelinePanel {
        height: 1fr;
        background: #0a1628;
    }
    PipelinePanel .progress-area {
        height: auto;
        max-height: 8;
        padding: 1 2;
        border-bottom: solid #2a3a5a;
    }
    PipelinePanel .progress-area .stage-label {
        height: 1;
        text-style: bold;
        color: #e0e8f0;
    }
    PipelinePanel .progress-area .eta-label {
        height: 1;
        color: #667788;
    }
    PipelinePanel .progress-area ProgressBar {
        margin: 0 0 0 0;
    }
    PipelinePanel .summary-label {
        height: 1;
        padding: 0 2;
        color: #8899aa;
    }
    PipelinePanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._started_at: datetime | None = None
        self._stage_started_at: datetime | None = None
        self._batch_markets: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="progress-area"):
            yield Label("Waiting for bot to start...", id="stage-label", classes="stage-label")
            yield ProgressBar(id="pipeline-progress", total=100, show_eta=False)
            yield Label("", id="eta-label", classes="eta-label")
        yield Static("", id="summary-label", classes="summary-label")
        yield DataTable(id="results-table")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("#", "Status", "Category", "YES", "Liquidity", "Question")
        table.cursor_type = "row"

    def on_pipeline_stage_update(self, event: PipelineStageUpdate) -> None:
        prog = event.progress
        label = self.query_one("#stage-label", Label)
        bar = self.query_one("#pipeline-progress", ProgressBar)
        eta_label = self.query_one("#eta-label", Label)

        if not prog.running:
            label.update("Waiting for bot to start...")
            bar.update(progress=0)
            eta_label.update("")
            return

        # Calculate weighted progress
        stage_idx = prog.stage_index
        base_progress = sum(STAGE_WEIGHTS[:stage_idx]) * 100

        # Intra-stage progress for categorize/extract (per-item)
        if prog.items_total > 0 and prog.items_processed > 0:
            intra = (prog.items_processed / prog.items_total) * STAGE_WEIGHTS[stage_idx] * 100
        else:
            intra = 0

        total_progress = min(base_progress + intra, 100)
        bar.update(progress=total_progress)

        items_str = ""
        if prog.items_total > 0:
            items_str = f" ({prog.items_processed}/{prog.items_total} items)"

        label.update(
            f"Filtering: Stage {stage_idx + 1}/{prog.total_stages}: {prog.current_stage}{items_str}"
        )

        # ETA calculation
        if prog.started_at and stage_idx > 0:
            elapsed = (datetime.now(timezone.utc) - prog.started_at).total_seconds()
            completed_weight = sum(STAGE_WEIGHTS[:stage_idx])
            if completed_weight > 0:
                total_est = elapsed / completed_weight
                remaining = total_est - elapsed
                if remaining > 0:
                    eta_label.update(f"ETA: ~{int(remaining)}s remaining")
                else:
                    eta_label.update("Almost done...")
            else:
                eta_label.update("")
        else:
            eta_label.update("")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drill down into a batch market row."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._batch_markets):
            self.post_message(DrillDownRequest(
                market_data=self._batch_markets[row_index],
            ))

    def on_pipeline_complete(self, event: PipelineComplete) -> None:
        label = self.query_one("#stage-label", Label)
        bar = self.query_one("#pipeline-progress", ProgressBar)
        eta_label = self.query_one("#eta-label", Label)
        summary = self.query_one("#summary-label", Static)

        label.update("Filtering complete \u2014 aggregating signals...")
        bar.update(progress=100)
        eta_label.update("")
        summary.update(
            f"{event.discovered} discovered | {event.filtered} filtered | {len(event.results)} ranked"
        )

    def on_batch_update(self, event: BatchUpdate) -> None:
        """Update the batch table with current aggregation progress."""
        self._batch_markets = list(event.markets)
        table = self.query_one("#results-table", DataTable)
        table.clear()

        label = self.query_one("#stage-label", Label)

        done_count = sum(1 for s in event.statuses.values() if s in ("done", "skipped", "error"))
        total = len(event.markets)

        if event.current_index >= 0:
            label.update(f"Aggregating market {event.current_index + 1}/{total}...")
        else:
            label.update(f"Batch ready: {total} markets")

        # Update progress bar for aggregation phase
        bar = self.query_one("#pipeline-progress", ProgressBar)
        if total > 0:
            bar.update(progress=(done_count / total) * 100)

        for i, m in enumerate(event.markets):
            question = m.get("question", "???")
            if len(question) > 50:
                question = question[:47] + "..."

            cond_id = m.get("conditionId", m.get("condition_id", ""))
            status_str = event.statuses.get(cond_id, "waiting")
            status_display = STATUS_DISPLAY.get(status_str, status_str)

            cat = m.get("_category", "?")[:11]

            # Get YES price
            prices_raw = m.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw)
                except (ValueError, TypeError):
                    prices = []
            else:
                prices = prices_raw
            yes_p = f"{float(prices[0]):.1%}" if prices else "---"

            liq = float(m.get("liquidityNum", m.get("liquidity", 0)) or 0)

            table.add_row(
                str(i + 1),
                status_display,
                cat,
                yes_p,
                f"${liq:,.0f}",
                question,
            )
