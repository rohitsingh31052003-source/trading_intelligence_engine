"""
Support and Resistance detection engine.
"""

from __future__ import annotations
from engine.config.levels_config import StructuralLevelConfig
from engine.models.support_resistance import (
    LevelStatus,
    LevelType,
    StructuralLevel,
)
from engine.models.swing import SwingType


class StructuralLevelsEngine:
    """
    Detects structural support and resistance levels.
    """

    def __init__(
        self,
        config: StructuralLevelConfig  | None = None,
    ) -> None:
        self.config = config or StructuralLevelConfig()

    def detect(self, swings):

        levels: list[StructuralLevel] = []

        for swing in swings:

            if not swing.confirmed:
                continue

            level_type = (
                LevelType.RESISTANCE
                if swing.swing_type == SwingType.HIGH
                else LevelType.SUPPORT
            )

            levels.append(
                StructuralLevel(
                    price=swing.price,
                    created_at=swing.timestamp,
                    level_type=level_type,
                    status=LevelStatus.ACTIVE,
                    touches=1,
                    strength=30.0,
                    originating_strength=swing.strength,
                )
            )

        return self._merge_levels(levels)

    def _merge_levels(
        self,
        levels: list[StructuralLevel],
) -> list[StructuralLevel]:

        merged: list[StructuralLevel] = []

        for level in levels:

            merged_into_existing = False

            for i, existing in enumerate(merged):

                if existing.level_type != level.level_type:
                    continue

                average_price = (
                    existing.price + level.price
                ) / 2

                difference_percent = (
                    abs(existing.price - level.price)
                    / average_price
                ) * 100

                if (
                    difference_percent
                    <= self.config.merge_tolerance_percent
                ):

                    touches = (
                        existing.touches
                        + level.touches
                    )

                    strength = (
                        30.0
                        + 5.0 * (touches - 1)
                    )

                    merged[i] = StructuralLevel(
                        price=average_price,
                        created_at=existing.created_at,
                        level_type=existing.level_type,
                        status=existing.status,
                        touches=touches,
                        strength=strength,
                        originating_strength=existing.originating_strength,
                    )

                    merged_into_existing = True
                    break

            if not merged_into_existing:
                merged.append(level)

        return merged