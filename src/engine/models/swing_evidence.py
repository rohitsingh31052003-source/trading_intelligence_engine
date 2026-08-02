"""
Evidence collected for a swing point.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class SwingEvidence:
    """
    Stores evidence collected by different analyzers.
    """

    move_percent: float = 0.0

    confidence: float = 0.0

    volume_score: float = 0.0

    atr_score: float = 0.0

    liquidity_score: float = 0.0

    structure_score: float = 0.0
