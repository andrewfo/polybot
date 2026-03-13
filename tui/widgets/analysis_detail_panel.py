"""Analysis detail panel — right pane showing unified detail view for selected market."""

import logging
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from tui.widgets.charts import C_DIM, C_RED
from tui.widgets.detail_builders import build_full_analysis

logger = logging.getLogger(__name__)


class AnalysisDetailPanel(VerticalScroll):
    """Right-pane detail view showing full analysis for the selected market."""

    DEFAULT_CSS = """
    AnalysisDetailPanel {
        height: 1fr;
        background: #0d1f3c;
    }
    AnalysisDetailPanel Static {
        width: 1fr;
        color: #8899aa;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{C_DIM}]Select a market from the list to see full analysis details.[/]",
            id="analysis-detail-content",
            markup=True,
        )

    def show_entry(self, entry: "AnalysisEntry") -> None:  # noqa: F821
        """Render the full analysis for the given entry."""
        try:
            content = build_full_analysis(
                market_data=entry.market_data,
                aggregation=entry.aggregation,
                decision=entry.decision,
            )
        except Exception as e:
            logger.error("Failed to build analysis view: %s", e, exc_info=True)
            content = f"[{C_RED}]Error rendering analysis: {e}[/{C_RED}]"

        try:
            detail = self.query_one("#analysis-detail-content", Static)
            detail.update(content)
            self.scroll_home(animate=False)
        except Exception:
            pass
