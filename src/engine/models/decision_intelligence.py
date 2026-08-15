"""
Domain models for the decision intelligence foundation layer
(Sprint 12A).

Sprint 11X answers: "How did the opportunities historically produced by
the engine perform, in aggregate?".
Sprint 11Y answers: "What historical evidence is strong enough, weak
enough, or insufficient to be useful to downstream intelligence?".
Sprint 11Z answers: "What does the historical evidence actually tell
us about this type of opportunity?".
Sprint 12A answers: "Given the characteristics of this CURRENT
opportunity, what historical evidence is relevant, how strong is that
evidence, and how should that evidence be presented to the existing
decision process?".

CRITICAL ARCHITECTURE — six strictly separate concerns, surfaced
separately in the models and in the report:

1. MARKET / OPPORTUNITY ANALYSIS   (Sprint 11O-11U; reused verbatim)
2. EXISTING DECISION / SCORING LOGIC (Sprint 11S / 11T; reused
   verbatim, represented WITHOUT alteration)
3. HISTORICAL OBSERVED PERFORMANCE  (reused Sprint 11X
   :class:`HistoricalPerformanceStatistics`)
4. EVIDENCE STRENGTH                 (reused Sprint 11Y
   :class:`EvidenceStrength`)
5. STRATEGY INTERPRETATION           (reused Sprint 11Z
   :class:`StrategyAssessmentStatus` + interpretation / limitations)
6. DECISION INTELLIGENCE             (Sprint 12A
   :class:`DecisionContextStatus` + factors + rationale)

These are NEVER collapsed into one score. Sprint 12A does NOT replace
the existing scoring system, does NOT alter existing signal decisions,
does NOT recompute performance statistics, does NOT re-classify
evidence strength, does NOT re-interpret strategy, does NOT re-evaluate
outcomes, does NOT re-read candles, and does NOT use future
information. It is an additional EXPLAINABLE / AUDITABLE INFORMATION
layer presented to the existing decision process.

DESIGN PRINCIPLE — no replacement of existing decision logic:

A :class:`DecisionIntelligenceContext` is DESCRIPTIVE. It is NOT a
trading signal, NOT a BUY / SELL / ENTER / EXIT / HOLD recommendation,
NOT a price prediction, NOT a probability of success, NOT a
profitability guarantee, and NOT a statistical-significance claim. The
:.class:`ExistingDecisionSummary` is a read-only PROJECTION of the
existing decision — it represents the existing decision WITHOUT
changing it. The Sprint 12A layer NEVER modifies the existing decision,
scoring, ranking, opportunity selection, risk geometry or execution
logic.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY, never silently
converted to zero:

* No matching cohort (11Z lookup NO_MATCH) ->
  :attr:`DecisionContextStatus.EVIDENCE_UNAVAILABLE` with
  ``observed_performance = None``, ``evidence_strength = None`` and
  ``strategy_interpretation = None``.
* Insufficient sample -> the reused
  :attr:`EvidenceStrength.INSUFFICIENT` propagates to
  :attr:`DecisionContextStatus.INSUFFICIENT_EVIDENCE`.
* Unavailable metric -> ``None`` (delegated to the reused statistics;
  never fabricated).
* ``BOTH_TOUCHED`` / ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` outcomes
  are preserved exactly by the reused statistics (they never contribute
  to win / loss rates or R aggregates); Sprint 12A inherits that
  honesty unchanged.

DESIGN PRINCIPLE — no leakage:

The layer consumes the ALREADY-COMPUTED Sprint 11Z
:class:`OpportunityEvidenceLookup` (itself built from already-computed
Sprint 11X / 11Y evidence). It never inspects future market candles,
never re-evaluates outcomes using future data, never calls the outcome
evaluator, never re-runs the pipeline, and never modifies the
historical replay semantics established in Sprint 11V / 11W.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. Identifiers hash the SORTED
canonical identity so a shuffled-equivalent input yields the same id.
Factor ordering is deterministic (canonical enum order).

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional metrics reuse ``None`` so "unavailable" is never silently a
  real value.
* The reused :class:`HistoricalPerformanceStatistics`,
  :class:`EvidenceStrength`, :class:`StrategyAssessmentStatus`,
  :class:`OpportunityProfile`, :class:`OpportunityEvidenceLookup`,
  :class:`CohortSpec` and :class:`HistoricalEvidenceCohort` are
  referenced (never mutated, never recomputed).
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.historical_evidence import (
    EvidenceStrength,
    HistoricalEvidenceCohort,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    OpportunityEvidenceLookup,
    OpportunityProfile,
    StrategyAssessmentStatus,
)


class DecisionContextStatus(Enum):
    """
    The controlled, explicit status of the decision-intelligence
    context for a current opportunity.

    This status describes the EVIDENCE CONTEXT available to the
    existing decision process. It is NOT the future outcome of a
    trade, NOT a trading recommendation, and NOT a probability of
    success. It is a deterministic mapping from the reused Sprint 11Z
    :class:`StrategyAssessmentStatus` (and the Sprint 11Z lookup match
    status); it does NOT re-classify evidence and does NOT override the
    reused evidence strength.

    EVIDENCE_SUPPORTED
        Relevant historical evidence is available AND at least usable
        (the reused evidence strength is MODERATE or STRONG). The
        existing decision process is informed that supporting
        historical evidence exists. Even supported evidence is
        DESCRIPTIVE and does NOT guarantee future performance.

    EVIDENCE_LIMITED
        Some historical evidence exists but it is limited / weak (the
        reused evidence strength is WEAK). The existing decision
        process is informed that the evidence is directional at best.

    INSUFFICIENT_EVIDENCE
        Historical evidence for this opportunity type is INSUFFICIENT
        (the reused evidence strength is INSUFFICIENT). The observed
        metrics (if any) must NOT be treated as reliable evidence.
        Insufficient evidence is NEVER transformed into a positive or
        negative trading decision.

    EVIDENCE_UNAVAILABLE
        No matching historical cohort exists for the current
        opportunity's characteristics (the Sprint 11Z lookup returned
        NO_MATCH), or no evidence lookup was supplied. No evidence is
        fabricated; the existing decision process is informed that no
        relevant historical evidence is available.

    The mapping is intentionally conservative and one-to-one with the
    reused Sprint 11Z assessment status (plus the NO_MATCH case). No
    statistical hypothesis test is performed.
    """

    EVIDENCE_SUPPORTED = "EVIDENCE_SUPPORTED"
    EVIDENCE_LIMITED = "EVIDENCE_LIMITED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (strongest context first)."""

        return {
            DecisionContextStatus.EVIDENCE_SUPPORTED: 0,
            DecisionContextStatus.EVIDENCE_LIMITED: 1,
            DecisionContextStatus.INSUFFICIENT_EVIDENCE: 2,
            DecisionContextStatus.EVIDENCE_UNAVAILABLE: 3,
        }[self]

    @property
    def evidence_available(self) -> bool:
        """Whether any relevant historical evidence is available at all."""

        return self != DecisionContextStatus.EVIDENCE_UNAVAILABLE

    @property
    def is_sufficient(self) -> bool:
        """Whether the evidence context is at least usable (not insufficient/unavailable)."""

        return self == DecisionContextStatus.EVIDENCE_SUPPORTED


