"""
Domain models for the historical setup research-report boundary
(Checkpoint 10.6).

Checkpoint 10.6 establishes the historical setup research-report layer
on top of the Checkpoint 10.5 historical quality-interpretation boundary.
It consumes a single
:class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
and produces a final, human-readable historical setup research report
that answers one question:

    What does the historical research tell us about this setup?

without answering:

    Should I take this trade?
    Will this setup work in the future?
    What is the probability of profit?
    What is the expected return?
    What position should be taken?

    Phase 6C Corpus (CorpusEvaluationPoint)
            |
    Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
            |
    Checkpoint 9.5/9.8 Outcome Observations (ForwardReturnObservation,
                                      PriceExcursionObservation)
            |
    Checkpoint 9.9 Evidence Aggregation (SetupEvidenceBatch)
            |
    Checkpoint 9.10 Statistical Evidence Analysis (SetupEvidenceStatisticsReport)
            |
    Checkpoint 9.11 Setup Quality Analysis (SetupQualityReport)
            |
    Checkpoint 10.3 Setup Evaluation (SetupEvaluationResult)
            |
    Checkpoint 10.4 Historical Behavioral Assessment (HistoricalSetupBehaviorReport)
            |
    Checkpoint 10.5 Historical Setup Quality Interpretation
            |
    Checkpoint 10.6 Historical Setup Research Report (this layer)

Checkpoint 10.6 is RESEARCH PRESENTATION / SUMMARIZATION ONLY:

* It is NOT a trading strategy, NOT a forecasting engine, NOT a decision
  engine, NOT a scoring layer, NOT a ranking layer, NOT a profitability
  analysis layer.
* It consumes a single
  :class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
  (Checkpoint 10.5) and produces a human-readable historical setup
  research report. It does NOT re-read candles, NOT re-evaluate
  outcomes, NOT re-run discovery, NOT recompute statistics, NOT
  recompose behavior, NOT reinterpret quality.
* The Checkpoint 9.9 evidence boundary, the Checkpoint 9.10 statistical
  layer, the Checkpoint 9.11 quality layer, the Checkpoint 10.3 evaluation
  layer, the Checkpoint 10.4 behavioral layer, and the Checkpoint 10.5
  interpretation layer remain authoritative and are untouched by these
  models.

DESIGN PRINCIPLE — smallest sufficient input:

The research-report layer consumes exactly one already-created output:

* :class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
  — provides the evaluation state, all occurrence counts, the
  descriptive categorical interpretations (evidence availability,
  forward-return behavior, directional consistency), and the
  historical behavior summary.

No smaller combination suffices: the interpretation is the single
composition point that already aggregates evaluation state and all
descriptive interpretations. The research report reads these already-
computed categorical values and produces a final human-readable
research conclusion.

DESIGN PRINCIPLE — descriptive research presentation, not trading decisions:

The report answers: "What does the historical research tell us about
this setup?"

Examples of acceptable research conclusions:

* "No historical occurrences were available for this setup criterion."
* "Historical observations are limited, so behavioral conclusions
   should be treated as limited."
* "Historical observations are sufficient for descriptive analysis."
* "Historical forward-return observations show predominantly positive
   direction."
* "Historical forward-return observations show predominantly negative
   direction."
* "Historical forward-return observations show mixed direction."
* "Historical observations show high directional consistency."
* "Historical observations show moderate directional consistency."
* "Historical observations show low directional consistency."

The report does NOT answer:

* "Will this setup work?"
* "This setup is profitable."
* "Take the trade."
* "Enter long." / "Enter short."

DESIGN PRINCIPLE — explicit categorical classifications preserved:

The report preserves the three transparent categorical classifications
from Checkpoint 10.5:

1. ``evidence_availability`` — describes the amount of historical data:
   NO_HISTORICAL_DATA / LIMITED_HISTORICAL_DATA / SUFFICIENT_HISTORICAL_DATA.

2. ``forward_return_behavior`` — describes the direction of historical
   forward returns: NO_DIRECTIONAL_OBSERVATION / PREDOMINANTLY_POSITIVE /
   PREDOMINANTLY_NEGATIVE / MIXED_DIRECTION.

3. ``directional_consistency`` — describes how consistently non-zero
   forward returns point in one direction: NOT_EVALUABLE /
   HIGH_CONSISTENCY / MODERATE_CONSISTENCY / LOW_CONSISTENCY.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero is a valid observation. A missing value is ``None``.
An insufficient-observation state is explicit. The research report never
manufactures a behavioral conclusion from missing data.

DESIGN PRINCIPLE — deterministic:

For identical input, ``generate_historical_setup_research_report``
produces identical output. The ``report_id`` is derived deterministically
from the interpretation identity. No wall-clock time, no randomness,
no unordered iteration.

DESIGN PRINCIPLE — no leakage:

The research-report layer consumes a single already-computed
:class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`.
It never re-reads candles, never re-evaluates outcomes, and never feeds
information backward into discovery. The point-in-time separation is
preserved.

DESIGN PRINCIPLE — no mutation:

The research-report layer does not mutate the source interpretation or
any upstream objects.

DESIGN PRINCIPLE — no reuse of incompatible abstractions:

The following existing abstractions are NOT reused because their
semantics are incompatible with neutral historical setup research
reporting:

* Sprint 11Y :class:`~engine.models.historical_evidence.EvidenceStrength`
  — operates on trade-level outcomes (Sprint 11W HistoricalOutcome with
  entry/stop/target geometry), not structural setup observations.
* Sprint 11Z :class:`~engine.models.strategy_intelligence.StrategyAssessmentStatus`
  — strategy-level interpretation built on trade-outcome evidence.
* Sprint 12A :class:`~engine.models.decision_intelligence.DecisionContextStatus`
  — decision-intelligence context for trade opportunities.
* Sprint 11Q :class:`~engine.models.setup_confluence.SetupClassification`
  — observation-time confluence classification, not behavioral interpretation.
* Sprint 11S :class:`~engine.models.trade_decision.DecisionScore`
  — trading decision score, explicitly forbidden.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from engine.models.historical_setup_quality_interpretation import (
    DirectionalConsistency,
    EvidenceAvailability,
    ForwardReturnBehavior,
    HistoricalSetupQualityInterpretation,
)
from engine.models.historical_setup_evaluation import SetupEvaluationStatus


def _compute_report_id(interpretation_id: str) -> str:
    """Compute a deterministic research-report identity hash."""
    return (
        "historical-setup-research-"
        + hashlib.sha256(interpretation_id.encode("utf-8")).hexdigest()[:16]
    )


def _build_research_conclusion(
    evidence_availability: EvidenceAvailability,
    forward_return_behavior: ForwardReturnBehavior,
    directional_consistency: DirectionalConsistency,
    evaluation_status: SetupEvaluationStatus,
    total_occurrence_count: int,
    sufficient_data_count: int,
    insufficient_data_count: int,
    forward_return_observation_count: int,
    min_observations_for_evaluation: int,
) -> str:
    """Build a neutral human-readable research conclusion."""
    if evidence_availability is EvidenceAvailability.NO_HISTORICAL_DATA:
        return "No historical occurrences were available for this setup criterion."

    if evidence_availability is EvidenceAvailability.LIMITED_HISTORICAL_DATA:
        parts = [
            f"Historical observations are limited "
            f"({total_occurrence_count} observations, "
            f"below the evaluation threshold of "
            f"{min_observations_for_evaluation})."
        ]
        if forward_return_observation_count > 0:
            parts.append(
                _describe_forward_return_conclusion(forward_return_behavior)
            )
            parts.append(
                _describe_consistency_conclusion(directional_consistency)
            )
        else:
            parts.append("No forward-return observations are available.")
        return " ".join(parts)

    # SUFFICIENT_HISTORICAL_DATA
    parts = [
        "Historical observations are sufficient for descriptive analysis."
    ]
    if forward_return_observation_count > 0:
        parts.append(
            _describe_forward_return_conclusion(forward_return_behavior)
        )
        parts.append(
            _describe_consistency_conclusion(directional_consistency)
        )
    else:
        parts.append("No forward-return observations are available.")
    return " ".join(parts)


def _describe_forward_return_conclusion(
    behavior: ForwardReturnBehavior,
) -> str:
    """Describe forward-return behavior in research-conclusion form."""
    if behavior is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION:
        return "No forward-return observations were available."
    elif behavior is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE:
        return (
            "Historical forward-return observations show predominantly "
            "positive direction."
        )
    elif behavior is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE:
        return (
            "Historical forward-return observations show predominantly "
            "negative direction."
        )
    else:
        return (
            "Historical forward-return observations show mixed direction."
        )


def _describe_consistency_conclusion(
    consistency: DirectionalConsistency,
) -> str:
    """Describe directional consistency in research-conclusion form."""
    if consistency is DirectionalConsistency.NOT_EVALUABLE:
        return "Directional consistency is not evaluable."
    elif consistency is DirectionalConsistency.HIGH_CONSISTENCY:
        return "Historical observations show high directional consistency."
    elif consistency is DirectionalConsistency.MODERATE_CONSISTENCY:
        return "Historical observations show moderate directional consistency."
    else:
        return "Historical observations show low directional consistency."


@dataclass(frozen=True, slots=True)
class HistoricalSetupResearchReport:
    """
    Immutable, deterministic historical setup research report
    (Checkpoint 10.6).

    Consumes a
    :class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
    (Checkpoint 10.5) and produces a final, human-readable historical
    setup research report describing what the historical research tells
    us about this setup.

    The report is free of trading decisions. It answers only:
    "What does the historical research tell us about this setup?"

    Identity fields:

    report_id
        Deterministic identifier derived from the interpretation identity.
    interpretation_id
        The source :class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
        identifier (traceability to interpretation).
    behavior_report_id
        The source behavior-report identifier (traceability to behavior).
    evaluation_id
        The source evaluation identifier (traceability to evaluation).
    batch_id
        The source evidence-batch identifier (traceability to evidence).
    criterion_key
        The setup criterion identifier shared by all occurrences.
    instrument
        Canonical instrument name.
    setup_timeframe
        Canonical setup timeframe label.
    context_timeframe
        Canonical context timeframe label.

    Evaluation-state fields:

    evaluation_status
        The descriptive data-sufficiency status (NO_DATA /
        INSUFFICIENT_DATA / EVALUABLE).
    total_occurrence_count
        Total number of occurrences in the batch.
    sufficient_data_count
        Number of occurrences with both observations AVAILABLE.
    insufficient_data_count
        Number of occurrences with insufficient data.
    forward_return_observation_count
        Number of occurrences with a sufficient forward-return
        observation.
    upward_excursion_observation_count
        Number of occurrences with a sufficient price-excursion
        observation (upward).
    downward_excursion_observation_count
        Number of occurrences with a sufficient price-excursion
        observation (downward).
    min_observations_for_evaluation
        The minimum observation threshold used for evaluation.

    Interpretation fields:

    forward_return_behavior
        Descriptive classification of the direction of historical
        forward returns.
    directional_consistency
        Descriptive classification of how consistently non-zero
        forward returns point in one direction.
    evidence_availability
        Descriptive classification of how much historical data is
        available.
    historical_behavior_summary
        Human-readable descriptive summary from the interpretation layer.

    Research conclusion:

    research_conclusion
        A neutral human-readable descriptive research statement
        summarizing what the historical research tells us about this
        setup.
    """

    # Identity
    report_id: str
    interpretation_id: str
    behavior_report_id: str
    evaluation_id: str
    batch_id: str
    criterion_key: str
    instrument: str
    setup_timeframe: str
    context_timeframe: str

    # Evaluation state
    evaluation_status: SetupEvaluationStatus
    total_occurrence_count: int
    sufficient_data_count: int
    insufficient_data_count: int
    forward_return_observation_count: int
    upward_excursion_observation_count: int
    downward_excursion_observation_count: int
    min_observations_for_evaluation: int

    # Interpretation
    forward_return_behavior: ForwardReturnBehavior
    directional_consistency: DirectionalConsistency
    evidence_availability: EvidenceAvailability
    historical_behavior_summary: str

    # Research conclusion
    research_conclusion: str

    @property
    def is_empty(self) -> bool:
        """Whether the source batch contains no occurrences."""
        return self.total_occurrence_count == 0

    @property
    def has_sufficient_evidence(self) -> bool:
        """Whether the evidence availability is SUFFICIENT_HISTORICAL_DATA."""
        return (
            self.evidence_availability
            is EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
        )

    def __post_init__(self) -> None:
        # --- Count non-negativity ---
        if self.total_occurrence_count < 0:
            raise ValueError("total_occurrence_count must be non-negative.")
        if self.sufficient_data_count < 0:
            raise ValueError("sufficient_data_count must be non-negative.")
        if self.insufficient_data_count < 0:
            raise ValueError("insufficient_data_count must be non-negative.")
        if self.forward_return_observation_count < 0:
            raise ValueError(
                "forward_return_observation_count must be non-negative."
            )
        if self.upward_excursion_observation_count < 0:
            raise ValueError(
                "upward_excursion_observation_count must be non-negative."
            )
        if self.downward_excursion_observation_count < 0:
            raise ValueError(
                "downward_excursion_observation_count must be non-negative."
            )

        # --- Count sum invariants ---
        if (
            self.sufficient_data_count + self.insufficient_data_count
            != self.total_occurrence_count
        ):
            raise ValueError(
                "sufficient_data_count + insufficient_data_count must equal "
                "total_occurrence_count."
            )
        if self.forward_return_observation_count > self.total_occurrence_count:
            raise ValueError(
                "forward_return_observation_count cannot exceed "
                "total_occurrence_count."
            )
        if self.upward_excursion_observation_count > self.total_occurrence_count:
            raise ValueError(
                "upward_excursion_observation_count cannot exceed "
                "total_occurrence_count."
            )
        if (
            self.downward_excursion_observation_count
            > self.total_occurrence_count
        ):
            raise ValueError(
                "downward_excursion_observation_count cannot exceed "
                "total_occurrence_count."
            )

        if self.min_observations_for_evaluation < 1:
            raise ValueError(
                "min_observations_for_evaluation must be at least 1."
            )

        # --- Status-specific invariants ---
        if self.evaluation_status is SetupEvaluationStatus.NO_DATA:
            if self.total_occurrence_count != 0:
                raise ValueError(
                    "NO_DATA requires total_occurrence_count == 0."
                )
            if (
                self.evidence_availability
                is not EvidenceAvailability.NO_HISTORICAL_DATA
            ):
                raise ValueError(
                    "NO_DATA requires evidence_availability == "
                    "NO_HISTORICAL_DATA."
                )
        elif self.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "INSUFFICIENT_DATA requires total_occurrence_count > 0."
                )
            if (
                self.evidence_availability
                is not EvidenceAvailability.LIMITED_HISTORICAL_DATA
            ):
                raise ValueError(
                    "INSUFFICIENT_DATA requires evidence_availability == "
                    "LIMITED_HISTORICAL_DATA."
                )
        elif self.evaluation_status is SetupEvaluationStatus.EVALUABLE:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "EVALUABLE requires total_occurrence_count > 0."
                )
            if (
                self.evidence_availability
                is not EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA
            ):
                raise ValueError(
                    "EVALUABLE requires evidence_availability == "
                    "SUFFICIENT_HISTORICAL_DATA."
                )

        # --- Forward-return behavior invariants ---
        if self.forward_return_observation_count == 0:
            if (
                self.forward_return_behavior
                is not ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
            ):
                raise ValueError(
                    "forward_return_behavior must be NO_DIRECTIONAL_OBSERVATION "
                    "when forward_return_observation_count is 0."
                )
        else:
            if (
                self.forward_return_behavior
                is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION
            ):
                raise ValueError(
                    "forward_return_behavior cannot be "
                    "NO_DIRECTIONAL_OBSERVATION when "
                    "forward_return_observation_count > 0."
                )

        # --- Directional consistency invariants ---
        if self.evaluation_status is SetupEvaluationStatus.NO_DATA:
            if (
                self.directional_consistency
                is not DirectionalConsistency.NOT_EVALUABLE
            ):
                raise ValueError(
                    "directional_consistency must be NOT_EVALUABLE "
                    "when evaluation_status is NO_DATA."
                )


def generate_historical_setup_research_report(
    interpretation: HistoricalSetupQualityInterpretation,
) -> HistoricalSetupResearchReport:
    """
    Produce a final, human-readable historical setup research report
    from a historical setup quality interpretation.

    Consumes a
    :class:`~engine.models.historical_setup_quality_interpretation.HistoricalSetupQualityInterpretation`
    (Checkpoint 10.5) and produces a deterministic, immutable research
    report describing what the historical research tells us about this
    setup.

    The function does NOT recompute statistics — it reads existing
    categorical interpretations and produces a final research conclusion.
    The source interpretation is never mutated.

    Args:
        interpretation: The :class:`HistoricalSetupQualityInterpretation`
            from Checkpoint 10.5. Provides the evaluation state and all
            descriptive categorical interpretations.

    Returns:
        An immutable :class:`HistoricalSetupResearchReport`.

    Raises:
        TypeError: If ``interpretation`` is not a
            :class:`HistoricalSetupQualityInterpretation`.
    """
    if not isinstance(interpretation, HistoricalSetupQualityInterpretation):
        raise TypeError(
            "interpretation must be a HistoricalSetupQualityInterpretation, "
            f"got {type(interpretation).__name__!r}."
        )

    report_id = _compute_report_id(interpretation.interpretation_id)

    research_conclusion = _build_research_conclusion(
        evidence_availability=interpretation.evidence_availability,
        forward_return_behavior=interpretation.forward_return_behavior,
        directional_consistency=interpretation.directional_consistency,
        evaluation_status=interpretation.evaluation_status,
        total_occurrence_count=interpretation.total_occurrence_count,
        sufficient_data_count=interpretation.sufficient_data_count,
        insufficient_data_count=interpretation.insufficient_data_count,
        forward_return_observation_count=(
            interpretation.forward_return_observation_count
        ),
        min_observations_for_evaluation=(
            interpretation.min_observations_for_evaluation
        ),
    )

    return HistoricalSetupResearchReport(
        report_id=report_id,
        interpretation_id=interpretation.interpretation_id,
        behavior_report_id=interpretation.behavior_report_id,
        evaluation_id=interpretation.evaluation_id,
        batch_id=interpretation.batch_id,
        criterion_key=interpretation.criterion_key,
        instrument=interpretation.instrument,
        setup_timeframe=interpretation.setup_timeframe,
        context_timeframe=interpretation.context_timeframe,
        evaluation_status=interpretation.evaluation_status,
        total_occurrence_count=interpretation.total_occurrence_count,
        sufficient_data_count=interpretation.sufficient_data_count,
        insufficient_data_count=interpretation.insufficient_data_count,
        forward_return_observation_count=(
            interpretation.forward_return_observation_count
        ),
        upward_excursion_observation_count=(
            interpretation.upward_excursion_observation_count
        ),
        downward_excursion_observation_count=(
            interpretation.downward_excursion_observation_count
        ),
        min_observations_for_evaluation=(
            interpretation.min_observations_for_evaluation
        ),
        forward_return_behavior=interpretation.forward_return_behavior,
        directional_consistency=interpretation.directional_consistency,
        evidence_availability=interpretation.evidence_availability,
        historical_behavior_summary=interpretation.historical_behavior_summary,
        research_conclusion=research_conclusion,
    )


__all__ = [
    "HistoricalSetupResearchReport",
    "generate_historical_setup_research_report",
]
