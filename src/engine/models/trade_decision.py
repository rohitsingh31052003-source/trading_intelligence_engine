"""
Domain models for the trade candidate ranking & decision layer
(Sprint 11S).

These models describe a deterministic, descriptive DECISION over one or
more trade candidates produced by Sprint 11R. The decision layer is the
next step of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)  <- this layer
    5. TRADE VALIDATION             (future)
    6. SIGNAL / EXECUTION           (future)

A ``TradeDecision`` is NOT a trading signal. It is a DESCRIPTIVE
classification of a candidate's relative technical-evidence strength and
completeness. It is NOT a probability of success, NOT a profitability
prediction, and NOT a trading recommendation.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* The ``Decision Score`` is an integer in ``[0, 100]`` expressing
  deterministic technical-evidence strength/completeness. It is NEVER
  described as a probability. The report layer repeats this disclaimer.
* Optional fields use ``None`` (or explicit ``UNKNOWN`` members) so
  "unobserved" is never silently reported as a real value.
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.trade_candidate import TradeCandidate


class DecisionClassification(Enum):
    """
    Descriptive decision classification for a trade candidate.

    This enum is DELIBERATELY DISTINCT from Sprint 11R's
    ``CandidateStatus`` (NO_CANDIDATE / WATCH / CANDIDATE). Sprint 11R
    describes candidate *generation*; Sprint 11S describes candidate
    *decision / ranking*. The two views are related but not identical:
    a generated CANDIDATE may, after ranking, be classified REJECTED or
    WATCH rather than PREFERRED when the technical evidence is weak or
    conflicting.

    REJECTED
        The candidate does not meet a minimum technical-evidence bar:
        no directional bias, no candidate, severe conflict, or a
        decision score below the watch threshold. Not worth monitoring.

    WATCH
        Some directional technical evidence exists but it is
        incomplete, weak, or carries a disqualifying cap (e.g. a
        conflicting candle, a range block, incomplete geometry). Worth
        monitoring but not a qualified opportunity.

    QUALIFIED
        A coherent, reasonably complete technical opportunity: clear
        directional bias, adequate confluence, acceptable evidence
        strength. Not strong enough / not clean enough to be the
        preferred candidate, but a legitimate candidate.

    PREFERRED
        The strongest available technical opportunity: high evidence
        strength, no disqualifying conflict, complete (or acceptable)
        geometry, and the highest ranked candidate at the point in
        time. PREFERRED is DESCRIPTIVE: it does NOT predict success or
        profitability; it identifies the strongest candidate among the
        available technical evidence.
    """

    REJECTED = "REJECTED"
    WATCH = "WATCH"
    QUALIFIED = "QUALIFIED"
    PREFERRED = "PREFERRED"

    @property
    def rank_value(self) -> int:
        """Higher is stronger; used for deterministic ordering."""

        return _RANK_VALUE[self]


_RANK_VALUE = {
    DecisionClassification.REJECTED: 0,
    DecisionClassification.WATCH: 1,
    DecisionClassification.QUALIFIED: 2,
    DecisionClassification.PREFERRED: 3,
}


@dataclass(frozen=True, slots=True)
class DecisionScoreComponent:
    """
    One interpretable component of a ``Decision Score``.

    Every point the engine awards (or withholds) is attributable to a
    named, human-readable component so a reviewer can understand WHY a
    score was reached without rerunning the pipeline.

    Attributes:

    name
        Short canonical name of the component (e.g. ``"trend"``,
        ``"geometry"``, ``"no_conflict"``).

    points
        Points awarded for this component (integer >= 0).

    max_points
        Maximum points available for this component (integer >= 0,
        >= ``points``).

    reason
        Human-readable explanation of how the points were derived
        (e.g. ``"trend aligned with candidate direction"``).
    """

    name: str
    points: int
    max_points: int
    reason: str

    def __post_init__(self) -> None:
        if self.points < 0:
            raise ValueError("points must be non-negative.")
        if self.max_points < 0:
            raise ValueError("max_points must be non-negative.")
        if self.points > self.max_points:
            raise ValueError("points cannot exceed max_points.")


@dataclass(frozen=True, slots=True)
class DecisionScore:
    """
    A deterministic ``Decision Score`` for one candidate.

    The ``Decision Score`` is an integer in ``[0, 100]`` representing
    the STRENGTH and COMPLETENESS of the deterministic technical
    evidence behind a candidate. It is NOT a probability of success,
    NOT a profitability prediction, and NOT a trading recommendation.

    Attributes:

    total
        The total score awarded (integer in ``[0, 100]``).

    max_total
        The maximum achievable score (``100``), carried for clarity.

    components
        Tuple of named ``DecisionScoreComponent`` items so the score
        is fully auditable.

    reason
        Human-readable summary of the score.
    """

    total: int
    max_total: int
    components: tuple[DecisionScoreComponent, ...] = field(
        default_factory=tuple,
    )
    reason: str = ""

    def __post_init__(self) -> None:
        if self.max_total <= 0:
            raise ValueError("max_total must be positive.")
        if self.total < 0 or self.total > self.max_total:
            raise ValueError(
                "total must lie within [0, max_total].",
            )


@dataclass(frozen=True, slots=True)
class TradeDecision:
    """
    A descriptive decision over a single trade candidate.

    The decision is DESCRIPTIVE. It is not a trade signal, not a
    prediction, and not a guarantee of profitability.

    Attributes:

    timestamp
        Timestamp of the triggering candle, when available.

    evaluation_index
        Chronological index of the evaluation point.

    candidate
        The Sprint 11R ``TradeCandidate`` this decision was made over
        (retained by reference; not modified).

    direction
        Candidate directional intent (reused verbatim from the
        candidate for convenient access).

    classification
        The decision classification (REJECTED / WATCH / QUALIFIED /
        PREFERRED).

    score
        The deterministic ``Decision Score`` (strength/completeness of
        technical evidence; NOT a probability).

    geometry_complete
        Whether the candidate's entry / stop / target geometry is
        complete (reused verbatim from the candidate).

    confluence_score
        Count of independent aligned evidence sources (reused from
        the candidate).

    supporting_count
        Number of evidence sources aligned with the candidate
        direction.

    conflicting_count
        Number of evidence sources conflicting with the candidate
        direction.

    risk_reward_ratio
        The candidate's risk/reward ratio, when available (reused
        verbatim). ``None`` when geometry is incomplete.

    rationale
        Human-readable, descriptive rationale for the classification
        and score. Descriptive only.
    """

    timestamp: datetime | None
    evaluation_index: int
    candidate: TradeCandidate
    direction: str
    classification: DecisionClassification
    score: DecisionScore
    geometry_complete: bool
    confluence_score: int
    supporting_count: int
    conflicting_count: int
    risk_reward_ratio: float | None
    rationale: str

    @property
    def decision_score(self) -> int:
        """The total deterministic decision score (alias)."""

        return self.score.total

    @property
    def is_preferred(self) -> bool:
        """Whether this decision reached PREFERRED."""

        return self.classification == DecisionClassification.PREFERRED


@dataclass(frozen=True, slots=True)
class RankedDecision:
    """
    A ``TradeDecision`` paired with its deterministic rank.

    Rank is 1-based: rank ``1`` is the strongest candidate. Ties are
    broken deterministically; no two decisions share a rank.

    Attributes:

    rank
        1-based deterministic rank (1 = strongest).

    decision
        The underlying ``TradeDecision``.
    """

    rank: int
    decision: TradeDecision


@dataclass(frozen=True, slots=True)
class TradeDecisionRanking:
    """
    A deterministic ranking of one or more trade candidates at a
    single point in time.

    The ranking is DESCRIPTIVE. It identifies the strongest candidate
    among the available technical evidence. It is NOT a prediction,
    NOT a profitability claim, and NOT a trading recommendation.

    Attributes:

    timestamp
        Timestamp of the triggering candle, when available.

    evaluation_index
        Chronological index of the evaluation point.

    decisions
        Tuple of ``RankedDecision`` ordered by rank ascending
        (strongest first).

    candidate_count
        Number of candidates ranked.

    preferred
        The preferred (rank 1) ``RankedDecision`` when a PREFERRED
        candidate exists, otherwise ``None``. A preferred candidate is
        NEVER manufactured: when no candidate reaches PREFERRED, this
        is ``None``.

    rationale
        Human-readable, descriptive summary of the ranking.
    """

    timestamp: datetime | None
    evaluation_index: int
    decisions: tuple[RankedDecision, ...] = field(
        default_factory=tuple,
    )
    candidate_count: int = 0
    preferred: RankedDecision | None = None
    rationale: str = ""

    @property
    def has_preferred(self) -> bool:
        """Whether a PREFERRED candidate was identified."""

        return self.preferred is not None

    @property
    def is_empty(self) -> bool:
        """Whether the ranking contains no candidates."""

        return self.candidate_count == 0


__all__ = [
    "DecisionClassification",
    "DecisionScore",
    "DecisionScoreComponent",
    "RankedDecision",
    "TradeDecision",
    "TradeDecisionRanking",
]