#: The deterministic one-to-one mapping from the reused Sprint 11Z
#: :class:`StrategyAssessmentStatus` to the Sprint 12A
#: :class:`DecisionContextStatus`. This is the core reuse contract:
#: Sprint 12A does NOT re-classify evidence — it presents the already-
#: interpreted strategy assessment as a decision-process-facing context.
ASSESSMENT_TO_CONTEXT: dict[StrategyAssessmentStatus, DecisionContextStatus] = {
    StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT: (
        DecisionContextStatus.EVIDENCE_SUPPORTED
    ),
    StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE: (
        DecisionContextStatus.EVIDENCE_SUPPORTED
    ),
    StrategyAssessmentStatus.LIMITED_EVIDENCE: (
        DecisionContextStatus.EVIDENCE_LIMITED
    ),
    StrategyAssessmentStatus.INSUFFICIENT_EVIDENCE: (
        DecisionContextStatus.INSUFFICIENT_EVIDENCE
    ),
}


class DecisionEvidenceFactor(Enum):
    """
    An explicit, auditable representation of how the historical
    evidence relates to the current opportunity.

    These factors are DESCRIPTIVE TOKENS, NOT numerical weights and
    NOT a predictive score. Each emitted factor carries a deterministic
    human-readable reason. They are ordered by :attr:`rank_value`
    (strongest / most informative first) for deterministic presentation.

    HISTORICAL_SUPPORT_PRESENT
        Relevant historical evidence is available and at least usable
        (evidence strength MODERATE or STRONG). Informs the existing
        decision process that supporting historical observation exists.

    FAVORABLE_HISTORICAL_CHARACTERISTICS
        At least one observed metric (win rate / average R / profit
        factor) meets its configured favourable threshold, AND the
        evidence is at least usable (so a tiny cohort is never flagged
        favourable). Descriptive context only; never upgrades the
        decision context status.

    HISTORICAL_CAUTION_PRESENT
        The historical evidence is limited / weak (evidence strength
        WEAK). Informs the existing decision process that the evidence
        is directional at best and should be treated cautiously.

    UNFAVORABLE_HISTORICAL_CHARACTERISTICS
        At least one observed metric is adverse (win rate below the
        favourable threshold, negative average R, or profit factor
        below 1.0), AND the evidence is at least usable. Descriptive
        context only; never transforms the evidence into a negative
        trading decision.

    INSUFFICIENT_HISTORICAL_EVIDENCE
        The historical evidence is INSUFFICIENT (sample below the
        configured minimum). Informs the existing decision process
        that the observed metrics must NOT be treated as reliable
        evidence.

    EVIDENCE_UNAVAILABLE
        No matching historical cohort exists for the current
        opportunity's characteristics. No evidence is fabricated.
    """

    HISTORICAL_SUPPORT_PRESENT = "HISTORICAL_SUPPORT_PRESENT"
    FAVORABLE_HISTORICAL_CHARACTERISTICS = "FAVORABLE_HISTORICAL_CHARACTERISTICS"
    HISTORICAL_CAUTION_PRESENT = "HISTORICAL_CAUTION_PRESENT"
    UNFAVORABLE_HISTORICAL_CHARACTERISTICS = "UNFAVORABLE_HISTORICAL_CHARACTERISTICS"
    INSUFFICIENT_HISTORICAL_EVIDENCE = "INSUFFICIENT_HISTORICAL_EVIDENCE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (most informative first)."""

        return {
            DecisionEvidenceFactor.HISTORICAL_SUPPORT_PRESENT: 0,
            DecisionEvidenceFactor.FAVORABLE_HISTORICAL_CHARACTERISTICS: 1,
            DecisionEvidenceFactor.HISTORICAL_CAUTION_PRESENT: 2,
            DecisionEvidenceFactor.UNFAVORABLE_HISTORICAL_CHARACTERISTICS: 3,
            DecisionEvidenceFactor.INSUFFICIENT_HISTORICAL_EVIDENCE: 4,
            DecisionEvidenceFactor.EVIDENCE_UNAVAILABLE: 5,
        }[self]


