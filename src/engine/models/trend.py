"""
Trend / Market Regime domain models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.structure_analysis import StructureBias


class TrendState(Enum):
    UNKNOWN = "UNKNOWN"

    RANGING = "RANGING"

    BULLISH = "BULLISH"

    BEARISH = "BEARISH"

    TRANSITION = "TRANSITION"


@dataclass(slots=True, frozen=True)
class TrendResult:
    """
    Final structural trend assessment.
    """

    state: TrendState

    confidence: float = 0.0

    previous_bias: StructureBias = StructureBias.UNKNOWN

    current_bias: StructureBias = StructureBias.UNKNOWN

    reasons: list[str] = field(default_factory=list)