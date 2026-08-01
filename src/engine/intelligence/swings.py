"""
Swing detection engine.
"""

from __future__ import annotations
from engine.config.swing_config import SwingConfig
from engine.intelligence.swing_quality import SwingQualityAnalyzer
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)


class SwingEngine:
    """
    Detects swing highs and swing lows.
    """

    def __init__(self, config: SwingConfig | None = None) -> None:
        self.config = config or SwingConfig()

        self.quality = SwingQualityAnalyzer()

        if self.config.lookback < 1:
            raise ValueError("lookback must be at least 1")

    def detect(
        self,
        candles: list[OHLCVCandle],
    ) -> list[SwingPoint]:
        """
        Detect swing points using a configurable fractal method.
        """

        swings: list[SwingPoint] = []

        if len(candles) < self.config.lookback:
            return swings

        for i in range(
            self.config.lookback,
            len(candles)
        ):
            current = candles[i]

            left = candles[
                i - self.config.lookback : i
            ]

            right = candles[
                i + 1 : min(
                    i + self.config.lookback + 1,
                    len(candles)
                )
            ]

            confirmed = len(right) == self.config.lookback

            candidate = 0 < len(right) < self.config.lookback

            is_high = all(
                current.high > candle.high
                for candle in left + right
            )

            if is_high and (confirmed or candidate):
                confirmation_index = (i + self.config.confirmation_candles)
                status = (SwingStatus.CONFIRMED if confirmed else SwingStatus.CANDIDATE)
                swing = SwingPoint(
                        timestamp=current.timestamp,
                        index=i,
                        price=current.high,
                        swing_type=SwingType.HIGH,
                        confirmation_index=confirmation_index,
                        confirmed=confirmed,
                        status=status,
                        strength=SwingStrength.NORMAL,
                    )

                self.quality.analyze(
                    swing,
                    candles,
                )

                swings.append(swing)

            is_low = all(
                current.low < candle.low
                for candle in left + right
            )

            if is_low and (confirmed or candidate):
                confirmation_index = (i + self.config.confirmation_candles)
                status = (SwingStatus.CONFIRMED if confirmed else SwingStatus.CANDIDATE)
                swing = SwingPoint(
                    timestamp=current.timestamp,
                    index=i,
                    price=current.low,
                    swing_type=SwingType.LOW,
                        confirmation_index=confirmation_index,
                        confirmed=confirmed,
                        status=status,
                        strength=SwingStrength.NORMAL,
                    )

                self.quality.analyze(
                    swing,
                    candles,
                )

                swings.append(swing)
            
        return swings