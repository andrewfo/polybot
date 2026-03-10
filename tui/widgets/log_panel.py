"""Logs tab — scrollable RichLog with level filtering."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import RichLog, Button, Static
from textual.reactive import reactive
from rich.text import Text

from tui.messages import LogMessage, CommandResult

MAX_LOG_LINES = 1000

LEVEL_COLORS = {
    "DEBUG": "dim white",
    "INFO": "white",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
    "COMMAND": "bold cyan",
}

LEVEL_PRIORITY = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4, "COMMAND": 1}


class LogPanel(Vertical):
    """Log viewer with level filter buttons."""

    DEFAULT_CSS = """
    LogPanel {
        height: 1fr;
    }
    LogPanel .log-filters {
        height: 3;
        dock: top;
        padding: 0 1;
    }
    LogPanel .log-filters Button {
        min-width: 10;
        margin: 0 1;
    }
    LogPanel RichLog {
        height: 1fr;
        border: solid $surface-lighten-2;
    }
    """

    min_level: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._write_count = 0

    def compose(self) -> ComposeResult:
        with Horizontal(classes="log-filters"):
            yield Button("ALL", id="log-all", variant="primary")
            yield Button("INFO+", id="log-info")
            yield Button("WARN+", id="log-warn")
            yield Button("ERROR+", id="log-error")
        yield RichLog(id="log-output", wrap=True, highlight=True, markup=True, max_lines=MAX_LOG_LINES)

    @property
    def _log(self) -> RichLog:
        return self.query_one("#log-output", RichLog)

    @property
    def write_count(self) -> int:
        """Number of lines written (for testing)."""
        return self._write_count

    def _append(self, text: Text) -> None:
        self._log.write(text)
        self._write_count += 1

    def on_log_message(self, event: LogMessage) -> None:
        level_num = LEVEL_PRIORITY.get(event.level, 0)
        if level_num < self.min_level:
            return
        color = LEVEL_COLORS.get(event.level, "white")
        text = Text(event.text, style=color)
        self._append(text)

    def on_command_result(self, event: CommandResult) -> None:
        prefix = "[COMMAND]"
        style = "bold cyan" if event.success else "bold red"
        text = Text(f"{prefix} :{event.command}\n{event.output}", style=style)
        self._append(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "log-all":
            self.min_level = 0
        elif btn_id == "log-info":
            self.min_level = 1
        elif btn_id == "log-warn":
            self.min_level = 2
        elif btn_id == "log-error":
            self.min_level = 3
        # Update button variants
        for btn in self.query("Button"):
            btn.variant = "default"
        event.button.variant = "primary"
