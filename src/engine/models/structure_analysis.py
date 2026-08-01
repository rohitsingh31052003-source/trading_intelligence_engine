"""
Domain models for market structure analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from engine.models.market_structure import StructurePoint


class StructureBias(Enum):
    """
    Overall directional bias of market structure.
    """

    UNKNOWN = "UNKNOWN"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(slots=True, frozen=True)
class StructureAnalysis:
    """
    Summary of the current market structure.
    """

    bias: StructureBias

    latest: StructurePoint| None

    bullish_sequence: int

    bearish_sequence: int

    structure_intact: bool

    protected_structure: StructurePoint | None = None