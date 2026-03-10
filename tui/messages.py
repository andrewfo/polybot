"""Custom Textual Message subclasses for inter-widget communication."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from textual.message import Message

from tui.state import ConnectionStatus, PipelineProgress


class LogMessage(Message):
    """A log record to display in the LogPanel."""
    def __init__(self, level: str, logger_name: str, text: str) -> None:
        super().__init__()
        self.level = level
        self.logger_name = logger_name
        self.text = text


class PipelineStageUpdate(Message):
    """Pipeline progress snapshot."""
    def __init__(self, progress: PipelineProgress) -> None:
        super().__init__()
        self.progress = progress


class PipelineComplete(Message):
    """Pipeline finished — carries ranked results."""
    def __init__(
        self,
        results: list[dict[str, Any]],
        discovered: int,
        filtered: int,
    ) -> None:
        super().__init__()
        self.results = results
        self.discovered = discovered
        self.filtered = filtered


class ConnectionUpdate(Message):
    """Health check result for a single connection."""
    def __init__(self, status: ConnectionStatus) -> None:
        super().__init__()
        self.status = status


class WalletUpdate(Message):
    """Updated wallet balances."""
    def __init__(
        self,
        usdc: float,
        matic: float,
        has_gas: bool,
        positions_count: int,
        address: str = "",
    ) -> None:
        super().__init__()
        self.usdc = usdc
        self.matic = matic
        self.has_gas = has_gas
        self.positions_count = positions_count
        self.address = address


class CostUpdate(Message):
    """Updated LLM cost data."""
    def __init__(
        self,
        daily: float,
        monthly: float,
        model_breakdown: list[tuple[str, int, int, int, float]],
        task_breakdown: list[tuple[str, int, float]],
        total_calls: int,
    ) -> None:
        super().__init__()
        self.daily = daily
        self.monthly = monthly
        self.model_breakdown = model_breakdown
        self.task_breakdown = task_breakdown
        self.total_calls = total_calls


class MarketsUpdate(Message):
    """Updated list of active markets from Gamma API."""
    def __init__(self, markets: list[dict[str, Any]]) -> None:
        super().__init__()
        self.markets = markets


class CommandResult(Message):
    """Result of a command bar command."""
    def __init__(self, command: str, success: bool, output: str) -> None:
        super().__init__()
        self.command = command
        self.success = success
        self.output = output
