"""
Configuration for the trade opportunity filter / ranking layer
(Sprint 11T).

All eligibility thresholds, ranking priorities and surfaced-opportunity
caps live here; no magic numbers are embedded in the engine. The
defaults are deliberately conservative and deterministic. They are NOT
calibrated to any market; they express interpretable, rule-based
opportunity-selection criteria.

The opportunity layer is DESCRIPTIVE. It identifies the best AVAILABLE
technical opportunity among the eligible candidates at a point in time.
It is NOT a probability of success, NOT a profitability prediction, and
NOT a trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.trade_candidate import CandidateStatus
from engine.models.trade_decision import DecisionClassification


class RankingPriority(Enum):
    """
    The deterministic priority dimensions used to rank eligible
    opportunities strongest-first.

    The enum members are listed in the EXACT order the engine applies
    them. Ties at each priority are broken by the next priority, and
    finally by a fully deterministic, direction-symmetric tie-breaker
    so identical inputs always produce identical rankings and no two
    eligible opportunities share a rank.

    A SHORT candidate never ranks above a LONG candidate (or vice
    versa) merely because of its direction: direction only influences
    the ranking through the underlying evidence / decision scores.
    """

    ELIGIBILITY = "ELIGIBILITY"
    DECISION_CLASSIFICATION = "DECISION_CLASSIFICATION"
    DECISION_SCORE = "DECISION_SCORE"
    GEOMETRY_COMPLETENESS = "GEOMETRY_COMPLETENESS"
    RISK_REWARD = "RISK_REWARD"
    CONFLUENCE = "CONFLUENCE"
    CONFLICT_FREE = "CONFLICT_FREE"
    SETUP_STRENGTH = "SETUP_STRENGTH"
    DETERMINISTIC_TIEBREAK = "DETERMINISTIC_TIEBREAK"


# The canonical ranking order, expressed as a tuple so it can be
# surfaced verbatim by the report layer (the implementation MUST make
# it obvious why candidate A ranks above candidate B).
RANKING_PRIORITY_ORDER: tuple[RankingPriority, ...] = (
    RankingPriority.ELIGIBILITY,
    RankingPriority.DECISION_CLASSIFICATION,
    RankingPriority.DECISION_SCORE,
    RankingPriority.GEOMETRY_COMPLETENESS,
    RankingPriority.RISK_REWARD,
    RankingPriority.CONFLUENCE,
    RankingPriority.CONFLICT_FREE,
    RankingPriority.SETUP_STRENGTH,
    RankingPriority.DETERMINISTIC_TIEBREAK,
)


@dataclass(slots=True, frozen=True)
class TradeOpportunityConfig:
    """
    Configuration for ``TradeOpportunityEngine``.

    Eligibility gates (a candidate must pass ALL active gates to be
    ELIGIBLE; an inactive gate is satisfied trivially):

    allowed_candidate_statuses
        Sprint 11R candidate statuses that may be considered an
        opportunity. Default excludes only ``NO_CANDIDATE`` (a
        NO_CANDIDATE point must never become an opportunity).

    allowed_decision_classifications
        Sprint 11S decision classifications that may be considered an
        opportunity. Default excludes only ``REJECTED`` (a REJECTED
        decision must never become an opportunity).

    min_decision_score
        Minimum Sprint 11S decision score (inclusive) required for
        eligibility. Default ``40`` (matches the Sprint 11S watch
        threshold so an opportunity is at least a WATCH-strength
        candidate). ``0`` disables the gate.

    require_geometry
        When True, incomplete entry / stop / target geometry makes a
        candidate INELIGIBLE. Default ``False`` (incomplete geometry
        is reported honestly and may still be a WATCH opportunity,
        matching the descriptive Sprint 11S spirit). Incomplete
        geometry is never fabricated.

    min_risk_reward_ratio
        Minimum risk/reward ratio (inclusive) required for
        eligibility WHEN the ratio is available. ``None`` disables the
        gate. A candidate with incomplete geometry (ratio unavailable)
        is NOT rejected by this gate (it is reported honestly instead).
        Default ``None``.

    disqualify_on_conflict
        When True, any conflicting evidence makes a candidate
        INELIGIBLE (a conflicting candidate can never silently become
        the best opportunity). Default ``False``: conflicting evidence
        is recorded and the candidate may remain a WATCH opportunity
        (never silently BEST). When True, conflicting candidates are
        filtered out entirely.

    require_no_conflict_for_best
        When True, a candidate with conflicting evidence can never be
        the BEST opportunity. Default ``True`` so a conflicting
        candidate never silently becomes the best opportunity. A
        conflicting eligible candidate that is otherwise eligible
        remains a WATCH opportunity (never an ALTERNATIVE, because an
        alternative must itself be best-quality).

    require_geometry_for_best
        When True (default), a candidate with incomplete entry / stop /
        target geometry can never be the BEST opportunity and can never
        be an ALTERNATIVE (it remains a WATCH opportunity). Incomplete
        geometry is reported honestly, never fabricated.

    min_confluence_for_best
        Minimum confluence score (inclusive) required for a candidate to
        be the BEST opportunity OR an ALTERNATIVE. Default ``0``
        (disabled) — confluence is already reflected in the Sprint 11S
        decision score. When set, a candidate below the threshold may
        remain a WATCH opportunity but never BEST/ALTERNATIVE.

    Surfacing policy:

    max_surfaced_opportunities
        Maximum number of opportunities to surface (best + alternatives)
        at a single evaluation point. ``None`` means unlimited (surface
        every best-quality eligible opportunity beyond the best as an
        alternative). Default ``None``. ``1`` surfaces only the best
        opportunity.

    Ranking:

    ranking_order
        The canonical priority order applied to rank eligible
        opportunities. Exposed for documentation / auditability; the
        engine always applies the canonical
        ``RANKING_PRIORITY_ORDER``.
    """

    allowed_candidate_statuses: tuple[CandidateStatus, ...] = (
        CandidateStatus.CANDIDATE,
        CandidateStatus.WATCH,
    )
    allowed_decision_classifications: tuple[DecisionClassification, ...] = (
        DecisionClassification.PREFERRED,
        DecisionClassification.QUALIFIED,
        DecisionClassification.WATCH,
    )

    min_decision_score: int = 40

    require_geometry: bool = False
    min_risk_reward_ratio: float | None = None

    disqualify_on_conflict: bool = False
    require_no_conflict_for_best: bool = True
    require_geometry_for_best: bool = True

    max_surfaced_opportunities: int | None = None
    min_confluence_for_best: int = 0

    ranking_order: tuple[RankingPriority, ...] = field(
        default_factory=lambda: tuple(RANKING_PRIORITY_ORDER),
    )

    def __post_init__(self) -> None:
        if self.min_decision_score < 0:
            raise ValueError(
                "min_decision_score must be non-negative.",
            )
        if self.min_risk_reward_ratio is not None and self.min_risk_reward_ratio <= 0:
            raise ValueError(
                "min_risk_reward_ratio must be positive when set.",
            )
        if self.min_confluence_for_best < 0:
            raise ValueError(
                "min_confluence_for_best must be non-negative.",
            )
        if self.max_surfaced_opportunities is not None and self.max_surfaced_opportunities < 1:
            raise ValueError(
                "max_surfaced_opportunities must be >= 1 when set.",
            )
        if not self.allowed_candidate_statuses:
            raise ValueError(
                "allowed_candidate_statuses must not be empty.",
            )
        if not self.allowed_decision_classifications:
            raise ValueError(
                "allowed_decision_classifications must not be empty.",
            )


__all__ = [
    "RANKING_PRIORITY_ORDER",
    "RankingPriority",
    "TradeOpportunityConfig",
]
