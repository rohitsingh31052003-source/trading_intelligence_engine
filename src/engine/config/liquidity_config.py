"""
Configuration for liquidity detection.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class LiquidityConfig:
    """
    Configuration for liquidity pool detection.
    """

    # Maximum percentage difference between two swing prices
    # for them to be considered equal highs/lows.
    equal_level_tolerance_percent: float = 0.15

    # Minimum confirmed swings required to form a liquidity pool.
    minimum_pool_size: int = 2