"""
Domain models for the controlled decision intelligence INTEGRATION
boundary (Sprint 12B).

Sprint 12A introduced the decision intelligence FOUNDATION: an
evidence-aware, descriptive context (``DecisionIntelligenceContext``)
presented to the existing decision process WITHOUT altering it. Sprint
12B creates the explicit, auditable INTEGRATION BOUNDARY that attaches
that decision-intelligence context to an EXISTING DECISION while
preserving the existing decision EXACTLY as-is.

The integration boundary is intentionally a CONTEXT / AUDIT layer, NOT
a new decision algorithm. It explicitly distinguishes three concerns
and keeps them separate in the model and in the report:

    Existing Decision
        ↓
    Decision Intelligence Context   (reused Sprint 12A, by reference)
        ↓
    Integrated Decision Context     (Sprint 12B — this layer)

CRITICAL DESIGN PRINCIPLE — the existing decision is AUTHORITATIVE:

The :class:`IntegratedDecisionContext` carries the ORIGINAL existing
decision object BY REFERENCE (``existing_decision``). The existing
decision is NEVER modified, NEVER re-scored, NEVER re-ranked, NEVER
re-classified, NEVER upgraded and NEVER downgraded by the integration.
Decision intelligence is CONTEXTUAL EVIDENCE attached to the existing
decision, NOT a replacement decision. The integration makes it
IMPOSSIBLE to accidentally interpret evidence-supported context as a new
trading signal: there is no BUY / SELL / ENTER / EXIT / HOLD
recommendation, no probability, no score adjustment, no hidden weight.

For auditability and serialization, the integration also carries a
read-only :class:`ExistingDecisionSummary` PROJECTION
(``existing_decision_summary``) of the existing decision. When the
existing decision passed in is already an
:class:`ExistingDecisionSummary`, the projection IS the passed object
(identity preserved). The heavy original object (e.g. a Sprint 11S
:class:`TradeDecision` carrying candidate references) is retained by
reference on the LIVE result but is NOT part of the persisted document;
on reload it reconstructs as ``None`` (regenerable by the caller),
matching the Sprint 11U / 11K / 11W serialization discipline. The
projection + integration id + status + decision-intelligence context
are persisted losslessly, so the audit view survives the round trip.

DESIGN PRINCIPLE — reuse, do not re-invent:

The integration layer consumes the ALREADY-COMPUTED Sprint 12A
:class:`DecisionIntelligenceContext` (itself built from already-computed
Sprint 11X / 11Y / 11Z evidence). It does NOT recompute performance
statistics, does NOT re-classify evidence strength, does NOT re-interpret
strategy, does NOT rebuild cohort matching, does NOT re-evaluate
outcomes, does NOT re-read candles and does NOT use future information.

Architecture (unchanged in direction, 12B sits BELOW 12A):

    11X -> 11Y -> 11Z -> 12A -> 12B

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY, never fabricated:

* No decision intelligence supplied (``None``) ->
  :attr:`IntegrationStatus.UNAVAILABLE`; no evidence is invented.
* No existing decision supplied (``None``) ->
  :attr:`IntegrationStatus.INVALID`; nothing to integrate into.
* Decision intelligence present but no matching cohort (12A
  ``EVIDENCE_UNAVAILABLE``) -> :attr:`IntegrationStatus.CONTEXT_ONLY`;
  the existing decision stands unchanged and the intelligence is
  informational context only (no supportive evidence).
* Existing decision + decision intelligence with available evidence ->
  :attr:`IntegrationStatus.INTEGRATED`.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. The integration id hashes the
canonical identity (existing-decision projection + 12A context id +
status + profile + factors + label + metadata); because the embedded 12A
context id is already shuffle-invariant (via Sprint 11Y / 11Z), the
integration id is shuffle-invariant for equivalent evidence.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* The reused :class:`DecisionIntelligenceContext`,
  :class:`ExistingDecisionSummary`, :class:`OpportunityProfile`,
  :class:`DecisionContextStatus` and :class:`StrategyAssessmentStatus`
  are referenced (never mutated, never recomputed).
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionIntelligenceContext,
    DecisionIntelligenceFactor,
    ExistingDecisionSummary,
)
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    OpportunityProfile,
    StrategyAssessmentStatus,
)


class IntegrationStatus(Enum):
    """
    The controlled, explicit status of the decision-intelligence
    integration boundary for an existing decision.

    This status communicates WHETHER decision intelligence was
    successfully attached to an existing decision (and, when attached,
    whether it constitutes supportive evidence or only informational
    context). It is NOT the future outcome of a trade, NOT a trading
    recommendation, NOT a probability of success, and NOT a replacement
    of the existing decision classification. It does NOT re-classify
    evidence and does NOT override the reused Sprint 12A
    :class:`DecisionContextStatus`.

    The statuses are MUTUALLY EXCLUSIVE and evaluated in the documented
    priority order by the integration engine:

    INVALID
        No existing decision was supplied (there is nothing to integrate
        the decision intelligence into). The integration cannot be
        performed. No decision intelligence context is fabricated; the
        result carries an explicit invalid state. (When BOTH the
        existing decision and the decision intelligence are absent the
        status is also INVALID — the unavailable-intelligence case is
        subordinate to the missing-decision case.)

    UNAVAILABLE
        An existing decision is present but NO decision intelligence
        context was supplied (``decision_intelligence is None``). No
        evidence is fabricated; the existing decision stands unchanged
        and the integration records that no decision intelligence is
        available.

    CONTEXT_ONLY
        An existing decision is present and a decision intelligence
        context is attached, BUT the decision intelligence carries no
        available evidence (the reused 12A
        :attr:`DecisionContextStatus.EVIDENCE_UNAVAILABLE`, i.e. no
        matching historical cohort). The intelligence is attached as
        INFORMATIONAL CONTEXT ONLY; it does NOT constitute supportive
        evidence and does NOT alter the existing decision.

    INTEGRATED
        An existing decision is present and a decision intelligence
        context with AVAILABLE evidence (a matched historical cohort,
        reused 12A status ``EVIDENCE_SUPPORTED`` /
        ``EVIDENCE_LIMITED`` / ``INSUFFICIENT_EVIDENCE``) is attached.
        The decision intelligence is successfully integrated as
        evidence-aware context. Even when integrated, the context is
        DESCRIPTIVE and does NOT guarantee future performance, does NOT
        upgrade the existing decision and does NOT produce a trading
        recommendation.

    The mapping is intentionally conservative and deterministic. No
    statistical hypothesis test is performed.
    """

    INTEGRATED = "INTEGRATED"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (most-integrated first)."""

        return {
            IntegrationStatus.INTEGRATED: 0,
            IntegrationStatus.CONTEXT_ONLY: 1,
            IntegrationStatus.UNAVAILABLE: 2,
            IntegrationStatus.INVALID: 3,
        }[self]

    @property
    def is_attached(self) -> bool:
        """Whether decision intelligence was attached at all."""

        return self in (
            IntegrationStatus.INTEGRATED,
            IntegrationStatus.CONTEXT_ONLY,
        )

    @property
    def is_integrated(self) -> bool:
        """Whether decision intelligence with available evidence was attached."""

        return self == IntegrationStatus.INTEGRATED


