from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.liquidity import LiquidityPool


class LiquidityEventType(Enum):

    NONE = "NONE"

    SWEEP = "SWEEP"

    BREAKOUT = "BREAKOUT"

    FAILED_BREAKOUT = "FAILED_BREAKOUT"


@dataclass(slots=True, frozen=True)
class LiquidityEvent:

    pool: LiquidityPool

    detected: bool

    event_type: LiquidityEventType

    event_price: float | None

    event_timestamp: datetime | None

    confidence: float

    reasons: list[str]