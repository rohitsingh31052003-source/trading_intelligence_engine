"""
Configuration for the market context intelligence layer (Sprint 11P).

All thresholds live here; no magic numbers are embedded in the
detection logic. The defaults are deliberately simple and
deterministic. They are NOT calibrated to any market.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class RangeDetectionConfig:
    """
    Configuration for the consolidation / range detection engine.

    Threshold semantics (documented in the engine):

    min_swings
        Minimum number of confirmed swings that must exist before a
        range classification is attempted. Default ``4``.

    range_tolerance
        A swing high (or low) is considered "flat" (part of a range)
        when its price is within this fraction of the average of its
        group. Default ``0.02`` (2%). A tighter tolerance demands more
        precise horizontal consolidation.

    min_flat_count
        Minimum number of flat highs AND flat lows required to declare
        an active range. Default ``2`` each.

    max_recent_structure_strength
        A range is suppressed when the most recent confirmed structures
        show strong directional dominance (more than this many
        consecutive HH+HL or LH+LL). Default ``2``.
    """

    min_swings: int = 4
    range_tolerance: float = 0.02
    min_flat_count: int = 2
    max_recent_structure_strength: int = 2

    def __post_init__(self) -> None:
        if self.min_swings < 1:
            raise ValueError("min_swings must be at least 1")
        if self.range_tolerance <= 0:
            raise ValueError("range_tolerance must be positive")
        if self.min_flat_count < 1:
            raise ValueError("min_flat_count must be at least 1")
        if self.max_recent_structure_strength < 1:
            raise ValueError(
                "max_recent_structure_strength must be at least 1",
            )


@dataclass(slots=True, frozen=True)
class SupportResistanceContextConfig:
    """
    Configuration for support / resistance context construction.

    Threshold semantics (documented in the engine):

    proximity_threshold
        Current price is ``NEAR_SUPPORT`` (or ``NEAR_RESISTANCE``) when
        it is within this fraction of the support (or resistance) level.
        Default ``0.02`` (2%).

    max_levels
        Maximum number of recent confirmed swing highs (resp. lows)
        considered when building resistance (resp. support) context.
        Default ``5``.
    """

    proximity_threshold: float = 0.02
    max_levels: int = 5

    def __post_init__(self) -> None:
        if self.proximity_threshold <= 0:
            raise ValueError("proximity_threshold must be positive")
        if self.max_levels < 1:
            raise ValueError("max_levels must be at least 1")


@dataclass(slots=True, frozen=True)
class MarketContextConfig:
    """
    Configuration for ``MarketContextEngine``.

    Bundles the sub-configurations. The swing lookback used to detect
    the underlying structure is left to the existing ``SwingConfig``
    (passed to the pipeline); this config only governs the new
    market-context intelligence.
    """

    range: RangeDetectionConfig = field(  # noqa: A003
        default_factory=RangeDetectionConfig,
    )
    support_resistance: SupportResistanceContextConfig = field(
        default_factory=SupportResistanceContextConfig,
    )
    recent_structure_count: int = 3

    def __post_init__(self) -> None:
        if self.recent_structure_count < 1:
            raise ValueError("recent_structure_count must be at least 1")
