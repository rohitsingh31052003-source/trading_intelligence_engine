"""
Configuration for structural support and resistance levels.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class StructuralLevelConfig:
    """
    Configuration for structural level detection.
    """

    merge_tolerance_percent: float = 0.30