"""
Domain models for the historical setup quality analysis boundary
(Checkpoint 9.11).

Checkpoint 9.11 establishes the FIRST historical setup-quality analysis
boundary on top of the Checkpoint 9.10 statistical evidence-analysis
layer. It consumes a :class:`~engine.models.historical_setup_evidence.SetupEvidenceBatch`
and/or a :class:`~engine.models.historical_setup_statistics.SetupEvidenceStatisticsReport`
and produces a transparent research-quality report that describes the
strength and consistency of the historical observations without
pretending to predict future profitability:

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
    Checkpoint 9.11 Setup Quality Analysis (this layer)
            |
    Future: research-quality interpretation

Checkpoint 9.11 is QUALITY ANALYSIS ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT a ranking layer, NOT a black-box
  quality score.
* It consumes an already-aggregated :class:`SetupEvidenceBatch`
  (Checkpoint 9.9) and/or :class:`SetupEvidenceStatisticsReport`
  (Checkpoint 9.10) and computes transparent derived indicators. It does
  NOT re-read candles, NOT re-evaluate outcomes, NOT re-run discovery,
  NOT compute win rates, success rates, profitability, trade accuracy,
  expectancy, Sharpe ratios, profit factors, or quality scores.
* The Checkpoint 9.9 evidence boundary, the Checkpoint 9.10 statistical
  layer, and the Checkpoint 9.5/9.8 outcome layer remain authoritative
  and are untouched by these models.

DESIGN PRINCIPLE — transparent derived indicators only:

All derived indicators computed by this layer are transparent counts
and proportions. They describe the historical observations without
introducing trading semantics. Each indicator is named for exactly
what it measures:

* ``positive_forward_return_observation_count`` — count of observations
  with forward return > 0.
* ``negative_forward_return_observation_count`` — count of observations
  with forward return < 0.
* ``zero_forward_return_observation_count`` — count of observations
  with forward return == 0.
* ``proportion_positive_forward_return`` — proportion of forward-return
  observations that are positive.
* ``forward_return_direction_consistency`` — descriptive measure of how
  consistently non-zero forward returns point in one direction.

DESIGN PRINCIPLE — explicit consistency definition:

The ``forward_return_direction_consistency`` measure is defined
mathematically as::

    max(positive_count, negative_count) / (positive_count + negative_count)

when ``positive_count + negative_count > 0``, otherwise ``None``.

This measure ranges from 0.5 (perfectly split between positive and
negative) to 1.0 (all non-zero returns agree in direction). It is
purely descriptive: it summarizes the agreement of historical
observations without predicting future direction.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero (e.g. a forward return of exactly 0.0) is a valid
observation counted separately. A missing observation
(``forward_return is None``) and an
:attr:`ObservationStatus.INSUFFICIENT_DATA` observation are excluded
from derived indicators — never silently converted to zero. The three
states (zero, missing, insufficient) are never conflated.

DESIGN PRINCIPLE — deterministic:

Identical input batches produce identical quality reports. No wall-clock
time, no randomness, no unordered iteration. Source evidence and
statistics are never mutated.

DESIGN PRINCIPLE — no leakage:

The quality layer consumes an already-aggregated
:class:`SetupEvidenceBatch` and/or :class:`SetupEvidenceStatisticsReport`.
It never re-reads candles, never re-evaluates outcomes, and never feeds
information backward into discovery. The point-in-time separation is
preserved: discovery uses only information at T, outcome observations
use data > T, statistics summarize observations, quality analysis
summarizes statistics/observations, nothing feeds backward into
discovery.

DESIGN PRINCIPLE — no Sprint 11Y EvidenceStrength reuse:

Sprint 11Y :class:`~engine.models.historical_evidence.EvidenceStrength`
is NOT reused because its semantics are incompatible with neutral setup
observations:

* Sprint 11Y operates on Sprint 11W ``HistoricalOutcome`` objects
  (trade-level outcomes with entry/stop/target geometry).
* Checkpoint 9.9/9.10 operates on ``SetupEvidenceOccurrence`` objects
  (structural setup candidates with NO trade geometry).
* Sprint 11Y uses WEAK/MODERATE/STRONG labels and sample-size hard
  gates on trade outcomes (resolved trades, valid-R observations).
* Checkpoint 9.11 uses transparent counts and proportions on
  direction-neutral forward-return observations.

The two abstraction layers describe fundamentally different things:
Sprint 11Y describes trade-opportunity evidence strength; Checkpoint
9.11 describes the consistency of structural setup observations.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.models.historical_setup_evidence import SetupEvidenceBatch
from engine.models.historical_setup_outcome import ObservationStatus
from engine.models.historical_setup_statistics import (
    SetupEvidenceStatisticsReport,
    compute_statistics,
)


@dataclass(frozen=True, slots=True)
class SetupQualityReport:
    """
    Transparent research-quality report for historical setup observations.

    Consumes a :class:`SetupEvidenceBatch` (Checkpoint 9.9) and/or
    :class:`SetupEvidenceStatisticsReport` (Checkpoint 9.10) and produces
    a transparent quality report that describes the strength and
    consistency of the historical observations without pretending to
    predict future profitability.

    All derived indicators are transparent counts and proportions. No
    black-box quality score is computed. No trading semantics are
    introduced.

    Attributes:

    batch_id
        The source :class:`SetupEvidenceBatch` identifier.
    criterion_key
        The setup criterion identifier shared by all occurrences.
    total_occurrence_count
        Total number of occurrences in the batch.
    sufficient_data_count
        Number of occurrences with BOTH observations AVAILABLE
        (the batch-level sufficient-data count).
    insufficient_data_count
        Number of occurrences with insufficient data at the batch
        level.
    forward_return_observation_count
        Number of occurrences that contributed to the forward-return
        statistics (those with a sufficient forward-return
        observation).
    upward_excursion_observation_count
        Number of occurrences that contributed to the upward-excursion
        statistics.
    downward_excursion_observation_count
        Number of occurrences that contributed to the
        downward-excursion statistics.
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
    positive_forward_return_observation_count
        Number of observations with forward return > 0.
    negative_forward_return_observation_count
        Number of observations with forward return < 0.
    zero_forward_return_observation_count
        Number of observations with forward return == 0.
    proportion_positive_forward_return
        Proportion of forward-return observations that are positive,
        or ``None`` when there are no forward-return observations.
        Computed as ``positive_count / forward_return_observation_count``.
    forward_return_direction_consistency
        Descriptive measure of how consistently non-zero forward returns
        point in one direction, or ``None`` when there are no non-zero
        forward-return observations. Defined as
        ``max(positive_count, negative_count) / (positive_count + negative_count)``.
        Ranges from 0.5 (perfectly split) to 1.0 (all agree).
    """

    batch_id: str
    criterion_key: str
    total_occurrence_count: int
    sufficient_data_count: int
    insufficient_data_count: int
    forward_return_observation_count: int
    upward_excursion_observation_count: int
    downward_excursion_observation_count: int
    forward_return_mean: float | None
    forward_return_median: float | None
    forward_return_minimum: float | None
    forward_return_maximum: float | None
    upward_excursion_mean: float | None
    upward_excursion_median: float | None
    downward_excursion_mean: float | None
    downward_excursion_median: float | None
    positive_forward_return_observation_count: int
    negative_forward_return_observation_count: int
    zero_forward_return_observation_count: int
    proportion_positive_forward_return: float | None
    forward_return_direction_consistency: float | None

    @property
    def is_empty(self) -> bool:
        """Whether the source batch contains no occurrences."""
        return self.total_occurrence_count == 0

    @property
    def has_sufficient_observations(self) -> bool:
        """Whether there are any forward-return observations."""
        return self.forward_return_observation_count > 0

    def __post_init__(self) -> None:
        if self.total_occurrence_count < 0:
            raise ValueError("total_occurrence_count must be non-negative.")
        if self.sufficient_data_count < 0:
            raise ValueError("sufficient_data_count must be non-negative.")
        if self.insufficient_data_count < 0:
            raise ValueError("insufficient_data_count must be non-negative.")
        if (
            self.sufficient_data_count + self.insufficient_data_count
            != self.total_occurrence_count
        ):
            raise ValueError(
                "sufficient_data_count + insufficient_data_count must equal "
                "total_occurrence_count."
            )
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

        # Forward-return statistics: all None iff no contributing observations.
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

        # Upward-excursion statistics: all None iff no contributing observations.
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

        # Downward-excursion statistics: all None iff no contributing observations.
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

        # Derived indicator invariants.
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
                abs(self.proportion_positive_forward_return - expected_proportion)
                > 1e-12
            ):
                raise ValueError(
                    "proportion_positive_forward_return must equal "
                    "positive_count / forward_return_observation_count."
                )

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


def analyze_setup_quality(
    batch: SetupEvidenceBatch,
    statistics: SetupEvidenceStatisticsReport | None = None,
) -> SetupQualityReport:
    """
    Analyze the quality of historical setup observations.

    Consumes a :class:`SetupEvidenceBatch` (Checkpoint 9.9) and/or
    :class:`SetupEvidenceStatisticsReport` (Checkpoint 9.10) and produces
    a transparent quality report. When ``statistics`` is ``None``, it is
    computed from the batch.

    The quality report describes the strength and consistency of the
    historical observations without pretending to predict future
    profitability. All derived indicators are transparent counts and
    proportions. No black-box quality score is computed. No trading
    semantics are introduced.

    Args:
        batch: The :class:`SetupEvidenceBatch` to analyze (Checkpoint 9.9).
        statistics: Optional pre-computed
            :class:`SetupEvidenceStatisticsReport` (Checkpoint 9.10).
            When ``None``, computed from the batch.

    Returns:
        An immutable :class:`SetupQualityReport`.
    """
    if statistics is None:
        statistics = compute_statistics(batch)

    positive_count = 0
    negative_count = 0
    zero_count = 0

    for occ in batch.occurrences:
        if (
            occ.forward_return is not None
            and occ.forward_return.observation_status
            is ObservationStatus.AVAILABLE
        ):
            forward_return = occ.forward_return.forward_return
            if forward_return > 0.0:
                positive_count += 1
            elif forward_return < 0.0:
                negative_count += 1
            else:
                zero_count += 1

    fr_count = statistics.forward_return_observation_count

    proportion_positive = None
    if fr_count > 0:
        proportion_positive = positive_count / fr_count

    direction_consistency = None
    non_zero_count = positive_count + negative_count
    if non_zero_count > 0:
        direction_consistency = max(positive_count, negative_count) / non_zero_count

    return SetupQualityReport(
        batch_id=batch.batch_id,
        criterion_key=batch.criterion_key,
        total_occurrence_count=statistics.total_occurrence_count,
        sufficient_data_count=statistics.sufficient_data_count,
        insufficient_data_count=statistics.insufficient_data_count,
        forward_return_observation_count=statistics.forward_return_observation_count,
        upward_excursion_observation_count=statistics.upward_excursion_observation_count,
        downward_excursion_observation_count=statistics.downward_excursion_observation_count,
        forward_return_mean=statistics.forward_return_mean,
        forward_return_median=statistics.forward_return_median,
        forward_return_minimum=statistics.forward_return_minimum,
        forward_return_maximum=statistics.forward_return_maximum,
        upward_excursion_mean=statistics.upward_excursion_mean,
        upward_excursion_median=statistics.upward_excursion_median,
        downward_excursion_mean=statistics.downward_excursion_mean,
        downward_excursion_median=statistics.downward_excursion_median,
        positive_forward_return_observation_count=positive_count,
        negative_forward_return_observation_count=negative_count,
        zero_forward_return_observation_count=zero_count,
        proportion_positive_forward_return=proportion_positive,
        forward_return_direction_consistency=direction_consistency,
    )


__all__ = [
    "SetupQualityReport",
    "analyze_setup_quality",
]
