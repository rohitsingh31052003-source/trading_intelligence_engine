from engine.config.levels_config import StructuralLevelConfig
from engine.models.structural_level_evidence import StructuralLevelEvidence
from engine.models.swing import SwingStrength


class StructuralStrengthEngine:
    def __init__(self, config=None):

        self.config = config or StructuralLevelConfig()

    def _origin_score(self, level) -> float:
        """
        Score based on the quality of the originating swing.
        """

        origin_map = {
            SwingStrength.WEAK: 10.0,
            SwingStrength.NORMAL: 15.0,
            SwingStrength.STRONG: 20.0,
            SwingStrength.MAJOR: 25.0,
        }

        return origin_map[level.originating_strength]

    def _freshness_score(self, level):

        age = level.age_in_bars

        score = self.config.max_freshness_score - age

        return max(
            0.0,
            min(
                score,
                self.config.max_freshness_score,
            ),
        )

    def _touch_score(self, level):

        table = {
            1: 5.0,
            2: 9.0,
            3: 12.0,
            4: 14.0,
        }

        return table.get(
            level.touches,
            15.0,
        )

    def _defense_score(self, level):

        return min(
            level.successful_defenses * 5.0,
            self.config.max_defense_score,
        )

    def _penalty_score(self, level):

        return min(
            level.failed_tests * 5.0,
            self.config.max_penalty_score,
        )

    def _build_evidence(self, level):

        return StructuralLevelEvidence(
            origin_score=self._origin_score(level),
            defense_score=self._defense_score(level),
            freshness_score=self._freshness_score(level),
            touch_score=self._touch_score(level),
            penalty_score=self._penalty_score(level),
        )

    def evaluate(
        self,
        levels,
    ):

        evaluated = []

        for level in levels:
            evidence = self._build_evidence(level)

            evaluated.append(
                level.with_strength(
                    evidence=evidence,
                )
            )

        return evaluated
