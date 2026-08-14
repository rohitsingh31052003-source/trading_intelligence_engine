"""
Domain models for the trade opportunity filter / ranking layer
(Sprint 11T).

These models describe a deterministic, descriptive TRADE OPPORTUNITY
view over one or more Sprint 11S ``TradeDecision`` objects. The
opportunity layer is the fifth step of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)  <- this layer
    6. TRADE VALIDATION             (future)
    7. SIGNAL / EXECUTION           (future)

A ``TradeOpportunity`` is NOT a trading signal. It is a DESCRIPTIVE
classification of whether a candidate should be surfaced as the best
available trade opportunity at this evaluation point. It is NOT a
probability of success, NOT a profitability prediction, NOT a
guarantee, and NOT a trading recommendation.

DESIGN PRINCIPLE — distinct from Sprint 11S:

Sprint 11S answers "How strong is this candidate?" (a per-candidate
decision classification + score). Sprint 11T answers "Should this
candidate actually be surfaced as the best available trade opportunity
at this evaluation point?" (an eligibility filter + a relative ranking
+ an opportunity status). The two views are related but not identical:
a strong PREFERRED candidate is a strong opportunity only when it also
passes the opportunity eligibility gates; an opportunity status
(BEST / ALTERNATIVE / WATCH / NO_OPPORTUNITY) is assigned RELATIVE to
the other candidates available at the same point in time.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently reported as a real value. In particular, risk/reward is
  ``None`` when the underlying candidate's geometry is incomplete.
* ``__post_init__`` validates internal consistency so the engine never
  produces contradictory states and hand-construction bugs surface
  early.
* No business logic lives here; the models are data carriers. The
  ``TradeDecision`` (Sprint 11S) is retained BY REFERENCE and never
  modified, so no candidate / decision logic is duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.trade_decision import TradeDecision


class OpportunityStatus(Enum):
    """
    Descriptive opportunity status for a trade candidate at one point
    in time.

    This enum is DELIBERATELY DISTINCT from Sprint 11S's
    ``DecisionClassification`` (REJECTED / WATCH / QUALIFIED /
    PREFERRED). Sprint 11S describes a candidate's *evidence strength*;
    Sprint 11T describes whether a candidate should be *surfaced as an
    opportunity* relative to its peers.

    NO_OPPORTUNITY
        The candidate is not a trade opportunity at this point: it
        failed the opportunity eligibility gates (e.g. NO_CANDIDATE
        status, REJECTED decision, score below the configured minimum,
        or a disqualifying conflict when configured to disqualify). It
        is filtered out and never ranked as a surfaced opportunity.

    WATCH
        The candidate is a legitimate monitorable opportunity but is
        not strong enough / clean enough to be the best or an
        alternative. Eligible but lower-ranked (e.g. incomplete
        geometry, weaker score, a non-disqualifying conflict).

    ALTERNATIVE_OPPORTUNITY
        A legitimate, eligible opportunity that ranks below the best
        opportunity. Surfaced only when at least one strictly stronger
        eligible opportunity exists. NEVER manufactured: when only one
        eligible opportunity exists, no alternative is produced.

    BEST_OPPORTUNITY
        The single strongest eligible opportunity at this evaluation
        point. Assigned to exactly one candidate (rank 1 among the
        eligible). DESCRIPTIVE: identifies the best AVAILABLE technical
        opportunity; does NOT predict success or profitability.
    """

    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    WATCH = "WATCH"
    ALTERNATIVE_OPPORTUNITY = "ALTERNATIVE_OPPORTUNITY"
    BEST_OPPORTUNITY = "BEST_OPPORTUNITY"

    @property
    def rank_value(self) -> int:
        """Higher is stronger; used for deterministic ordering."""

        return _OPP_RANK_VALUE[self]


_OPP_RANK_VALUE = {
    OpportunityStatus.NO_OPPORTUNITY: 0,
    OpportunityStatus.WATCH: 1,
    OpportunityStatus.ALTERNATIVE_OPPORTUNITY: 2,
    OpportunityStatus.BEST_OPPORTUNITY: 3,
}


class EligibilityStatus(Enum):
    """
    Whether a candidate is eligible to be considered a trade
    opportunity, before any relative ranking is applied.

    ELIGIBLE
        The candidate passed all opportunity eligibility gates and may
        be ranked as a surfaced opportunity.

    INELIGIBLE
        The candidate failed one or more eligibility gates and is
        filtered out (NO_OPPORTUNITY). The specific failing gate is
        recorded as a rejection reason on the opportunity.
    """

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True, slots=True)
class EligibilityReason:
    """
    One named, auditable eligibility gate result for a candidate.

    Every gate the opportunity engine evaluates is recorded so a
    reviewer can understand WHY a candidate was eligible or filtered
    without rerunning the pipeline.

    Attributes:

    gate
        Short canonical name of the gate (e.g. ``"candidate_status"``,
        ``"decision_classification"``, ``"decision_score"``,
        ``"geometry"``, ``"conflict"``, ``"risk_reward"``).

    passed
        Whether this gate was satisfied.

    reason
        Human-readable explanation of the gate result (e.g.
        ``"score 92 >= min 60"`` or
        ``"conflicting evidence present and conflicts disqualify"``).
    """

    gate: str
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class TradeOpportunity:
    """
    A descriptive trade opportunity view over a single Sprint 11S
    ``TradeDecision``.

    A ``TradeOpportunity`` is NOT a trade signal. It is a structured,
    descriptive answer to "should this candidate be surfaced as a trade
    opportunity at this evaluation point, and how does it rank against
    its peers?". It makes no profitability or predictive claim.

    The underlying ``TradeDecision`` (and through it the Sprint 11R
    ``TradeCandidate``) is retained BY REFERENCE and never modified, so
    no candidate / decision logic is duplicated.

    Attributes:

    timestamp
        Timestamp of the triggering candle, when available (reused from
        the decision).

    evaluation_index
        Chronological index of the evaluation point (reused from the
        decision).

    decision
        The Sprint 11S ``TradeDecision`` this opportunity was built
        over (retained by reference; not modified).

    direction
        Candidate directional intent (reused verbatim from the
        decision for convenient access).

    rank
        1-based deterministic rank AMONG THE ELIGIBLE opportunities
        only (1 = strongest). ``0`` when the candidate is ineligible
        (NO_OPPORTUNITY) and therefore not ranked as a surfaced
        opportunity. Ranks are unique among eligible candidates; no
        two eligible opportunities share a rank.

    status
        The opportunity status (NO_OPPORTUNITY / WATCH /
        ALTERNATIVE_OPPORTUNITY / BEST_OPPORTUNITY).

    eligibility
        Whether the candidate is eligible to be considered an
        opportunity (ELIGIBLE / INELIGIBLE).

    eligibility_reasons
        Tuple of named ``EligibilityReason`` gate results so the
        eligibility decision is fully auditable.

    decision_classification
        The Sprint 11S decision classification (reused verbatim from
        the decision for convenient access).

    decision_score
        The Sprint 11S decision score total (reused verbatim).

    geometry_complete
        Whether the candidate's entry / stop / target geometry is
        complete (reused verbatim from the decision).

    confluence_score
        Count of independent aligned evidence sources (reused from
        the decision).

    supporting_count
        Number of evidence sources aligned with the candidate direction
        (reused from the decision).

    conflicting_count
        Number of evidence sources conflicting with the candidate
        direction (reused from the decision).

    risk_reward_ratio
        The candidate's risk/reward ratio, when available (reused
        verbatim). ``None`` when geometry is incomplete; never
        fabricated.

    rejection_reason
        When ineligible, a short human-readable summary of the failing
        gate(s). Empty string when eligible.

    ranking_reason
        Human-readable, descriptive explanation of the rank / status
        assignment (e.g. why this candidate is the best opportunity,
        or why it was filtered out). Descriptive only.
    """

    timestamp: datetime | None
    evaluation_index: int
    decision: TradeDecision
    direction: str
    rank: int
    status: OpportunityStatus
    eligibility: EligibilityStatus
    eligibility_reasons: tuple[EligibilityReason, ...] = field(
        default_factory=tuple,
    )
    decision_classification: str = ""
    decision_score: int = 0
    geometry_complete: bool = False
    confluence_score: int = 0
    supporting_count: int = 0
    conflicting_count: int = 0
    risk_reward_ratio: float | None = None
    rejection_reason: str = ""
    ranking_reason: str = ""

    @property
    def is_eligible(self) -> bool:
        """Whether this candidate passed the eligibility gates."""

        return self.eligibility == EligibilityStatus.ELIGIBLE

    @property
    def is_best(self) -> bool:
        """Whether this opportunity is the best available."""

        return self.status == OpportunityStatus.BEST_OPPORTUNITY

    @property
    def is_surfaced(self) -> bool:
        """Whether this opportunity is surfaced (best or alternative)."""

        return self.status in (
            OpportunityStatus.BEST_OPPORTUNITY,
            OpportunityStatus.ALTERNATIVE_OPPORTUNITY,
        )

    def __post_init__(self) -> None:
        """
        Validate internal consistency.

        The engine never produces inconsistent states; this guards
        against hand-construction bugs.
        """

        if self.rank < 0:
            raise ValueError("rank must be non-negative.")
        if self.is_eligible and self.rank == 0:
            raise ValueError(
                "An eligible opportunity must carry a 1-based rank.",
            )
        if not self.is_eligible and self.rank > 0:
            raise ValueError(
                "An ineligible opportunity must not carry a rank "
                "(rank must be 0).",
            )
        if self.status == OpportunityStatus.BEST_OPPORTUNITY and self.rank != 1:
            raise ValueError(
                "A BEST_OPPORTUNITY must be rank 1.",
            )
        if self.status == OpportunityStatus.NO_OPPORTUNITY and self.is_eligible:
            raise ValueError(
                "An ELIGIBLE opportunity cannot be NO_OPPORTUNITY.",
            )
        if (
            self.status == OpportunityStatus.ALTERNATIVE_OPPORTUNITY
            and self.rank <= 1
        ):
            raise ValueError(
                "An ALTERNATIVE_OPPORTUNITY must be rank 2 or higher.",
            )


@dataclass(frozen=True, slots=True)
class TradeOpportunityRanking:
    """
    A deterministic ranking of trade opportunities at a single point
    in time.

    The ranking is DESCRIPTIVE. It identifies the best available
    technical opportunity among the eligible candidates. It is NOT a
    prediction, NOT a profitability claim, and NOT a trading
    recommendation.

    Attributes:

    timestamp
        Timestamp of the triggering candle, when available.

    evaluation_index
        Chronological index of the evaluation point.

    opportunities
        Tuple of ``TradeOpportunity`` covering EVERY candidate
        evaluated at this point, ordered by opportunity strength
        (strongest first). Ineligible candidates appear last with
        NO_OPPORTUNITY and rank 0.

    candidate_count
        Total number of candidates evaluated (eligible + ineligible).

    eligible_count
        Number of candidates that passed the eligibility gates.

    best
        The best (rank 1) ``TradeOpportunity`` when an eligible
        opportunity exists, otherwise ``None``. A best opportunity is
        NEVER manufactured: when no candidate is eligible, this is
        ``None``.

    rationale
        Human-readable, descriptive summary of the ranking.
    """

    timestamp: datetime | None
    evaluation_index: int
    opportunities: tuple[TradeOpportunity, ...] = field(
        default_factory=tuple,
    )
    candidate_count: int = 0
    eligible_count: int = 0
    best: TradeOpportunity | None = None
    rationale: str = ""

    @property
    def has_best(self) -> bool:
        """Whether a best opportunity was identified."""

        return self.best is not None

    @property
    def is_empty(self) -> bool:
        """Whether the ranking contains no candidates."""

        return self.candidate_count == 0

    @property
    def surfaced(self) -> tuple[TradeOpportunity, ...]:
        """The surfaced opportunities (best + alternatives), strongest first."""

        return tuple(
            o for o in self.opportunities if o.is_surfaced
        )


__all__ = [
    "EligibilityReason",
    "EligibilityStatus",
    "OpportunityStatus",
    "TradeOpportunity",
    "TradeOpportunityRanking",
]
