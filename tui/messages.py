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


class BotToggle(Message):
    """Request to start or stop the bot."""
    def __init__(self, running: bool) -> None:
        super().__init__()
        self.running = running


class BotStatusUpdate(Message):
    """Bot running state changed."""
    def __init__(self, running: bool) -> None:
        super().__init__()
        self.running = running


class CommandResult(Message):
    """Result of a command bar command."""
    def __init__(self, command: str, success: bool, output: str) -> None:
        super().__init__()
        self.command = command
        self.success = success
        self.output = output


class AggregationResult(Message):
    """Carries a full AggregatedSignal + market dict for drill-down storage."""
    def __init__(
        self,
        market_data: dict[str, Any],
        aggregation: Any,  # AggregatedSignal | None
        market_question: str,
    ) -> None:
        super().__init__()
        self.market_data = market_data
        self.aggregation = aggregation
        self.market_question = market_question


class DrillDownRequest(Message):
    """Posted by panels when a row is selected for detail view."""
    def __init__(
        self,
        market_data: dict[str, Any],
        aggregation: Any = None,  # AggregatedSignal | None
    ) -> None:
        super().__init__()
        self.market_data = market_data
        self.aggregation = aggregation


class SignalUpdate(Message):
    """Live update from a signal pipeline."""
    def __init__(
        self,
        market_question: str,
        stage: str,
        detail: str = "",
        probability: float | None = None,
        confidence: float = 0.0,
        data_points: int = 0,
        done: bool = False,
        source: str = "",
    ) -> None:
        super().__init__()
        self.market_question = market_question
        self.stage = stage
        self.detail = detail
        self.probability = probability
        self.confidence = confidence
        self.data_points = data_points
        self.done = done
        self.source = source


class BotProcessUpdate(Message):
    """Current bot process phase for the home tab."""
    def __init__(self, phase: str, detail: str = "", cycle: int = 0) -> None:
        super().__init__()
        self.phase = phase      # "idle" | "filtering" | "aggregating" | "waiting"
        self.detail = detail    # e.g. "Market 3/20: Will Bitcoin..."
        self.cycle = cycle      # which filter-aggregate cycle we're on


class BetUpdate(Message):
    """A Kelly-sized trade decision to display in the Bets tab."""
    def __init__(
        self,
        decision: Any,
        market_data: dict[str, Any] | None = None,
        aggregation: Any = None,
    ) -> None:
        super().__init__()
        self.decision = decision      # TradeDecision
        self.market_data = market_data
        self.aggregation = aggregation  # AggregatedSignal | None


class BatchUpdate(Message):
    """Update for the In Progress tab — current batch of markets being processed."""
    def __init__(
        self,
        markets: list[dict[str, Any]],
        current_index: int,
        statuses: dict[str, str],
    ) -> None:
        super().__init__()
        self.markets = markets          # top 20 filtered markets
        self.current_index = current_index  # -1 = not started, 0..N = processing index
        self.statuses = statuses        # condition_id -> "waiting"|"processing"|"done"|"skipped"|"error"
