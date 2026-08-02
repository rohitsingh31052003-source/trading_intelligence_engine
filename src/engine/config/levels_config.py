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

    # Origin
    max_origin_score: float = 25.0
    origin_confidence_multiplier: float = 0.25

    # Freshness
    max_freshness_score: float = 15.0
    freshness_decay_bars: int = 10

    # Touches
    max_touch_score: float = 15.0

    # Defenses
    max_defense_score: float = 20.0

    # Penalties
    max_penalty_score: float = 20.0
