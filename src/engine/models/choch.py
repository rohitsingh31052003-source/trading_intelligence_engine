"""
Change of Character domain models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.structure_analysis import StructureBias


class CHOCHType(Enum):
    NONE = "NONE"
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(slots=True, frozen=True)
class CHOCHResult:
    """
    Result produced by the CHOCHEngine.
    """

    detected: bool

    choch_type: CHOCHType

    previous_bias: StructureBias

    new_bias: StructureBias

    confidence: float = 0.0

    reasons: list[str] = field(default_factory=list)