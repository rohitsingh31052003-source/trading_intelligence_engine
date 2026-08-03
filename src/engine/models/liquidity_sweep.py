from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.liquidity import LiquidityPool


class LiquiditySweepType(Enum):
    NONE = "NONE"
    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


@dataclass(slots=True, frozen=True)
class LiquiditySweep:

    pool: LiquidityPool

    detected: bool

    sweep_type: LiquiditySweepType

    sweep_price: float | None

    sweep_timestamp: datetime | None

    confidence: float

    reasons: list[str]