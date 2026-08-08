"""
Configuration for liquidity event confirmation and strength.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiquidityEventConfig:
    """
    Configuration for liquidity event confirmation,
    classification, and confidence scoring.
    """

    confirmation_candles: int = 3

    sweep_confidence: float = 90.0

    breakout_confidence: float = 85.0

    minimum_rejection_ratio: float = 0.5

    maximum_confidence: float = 100.0