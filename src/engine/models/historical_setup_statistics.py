"""
Domain models for the statistical evidence-analysis layer
(Checkpoint 9.10).

Checkpoint 9.10 adds the statistical evidence-analysis layer on top of
the Checkpoint 9.9 evidence aggregation boundary. It consumes a
:class:`~engine.models.historical_setup_evidence.SetupEvidenceBatch`
and produces a minimal, immutable statistical evidence report containing
neutral descriptive statistics only:

    Phase 6C Corpus (CorpusEvaluationPoint)
            |
    Checkpoint 9.1 Setup Discovery (HistoricalSetupCandidate at T)
            |
    Checkpoint 9.5/9.8 Outcome Observations (ForwardReturnObservation,
                                      PriceExcursionObservation)
            |
    Checkpoint 9.9 Evidence Aggregation Boundary (SetupEvidenceBatch)
            |
    Checkpoint 9.10 Statistical Evidence Analysis (this layer)
            |
    Future: setup quality analysis

Checkpoint 9.10 is STATISTICAL ANALYSIS ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT a setup-quality engine.
* It consumes an already-aggregated :class:`SetupEvidenceBatch`
  (Checkpoint 9.9) and computes neutral descriptive statistics. It does
  NOT re-read candles, NOT re-evaluate outcomes, NOT re-run discovery,
  NOT compute win rates, loss rates, expectancy, Sharpe ratios, profit
  factors, statistical significance, predictive power, or quality scores.
* The Checkpoint 9.9 evidence boundary and the Checkpoint 9.5/9.8 outcome
  layer remain authoritative and are untouched by these models.

DESIGN PRINCIPLE — statistics describe historical observations only:

Statistics computed by this layer describe the historical observations
already collected in the evidence batch. They do NOT determine setup
quality, do NOT predict future performance, and do NOT constitute a
trading recommendation. They are neutral summaries of what was observed.

DESIGN PRINCIPLE — sufficient-data gating per metric (HARD REQUIREMENT):

Each statistic is computed ONLY from occurrences with sufficient data
for the RELEVANT metric:

* Forward-return statistics use only occurrences whose
  :class:`ForwardReturnObservation` carries
  :attr:`ObservationStatus.AVAILABLE`.
* Excursion statistics use only occurrences whose
  :class:`PriceExcursionObservation` carries
  :attr:`ObservationStatus.AVAILABLE`.

An occurrence that has sufficient forward-return data but insufficient
price-excursion data still contributes to the forward-return statistics;
it is excluded only from the excursion statistics (and vice versa). This
per-metric gating preserves the maximum information without fabricating
values.

DESIGN PRINCIPLE — preserve zero / missing / insufficient distinction:

A real observed zero (e.g. a forward return of exactly 0.0) is a valid
observation and contributes to the statistics. A missing observation
(``forward_return is None``) and an
:attr:`ObservationStatus.INSUFFICIENT_DATA` observation are excluded
from the relevant statistic — never silently converted to zero. The
three states (zero, missing, insufficient) are never conflated.

DESIGN PRINCIPLE — paired excursions remain associated:

The paired excursion values (``max_upward_excursion``,
``max_downward_excursion``) remain associated with their originating
occurrence. Separate aggregate distributions are computed for upward
and downward excursions, but the occurrence model is NOT altered and
the source observation is NOT split.

DESIGN PRINCIPLE — deterministic:

Identical input batches produce identical reports. No wall-clock time,
no randomness, no unordered iteration. Source evidence is never mutated.

DESIGN PRINCIPLE — no leakage:

The statistical layer consumes an already-aggregated
:class:`SetupEvidenceBatch`. It never re-reads candles, never re-evaluates
outcomes, and never feeds information backward into discovery. The
point-in-time separation is preserved: discovery uses only information
at T, outcome metrics use future data after T, statistical aggregation
only summarizes already-created observations.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median as _stat_median

from engine.models.historical_setup_evidence import SetupEvidenceBatch
from engine.models.historical_setup_outcome import ObservationStatus


def _mean(values: list[float]) -> float | None:
    """Return the arithmetic mean, or ``None`` when ``values`` is empty."""
    if not values:
        return None
    return sum(values) / len(values)


def _minimum(values: list[float]) -> float | None:
    """Return the minimum, or ``None`` when ``values`` is empty."""
    if not values:
        return None
    return min(values)


def _maximum(values: list[float]) -> float | None:
    """Return the maximum, or ``None`` when ``values`` is empty."""
    if not values:
        return None
    return max(values)


def _median(values: list[float]) -> float | None:
    """Return the median, or ``None`` when ``values`` is empty."""
    if not values:
        return None
    return _stat_median(values)


@dataclass(frozen=True, slots=True)
class SetupEvidenceStatisticsReport:
    """
    Minimal immutable statistical evidence report (Checkpoint 9.10).

    Consumes a :class:`SetupEvidenceBatch` (Checkpoint 9.9) and reports
    neutral descriptive statistics computed ONLY from occurrences with
    sufficient data for each relevant metric. Statistics that cannot be
    computed (no sufficient observations) are ``None``.

    The paired excursion values remain associated with their originating
    occurrence; separate aggregate distributions are computed for upward
    and downward excursions without altering the occurrence model.

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
        forward_return_observation_count
            Number of occurrences that contributed to the forward-return
            statistics (those with a sufficient forward-return
            observation).
        upward_excursion_mean
            Mean upward excursion, or ``None`` when no occurrence has a
            sufficient price-excursion observation.
        upward_excursion_median
            Median upward excursion, or ``None`` when no occurrence has a
            sufficient price-excursion observation.
        upward_excursion_observation_count
            Number of occurrences that contributed to the upward-excursion
            statistics.
        downward_excursion_mean
            Mean downward excursion, or ``None`` when no occurrence has a
            sufficient price-excursion observation.
        downward_excursion_median
            Median downward excursion, or ``None`` when no occurrence has
            a sufficient price-excursion observation.
        downward_excursion_observation_count
            Number of occurrences that contributed to the
            downward-excursion statistics.
    """

    batch_id: str
    criterion_key: str
    total_occurrence_count: int
    sufficient_data_count: int
    insufficient_data_count: int
    forward_return_mean: float | None
    forward_return_median: float | None
    forward_return_minimum: float | None
    forward_return_maximum: float | None
    forward_return_observation_count: int
    upward_excursion_mean: float | None
    upward_excursion_median: float | None
    upward_excursion_observation_count: int
    downward_excursion_mean: float | None
    downward_excursion_median: float | None
    downward_excursion_observation_count: int

    @property
    def is_empty(self) -> bool:
        """Whether the source batch contains no occurrences."""
        return self.total_occurrence_count == 0

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


def compute_statistics(
    batch: SetupEvidenceBatch,
) -> SetupEvidenceStatisticsReport:
    """
    Compute neutral descriptive statistics for an evidence batch.

    Statistics are calculated ONLY from occurrences with sufficient data
    for the relevant metric:

    * Forward-return statistics use occurrences whose
      :class:`ForwardReturnObservation` carries
      :attr:`ObservationStatus.AVAILABLE`.
    * Excursion statistics use occurrences whose
      :class:`PriceExcursionObservation` carries
      :attr:`ObservationStatus.AVAILABLE`.

    Missing or insufficient observations are excluded from the relevant
    statistic — never silently converted to zero. A real observed zero is
    preserved as a valid observation. The source batch and its occurrences
    are never mutated.

    Args:
        batch: The :class:`SetupEvidenceBatch` to analyze (Checkpoint 9.9).

    Returns:
        An immutable :class:`SetupEvidenceStatisticsReport`.
    """
    total = batch.total_occurrences
    sufficient = batch.sufficient_data_count
    insufficient = batch.insufficient_data_count

    forward_return_values: list[float] = []
    upward_excursion_values: list[float] = []
    downward_excursion_values: list[float] = []

    for occ in batch.occurrences:
        if (
            occ.forward_return is not None
            and occ.forward_return.observation_status
            is ObservationStatus.AVAILABLE
        ):
            forward_return_values.append(occ.forward_return.forward_return)

        if (
            occ.price_excursion is not None
            and occ.price_excursion.observation_status
            is ObservationStatus.AVAILABLE
        ):
            upward_excursion_values.append(
                occ.price_excursion.max_upward_excursion
            )
            downward_excursion_values.append(
                occ.price_excursion.max_downward_excursion
            )

    forward_return_observation_count = len(forward_return_values)
    upward_excursion_observation_count = len(upward_excursion_values)
    downward_excursion_observation_count = len(downward_excursion_values)

    return SetupEvidenceStatisticsReport(
        batch_id=batch.batch_id,
        criterion_key=batch.criterion_key,
        total_occurrence_count=total,
        sufficient_data_count=sufficient,
        insufficient_data_count=insufficient,
        forward_return_mean=_mean(forward_return_values),
        forward_return_median=_median(forward_return_values),
        forward_return_minimum=_minimum(forward_return_values),
        forward_return_maximum=_maximum(forward_return_values),
        forward_return_observation_count=forward_return_observation_count,
        upward_excursion_mean=_mean(upward_excursion_values),
        upward_excursion_median=_median(upward_excursion_values),
        upward_excursion_observation_count=upward_excursion_observation_count,
        downward_excursion_mean=_mean(downward_excursion_values),
        downward_excursion_median=_median(downward_excursion_values),
        downward_excursion_observation_count=downward_excursion_observation_count,
    )


__all__ = [
    "SetupEvidenceStatisticsReport",
    "compute_statistics",
]
