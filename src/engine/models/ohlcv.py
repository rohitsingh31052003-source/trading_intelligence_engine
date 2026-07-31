"""
Domain model representing a single OHLCV market candle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OHLCVCandle:
    """
    Immutable representation of a single OHLCV candle.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("High price cannot be lower than low price.")

        if not self.low <= self.open <= self.high:
            raise ValueError("Open price must lie between low and high.")

        if not self.low <= self.close <= self.high:
            raise ValueError("Close price must lie between low and high.")

        if self.volume < 0:
            raise ValueError("Volume cannot be negative.")

    @property
    def body_size(self) -> float:
        """Absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def candle_range(self) -> float:
        """High-low range of the candle."""
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        """Return True if the candle closed above its open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Return True if the candle closed below its open."""
        return self.close < self.open
