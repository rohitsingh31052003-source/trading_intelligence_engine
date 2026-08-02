"""
Support and Resistance detection engine.
"""

from __future__ import annotations

from engine.config.levels_config import StructuralLevelConfig
from engine.models.structural_level_evidence import (
    StructuralLevelEvidence,
)
from engine.models.support_resistance import (
    LevelStatus,
    LevelType,
    StructuralLevel,
)
from engine.models.swing import SwingType
from engine.models.swing import SwingStrength


class StructuralLevelsEngine:
    """
    Detects structural support and resistance levels.
    """

    def __init__(
        self,
        config: StructuralLevelConfig | None = None,
    ) -> None:
        self.config = config or StructuralLevelConfig()

    def detect(self, swings) -> list[StructuralLevel]:
        """
        Detect structural levels from confirmed swings.
        """

        levels: list[StructuralLevel] = []

        # -----------------------------
        # Create initial levels
        # -----------------------------
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
                    originating_strength=swing.strength,
                    successful_defenses=0,
                    failed_tests=0,
                    last_touch=swing.timestamp,
                    broken_at=None,
                    age_in_bars=0,
                    evidence=StructuralLevelEvidence(),
                )
            )

        # -----------------------------
        # Merge nearby levels
        # -----------------------------
        levels = self._merge_levels(levels)

        # -----------------------------
        # Evaluate evidence
        # -----------------------------
        evaluated: list[StructuralLevel] = []

        for level in levels:

            evidence = self._calculate_evidence(level)

            evaluated.append(
                StructuralLevel(
                    price=level.price,
                    created_at=level.created_at,
                    level_type=level.level_type,
                    status=level.status,
                    touches=level.touches,
                    originating_strength=level.originating_strength,
                    successful_defenses=level.successful_defenses,
                    failed_tests=level.failed_tests,
                    last_touch=level.last_touch,
                    broken_at=level.broken_at,
                    age_in_bars=level.age_in_bars,
                    evidence=evidence,
                )
            )

        return evaluated

    def _merge_levels(
        self,
        levels: list[StructuralLevel],
    ) -> list[StructuralLevel]:
        """
        Merge nearby structural levels.
        """

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

                    merged[i] = StructuralLevel(
                        price=average_price,
                        created_at=min(
                            existing.created_at,
                            level.created_at,
                        ),
                        level_type=existing.level_type,
                        status=existing.status,
                        touches=existing.touches + level.touches,
                        originating_strength=existing.originating_strength,
                        successful_defenses=(
                            existing.successful_defenses
                            + level.successful_defenses
                        ),
                        failed_tests=(
                            existing.failed_tests
                            + level.failed_tests
                        ),
                        last_touch=max(
                            existing.last_touch,
                            level.last_touch,
                        ),
                        broken_at=existing.broken_at,
                        age_in_bars=min(
                            existing.age_in_bars,
                            level.age_in_bars,
                        ),
                        evidence=StructuralLevelEvidence(),
                    )

                    merged_into_existing = True
                    break

            if not merged_into_existing:
                merged.append(level)

        return merged

    def _calculate_evidence(
        self,
        level: StructuralLevel,
    ) -> StructuralLevelEvidence:
        """
        Calculate evidence supporting a structural level.
        """

        # Origin Quality (Max 25)
        origin_map = {
            SwingStrength.WEAK: 10.0,
            SwingStrength.NORMAL: 15.0,
            SwingStrength.STRONG: 20.0,
            SwingStrength.MAJOR: 25.0,
        }
        origin_score = origin_map[level.originating_strength]

        # Defense Score (Max 20)
        defense_score = min(
            level.successful_defenses * 5.0,
            20.0,
        )

        # Freshness (Max 15)
        freshness_score = max(
            15.0 - level.age_in_bars,
            0.0,
        )

        # Touch Quality (Max 20)
        touch_score = min(
            level.touches * 5.0,
            20.0,
        )

        # Penalty (Max -20)
        penalty_score = min(
            level.failed_tests * 5.0,
            20.0,
        )

        return StructuralLevelEvidence(
            origin_score=origin_score,
            defense_score=defense_score,
            freshness_score=freshness_score,
            touch_score=touch_score,
            penalty_score=penalty_score,
        )

    def _calculate_strength(
        self,
        level: StructuralLevel,
    ) -> float:
        """
        Calculate the final structural strength.
        """

        strength = self._calculate_evidence(level).total

        return max(0.0, min(100.0, strength))