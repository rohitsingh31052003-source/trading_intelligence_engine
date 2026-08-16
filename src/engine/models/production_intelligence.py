"""
Domain models for the PRODUCTION INTEGRATION + FINAL HARDENING layer
(Sprint 12E).

Sprint 12E is the FINAL planned sprint of the current architecture. It
is NOT another intelligence / scoring layer and NOT a new orchestration
package. It is the smallest clean PRODUCTION INTEGRATION BOUNDARY that
bundles the ALREADY-COMPUTED outputs of the completed architecture into
ONE coherent, inspectable, production-facing artifact WITHOUT altering
the meaning of any previous layer.

Architecture (12E sits BELOW 12D; it consumes already-computed outputs
of the existing chain):

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C -> 12D -> 12E

CRITICAL DESIGN PRINCIPLE — the existing decision is AUTHORITATIVE:

The :class:`ProductionIntelligenceContext` carries the ORIGINAL existing
decision ONLY via the reused Sprint 12B
:class:`IntegratedDecisionContext` (retained BY REFERENCE). The existing
decision is NEVER modified, NEVER re-scored, NEVER re-ranked, NEVER
re-classified, NEVER upgraded and NEVER downgraded by 12E. The
integration / evidence / strategy / observed-performance values are all
SURFACED from the reused 12B context (never recomputed). There is NO
BUY / SELL / ENTER / EXIT / HOLD recommendation, NO probability, NO
score adjustment, NO hidden weight, NO re-ranking and NO re-selection.

DESIGN PRINCIPLE — reuse, do not re-invent:

The production layer consumes the ALREADY-COMPUTED Sprint 12B
:class:`IntegratedDecisionContext` (which itself embeds the reused 12A
decision-intelligence context, the reused 11Z strategy assessment, the
reused 11Y evidence strength and the reused 11X statistics). It
OPTIONALLY attaches the ALREADY-COMPUTED Sprint 12C
:class:`BacktestValidationReport` and / or Sprint 12D
:class:`RobustnessValidationReport` as OFFLINE / HISTORICAL validation
state. It does NOT recompute statistics, re-classify evidence,
re-interpret strategy, rebuild cohort matching, re-evaluate outcomes,
re-read candles, re-run the pipeline, re-run validation or use future
information. It only BUNDLES.

DESIGN PRINCIPLE — historical vs live boundary:

The production runtime NEVER runs historical replay against future
candles. The validation reports attached here are PRE-COMPUTED OFFLINE
artifacts (produced by the 12C / 12D engines over historical outcome
corpora); 12E merely REFERENCES them. The production engine's public
API takes NO candle / future-market-data argument. The point-in-time
correctness established in 11V / 11W is preserved unchanged.

DESIGN PRINCIPLE — eight separated concerns, never collapsed:

The production context surfaces, WITHOUT merging into one score:

1. Market / opportunity information   (reused OpportunityProfile)
2. Existing decision                   (reused ExistingDecisionSummary)
3. Historical observed performance    (reused 11X statistics)
4. Evidence strength                   (reused 11Y EvidenceStrength)
5. Strategy interpretation             (reused 11Z StrategyAssessmentStatus)
6. Decision intelligence context       (reused 12A DecisionIntelligenceContext)
7. Controlled integration status       (reused 12B IntegrationStatus)
8. Validation / robustness state       (optional 12C / 12D reports)

These are independently inspectable through the reused 12B context
(``integrated_context``) and the optional validation references.

DESIGN PRINCIPLE — honest fallbacks:

Unavailable information is represented EXPLICITLY, never fabricated:

* No integrated context supplied (``None``) ->
  :attr:`ProductionIntegrationStatus.INVALID`; nothing to expose.
* Integrated context present but no decision intelligence (12B
  ``UNAVAILABLE``) -> surfaced unchanged; no evidence fabricated.
* No validation reports supplied -> :attr:`ProductionValidationState.NONE`;
  the production result notes that no offline validation state is
  attached (never a fake PASS).
* Validation reports carry their own honest SKIPPED / UNAVAILABLE /
  INVALID semantics inherited from 12C / 12D.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. The production id hashes the
canonical identity (12B integration id + validation ids + validation
state + label + metadata); because the embedded 12B integration id is
already shuffle-invariant (via 11Y / 11Z), the production id is
shuffle-invariant for equivalent evidence.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* The reused 12B context and the 12C / 12D validation reports are
  referenced (never mutated, never recomputed).
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.models.backtest_validation import BacktestValidationReport
    from engine.models.decision_intelligence_integration import (
        IntegratedDecisionContext,
    )
    from engine.models.robustness_validation import RobustnessValidationReport


class ProductionIntegrationStatus(Enum):
    """
    The production-facing integration status, SURFACED (never
    recomputed) from the reused Sprint 12B
    :class:`~engine.models.decision_intelligence_integration.IntegrationStatus`.

    This status communicates WHETHER a coherent production-facing
    artifact could be assembled. It is NOT the future outcome of a
    trade, NOT a trading recommendation, NOT a probability of success,
    NOT a live-trading-readiness claim, and NOT a replacement of the
    existing decision classification. It does NOT re-derive the 12B
    integration status; it mirrors it so a production caller has a
    single explicit status without reaching into the 12B context.

    The statuses are MUTUALLY EXCLUSIVE:

    INTEGRATED
        A 12B :class:`IntegratedDecisionContext` with available evidence
        (12B ``INTEGRATED``) was supplied. The production artifact
        coherently bundles the existing decision, the historical
        observed performance, the evidence strength, the strategy
        interpretation, the decision-intelligence context, the
        controlled integration status and (optionally) the offline
        validation state. Even when integrated, the context is
        DESCRIPTIVE and does NOT guarantee future performance, does NOT
        upgrade the existing decision and does NOT produce a trading
        recommendation.

    CONTEXT_ONLY
        A 12B context was supplied but its decision intelligence
        carried no available evidence (12B ``CONTEXT_ONLY``). The
        production artifact exposes informational context only; it does
        NOT constitute supportive evidence and does NOT alter the
        existing decision.

    UNAVAILABLE
        A 12B context was supplied but no decision intelligence was
        attached (12B ``UNAVAILABLE``). No evidence is fabricated; the
        existing decision stands unchanged.

    INVALID
        No 12B :class:`IntegratedDecisionContext` was supplied (or the
        underlying 12B status was ``INVALID``). The production artifact
        carries an explicit invalid state; nothing is fabricated.

    No statistical hypothesis test is performed.
    """

    INTEGRATED = "INTEGRATED"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"

    @property
    def rank_value(self) -> int:
        """Deterministic ordering value (most-integrated first)."""

        return {
            ProductionIntegrationStatus.INTEGRATED: 0,
            ProductionIntegrationStatus.CONTEXT_ONLY: 1,
            ProductionIntegrationStatus.UNAVAILABLE: 2,
            ProductionIntegrationStatus.INVALID: 3,
        }[self]

    @property
    def is_attached(self) -> bool:
        """Whether decision intelligence was attached at all."""

        return self in (
            ProductionIntegrationStatus.INTEGRATED,
            ProductionIntegrationStatus.CONTEXT_ONLY,
        )

    @property
    def is_integrated(self) -> bool:
        """Whether decision intelligence with available evidence was attached."""

        return self == ProductionIntegrationStatus.INTEGRATED


class ProductionValidationState(Enum):
    """
    Descriptive state of the OFFLINE / HISTORICAL validation evidence
    attached to the production artifact.

    This state describes WHICH pre-computed validation reports were
    attached. It is NOT a trading-readiness claim, NOT a PASS / FAIL
    verdict on the live decision, and NOT a guarantee of future
    performance. The attached reports carry their own descriptive
    overall statuses (inherited from 12C / 12D); this enum only
    enumerates which are present.

    NONE
        No offline validation report was attached. The production
        artifact notes that no validation state is available (never a
        fake PASS).

    BACKTEST_VALIDATION
        A pre-computed Sprint 12C :class:`BacktestValidationReport` is
        attached by reference.

    ROBUSTNESS_VALIDATION
        A pre-computed Sprint 12D :class:`RobustnessValidationReport`
        is attached by reference.

    FULL_VALIDATION
        Both a 12C and a 12D report are attached by reference.
    """

    NONE = "NONE"
    BACKTEST_VALIDATION = "BACKTEST_VALIDATION"
    ROBUSTNESS_VALIDATION = "ROBUSTNESS_VALIDATION"
    FULL_VALIDATION = "FULL_VALIDATION"

    @property
    def has_validation(self) -> bool:
        """Whether any offline validation report is attached."""

        return self != ProductionValidationState.NONE


@dataclass(frozen=True, slots=True)
class ProductionIntelligenceContext:
    """
    The coherent PRODUCTION-FACING artifact bundling the completed
    architecture (Sprint 12E).

    The context is DESCRIPTIVE. It connects, WITHOUT recomputing and
    WITHOUT replacing the existing decision logic:

    * the INTEGRATED DECISION CONTEXT (reused Sprint 12B
      :class:`IntegratedDecisionContext`, retained by reference — the
      single source of truth for the existing decision, the decision
      intelligence, the evidence, the strategy interpretation, the
      observed performance and the controlled integration status),
    * the OPTIONAL BACKTEST VALIDATION (reused Sprint 12C
      :class:`BacktestValidationReport`, retained by reference; a
      pre-computed OFFLINE artifact),
    * the OPTIONAL ROBUSTNESS VALIDATION (reused Sprint 12D
      :class:`RobustnessValidationReport`, retained by reference; a
      pre-computed OFFLINE artifact),
    * the PRODUCTION INTEGRATION STATUS (mirrored from the reused 12B
      status; never recomputed),
    * the VALIDATION STATE (which offline reports are attached),
    * and the production rationale / limitations / audit metadata.

    It is NOT a trading signal, NOT a BUY / SELL / ENTER / EXIT / HOLD
    recommendation, NOT a price prediction, NOT a probability of
    success, NOT a profitability guarantee, NOT a statistical-
    significance claim, NOT a live-trading-readiness claim, and NOT a
    replacement of the existing decision / scoring / ranking /
    opportunity-selection / risk-geometry / execution logic. The
    existing decision classification / score / rank are represented
    WITHOUT alteration; evidence-supported context is NEVER interpreted
    as an upgrade of the existing decision.

    Attributes:

    production_id
        Deterministic identifier (``"prod-"`` + sha256[:16] of the
        canonical production identity).

    integrated_context
        The reused Sprint 12B :class:`IntegratedDecisionContext`
        (retained by reference; never mutated). ``None`` only when no
        integration context was supplied (INVALID). On serialization the
        integrated context is embedded by value (reusing the 12B
        serializer), so the audit view survives the round trip; the
        heavy original existing-decision reference reconstructs as
        ``None`` on load (regenerable by the caller), matching the 12B
        serialization discipline.

    backtest_validation
        The optional reused Sprint 12C
        :class:`BacktestValidationReport` (retained by reference; a
        pre-computed OFFLINE artifact), or ``None``.

    robustness_validation
        The optional reused Sprint 12D
        :class:`RobustnessValidationReport` (retained by reference; a
        pre-computed OFFLINE artifact), or ``None``.

    integration_status
        The :class:`ProductionIntegrationStatus` mirrored from the
        reused 12B status; never recomputed.

    validation_state
        The :class:`ProductionValidationState` describing which offline
        validation reports are attached.

    rationale
        Human-readable, deterministic rationale explaining which
        integrated context was bundled, which validation state is
        attached, and why the production status was assigned.
        Descriptive only.

    limitations
        Human-readable, deterministic statement of the limitations of
        the production integration (context-not-decision, no upgrade of
        the existing decision, no future guarantee, no statistical
        claim, historical-vs-live boundary, no replacement of existing
        scoring / ranking / selection / risk geometry / execution).
        Descriptive only.

    label
        Optional descriptive label identifying the production run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).
    """

    production_id: str
    integrated_context: "IntegratedDecisionContext | None"
    backtest_validation: "BacktestValidationReport | None" = None
    robustness_validation: "RobustnessValidationReport | None" = None
    integration_status: ProductionIntegrationStatus = (
        ProductionIntegrationStatus.INVALID
    )
    validation_state: ProductionValidationState = ProductionValidationState.NONE
    rationale: str = ""
    limitations: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------
    # Delegating properties (read-only views over the reused 12B
    # context). These NEVER recompute; they surface the 12B values so
    # a production caller can inspect the eight separated concerns
    # through one artifact without reaching past the boundary. When no
    # integrated context is present they return honest empty / None
    # values (never fabricated).
    # ------------------------------------------------------------

    @property
    def has_integrated_context(self) -> bool:
        """Whether a 12B integrated decision context was supplied."""

        return self.integrated_context is not None

    @property
    def has_validation(self) -> bool:
        """Whether any offline validation report is attached."""

        return self.validation_state.has_validation

    @property
    def existing_decision(self) -> Any:
        """
        The original existing decision object (reused from 12B, by
        reference). ``None`` when no integrated context / no decision.
        """

        if self.integrated_context is None:
            return None
        return self.integrated_context.existing_decision

    @property
    def existing_decision_summary(self):
        """The reused 12B :class:`ExistingDecisionSummary` projection."""

        if self.integrated_context is None:
            from engine.models.decision_intelligence import (
                ExistingDecisionSummary,
            )

            return ExistingDecisionSummary()
        return self.integrated_context.existing_decision_summary

    @property
    def profile(self):
        """The reused :class:`OpportunityProfile`."""

        if self.integrated_context is None:
            from engine.models.strategy_intelligence import OpportunityProfile

            return OpportunityProfile()
        return self.integrated_context.profile

    @property
    def decision_intelligence(self):
        """The reused Sprint 12A :class:`DecisionIntelligenceContext`."""

        if self.integrated_context is None:
            return None
        return self.integrated_context.decision_intelligence

    @property
    def evidence_status(self):
        """The reused :class:`DecisionContextStatus`, or ``None``."""

        if self.integrated_context is None:
            return None
        return self.integrated_context.evidence_status

    @property
    def strategy_interpretation(self):
        """The reused :class:`StrategyAssessmentStatus`, or ``None``."""

        if self.integrated_context is None:
            return None
        return self.integrated_context.strategy_interpretation

    @property
    def observed_performance(self):
        """The reused 11X :class:`HistoricalPerformanceStatistics`, or ``None``."""

        if self.integrated_context is None:
            return None
        return self.integrated_context.observed_performance

    @property
    def evidence_strength(self):
        """The reused 11Y :class:`EvidenceStrength`, or ``None``."""

        if self.integrated_context is None:
            return None
        return self.integrated_context.evidence_strength

    @property
    def contextual_factors(self) -> tuple:
        """The reused 12A :class:`DecisionIntelligenceFactor` tuple."""

        if self.integrated_context is None:
            return ()
        return self.integrated_context.contextual_factors

    @property
    def has_evidence(self) -> bool:
        """Whether the attached decision intelligence has available evidence."""

        return (
            self.integrated_context is not None
            and self.integrated_context.has_evidence
        )

    @property
    def evidence_supported(self) -> bool:
        """Whether the attached decision intelligence is evidence-supported."""

        return (
            self.integrated_context is not None
            and self.integrated_context.evidence_supported
        )

    @property
    def is_empty(self) -> bool:
        """Whether no integrated context and no validation were supplied."""

        return (
            self.integrated_context is None
            and self.backtest_validation is None
            and self.robustness_validation is None
        )


#: The fixed, explicit limitations / disclaimer carried on every
#: production intelligence context. It is intentionally NOT configurable
#: so the honesty contract cannot be weakened.
PRODUCTION_INTELLIGENCE_LIMITATIONS = (
    "Production intelligence is a coherent bundle of already-computed "
    "descriptive artifacts; it does NOT establish predictive validity, "
    "statistical significance, or future profitability, does NOT "
    "constitute a trading recommendation, and does NOT modify the "
    "existing decision / scoring logic. Historical evidence and offline "
    "validation are descriptive and do not guarantee future performance."
)


__all__ = [
    "PRODUCTION_INTELLIGENCE_LIMITATIONS",
    "ProductionIntelligenceContext",
    "ProductionIntegrationStatus",
    "ProductionValidationState",
]
