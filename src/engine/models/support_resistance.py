"""
Domain models for structural support and resistance levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from dataclasses import replace

from engine.models.structural_level_evidence import (
    StructuralLevelEvidence,
)
from engine.models.swing import SwingStrength


class LevelType(Enum):
    """
    Type of structural level.
    """

    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class LevelStatus(Enum):
    """
    Current lifecycle stage of the structural level.
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

    successful_defenses: int

    failed_tests: int

    last_touch: datetime

    broken_at: datetime | None

    age_in_bars: int

    originating_strength: SwingStrength

    evidence: StructuralLevelEvidence

    @property
    def strength(self) -> float:
        """
        Overall strength derived from evidence.
        """
        return self.evidence.total

    def with_strength(
        self,
        evidence,
    ):
        return replace(
            self,
            evidence=evidence,
        )
