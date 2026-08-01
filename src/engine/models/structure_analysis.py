"""
Domain models for market structure analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.market_structure import StructurePoint


class StructureBias(Enum):
    UNKNOWN = "UNKNOWN"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(slots=True, frozen=True)
class StructureAnalysis:
    """
    Summary of the current market structure.
    """

    previous_bias: StructureBias
    current_bias: StructureBias

    previous_protected_structure: StructurePoint | None
    current_protected_structure: StructurePoint | None

    latest: StructurePoint | None

    bullish_sequence: int
    bearish_sequence: int

    structure_intact: bool

    reasons: list[str] = field(default_factory=list)