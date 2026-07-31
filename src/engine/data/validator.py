"""
Validation utilities for market data.
"""

from __future__ import annotations

from engine.models.ohlcv import OHLCVCandle


class DataValidator:
    """
    Validates individual candles and collections of candles.
    """

    @staticmethod
    def validate_candle(candle: OHLCVCandle) -> None:
        """
        Validate a single OHLCVCandle.

        Raises:
            ValueError: If the candle is invalid.
        """
        if candle.high < candle.low:
            raise ValueError("High price cannot be lower than low price.")

        if not candle.low <= candle.open <= candle.high:
            raise ValueError("Open price must lie between low and high.")

        if not candle.low <= candle.close <= candle.high:
            raise ValueError("Close price must lie between low and high.")

        if candle.volume < 0:
            raise ValueError("Volume cannot be negative.")

    @classmethod
    def validate_dataset(cls, candles: list[OHLCVCandle]) -> None:
        """
        Validate an entire candle dataset.

        Raises:
            ValueError: If the dataset is invalid.
        """
        if not candles:
            raise ValueError("Dataset cannot be empty.")

        cls.check_sorted(candles)
        cls.check_duplicates(candles)

        for candle in candles:
            cls.validate_candle(candle)

    @staticmethod
    def check_duplicates(candles: list[OHLCVCandle]) -> None:
        """
        Ensure timestamps are unique.
        """
        timestamps = [c.timestamp for c in candles]

        if len(timestamps) != len(set(timestamps)):
            raise ValueError("Duplicate timestamps detected.")

    @staticmethod
    def check_sorted(candles: list[OHLCVCandle]) -> None:
        """
        Ensure candles are sorted by timestamp.
        """
        timestamps = [c.timestamp for c in candles]

        if timestamps != sorted(timestamps):
            raise ValueError("Candles are not sorted chronologically.")
