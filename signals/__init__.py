"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.news import NewsSignalProvider

__all__ = ["SignalProvider", "SignalResult", "NewsSignalProvider"]
