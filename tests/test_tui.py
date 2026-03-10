"""Tests for the TUI dashboard using Textual's pilot framework."""

import pytest
from datetime import datetime, timezone

from textual.widgets import DataTable, ProgressBar, RichLog, Label, Static

from tui.app import TUIApp
from tui.messages import (
    ConnectionUpdate,
    LogMessage,
    PipelineComplete,
    PipelineStageUpdate,
)
from tui.state import ConnectionStatus, PipelineProgress


@pytest.fixture
def app():
    """Create a TUIApp instance."""
    return TUIApp()


@pytest.mark.asyncio
async def test_app_mounts(app):
    """App should mount without errors and show all tabs."""
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("Header") is not None
        assert app.query_one("Footer") is not None
        assert app.query_one("TabbedContent") is not None
        assert app.query_one("StatusPanel") is not None


@pytest.mark.asyncio
async def test_log_message_appears(app):
    """LogMessage should appear in the LogPanel's RichLog."""
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")

        app.post_message(LogMessage(level="INFO", logger_name="test", text="Hello from test"))
        await pilot.pause()
        await pilot.pause()  # Extra pause for message routing

        from tui.widgets.log_panel import LogPanel
        log_panel = app.query_one(LogPanel)
        assert log_panel.write_count > 0


@pytest.mark.asyncio
async def test_pipeline_stage_update(app):
    """PipelineStageUpdate should update the stage label."""
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")

        progress = PipelineProgress(
            running=True,
            current_stage="categorize",
            stage_index=2,
            total_stages=5,
            items_processed=5,
            items_total=10,
            started_at=datetime.now(timezone.utc),
            stage_started_at=datetime.now(timezone.utc),
        )
        app.post_message(PipelineStageUpdate(progress))
        await pilot.pause()
        await pilot.pause()

        label = app.query_one("#stage-label", Label)
        assert "categorize" in str(label.content).lower()


@pytest.mark.asyncio
async def test_connection_update_changes_display(app):
    """ConnectionUpdate should update the status panel indicators."""
    async with app.run_test(size=(120, 40)) as pilot:
        app.post_message(ConnectionUpdate(
            ConnectionStatus("Polymarket API", True, datetime.now(timezone.utc))
        ))
        await pilot.pause()
        await pilot.pause()

        from tui.widgets.status_panel import StatusPanel
        panel = app.query_one(StatusPanel)
        status = panel._connections.get("Polymarket API")
        assert status is not None
        assert status.healthy is True


@pytest.mark.asyncio
async def test_tab_switching(app):
    """Number keys should switch between tabs."""
    async with app.run_test(size=(120, 40)) as pilot:
        from textual.widgets import TabbedContent

        tc = app.query_one(TabbedContent)

        await pilot.press("2")
        await pilot.pause()
        assert tc.active == "markets"

        await pilot.press("3")
        await pilot.pause()
        assert tc.active == "filter"

        await pilot.press("1")
        await pilot.pause()
        assert tc.active == "home"


@pytest.mark.asyncio
async def test_pipeline_complete_populates_table(app):
    """PipelineComplete should populate the results DataTable."""
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("3")

        app.post_message(PipelineComplete(
            results=[
                {
                    "question": "Will BTC reach 100k?",
                    "_score": 8,
                    "_category": "crypto",
                    "tokens": [{"outcome": "YES", "price": "0.65"}],
                    "liquidity": 5000,
                    "endDate": "2026-06-01",
                },
            ],
            discovered=100,
            filtered=20,
        ))
        await pilot.pause()
        await pilot.pause()

        table = app.query_one("#results-table", DataTable)
        assert table.row_count == 1
