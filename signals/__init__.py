"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.aggregator import AggregatedSignal, SignalAggregator
from signals.news import NewsSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.resolution_econ import EconomicsResolutionProvider

__all__ = [
    "SignalProvider",
    "SignalResult",
    "AggregatedSignal",
    "SignalAggregator",
    "NewsSignalProvider",
    "CryptoResolutionProvider",
    "EconomicsResolutionProvider",
]
