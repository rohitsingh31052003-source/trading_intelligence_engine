"""
Abstract interface for all market data providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from engine.models.ohlcv import OHLCVCandle


class MarketDataProvider(ABC):
    """
    Abstract base class for market data providers.
    """

    @abstractmethod
    def connect(self) -> None:
        """
        Initialize the provider.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True if the provider is ready to serve requests.
        """
        raise NotImplementedError

    @abstractmethod
    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[OHLCVCandle]:
        """
        Retrieve historical OHLCV candles.
        """
        raise NotImplementedError

    @abstractmethod
    def get_latest(
        self,
        symbol: str,
        interval: str,
    ) -> OHLCVCandle:
        """
        Retrieve the latest completed candle.
        """
        raise NotImplementedError