@dataclass(frozen=True, slots=True)
class DecisionIntelligenceFactor:
    """
    One auditable decision-intelligence factor: a descriptive token
    paired with a deterministic human-readable reason.

    Attributes:

    factor
        The :class:`DecisionEvidenceFactor` token.

    reason
        Human-readable, descriptive reason explaining why the factor
        applies. Descriptive only; never predictive.
    """

    factor: DecisionEvidenceFactor
    reason: str


@dataclass(frozen=True, slots=True)
class ExistingDecisionSummary:
    """
    A read-only PROJECTION of the existing decision / opportunity
    information for a current opportunity.

    This is a lightweight, serializable summary of the Sprint 11S
    :class:`~engine.models.trade_decision.TradeDecision` and Sprint 11T
    :class:`~engine.models.opportunity.TradeOpportunity` information.
    It REPRESENTS the existing decision WITHOUT changing it. The
    Sprint 12A layer NEVER modifies the underlying decision, scoring,
    ranking, opportunity selection, risk geometry or execution logic.

    Fields use the empty-string ``""`` / ``0`` / ``None`` "unavailable"
    sentinel convention matching Sprint 11W / 11X / 11Y / 11Z. No value
    is fabricated; when no decision exists, the summary carries empty
    / zero / None values.

    Attributes:

    direction
        Directional intent (``"LONG"`` / ``"SHORT"`` / ``"NONE"``),
        or ``""`` when not specified.

    decision_classification
        Sprint 11S decision-classification name, or ``""`` when no
        decision exists.

    decision_score
        Sprint 11S decision score total, or ``0`` when no decision
        exists.

    opportunity_status
        Sprint 11T opportunity-status name, or ``""`` when no
        opportunity exists.

    rank
        1-based market-level rank among eligible opportunities, or
        ``0`` when ineligible / unranked.

    geometry_complete
        Whether the candidate's entry / stop / target geometry is
        complete. ``False`` when no candidate / incomplete geometry.

    confluence_score
        Count of independent aligned evidence sources (reused from the
        candidate). ``0`` when no candidate.

    risk_reward_ratio
        The candidate's risk/reward ratio, when available. ``None``
        when geometry is incomplete / no candidate.

    entry / stop / target
        Entry / stop / target price references, when available.
        ``None`` when geometry is incomplete / no candidate.
    """

    direction: str = ""
    decision_classification: str = ""
    decision_score: int = 0
    opportunity_status: str = ""
    rank: int = 0
    geometry_complete: bool = False
    confluence_score: int = 0
    risk_reward_ratio: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None

    @property
    def has_decision(self) -> bool:
        """Whether any existing decision information is present."""

        return bool(
            self.direction
            or self.decision_classification
            or self.opportunity_status
            or self.decision_score
            or self.rank,
        )