@dataclass(frozen=True, slots=True)
class IntegratedDecisionContext:
    """
    The controlled integration of an EXISTING DECISION with a
    decision-intelligence CONTEXT (Sprint 12B).

    The integration is DESCRIPTIVE. It connects, WITHOUT recomputing and
WITHOUT replacing the existing decision logic:

    * the EXISTING DECISION (the original decision object, retained BY
      REFERENCE and NEVER modified — ``existing_decision``),
    * the EXISTING DECISION PROJECTION (a read-only
      :class:`ExistingDecisionSummary` for audit / serialization —
      ``existing_decision_summary``),
    * the OPPORTUNITY IDENTITY (reused :class:`OpportunityProfile`),
    * the DECISION INTELLIGENCE CONTEXT (reused Sprint 12A
      :class:`DecisionIntelligenceContext`, retained by reference),
    * the EVIDENCE STATUS (reused :class:`DecisionContextStatus`,
      ``None`` when no decision intelligence),
    * the STRATEGY INTERPRETATION (reused
      :class:`StrategyAssessmentStatus`, ``None`` when no match / no
      decision intelligence),
    * the INTEGRATION STATUS (:class:`IntegrationStatus`),
    * the CONTEXTUAL FACTORS (reused Sprint 12A
      :class:`DecisionIntelligenceFactor` tuple, ``()`` when no decision
      intelligence),
    * and the integration rationale / limitations / audit metadata.

    It is NOT a trading signal, NOT a BUY / SELL / ENTER / EXIT / HOLD
    recommendation, NOT a price prediction, NOT a probability of
    success, NOT a profitability guarantee, NOT a statistical-
    significance claim, and NOT a replacement of the existing decision
    / scoring / ranking / opportunity-selection / risk-geometry /
    execution logic. The existing decision classification / score /
    rank are represented WITHOUT alteration; evidence-supported context
    is NEVER interpreted as an upgrade of the existing decision.

    Attributes:

    integration_id
        Deterministic identifier (``"int-"`` + sha256[:16] of the
        canonical integration identity).

    existing_decision
        The ORIGINAL existing decision object, retained BY REFERENCE and
        NEVER modified. May be any decision-like object (e.g. a Sprint
        11S :class:`~engine.models.trade_decision.TradeDecision`, a
        Sprint 11T :class:`~engine.models.opportunity.TradeOpportunity`,
        an :class:`ExistingDecisionSummary`, or ``None`` when absent).
        On serialization the heavy original object is NOT persisted and
        reconstructs as ``None`` (regenerable by the caller); the
        :attr:`existing_decision_summary` projection preserves the audit
        view losslessly.

    existing_decision_summary
        A read-only :class:`ExistingDecisionSummary` projection of the
        existing decision (for audit + serialization). When the existing
        decision passed in is already an :class:`ExistingDecisionSummary`,
        this IS that object (identity preserved).

    profile
        The reused :class:`OpportunityProfile` describing the current
        opportunity's characteristics. Defaults to an empty profile when
        none is supplied.

    decision_intelligence
        The reused Sprint 12A :class:`DecisionIntelligenceContext`
        (retained by reference; never mutated). ``None`` when no
        decision intelligence was supplied.

    integration_status
        The :class:`IntegrationStatus` describing whether / how decision
        intelligence was attached to the existing decision.

    evidence_status
        The reused :class:`DecisionContextStatus` (the evidence context
        from the 12A context), or ``None`` when no decision intelligence
        is attached. Surfaced for audit; never re-classified.

    strategy_interpretation
        The reused :class:`StrategyAssessmentStatus` (the strategy
        interpretation from the 12A context), or ``None`` when no
        matching cohort / no decision intelligence. Surfaced for audit.

    observed_performance
        The reused :class:`HistoricalPerformanceStatistics` (the observed
        historical result), or ``None`` when no match / no decision
        intelligence. Referenced, never recomputed.

    evidence_strength
        The reused :class:`EvidenceStrength` (the evidence quality), or
        ``None`` when no match / no decision intelligence.

    contextual_factors
        Tuple of reused :class:`DecisionIntelligenceFactor`,
        deterministically ordered (reused from the 12A context).
        ``()`` when no decision intelligence is attached.

    rationale
        Human-readable, deterministic rationale explaining which existing
        decision was integrated, which opportunity was involved, whether
        decision intelligence was available, what evidence status /
        strategy interpretation was attached, and why the integration
        status was assigned. Descriptive only.

    limitations
        Human-readable, deterministic statement of the limitations of
        the integration (context-not-decision, no upgrade of the existing
        decision, no future guarantee, no statistical claim, no
        replacement of existing scoring / ranking / selection / risk
        geometry / execution). Descriptive only.

    label
        Optional descriptive label identifying the integration run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).
    """

    integration_id: str
    existing_decision: Any
    existing_decision_summary: ExistingDecisionSummary
    profile: OpportunityProfile
    decision_intelligence: DecisionIntelligenceContext | None
    integration_status: IntegrationStatus
    evidence_status: DecisionContextStatus | None = None
    strategy_interpretation: StrategyAssessmentStatus | None = None
    observed_performance: HistoricalPerformanceStatistics | None = None
    evidence_strength: EvidenceStrength | None = None
    contextual_factors: tuple[DecisionIntelligenceFactor, ...] = field(
        default_factory=tuple,
    )
    rationale: str = ""
    limitations: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def has_decision_intelligence(self) -> bool:
        """Whether a decision intelligence context was supplied."""

        return self.decision_intelligence is not None

    @property
    def has_existing_decision(self) -> bool:
        """Whether an existing decision object was supplied."""

        return self.existing_decision is not None

    @property
    def has_evidence(self) -> bool:
        """Whether the attached decision intelligence has available evidence."""

        return (
            self.decision_intelligence is not None
            and self.decision_intelligence.has_evidence
        )

    @property
    def evidence_supported(self) -> bool:
        """Whether the attached decision intelligence is evidence-supported."""

        return (
            self.decision_intelligence is not None
            and self.decision_intelligence.evidence_supported
        )


__all__ = [
    "IntegratedDecisionContext",
    "IntegrationStatus",
]
