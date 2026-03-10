"""Structured state dataclasses for the TUI dashboard."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ConnectionStatus:
    """Health status of an external service connection."""
    name: str          # "Polymarket API", "Polygon RPC", "OpenRouter"
    healthy: bool
    last_check: datetime | None
    error: str = ""


@dataclass
class PipelineProgress:
    """Snapshot of the filter pipeline's current progress."""
    running: bool = False
    current_stage: str = "idle"  # "discover" | "filter" | "categorize" | "extract" | "rank" | "done"
    stage_index: int = 0
    total_stages: int = 5
    items_processed: int = 0
    items_total: int = 0
    started_at: datetime | None = None
    stage_started_at: datetime | None = None
