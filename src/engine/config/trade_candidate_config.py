"""
Configuration for the trade candidate generation layer (Sprint 11R).

All thresholds live here; no magic numbers are embedded in the engine.
The defaults are deliberately conservative and deterministic. They are
NOT calibrated to any market; they express interpretable, rule-based
candidate-generation criteria.

The candidate layer reuses the Sprint 11Q setup classification rather
than inventing a separate scoring system. This config only governs the
additional gating that turns a ``POTENTIAL_SETUP`` assessment into a
``CANDIDATE`` trade candidate (or downgrades it to ``WATCH``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TradeCandidateConfig:
    """
    Configuration for ``TradeCandidateEngine``.

    Threshold semantics (documented in the engine):

    min_confluence_for_candidate
        Minimum number of ALIGNED independent evidence sources (the
        Sprint 11Q confluence score) required to promote a
        ``POTENTIAL_SETUP`` to a ``CANDIDATE``. Default ``3``. This
        mirrors the default Sprint 11Q
        ``min_supporting_for_potential_setup`` so the candidate layer
        does not silently weaken the setup layer's evidence bar.

    allow_range_setups
        When False (default), a ``POTENTIAL_SETUP`` that occurs while
        the market is classified ``IN_RANGE`` is downgraded to
        ``WATCH`` rather than promoted to ``CANDIDATE``. A range is
        not treated as a directional trade setup by default. When
        True, range-bound potential setups may be promoted (still
        subject to the confluence requirement). The default is
        conservative.

    min_risk_reward_ratio
        Optional minimum risk/reward ratio a candidate must satisfy
        to remain a ``CANDIDATE``. ``None`` (default) disables the
        ratio gate. When set, a candidate whose geometry is complete
        but whose ``risk_reward_ratio`` falls below the threshold is
        downgraded to ``WATCH`` with a clear reason. Candidates with
        incomplete geometry (no computable ratio) are NOT rejected by
        this gate; their incompleteness is reported honestly instead.
    """

    min_confluence_for_candidate: int = 3
    allow_range_setups: bool = False
    min_risk_reward_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.min_confluence_for_candidate < 1:
            raise ValueError(
                "min_confluence_for_candidate must be at least 1",
            )
        if (
            self.min_risk_reward_ratio is not None
            and self.min_risk_reward_ratio <= 0
        ):
            raise ValueError(
                "min_risk_reward_ratio must be positive when set",
            )


__all__ = ["TradeCandidateConfig"]
