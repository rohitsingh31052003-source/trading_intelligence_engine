"""
Trend confidence evidence model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TrendEvidence:
    """
    Individual evidence scores used to determine
    trend confidence.
    """

    structure_score: float = 0.0
    sequence_score: float = 0.0
    bos_score: float = 0.0
    choch_score: float = 0.0
    quality_score: float = 0.0

    @property
    def total(self) -> float:
        """
        Total confidence score.
        """
        return (
            self.structure_score
            + self.sequence_score
            + self.bos_score
            + self.choch_score
            + self.quality_score
        )
