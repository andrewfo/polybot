"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.news import NewsSignalProvider
from signals.polling import PollingSignalProvider

__all__ = ["SignalProvider", "SignalResult", "NewsSignalProvider", "PollingSignalProvider"]
