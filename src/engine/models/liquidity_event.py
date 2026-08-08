"""
Domain models for liquidity events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.liquidity import LiquidityPool


class LiquidityEventType(Enum):
    """
    Classification of a liquidity event.
    """

    NONE = "NONE"
    SWEEP = "SWEEP"
    BREAKOUT = "BREAKOUT"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"


@dataclass(frozen=True, slots=True)
class LiquidityEventEvidence:
    """
    Evidence used to classify and score a liquidity event.
    """

    liquidity_breached: bool

    rejection_confirmed: bool

    continuation_confirmed: bool

    candles_checked: int

    rejection_strength: float


@dataclass(frozen=True, slots=True)
class LiquidityEvent:
    """
    Immutable result of liquidity event analysis.
    """

    pool: LiquidityPool

    detected: bool

    event_type: LiquidityEventType

    event_price: float | None

    event_timestamp: datetime | None

    confidence: float

    evidence: LiquidityEventEvidence

    reasons: tuple[str, ...]