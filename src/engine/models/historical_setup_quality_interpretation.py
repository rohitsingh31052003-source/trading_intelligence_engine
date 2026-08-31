"""
Domain models for the historical setup quality interpretation boundary
(Checkpoint 10.5).

Checkpoint 10.5 establishes the historical setup quality interpretation
layer on top of the Checkpoint 10.4 historical behavioral-assessment
boundary. It consumes a
:class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
and produces a transparent, deterministic, descriptive interpretation
that answers one question:

    What does the historical behavior of this setup indicate descriptively?

without answering:

    Will this setup work?
    Should I take this trade?
    Is this a good trade?
    Should the system enter a position?

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
    Checkpoint 10.5 Historical Setup Quality Interpretation (this layer)

Checkpoint 10.5 is INTERPRETATION ONLY:

* It is NOT a trading strategy, NOT a forecasting engine, NOT a decision
  engine, NOT a scoring layer, NOT a ranking layer.
* It consumes a single
  :class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
  (Checkpoint 10.4) and produces a transparent descriptive interpretation.
  It does NOT re-read candles, NOT re-evaluate outcomes, NOT re-run
  discovery, NOT recompute statistics, NOT recompose behavior.
* The Checkpoint 9.9 evidence boundary, the Checkpoint 9.10 statistical
  layer, the Checkpoint 9.11 quality layer, the Checkpoint 10.3 evaluation
  layer, and the Checkpoint 10.4 behavioral layer remain authoritative
  and are untouched by these models.

DESIGN PRINCIPLE — smallest sufficient input:

The interpretation layer consumes exactly one already-created output:

* :class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
  — provides the evaluation state, all behavioral statistics, forward-return
  sign counts, proportions, consistency, and excursion statistics.

No smaller combination suffices: the behavior report is the single
composition point that already aggregates evaluation state and all
behavioral statistics. The interpretation layer reads descriptive
categorical interpretations from these already-computed values.

DESIGN PRINCIPLE — descriptive interpretation, not trading decisions:

The interpretation answers: "What does the historical behavior of this
setup indicate descriptively?"

Examples of acceptable interpretations:

* "Historical observations were predominantly positive."
* "Historical observations were predominantly negative."
* "Historical observations were mixed."
* "Historical observations showed high directional consistency."
* "Historical sample size is insufficient for interpretation."
* "No sufficient historical observations are available."

The interpretation does NOT answer:

* "Will this setup work?"
* "This setup is profitable."
* "Take the trade."
* "Enter long." / "Enter short."

DESIGN PRINCIPLE — explicit categorical classifications:

The interpretation uses three transparent categorical classifications:

1. ``evidence_availability`` — describes the amount of historical data:

   * ``NO_HISTORICAL_DATA`` — No occurrences exist.
   * ``LIMITED_HISTORICAL_DATA`` — Occurrences exist but are below the
     evaluation threshold.
   * ``SUFFICIENT_HISTORICAL_DATA`` — Enough observations exist for
     evaluation.

2. ``forward_return_behavior`` — describes the direction of historical
   forward returns:

   * ``NO_DIRECTIONAL_OBSERVATION`` — No forward-return observations.
   * ``PREDOMINANTLY_POSITIVE`` — More than 60% of observations positive.
   * ``PREDOMINANTLY_NEGATIVE`` — Less than 40% of observations positive.
   * ``MIXED_DIRECTION`` — Between 40% and 60% positive.

3. ``directional_consistency`` — describes how consistently non-zero
   forward returns point in one direction:

   * ``NOT_EVALUABLE`` — No non-zero forward-return observations.
   * ``HIGH_CONSISTENCY`` — Consistency >= 0.75.
   * ``MODERATE_CONSISTENCY`` — 0.6 <= Consistency < 0.75.
   * ``LOW_CONSISTENCY`` — 0.5 <= Consistency < 0.6.

Threshold documentation:

* The 0.6 / 0.4 boundaries for forward-return behavior are symmetric
  around 0.5 (perfectly split). They describe a meaningful predominance
  without claiming forecasting power.
* The 0.75 / 0.6 boundaries for directional consistency partition the
  [0.5, 1.0] range into three descriptive bands. A consistency of 0.5
  means perfectly split (no consistency); 1.0 means perfect agreement.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero is a valid observation. A missing value is ``None``.
An insufficient-observation state is explicit. The interpretation never
manufactures a classification from missing data.

DESIGN PRINCIPLE — deterministic:

For identical input, ``interpret_setup_behavior`` produces identical
output. The ``interpretation_id`` is derived deterministically from
the behavior report identity. No wall-clock time, no randomness, no
unordered iteration.

DESIGN PRINCIPLE — no leakage:

The interpretation layer consumes a single already-computed
:class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`.
It never re-reads candles, never re-evaluates outcomes, and never feeds
information backward into discovery. The point-in-time separation is
preserved.

DESIGN PRINCIPLE — no mutation:

The interpretation layer does not mutate the source behavior report or
any upstream objects.

DESIGN PRINCIPLE — no reuse of incompatible abstractions:

The following existing abstractions are NOT reused because their
semantics are incompatible with neutral historical setup interpretation:

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
from enum import Enum

from engine.models.historical_setup_behavior import HistoricalSetupBehaviorReport
from engine.models.historical_setup_evaluation import SetupEvaluationStatus


class EvidenceAvailability(Enum):
    """
    Descriptive classification of how much historical data is available
    for a setup criterion.

    This enum describes the amount of historical evidence. It does NOT
    describe whether the setup is good or bad, profitable or unprofitable,
    or whether to trade it.

    NO_HISTORICAL_DATA
        No historical occurrences exist for this setup criterion. There
        is nothing to interpret.

    LIMITED_HISTORICAL_DATA
        Historical occurrences exist, but they do not meet the minimum
        observation threshold for evaluation. Available descriptive
        statistics are preserved, but the sample is below the threshold.

    SUFFICIENT_HISTORICAL_DATA
        Enough historical observations exist to evaluate the setup.
    """

    NO_HISTORICAL_DATA = "NO_HISTORICAL_DATA"
    LIMITED_HISTORICAL_DATA = "LIMITED_HISTORICAL_DATA"
    SUFFICIENT_HISTORICAL_DATA = "SUFFICIENT_HISTORICAL_DATA"


class ForwardReturnBehavior(Enum):
    """
    Descriptive classification of the direction of historical forward
    returns for a setup criterion.

    This enum describes the historical direction of observations. It does
    NOT forecast future direction.

    NO_DIRECTIONAL_OBSERVATION
        No forward-return observations are available.

    PREDOMINANTLY_POSITIVE
        More than 60% of available forward-return observations are
        positive. This describes historical observations only.

    PREDOMINANTLY_NEGATIVE
        Less than 40% of available forward-return observations are
        positive (i.e., more than 60% are negative). This describes
        historical observations only.

    MIXED_DIRECTION
        Between 40% and 60% of available forward-return observations are
        positive. Historical observations do not show a clear direction.
    """

    NO_DIRECTIONAL_OBSERVATION = "NO_DIRECTIONAL_OBSERVATION"
    PREDOMINANTLY_POSITIVE = "PREDOMINANTLY_POSITIVE"
    PREDOMINANTLY_NEGATIVE = "PREDOMINANTLY_NEGATIVE"
    MIXED_DIRECTION = "MIXED_DIRECTION"


class DirectionalConsistency(Enum):
    """
    Descriptive classification of how consistently historical non-zero
    forward returns point in one direction.

    This enum describes the agreement of historical observations. It does
    NOT forecast future consistency.

    NOT_EVALUABLE
        No non-zero forward-return observations are available. Consistency
        cannot be evaluated.

    HIGH_CONSISTENCY
        Non-zero forward returns show strong agreement (>= 75% in one
        direction).

    MODERATE_CONSISTENCY
        Non-zero forward returns show moderate agreement (60-75% in one
        direction).

    LOW_CONSISTENCY
        Non-zero forward returns show weak agreement (50-60% in one
        direction, near perfectly split).
    """

    NOT_EVALUABLE = "NOT_EVALUABLE"
    HIGH_CONSISTENCY = "HIGH_CONSISTENCY"
    MODERATE_CONSISTENCY = "MODERATE_CONSISTENCY"
    LOW_CONSISTENCY = "LOW_CONSISTENCY"


# ---------------------------------------------------------------------------
# Threshold constants (explicit, documented, deterministic)
# ---------------------------------------------------------------------------

# Forward-return behavior boundaries (symmetric around 0.5).
_FORWARD_RETURN_PREDOMINANTLY_POSITIVE_THRESHOLD = 0.6
_FORWARD_RETURN_PREDOMINANTLY_NEGATIVE_THRESHOLD = 0.4

# Directional consistency boundaries (partition [0.5, 1.0]).
_DIRECTIONAL_CONSISTENCY_HIGH_THRESHOLD = 0.75
_DIRECTIONAL_CONSISTENCY_MODERATE_THRESHOLD = 0.6


def _compute_interpretation_id(behavior_report_id: str) -> str:
    """Compute a deterministic interpretation identity hash."""
    return (
        "setup-quality-interpretation-"
        + hashlib.sha256(behavior_report_id.encode("utf-8")).hexdigest()[:16]
    )


@dataclass(frozen=True, slots=True)
class HistoricalSetupQualityInterpretation:
    """
    Immutable, deterministic historical setup quality interpretation
    (Checkpoint 10.5).

    Consumes a
    :class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
    (Checkpoint 10.4) and produces a transparent, deterministic,
    descriptive interpretation of what the historical behavior of this
    setup indicates descriptively.

    The interpretation is free of trading decisions. It answers only:
    "What does the historical behavior of this setup indicate
    descriptively?"

    Identity fields:

    interpretation_id
        Deterministic identifier derived from the behavior report identity.
    behavior_report_id
        The source :class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
        identifier (traceability to behavior).
    evaluation_id
        The source evaluation identifier (traceability to evaluation).
    batch_id
        The source evidence batch identifier (traceability to evidence).
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

    Descriptive interpretation fields:

    evidence_availability
        Descriptive classification of how much historical data is
        available.
    forward_return_behavior
        Descriptive classification of the direction of historical
        forward returns.
    directional_consistency
        Descriptive classification of how consistently non-zero
        forward returns point in one direction.
    historical_behavior_summary
        Human-readable descriptive summary of the interpretation.
    """

    # Identity
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

    # Descriptive interpretation
    evidence_availability: EvidenceAvailability
    forward_return_behavior: ForwardReturnBehavior
    directional_consistency: DirectionalConsistency
    historical_behavior_summary: str

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


def _classify_evidence_availability(
    evaluation_status: SetupEvaluationStatus,
) -> EvidenceAvailability:
    """Classify evidence availability from evaluation status."""
    if evaluation_status is SetupEvaluationStatus.NO_DATA:
        return EvidenceAvailability.NO_HISTORICAL_DATA
    elif evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
        return EvidenceAvailability.LIMITED_HISTORICAL_DATA
    else:
        return EvidenceAvailability.SUFFICIENT_HISTORICAL_DATA


def _classify_forward_return_behavior(
    forward_return_observation_count: int,
    proportion_positive_forward_return: float | None,
) -> ForwardReturnBehavior:
    """Classify forward-return behavior from observation statistics."""
    if forward_return_observation_count == 0:
        return ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION

    proportion = proportion_positive_forward_return
    if proportion is None:
        return ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION

    if proportion > _FORWARD_RETURN_PREDOMINANTLY_POSITIVE_THRESHOLD:
        return ForwardReturnBehavior.PREDOMINANTLY_POSITIVE
    elif proportion < _FORWARD_RETURN_PREDOMINANTLY_NEGATIVE_THRESHOLD:
        return ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE
    else:
        return ForwardReturnBehavior.MIXED_DIRECTION


def _classify_directional_consistency(
    forward_return_direction_consistency: float | None,
) -> DirectionalConsistency:
    """Classify directional consistency from the consistency measure."""
    if forward_return_direction_consistency is None:
        return DirectionalConsistency.NOT_EVALUABLE

    consistency = forward_return_direction_consistency

    if consistency >= _DIRECTIONAL_CONSISTENCY_HIGH_THRESHOLD:
        return DirectionalConsistency.HIGH_CONSISTENCY
    elif consistency >= _DIRECTIONAL_CONSISTENCY_MODERATE_THRESHOLD:
        return DirectionalConsistency.MODERATE_CONSISTENCY
    else:
        return DirectionalConsistency.LOW_CONSISTENCY


def _build_historical_behavior_summary(
    evidence_availability: EvidenceAvailability,
    forward_return_behavior: ForwardReturnBehavior,
    directional_consistency: DirectionalConsistency,
    forward_return_observation_count: int,
    proportion_positive_forward_return: float | None,
    forward_return_direction_consistency: float | None,
    total_occurrence_count: int,
    min_observations_for_evaluation: int,
) -> str:
    """Build a human-readable descriptive summary of the interpretation."""
    if (
        evidence_availability is EvidenceAvailability.NO_HISTORICAL_DATA
    ):
        return "No historical observations are available for this setup criterion."

    if (
        evidence_availability
        is EvidenceAvailability.LIMITED_HISTORICAL_DATA
    ):
        parts = [
            f"Limited historical data ({total_occurrence_count} observations, "
            f"below evaluation threshold of {min_observations_for_evaluation})."
        ]
        if forward_return_observation_count > 0:
            parts.append(_describe_forward_return(
                forward_return_behavior,
                proportion_positive_forward_return,
            ))
            parts.append(_describe_consistency(
                directional_consistency,
                forward_return_direction_consistency,
            ))
        return " ".join(parts)

    # SUFFICIENT_HISTORICAL_DATA
    parts = [
        f"Sufficient historical data ({total_occurrence_count} observations)."
    ]
    if forward_return_observation_count > 0:
        parts.append(_describe_forward_return(
            forward_return_behavior,
            proportion_positive_forward_return,
        ))
        parts.append(_describe_consistency(
            directional_consistency,
            forward_return_direction_consistency,
        ))
    return " ".join(parts)


def _describe_forward_return(
    behavior: ForwardReturnBehavior,
    proportion: float | None,
) -> str:
    """Describe forward-return behavior in words."""
    if behavior is ForwardReturnBehavior.NO_DIRECTIONAL_OBSERVATION:
        return "No forward-return observations are available."
    elif behavior is ForwardReturnBehavior.PREDOMINANTLY_POSITIVE:
        return (
            f"Historical forward returns were predominantly positive "
            f"({proportion:.0%} of observations)."
        )
    elif behavior is ForwardReturnBehavior.PREDOMINANTLY_NEGATIVE:
        return (
            f"Historical forward returns were predominantly negative "
            f"({1.0 - proportion:.0%} of observations were negative)."
        )
    else:
        return (
            f"Historical forward returns were mixed "
            f"({proportion:.0%} positive)."
        )


def _describe_consistency(
    consistency: DirectionalConsistency,
    value: float | None,
) -> str:
    """Describe directional consistency in words."""
    if consistency is DirectionalConsistency.NOT_EVALUABLE:
        return "Directional consistency is not evaluable."
    elif consistency is DirectionalConsistency.HIGH_CONSISTENCY:
        return (
            f"Historical observations showed high directional consistency "
            f"(consistency measure: {value:.2f})."
        )
    elif consistency is DirectionalConsistency.MODERATE_CONSISTENCY:
        return (
            f"Historical observations showed moderate directional consistency "
            f"(consistency measure: {value:.2f})."
        )
    else:
        return (
            f"Historical observations showed low directional consistency "
            f"(consistency measure: {value:.2f})."
        )


def interpret_setup_behavior(
    behavior: HistoricalSetupBehaviorReport,
) -> HistoricalSetupQualityInterpretation:
    """
    Produce a transparent, deterministic, descriptive interpretation of
    a historical setup behavior report.

    Consumes a
    :class:`~engine.models.historical_setup_behavior.HistoricalSetupBehaviorReport`
    (Checkpoint 10.4) and produces a direction-neutral, deterministic,
    immutable interpretation describing what the historical behavior of
    this setup indicates descriptively.

    The function does NOT recompute statistics — it classifies existing
    results into descriptive categories. The source behavior report is
    never mutated.

    Args:
        behavior: The :class:`HistoricalSetupBehaviorReport` from
            Checkpoint 10.4. Provides the evaluation state and all
            behavioral statistics.

    Returns:
        An immutable :class:`HistoricalSetupQualityInterpretation`.

    Raises:
        TypeError: If ``behavior`` is not a
            :class:`HistoricalSetupBehaviorReport`.
    """
    if not isinstance(behavior, HistoricalSetupBehaviorReport):
        raise TypeError(
            "behavior must be a HistoricalSetupBehaviorReport, "
            f"got {type(behavior).__name__!r}."
        )

    evidence_availability = _classify_evidence_availability(
        behavior.evaluation_status
    )

    forward_return_behavior = _classify_forward_return_behavior(
        behavior.forward_return_observation_count,
        behavior.proportion_positive_forward_return,
    )

    directional_consistency = _classify_directional_consistency(
        behavior.forward_return_direction_consistency,
    )

    historical_behavior_summary = _build_historical_behavior_summary(
        evidence_availability=evidence_availability,
        forward_return_behavior=forward_return_behavior,
        directional_consistency=directional_consistency,
        forward_return_observation_count=behavior.forward_return_observation_count,
        proportion_positive_forward_return=(
            behavior.proportion_positive_forward_return
        ),
        forward_return_direction_consistency=(
            behavior.forward_return_direction_consistency
        ),
        total_occurrence_count=behavior.total_occurrence_count,
        min_observations_for_evaluation=(
            behavior.min_observations_for_evaluation
        ),
    )

    interpretation_id = _compute_interpretation_id(behavior.behavior_report_id)

    return HistoricalSetupQualityInterpretation(
        interpretation_id=interpretation_id,
        behavior_report_id=behavior.behavior_report_id,
        evaluation_id=behavior.evaluation_id,
        batch_id=behavior.batch_id,
        criterion_key=behavior.criterion_key,
        instrument=behavior.instrument,
        setup_timeframe=behavior.setup_timeframe,
        context_timeframe=behavior.context_timeframe,
        evaluation_status=behavior.evaluation_status,
        total_occurrence_count=behavior.total_occurrence_count,
        sufficient_data_count=behavior.sufficient_data_count,
        insufficient_data_count=behavior.insufficient_data_count,
        forward_return_observation_count=(
            behavior.forward_return_observation_count
        ),
        upward_excursion_observation_count=(
            behavior.upward_excursion_observation_count
        ),
        downward_excursion_observation_count=(
            behavior.downward_excursion_observation_count
        ),
        min_observations_for_evaluation=(
            behavior.min_observations_for_evaluation
        ),
        evidence_availability=evidence_availability,
        forward_return_behavior=forward_return_behavior,
        directional_consistency=directional_consistency,
        historical_behavior_summary=historical_behavior_summary,
    )


__all__ = [
    "HistoricalSetupQualityInterpretation",
    "EvidenceAvailability",
    "ForwardReturnBehavior",
    "DirectionalConsistency",
    "interpret_setup_behavior",
]
