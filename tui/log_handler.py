"""Logging handler that redirects log records into the TUI as messages."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tui.app import TUIApp

from tui.messages import LogMessage


class TUILogHandler(logging.Handler):
    """Attaches to the root logger and posts LogMessage to the Textual app."""

    def __init__(self, app: "TUIApp") -> None:
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            level = record.levelname
            logger_name = record.name
            self._app.post_message(LogMessage(level=level, logger_name=logger_name, text=text))
        except Exception:
            self.handleError(record)
