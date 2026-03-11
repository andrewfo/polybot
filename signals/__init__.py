"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.news import NewsSignalProvider
from signals.polling import PollingSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.resolution_econ import EconomicsResolutionProvider

__all__ = [
    "SignalProvider",
    "SignalResult",
    "NewsSignalProvider",
    "PollingSignalProvider",
    "CryptoResolutionProvider",
    "EconomicsResolutionProvider",
]
