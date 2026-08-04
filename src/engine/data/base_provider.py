"""
Base implementation shared by all market data providers.
"""

from __future__ import annotations

from abc import abstractmethod

from engine.data.provider import MarketDataProvider


class BaseDataProvider(MarketDataProvider):
    """
    Shared functionality for market data providers.
    """

    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name
        self._connected = False

    @property
    def provider_name(self) -> str:
        """Return the provider name."""
        return self._provider_name

    @property
    def connected(self) -> bool:
        """Return whether the provider is connected."""
        return self._connected

    def is_available(self) -> bool:
        """Return provider availability."""
        return self._connected

    @abstractmethod
    def connect(self) -> None:
        """Connect to the provider."""
        raise NotImplementedError
