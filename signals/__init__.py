"""Signal providers for market analysis."""

from signals.base import SignalProvider, SignalResult
from signals.aggregator import AggregatedSignal, SignalAggregator
from signals.onchain_flow import OnchainFlowProvider
from signals.prediction_markets import PredictionMarketsSignalProvider
from signals.resolution_crypto import CryptoResolutionProvider
from signals.web_search import WebSearchSignalProvider

__all__ = [
    "SignalProvider",
    "SignalResult",
    "AggregatedSignal",
    "SignalAggregator",
    "CryptoResolutionProvider",
    "OnchainFlowProvider",
    "PredictionMarketsSignalProvider",
    "WebSearchSignalProvider",
]
