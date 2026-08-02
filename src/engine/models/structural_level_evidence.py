"""
Evidence used to evaluate structural level strength.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class StructuralLevelEvidence:
    """
    Evidence contributing to the strength of a structural level.
    """

    origin_score: float = 0.0
    defense_score: float = 0.0
    freshness_score: float = 0.0
    touch_score: float = 0.0
    penalty_score: float = 0.0

    @property
    def total(self) -> float:
        """
        Overall structural strength.
        """
        return (
            self.origin_score
            + self.defense_score
            + self.freshness_score
            + self.touch_score
            - self.penalty_score
        )