"""
Configuration for the market setup / confluence intelligence layer
(Sprint 11Q).

All thresholds live here; no magic numbers are embedded in the engine.
The defaults are deliberately simple and deterministic. They are NOT
calibrated to any market; they express interpretable, rule-based
confluence criteria.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SetupConfluenceConfig:
    """
    Configuration for the setup / confluence engine.

    Threshold semantics (documented in the engine):

    min_supporting_for_watch
        Minimum number of ALIGNED independent evidence sources
        required to classify a point as ``WATCH`` (given a clear
        candidate direction and no disqualifying conflict). Default
        ``1``.

    min_supporting_for_potential_setup
        Minimum number of ALIGNED independent evidence sources
        required to classify a point as ``POTENTIAL_SETUP`` (given a
        clear candidate direction and no disqualifying conflict).
        Default ``3``. This enforces the "multiple independent
        evidence sources agree" requirement.

    conflicting_blocks_potential_setup
        When True (default), the presence of any CONFLICTING evidence
        prevents a ``POTENTIAL_SETUP`` classification (the point is
        capped at ``WATCH`` at most). When False, conflicts are
        recorded but do not by themselves block the classification;
        the confluence score still governs. The default keeps
        conflicts structurally disqualifying so a conflicting setup is
        never silently reported as a clean candidate.

    neutral_candle_contributes
        When True, a NEUTRAL candle pattern (doji / inside bar)
        contributes to the confluence score as an ALIGNED neutral
        observation. When False (default), neutral candle patterns are
        recorded as NEUTRAL evidence and do NOT count toward the
        confluence score — only directional candle patterns (hammer /
        shooting star / engulfing) count. The default demands
        directional candle confirmation.

    range_caps_classification
        When True (default), an active consolidation range
        (``IN_RANGE``) caps the classification at ``WATCH``: a range
        is not treated as a directional trend setup. The range
        evidence is still recorded. When False, range state does not
        alter the classification. The default ensures the engine does
        not blindly treat trend evidence as a trend setup when the
        market is explicitly classified as a range.

    min_structure_for_evidence
        Minimum number of confirmed structures required before
        structure evidence is considered directional (rather than
        ABSENT). Default ``2``. Below this, structure evidence is
        ABSENT and cannot contribute to the confluence score.
    """

    min_supporting_for_watch: int = 1
    min_supporting_for_potential_setup: int = 3
    conflicting_blocks_potential_setup: bool = True
    neutral_candle_contributes: bool = False
    range_caps_classification: bool = True
    min_structure_for_evidence: int = 2

    def __post_init__(self) -> None:
        if self.min_supporting_for_watch < 1:
            raise ValueError(
                "min_supporting_for_watch must be at least 1",
            )
        if self.min_supporting_for_potential_setup < 1:
            raise ValueError(
                "min_supporting_for_potential_setup must be at least 1",
            )
        if (
            self.min_supporting_for_potential_setup
            < self.min_supporting_for_watch
        ):
            raise ValueError(
                "min_supporting_for_potential_setup must be >= "
                "min_supporting_for_watch",
            )
        if self.min_structure_for_evidence < 1:
            raise ValueError(
                "min_structure_for_evidence must be at least 1",
            )
