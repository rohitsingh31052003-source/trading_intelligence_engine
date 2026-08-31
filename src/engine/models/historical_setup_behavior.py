"""
Domain models for the historical setup behavioral-assessment boundary
(Checkpoint 10.4).

Checkpoint 10.4 establishes the historical behavioral-assessment layer
on top of the Checkpoint 10.3 setup-evaluation boundary. It consumes
already-computed evaluation and quality outputs and produces a
direction-neutral, auditable behavioral report describing how a setup
has behaved historically:

    Phase 6C Corpus (CorpusEvaluationPoint)
            |
    Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
            |
    Checkpoint 9.5/9.8 Outcome Observations (ForwardReturnObservation,
                                      PriceExcursionObservation)
            |
    Checkpoint 9.9 Evidence Aggregation Boundary (SetupEvidenceBatch)
            |
    Checkpoint 9.10 Statistical Evidence Analysis (SetupEvidenceStatisticsReport)
            |
    Checkpoint 9.11 Setup Quality Analysis (SetupQualityReport)
            |
    Checkpoint 10.3 Setup Evaluation (SetupEvaluationResult)
            |
    Checkpoint 10.4 Historical Behavioral Assessment (this layer)

Checkpoint 10.4 is BEHAVIORAL ASSESSMENT ONLY:

* It is NOT a trading strategy, NOT a decision
  engine, NOT a scoring layer, NOT a ranking layer.
* It consumes an already-evaluated :class:`SetupEvaluationResult`
  (Checkpoint 10.3) and an already-computed
  :class:`~engine.models.historical_setup_quality.SetupQualityReport`
  (Checkpoint 9.11) and composes them into a coherent behavioral
  report. It does NOT re-read candles, NOT re-evaluate outcomes, NOT
  re-run discovery, NOT recompute statistics.
* The Checkpoint 9.9 evidence boundary, the Checkpoint 9.10 statistical
  layer, the Checkpoint 9.11 quality layer, and the Checkpoint 10.3
  evaluation layer remain authoritative and are untouched by these
  models.

DESIGN PRINCIPLE — smallest sufficient input:

The behavioral layer consumes exactly two already-created outputs:

* :class:`SetupEvaluationResult` — provides the evaluation state
  (NO_DATA / INSUFFICIENT_DATA / EVALUABLE) and occurrence counts.
* :class:`~engine.models.historical_setup_quality.SetupQualityReport`
  — provides all behavioral statistics (forward-return statistics,
  excursion statistics, sign counts, proportions, consistency).

No smaller combination suffices: the evaluation result lacks behavioral
statistics, and the quality report lacks the evaluation state. Both are
immutable, point-in-time-safe, and derived from the same evidence
batch. The behavioral layer composes them — it does NOT recompute
candle-level metrics.

DESIGN PRINCIPLE — describe historical behavior, not trading decisions:

The behavioral report answers: "How has this setup behaved historically?"

Examples of acceptable information:

* Historical forward return averaged X.
* Median forward return was Y.
* Z% of available observations had a positive forward return.
* Historical upward excursion averaged A.
* Historical downward excursion averaged B.
* N observations were available.
* M observations lacked sufficient future history.

The report does NOT answer:

* "Will this setup work?"
* "This setup is profitable."
* "Take the trade."
* "Enter long." / "Enter short."

Even when statistics appear favorable, the implementation remains
descriptive.

DESIGN PRINCIPLE — explicit evaluation-state handling:

The report distinguishes three explicit states:

* ``NO_DATA`` — No historical occurrences exist. There is no behavioral
  evidence. Statistics are ``None`` (not fabricated zeros).
* ``INSUFFICIENT_DATA`` — Occurrences exist but do not meet the minimum
  evidence requirement. Available descriptive statistics are preserved;
  missing values are not converted to zero; no behavioral conclusion is
  manufactured.
* ``EVALUABLE`` — Enough observations exist. Descriptive historical
  behavior is exposed with exact sample sizes; real zeros are
  distinguished from missing; traceability to the source
  batch/evaluation is retained.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero is a valid observation. A missing value is
``None``. An insufficient-observation state is explicit. The four
states (real 0.0, None/missing, INSUFFICIENT_DATA, NO_DATA) are never
conflated. Missing is never silently converted to zero.

DESIGN PRINCIPLE — paired excursion integrity:

The paired excursion values remain associated with their originating
occurrence. Separate aggregate descriptive statistics are exposed for
upward and downward excursion, but the underlying paired observation
is not split.

DESIGN PRINCIPLE — deterministic:

For identical inputs, ``assess_setup_behavior`` produces identical
output. The ``behavior_report_id`` is derived deterministically from
the evaluation identity. No wall-clock time, no randomness, no
unordered iteration.

DESIGN PRINCIPLE — no leakage:

The behavioral layer consumes already-computed evaluation and quality
outputs. It never re-reads candles, never re-evaluates outcomes, and
never feeds information backward into discovery. The point-in-time
separation is preserved.

DESIGN PRINCIPLE — no mutation:

The behavioral layer does not mutate the source evaluation result,
quality report, or any upstream objects. All inputs are retained by
reference and never modified.

DESIGN PRINCIPLE — no reuse of incompatible abstractions:

The following existing abstractions are NOT reused because their
semantics are incompatible with neutral historical behavioral
assessment:

* Sprint 11Y :class:`~engine.models.historical_evidence.EvidenceStrength`
  — operates on trade-level outcomes (Sprint 11W HistoricalOutcome with
  entry/stop/target geometry), not structural setup observations.
* Sprint 11X :class:`~engine.models.historical_performance.HistoricalPerformanceStatistics`
  — trade-performance statistics with win/loss/target/stop semantics.
* Sprint 11Z :class:`~engine.models.strategy_intelligence.StrategyAssessmentStatus`
  — strategy-level interpretation built on trade-outcome evidence.
* Sprint 12A :class:`~engine.models.decision_intelligence.DecisionContextStatus`
  — decision-intelligence context for trade opportunities.
* Sprint 11Q :class:`~engine.models.setup_confluence.SetupClassification`
  — observation-time confluence classification, not behavioral assessment.
* Sprint 11S :class:`~engine.models.trade_decision.DecisionScore`
  — trading decision score, explicitly forbidden.
* Sprint 11I :class:`~engine.models.research.DataSufficiencyReport`
  — research-pipeline data sufficiency (trades, regimes, OOS), not
  setup-evidence behavioral assessment.

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

from engine.models.historical_setup_evaluation import (
    SetupEvaluationResult,
    SetupEvaluationStatus,
)
from engine.models.historical_setup_quality import SetupQualityReport


def _compute_behavior_report_id(evaluation_id: str) -> str:
    """Compute a deterministic behavioral-report identity hash."""
    return (
        "behavior-"
        + hashlib.sha256(evaluation_id.encode("utf-8")).hexdigest()[:16]
    )


@dataclass(frozen=True, slots=True)
class HistoricalSetupBehaviorReport:
    """
    Immutable, deterministic historical behavioral-assessment report
    (Checkpoint 10.4).

    Consumes an already-evaluated :class:`SetupEvaluationResult`
    (Checkpoint 10.3) and an already-computed
    :class:`SetupQualityReport` (Checkpoint 9.11) and composes them
    into a direction-neutral, auditable report describing how a setup
    has behaved historically.

    The report is free of trading decisions. It answers only: "How has
    this setup behaved historically?"

    Identity fields:

    behavior_report_id
        Deterministic identifier derived from the evaluation identity.
    evaluation_id
        The source :class:`SetupEvaluationResult` identifier
        (traceability to evaluation).
    batch_id
        The source :class:`~engine.models.historical_setup_evidence.SetupEvidenceBatch`
        identifier (traceability to evidence).
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

    Forward-return behavioral fields:

    forward_return_mean
        Mean forward return, or ``None`` when no occurrence has a
        sufficient forward-return observation.
    forward_return_median
        Median forward return, or ``None`` when no occurrence has a
        sufficient forward-return observation.
    forward_return_minimum
        Minimum forward return, or ``None`` when no occurrence has a
        sufficient forward-return observation.
    forward_return_maximum
        Maximum forward return, or ``None`` when no occurrence has a
        sufficient forward-return observation.
    positive_forward_return_observation_count
        Number of observations with forward return > 0.
    negative_forward_return_observation_count
        Number of observations with forward return < 0.
    zero_forward_return_observation_count
        Number of observations with forward return == 0.
    proportion_positive_forward_return
        Proportion of forward-return observations that are positive,
        or ``None`` when there are no forward-return observations.
    forward_return_direction_consistency
        Descriptive measure of how consistently non-zero forward returns
        point in one direction, or ``None`` when there are no non-zero
        forward-return observations. Defined as
        ``max(positive, negative) / (positive + negative)``.
        Ranges from 0.5 (perfectly split) to 1.0 (all agree).

    Price-excursion behavioral fields:

    upward_excursion_mean
        Mean upward excursion, or ``None`` when no occurrence has a
        sufficient price-excursion observation.
    upward_excursion_median
        Median upward excursion, or ``None`` when no occurrence has a
        sufficient price-excursion observation.
    downward_excursion_mean
        Mean downward excursion, or ``None`` when no occurrence has a
        sufficient price-excursion observation.
    downward_excursion_median
        Median downward excursion, or ``None`` when no occurrence has a
        sufficient price-excursion observation.
    """

    # Identity
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

    # Forward-return behavior
    forward_return_mean: float | None
    forward_return_median: float | None
    forward_return_minimum: float | None
    forward_return_maximum: float | None
    positive_forward_return_observation_count: int
    negative_forward_return_observation_count: int
    zero_forward_return_observation_count: int
    proportion_positive_forward_return: float | None
    forward_return_direction_consistency: float | None

    # Price-excursion behavior
    upward_excursion_mean: float | None
    upward_excursion_median: float | None
    downward_excursion_mean: float | None
    downward_excursion_median: float | None

    @property
    def is_empty(self) -> bool:
        """Whether the source batch contains no occurrences."""
        return self.total_occurrence_count == 0

    @property
    def has_forward_return_observations(self) -> bool:
        """Whether any forward-return observations are available."""
        return self.forward_return_observation_count > 0

    @property
    def has_excursion_observations(self) -> bool:
        """Whether any price-excursion observations are available."""
        return self.upward_excursion_observation_count > 0

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
        elif self.evaluation_status is SetupEvaluationStatus.INSUFFICIENT_DATA:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "INSUFFICIENT_DATA requires total_occurrence_count > 0."
                )
        elif self.evaluation_status is SetupEvaluationStatus.EVALUABLE:
            if self.total_occurrence_count == 0:
                raise ValueError(
                    "EVALUABLE requires total_occurrence_count > 0."
                )

        # --- Forward-return statistics: all None iff no observations ---
        if self.forward_return_observation_count == 0:
            if (
                self.forward_return_mean is not None
                or self.forward_return_median is not None
                or self.forward_return_minimum is not None
                or self.forward_return_maximum is not None
            ):
                raise ValueError(
                    "Forward-return statistics must be None when "
                    "forward_return_observation_count is 0."
                )
        else:
            if (
                self.forward_return_mean is None
                or self.forward_return_median is None
                or self.forward_return_minimum is None
                or self.forward_return_maximum is None
            ):
                raise ValueError(
                    "Forward-return statistics must be populated when "
                    "forward_return_observation_count > 0."
                )

        # --- Upward-excursion statistics: all None iff no observations ---
        if self.upward_excursion_observation_count == 0:
            if (
                self.upward_excursion_mean is not None
                or self.upward_excursion_median is not None
            ):
                raise ValueError(
                    "Upward-excursion statistics must be None when "
                    "upward_excursion_observation_count is 0."
                )
        else:
            if (
                self.upward_excursion_mean is None
                or self.upward_excursion_median is None
            ):
                raise ValueError(
                    "Upward-excursion statistics must be populated when "
                    "upward_excursion_observation_count > 0."
                )

        # --- Downward-excursion statistics: all None iff no observations ---
        if self.downward_excursion_observation_count == 0:
            if (
                self.downward_excursion_mean is not None
                or self.downward_excursion_median is not None
            ):
                raise ValueError(
                    "Downward-excursion statistics must be None when "
                    "downward_excursion_observation_count is 0."
                )
        else:
            if (
                self.downward_excursion_mean is None
                or self.downward_excursion_median is None
            ):
                raise ValueError(
                    "Downward-excursion statistics must be populated when "
                    "downward_excursion_observation_count > 0."
                )

        # --- Sign-count invariant ---
        if (
            self.positive_forward_return_observation_count
            + self.negative_forward_return_observation_count
            + self.zero_forward_return_observation_count
            != self.forward_return_observation_count
        ):
            raise ValueError(
                "positive + negative + zero forward-return counts must equal "
                "forward_return_observation_count."
            )

        # --- Proportion invariant ---
        if self.forward_return_observation_count == 0:
            if self.proportion_positive_forward_return is not None:
                raise ValueError(
                    "proportion_positive_forward_return must be None when "
                    "forward_return_observation_count is 0."
                )
        else:
            if self.proportion_positive_forward_return is None:
                raise ValueError(
                    "proportion_positive_forward_return must be populated "
                    "when forward_return_observation_count > 0."
                )
            expected_proportion = (
                self.positive_forward_return_observation_count
                / self.forward_return_observation_count
            )
            if (
                abs(
                    self.proportion_positive_forward_return
                    - expected_proportion
                )
                > 1e-12
            ):
                raise ValueError(
                    "proportion_positive_forward_return must equal "
                    "positive_count / forward_return_observation_count."
                )

        # --- Direction-consistency invariant ---
        non_zero_count = (
            self.positive_forward_return_observation_count
            + self.negative_forward_return_observation_count
        )
        if non_zero_count == 0:
            if self.forward_return_direction_consistency is not None:
                raise ValueError(
                    "forward_return_direction_consistency must be None when "
                    "there are no non-zero forward-return observations."
                )
        else:
            if self.forward_return_direction_consistency is None:
                raise ValueError(
                    "forward_return_direction_consistency must be populated "
                    "when there are non-zero forward-return observations."
                )
            expected_consistency = max(
                self.positive_forward_return_observation_count,
                self.negative_forward_return_observation_count,
            ) / non_zero_count
            if (
                abs(
                    self.forward_return_direction_consistency
                    - expected_consistency
                )
                > 1e-12
            ):
                raise ValueError(
                    "forward_return_direction_consistency must equal "
                    "max(positive, negative) / (positive + negative)."
                )


def assess_setup_behavior(
    evaluation: SetupEvaluationResult,
    quality: SetupQualityReport,
) -> HistoricalSetupBehaviorReport:
    """
    Compose a historical behavioral-assessment report from already-computed
    evaluation and quality outputs.

    Consumes a :class:`SetupEvaluationResult` (Checkpoint 10.3) and a
    :class:`SetupQualityReport` (Checkpoint 9.11) and produces a
    direction-neutral, deterministic, immutable behavioral report
    describing how a setup has behaved historically.

    The function does NOT recompute statistics — it composes existing
    results. Both inputs must derive from the same evidence batch
    (matched by ``batch_id``).

    Args:
        evaluation: The :class:`SetupEvaluationResult` from Checkpoint
            10.3. Provides the evaluation state and occurrence counts.
        quality: The :class:`SetupQualityReport` from Checkpoint 9.11.
            Provides all behavioral statistics.

    Returns:
        An immutable :class:`HistoricalSetupBehaviorReport`.

    Raises:
        ValueError: If the ``batch_id`` of the evaluation and quality
            report do not match.
    """
    if evaluation.batch_id != quality.batch_id:
        raise ValueError(
            "evaluation and quality report must share the same batch_id: "
            f"evaluation={evaluation.batch_id!r}, quality={quality.batch_id!r}."
        )

    behavior_report_id = _compute_behavior_report_id(evaluation.evaluation_id)

    return HistoricalSetupBehaviorReport(
        behavior_report_id=behavior_report_id,
        evaluation_id=evaluation.evaluation_id,
        batch_id=evaluation.batch_id,
        criterion_key=evaluation.criterion_key,
        instrument=evaluation.instrument,
        setup_timeframe=evaluation.setup_timeframe,
        context_timeframe=evaluation.context_timeframe,
        evaluation_status=evaluation.evaluation_status,
        total_occurrence_count=evaluation.total_occurrence_count,
        sufficient_data_count=evaluation.sufficient_data_count,
        insufficient_data_count=evaluation.insufficient_data_count,
        forward_return_observation_count=(
            quality.forward_return_observation_count
        ),
        upward_excursion_observation_count=(
            quality.upward_excursion_observation_count
        ),
        downward_excursion_observation_count=(
            quality.downward_excursion_observation_count
        ),
        min_observations_for_evaluation=(
            evaluation.min_observations_for_evaluation
        ),
        forward_return_mean=quality.forward_return_mean,
        forward_return_median=quality.forward_return_median,
        forward_return_minimum=quality.forward_return_minimum,
        forward_return_maximum=quality.forward_return_maximum,
        positive_forward_return_observation_count=(
            quality.positive_forward_return_observation_count
        ),
        negative_forward_return_observation_count=(
            quality.negative_forward_return_observation_count
        ),
        zero_forward_return_observation_count=(
            quality.zero_forward_return_observation_count
        ),
        proportion_positive_forward_return=(
            quality.proportion_positive_forward_return
        ),
        forward_return_direction_consistency=(
            quality.forward_return_direction_consistency
        ),
        upward_excursion_mean=quality.upward_excursion_mean,
        upward_excursion_median=quality.upward_excursion_median,
        downward_excursion_mean=quality.downward_excursion_mean,
        downward_excursion_median=quality.downward_excursion_median,
    )


__all__ = [
    "HistoricalSetupBehaviorReport",
    "assess_setup_behavior",
]