@dataclass(frozen=True, slots=True)
class DecisionIntelligenceContext:
    """
    The evidence-aware DECISION INTELLIGENCE context for a current
    opportunity.

    The context is DESCRIPTIVE. It connects, WITHOUT recomputing and
    WITHOUT replacing the existing decision logic:

    * the CURRENT OPPORTUNITY identity (reused Sprint 11Z
      :class:`OpportunityProfile`),
    * the EXISTING DECISION (read-only :class:`ExistingDecisionSummary`
      projection — represented, never altered),
    * the RELEVANT HISTORICAL EVIDENCE (reused Sprint 11Z
      :class:`OpportunityEvidenceLookup`, carrying the matched
      :class:`HistoricalEvidenceCohort` and
      :class:`StrategyEvidenceAssessment`),
    * the OBSERVED PERFORMANCE (reused Sprint 11X
      :class:`HistoricalPerformanceStatistics`, ``None`` when no match),
    * the EVIDENCE STRENGTH (reused Sprint 11Y
      :class:`EvidenceStrength`, ``None`` when no match),
    * the STRATEGY INTERPRETATION (reused Sprint 11Z
      :class:`StrategyAssessmentStatus`, ``None`` when no match), and
    * the DECISION INTELLIGENCE view (:class:`DecisionContextStatus`
      + factors + rationale + limitations).

    It is NOT a trading signal, NOT a BUY / SELL / ENTER / EXIT / HOLD
    recommendation, NOT a price prediction, NOT a probability of
    success, NOT a profitability guarantee, NOT a statistical-
    significance claim, and NOT a replacement of the existing decision
    / scoring / ranking / opportunity-selection / risk-geometry /
    execution logic.

    Attributes:

    context_id
        Deterministic identifier (``"di-"`` + sha256[:16] of the
        canonical context identity).

    profile
        The reused :class:`OpportunityProfile` describing the current
        opportunity's characteristics (used for the 11Z lookup).

    existing_decision
        The read-only :class:`ExistingDecisionSummary` projection of
        the existing decision / opportunity information.

    lookup
        The reused Sprint 11Z :class:`OpportunityEvidenceLookup`
        (retained by reference; never mutated). Carries the matched
        cohort, the strategy assessment, and the lookup limitations.
        May be a NO_MATCH lookup.

    decision_context_status
        The :class:`DecisionContextStatus` (the DECISION INTELLIGENCE
        view). Deterministic mapping from the reused strategy
        assessment status (or NO_MATCH).

    factors
        Tuple of :class:`DecisionIntelligenceFactor`, deterministically
        ordered by :attr:`DecisionEvidenceFactor.rank_value`. Each
        factor is a descriptive token with a reason; no numerical
        weights, no predictive score.

    observed_performance
        The reused :class:`HistoricalPerformanceStatistics` (the
        OBSERVED historical result), or ``None`` when no matching
        cohort. Referenced, never recomputed.

    evidence_strength
        The reused :class:`EvidenceStrength` (the EVIDENCE QUALITY),
        or ``None`` when no matching cohort.

    strategy_interpretation
        The reused :class:`StrategyAssessmentStatus` (the STRATEGY
        INTERPRETATION), or ``None`` when no matching cohort.

    rationale
        Human-readable, deterministic rationale explaining the current
        opportunity characteristics, historical evidence availability,
        observed result, evidence strength, strategy interpretation
        and relevant limitations. Descriptive only.

    limitations
        Human-readable, deterministic statement of the limitations of
        the decision-intelligence context (sample size, no statistical
        test, no future guarantee, BOTH_TOUCHED / NO_GEOMETRY /
        INSUFFICIENT_DATA handling, no replacement of existing decision
        logic). Descriptive only.

    label
        Optional descriptive label identifying the decision-intelligence
        run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).
    """

    context_id: str
    profile: OpportunityProfile
    existing_decision: ExistingDecisionSummary
    lookup: OpportunityEvidenceLookup
    decision_context_status: DecisionContextStatus
    factors: tuple[DecisionIntelligenceFactor, ...] = field(
        default_factory=tuple,
    )
    observed_performance: HistoricalPerformanceStatistics | None = None
    evidence_strength: EvidenceStrength | None = None
    strategy_interpretation: StrategyAssessmentStatus | None = None
    rationale: str = ""
    limitations: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def has_evidence(self) -> bool:
        """Whether relevant historical evidence is available at all."""

        return self.decision_context_status.evidence_available

    @property
    def evidence_supported(self) -> bool:
        """Whether the evidence context is at least usable (supported)."""

        return self.decision_context_status.is_sufficient

    @property
    def matched(self) -> bool:
        """Whether a matching historical cohort was found (reused lookup)."""

        return self.lookup.matched

    @property
    def matched_cohort(self) -> HistoricalEvidenceCohort | None:
        """The matched cohort (reused), or ``None`` when no match."""

        return self.lookup.matched_cohort


__all__ = [
    "ASSESSMENT_TO_CONTEXT",
    "DecisionContextStatus",
    "DecisionEvidenceFactor",
    "DecisionIntelligenceContext",
    "DecisionIntelligenceFactor",
    "ExistingDecisionSummary",
]
