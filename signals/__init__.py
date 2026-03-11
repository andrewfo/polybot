"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.aggregator import AggregatedSignal, SignalAggregator
from signals.historical_base_rate import HistoricalBaseRateProvider
from signals.monte_carlo import MonteCarloProvider
from signals.news import NewsSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.resolution_econ import EconomicsResolutionProvider
from signals.technical_analysis import TechnicalAnalysisProvider

__all__ = [
    "SignalProvider",
    "SignalResult",
    "AggregatedSignal",
    "SignalAggregator",
    "HistoricalBaseRateProvider",
    "MonteCarloProvider",
    "NewsSignalProvider",
    "CryptoResolutionProvider",
    "EconomicsResolutionProvider",
    "TechnicalAnalysisProvider",
]
