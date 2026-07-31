"""
Swing detection engine.
"""

from __future__ import annotations

from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import (
    SwingPoint,
    SwingStrength,
    SwingType,
)


class SwingEngine:
    """
    Detects swing highs and swing lows.
    """

    def __init__(self, lookback: int = 2) -> None:
        if lookback < 1:
            raise ValueError("lookback must be at least 1")

        self.lookback = lookback

    def detect(
        self,
        candles: list[OHLCVCandle],
    ) -> list[SwingPoint]:
        """
        Detect swing points using a configurable fractal method.
        """

        swings: list[SwingPoint] = []

        if len(candles) < (self.lookback * 2 + 1):
            return swings

        for i in range(
            self.lookback,
            len(candles) - self.lookback,
        ):
            current = candles[i]

            left = candles[
                i - self.lookback : i
            ]

            right = candles[
                i + 1 : i + self.lookback + 1
            ]

            is_high = all(
                current.high > candle.high
                for candle in left + right
            )

            if is_high:
                swings.append(
                    SwingPoint(
                        timestamp=current.timestamp,
                        index=i,
                        price=current.high,
                        swing_type=SwingType.HIGH,
                        confirmed=True,
                        strength=SwingStrength.NORMAL,
                    )
                )

            is_low = all(
                current.low < candle.low
                for candle in left + right
            )

            if is_low:
                swings.append(
                    SwingPoint(
                        timestamp=current.timestamp,
                        index=i,
                        price=current.low,
                        swing_type=SwingType.LOW,
                        confirmed=True,
                        strength=SwingStrength.NORMAL,
                    )
                )

        return swings