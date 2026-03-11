"""Filter tab — pipeline progress bar and ranked results DataTable."""

import json
from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import DataTable, Button, Static, ProgressBar, Label

from tui.messages import DrillDownRequest, PipelineStageUpdate, PipelineComplete

STAGE_NAMES = ["discover", "filter", "categorize", "extract", "rank"]
STAGE_WEIGHTS = [0.10, 0.20, 0.35, 0.15, 0.20]  # Weighted progress per stage


class PipelinePanel(Vertical):
    """Filter pipeline progress and ranked results."""

    DEFAULT_CSS = """
    PipelinePanel {
        height: 1fr;
        background: #0a0a0a;
    }
    PipelinePanel .progress-area {
        height: auto;
        max-height: 8;
        padding: 1 2;
        border-bottom: solid #1a3a1a;
    }
    PipelinePanel .progress-area .stage-label {
        height: 1;
        text-style: bold;
        color: #00ff41;
    }
    PipelinePanel .progress-area .eta-label {
        height: 1;
        color: #007a20;
    }
    PipelinePanel .progress-area ProgressBar {
        margin: 0 0 0 0;
    }
    PipelinePanel .btn-row {
        height: 3;
        padding: 0 1;
    }
    PipelinePanel .btn-row Button {
        margin: 0 1;
    }
    PipelinePanel .summary-label {
        height: 1;
        padding: 0 2;
        color: #00cc33;
    }
    PipelinePanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._started_at: datetime | None = None
        self._stage_started_at: datetime | None = None
        self._result_data: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="progress-area"):
            yield Label("Pipeline idle. Press [bold]f[/bold] to run.", id="stage-label", classes="stage-label")
            yield ProgressBar(id="pipeline-progress", total=100, show_eta=False)
            yield Label("", id="eta-label", classes="eta-label")
        with Horizontal(classes="btn-row"):
            yield Button("Run Pipeline (f)", id="run-pipeline", variant="primary")
        yield Static("", id="summary-label", classes="summary-label")
        yield DataTable(id="results-table")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("#", "Score", "Category", "YES", "Liquidity", "Expires", "Question")
        table.cursor_type = "row"

    def on_pipeline_stage_update(self, event: PipelineStageUpdate) -> None:
        prog = event.progress
        label = self.query_one("#stage-label", Label)
        bar = self.query_one("#pipeline-progress", ProgressBar)
        eta_label = self.query_one("#eta-label", Label)

        if not prog.running:
            label.update("Pipeline idle.")
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
            f"Stage {stage_idx + 1}/{prog.total_stages}: {prog.current_stage}{items_str}"
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
        """Drill down into a pipeline result row."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._result_data):
            self.post_message(DrillDownRequest(
                market_data=self._result_data[row_index],
            ))

    def on_pipeline_complete(self, event: PipelineComplete) -> None:
        self._result_data = list(event.results)
        label = self.query_one("#stage-label", Label)
        bar = self.query_one("#pipeline-progress", ProgressBar)
        eta_label = self.query_one("#eta-label", Label)
        summary = self.query_one("#summary-label", Static)

        label.update("Pipeline complete!")
        bar.update(progress=100)
        eta_label.update("")
        summary.update(
            f"{event.discovered} discovered | {event.filtered} filtered | {len(event.results)} ranked"
        )

        # Populate results table
        table = self.query_one("#results-table", DataTable)
        table.clear()

        for i, m in enumerate(event.results):
            question = m.get("question", "???")
            if len(question) > 45:
                question = question[:42] + "..."
            score = m.get("_score", 0)
            cat = m.get("_category", "?")[:11]

            # Get YES price
            tokens = m.get("tokens", [])
            yes_p = "---"
            for tok in tokens:
                if tok.get("outcome", "").upper() == "YES":
                    try:
                        yes_p = f"{float(tok.get('price', 0)):.1%}"
                    except (TypeError, ValueError):
                        pass

            liq = float(m.get("liquidity", 0) or 0)
            end_raw = m.get("end_date_iso", m.get("endDate", ""))
            end_str = str(end_raw)[:10] if end_raw else "---"

            table.add_row(
                str(i + 1),
                str(score),
                cat,
                yes_p,
                f"${liq:,.0f}",
                end_str,
                question,
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-pipeline":
            self.app.run_filter_pipeline()
