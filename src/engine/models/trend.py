"""
Trend / Market Regime domain models.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from engine.intelligence.evidence.trend_evidence import TrendEvidence
from engine.models.structure_analysis import StructureBias


class TrendState(Enum):
    UNKNOWN = "UNKNOWN"

    RANGING = "RANGING"

    TRANSITION = "TRANSITION"

    WEAK_BULLISH = "WEAK_BULLISH"

    BULLISH = "BULLISH"

    WEAK_BEARISH = "WEAK_BEARISH"

    BEARISH = "BEARISH"


@dataclass(slots=True, frozen=True)
class TrendResult:
    """
    Final structural trend assessment.
    """

    state: TrendState

    evidence: TrendEvidence

    previous_bias: StructureBias = StructureBias.UNKNOWN

    current_bias: StructureBias = StructureBias.UNKNOWN

    reasons: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.evidence.total
