"""
Quality analysis for swing points.
"""

from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingPoint
from engine.models.swing import SwingType

class SwingQualityAnalyzer:
    """
    Evaluates the quality of detected swing points.
    """
    def __init__(self) -> None:
        self.confidence_multiplier = 10.0

    def analyze(
        self,
        swing: SwingPoint,
        candles: list[OHLCVCandle],
    ) -> None:
        if swing.price <= 0:
            return
        if swing.index >= len(candles) - 1:
            return

        future = candles[swing.index + 1 :]

        if swing.swing_type == SwingType.HIGH:
            lowest = min(c.low for c in future)

            move = (
                (swing.price - lowest)
                / swing.price
            ) * 100

        else:
            highest = max(c.high for c in future)

            move = (
                (highest - swing.price)
                / swing.price
            ) * 100

        swing.evidence.move_percent = round(move, 2)

        swing.evidence.confidence = min(
            round(move * self.confidence_multiplier, 2),
            100,
        )