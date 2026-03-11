"""Abstract signal provider interface and shared dataclass.

All signal providers inherit from SignalProvider and return SignalResult.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalResult:
    """Result from a signal provider's analysis of a market."""

    source: str                    # e.g., "news", "polling"
    probability: Optional[float]   # 0-1, or None if insufficient data
    confidence: float              # 0-1, how confident this signal is
    reasoning: str                 # Human-readable explanation
    model_used: str                # Which LLM model produced this
    data_points: int               # How many articles/polls/etc. were analyzed
    raw_data: dict = field(default_factory=dict)  # Raw inputs for debugging


class SignalProvider:
    """Abstract base class for all signal sources."""

    name: str = "base"

    async def get_signal(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        **kwargs,
    ) -> SignalResult:
        """Produce a signal for a given market.

        kwargs may include:
        - resolution_keywords: dict from extract_resolution_params()
          for crypto markets
        """
        raise NotImplementedError
