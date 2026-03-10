"""Costs tab — LLM spending breakdown from SQLite."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static, Button

from tui.messages import CostUpdate


class CostsPanel(Vertical):
    """LLM cost breakdown with model and task tables."""

    DEFAULT_CSS = """
    CostsPanel {
        height: 1fr;
        padding: 1 2;
        background: #0a0a0a;
    }
    CostsPanel .section-title {
        text-style: bold;
        color: #00ff41;
        margin: 1 0 0 0;
    }
    CostsPanel .cost-summary {
        margin: 0 0 0 2;
        height: 1;
        color: #00cc33;
    }
    CostsPanel DataTable {
        height: auto;
        max-height: 12;
        margin: 0 0 1 0;
    }
    CostsPanel Button {
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("LLM COST REPORT", classes="section-title")
        yield Static("Today:       $0.0000", id="cost-daily", classes="cost-summary")
        yield Static("This Month:  $0.0000", id="cost-monthly", classes="cost-summary")
        yield Static("Total Calls: 0", id="cost-total", classes="cost-summary")

        yield Static("MODEL BREAKDOWN", classes="section-title")
        yield DataTable(id="model-table")

        yield Static("TASK BREAKDOWN", classes="section-title")
        yield DataTable(id="task-table")

        yield Button("Refresh", id="refresh-costs")

    def on_mount(self) -> None:
        model_table = self.query_one("#model-table", DataTable)
        model_table.add_columns("Model", "Calls", "Input Tok", "Output Tok", "Cost")
        model_table.cursor_type = "row"

        task_table = self.query_one("#task-table", DataTable)
        task_table.add_columns("Task", "Calls", "Cost")
        task_table.cursor_type = "row"

    def on_cost_update(self, event: CostUpdate) -> None:
        self.query_one("#cost-daily", Static).update(f"Today:       ${event.daily:.4f}")
        self.query_one("#cost-monthly", Static).update(f"This Month:  ${event.monthly:.4f}")
        self.query_one("#cost-total", Static).update(f"Total Calls: {event.total_calls}")

        # Model breakdown
        model_table = self.query_one("#model-table", DataTable)
        model_table.clear()
        for model, calls, inp, outp, cost in event.model_breakdown:
            if len(model) > 36:
                model = model[:33] + "..."
            model_table.add_row(model, str(calls), str(inp), str(outp), f"${cost:.4f}")

        # Task breakdown
        task_table = self.query_one("#task-table", DataTable)
        task_table.clear()
        for task, calls, cost in event.task_breakdown:
            task_table.add_row(task, str(calls), f"${cost:.4f}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-costs":
            self.app.refresh_costs()
