"""
Domain models for liquidity pools.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.strength import StrengthCategory


class LiquidityType(Enum):
    """
    Type of liquidity pool.
    """

    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


class LiquidityStatus(Enum):
    """
    Current state of the liquidity pool.
    """

    ACTIVE = "ACTIVE"
    SWEPT = "SWEPT"


@dataclass(slots=True, frozen=True)
class LiquidityPool:
    """
    Represents a detected liquidity pool formed by
    multiple nearly equal swing highs or lows.
    """

    price: float

    liquidity_type: LiquidityType

    created_at: datetime

    swing_count: int

    strength: float

    category: StrengthCategory

    status: LiquidityStatus