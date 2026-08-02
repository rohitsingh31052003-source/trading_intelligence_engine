"""
Domain models for structural support and resistance levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.swing import SwingStrength


class LevelType(Enum):
    """
    Type of structural level.
    """

    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class LevelStatus(Enum):
    """
    Current state of the structural level.
    """

    ACTIVE = "ACTIVE"
    BROKEN = "BROKEN"
    FLIPPED = "FLIPPED"


@dataclass(slots=True, frozen=True)
class StructuralLevel:
    """
    Represents a structural support or resistance level.
    """

    price: float

    created_at: datetime

    level_type: LevelType

    status: LevelStatus

    touches: int

    strength: float

    originating_strength: SwingStrength