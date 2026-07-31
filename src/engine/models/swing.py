"""
Domain models for swing analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SwingType(Enum):
    """Type of swing."""

    HIGH = "HIGH"
    LOW = "LOW"


class SwingStrength(Enum):
    """Relative importance of a swing."""

    WEAK = "WEAK"
    NORMAL = "NORMAL"
    STRONG = "STRONG"
    MAJOR = "MAJOR"


@dataclass(slots=True, frozen=True)
class SwingPoint:
    """
    Represents one confirmed swing point.
    """

    timestamp: datetime
    index: int
    price: float
    swing_type: SwingType
    confirmed: bool
    strength: SwingStrength = SwingStrength.NORMAL