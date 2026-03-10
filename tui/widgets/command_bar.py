"""Bottom command bar for ad-hoc commands (categorize, llm-test, refresh)."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Static
from textual.reactive import reactive


class CommandBar(Horizontal):
    """Vim-style command input bar toggled with ':' key."""

    DEFAULT_CSS = """
    CommandBar {
        height: 3;
        dock: bottom;
        padding: 0 1;
        display: none;
    }
    CommandBar.visible {
        display: block;
    }
    CommandBar Input {
        width: 1fr;
    }
    CommandBar .cmd-hint {
        width: auto;
        min-width: 40;
        color: $text-muted;
        content-align: right middle;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder=":command (categorize, llm-test, refresh)", id="cmd-input")
        yield Static("categorize <q> | llm-test <p> | refresh", classes="cmd-hint")

    def toggle(self) -> None:
        """Show/hide the command bar."""
        if self.has_class("visible"):
            self.remove_class("visible")
        else:
            self.add_class("visible")
            self.query_one("#cmd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        event.input.value = ""

        if not raw:
            return

        # Remove leading ':' if present
        if raw.startswith(":"):
            raw = raw[1:].strip()

        if not raw:
            return

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "categorize" and arg:
            self.app.run_categorize(arg)
        elif cmd in ("llm-test", "llmtest", "llm_test") and arg:
            self.app.run_llm_test(arg)
        elif cmd == "refresh":
            self.app.run_health_check()
            self.app.refresh_markets()
        else:
            from tui.messages import CommandResult
            self.app.post_message(CommandResult(
                command=raw,
                success=False,
                output=f"Unknown command: '{cmd}'. Available: categorize <question>, llm-test <prompt>, refresh",
            ))

        # Hide bar after submission
        self.remove_class("visible")
