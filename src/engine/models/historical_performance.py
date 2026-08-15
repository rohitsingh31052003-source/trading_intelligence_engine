"""
Domain models for the historical performance analytics layer
(Sprint 11X).

Sprint 11W answers: "What happened to this opportunity AFTER ``T``?".
Sprint 11X answers: "How did the opportunities historically produced by
the engine perform, in aggregate?".

These models describe AGGREGATE, DESCRIPTIVE historical performance
statistics computed from a collection of Sprint 11W
:class:`~engine.models.historical_outcome.HistoricalOutcome` objects.
The analytics layer is DOWNSTREAM ONLY: it consumes already-computed
historical outcomes and aggregates them. It NEVER re-evaluates
outcomes, NEVER re-runs the pipeline, NEVER uses future information,
NEVER introduces machine learning, predictive models, parameter
optimization, or live trading.

These models are DELIBERATELY DISTINCT from the Sprint 11E
``PerformanceAnalytics`` model (which aggregates signal-validation
results from the single-series pipeline). Sprint 11X aggregates
historical opportunity outcomes from the multi-instrument / multi-
timeframe market scan + outcome evaluation pipeline. The names
(``HistoricalPerformance*``) reflect this distinction.

A :class:`HistoricalPerformanceAnalytics` result is NOT a trading
signal, NOT a prediction, NOT a probability of success, NOT a
profitability guarantee, and NOT a trading recommendation. It is a
DESCRIPTIVE measurement of how previously-evaluated opportunities
historically resolved.

DESIGN PRINCIPLE — no fabricated values:

Unavailable metrics remain ``None``. Counts remain ``0``. The
distinction between "observed zero" and "not available" is preserved:

* Win / loss rates use the resolved target-vs-stop denominator
  ``TARGET_HIT + STOP_HIT``. When this denominator is zero, the rate is
  ``None`` (not ``0.0`` and not ``1.0``).
* R-multiple aggregates (total / average / median / gross positive /
  gross negative / profit factor) are computed ONLY over outcomes with
  a valid ``realized_r``. ``BOTH_TOUCHED`` (ambiguous, ``realized_r``
  is ``None``), ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA`` are excluded.
* Profit factor is ``gross_positive_R / abs(gross_negative_R)``. When
  there is no valid negative R, the profit factor is ``None`` (never a
  fabricated finite number). When there is no valid positive R and no
  valid negative R, it is also ``None``.
* MFE / MAE aggregates are computed ONLY over outcomes where the
  excursion value is available. Unavailable values are NOT converted
  to zero.

DESIGN PRINCIPLE — deterministic:

The same collection of historical outcomes always produces identical
analytics. Group ordering is deterministic (see
:class:`HistoricalPerformanceBreakdown`).

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional float metrics use ``None`` so "unavailable" is never silently
  reported as a real value.
* Counts use ``int`` (``0`` is a legitimate observed-zero count).
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BreakdownDimension(Enum):
    """
    A dimension along which historical performance may be grouped.

    Each dimension corresponds to metadata already present on the
    Sprint 11W :class:`~engine.models.historical_outcome.OutcomeSubject`
    (itself a projection of Sprint 11R/11S/11T/11U fields). No metadata
    is invented; when a value is unavailable it groups under the empty
    sentinel ``""`` (displayed as ``"unavailable"``).
    """

    INSTRUMENT = "INSTRUMENT"
    DIRECTION = "DIRECTION"
    DECISION = "DECISION"
    OPPORTUNITY_STATUS = "OPPORTUNITY_STATUS"
    MTF_ALIGNMENT = "MTF_ALIGNMENT"
    SETUP_TYPE = "SETUP_TYPE"
    OPPORTUNITY_RANK = "OPPORTUNITY_RANK"


@dataclass(frozen=True, slots=True)
class HistoricalPerformanceStatistics:
    """
    Core descriptive performance statistics for a collection of outcomes.

    These statistics are computed identically for the overall result
    and for each breakdown group.

    Counts (``int``, ``0`` is a legitimate observed zero):

    total
        Total number of outcomes evaluated.

    resolved
        Number of determinate terminal outcomes
        (``TARGET_HIT + STOP_HIT + BOTH_TOUCHED + EXPIRED``).

    target_hits
        Count of :attr:`TARGET_HIT
        <engine.models.historical_outcome.OutcomeStatus.TARGET_HIT>`.

    stop_hits
        Count of :attr:`STOP_HIT
        <engine.models.historical_outcome.OutcomeStatus.STOP_HIT>`.

    expired
        Count of :attr:`EXPIRED
        <engine.models.historical_outcome.OutcomeStatus.EXPIRED>`.

    both_touched
        Count of :attr:`BOTH_TOUCHED
        <engine.models.historical_outcome.OutcomeStatus.BOTH_TOUCHED>`
        (ambiguous — excluded from resolved win / loss rate).

    no_geometry
        Count of :attr:`NO_GEOMETRY
        <engine.models.historical_outcome.OutcomeStatus.NO_GEOMETRY>`.

    insufficient_data
        Count of :attr:`INSUFFICIENT_DATA
        <engine.models.historical_outcome.OutcomeStatus.INSUFFICIENT_DATA>`.

    Rates (``float | None``; ``None`` when the denominator is zero):

    win_rate
        ``target_hits / (target_hits + stop_hits)``. ``EXPIRED``,
        ``BOTH_TOUCHED``, ``NO_GEOMETRY`` and ``INSUFFICIENT_DATA`` are
        excluded from the denominator. ``None`` when no target / stop
        outcomes were resolved.

    loss_rate
        ``stop_hits / (target_hits + stop_hits)``. ``None`` when no
        target / stop outcomes were resolved.

    expiration_rate
        ``expired / total``. ``None`` when ``total == 0``.

    ambiguous_rate
        ``both_touched / total``. ``None`` when ``total == 0``.

    R-multiple metrics (``float | None``; computed ONLY over outcomes
    with a valid ``realized_r``):

    total_realized_r
        Sum of valid realized R.

    average_realized_r
        Mean of valid realized R.

    median_realized_r
        Median of valid realized R.

    gross_positive_r
        Sum of valid positive realized R.

    gross_negative_r
        Sum of valid negative realized R (a non-positive number).

    profit_factor
        ``gross_positive_r / abs(gross_negative_r)``. ``None`` when
        there is no valid negative R (never fabricated).

    valid_r_count
        Number of outcomes contributing a valid realized R.

    Excursion metrics (``float | None``; computed ONLY over outcomes
    where the value is available):

    average_mfe
        Mean of available MFE (absolute).

    average_mae
        Mean of available MAE (absolute).

    average_mfe_r
        Mean of available MFE in R-multiples.

    average_mae_r
        Mean of available MAE in R-multiples.
    """

    total: int = 0
    resolved: int = 0
    target_hits: int = 0
    stop_hits: int = 0
    expired: int = 0
    both_touched: int = 0
    no_geometry: int = 0
    insufficient_data: int = 0

    win_rate: float | None = None
    loss_rate: float | None = None
    expiration_rate: float | None = None
    ambiguous_rate: float | None = None

    total_realized_r: float | None = None
    average_realized_r: float | None = None
    median_realized_r: float | None = None
    gross_positive_r: float | None = None
    gross_negative_r: float | None = None
    profit_factor: float | None = None
    valid_r_count: int = 0

    average_mfe: float | None = None
    average_mae: float | None = None
    average_mfe_r: float | None = None
    average_mae_r: float | None = None


@dataclass(frozen=True, slots=True)
class HistoricalPerformanceGroup:
    """
    One grouped performance row: a dimension value paired with the
    core :class:`HistoricalPerformanceStatistics` for the outcomes in
    that group.

    Attributes:

    key
        The grouping key (e.g. instrument name, direction, rank as a
        string). The empty string ``""`` represents "unavailable"
        metadata (never invented).

    statistics
        The :class:`HistoricalPerformanceStatistics` for outcomes in
        this group.
    """

    key: str
    statistics: HistoricalPerformanceStatistics


@dataclass(frozen=True, slots=True)
class HistoricalPerformanceBreakdown:
    """
    The grouped performance analytics for one
    :class:`BreakdownDimension`.

    Groups are ordered DETERMINISTICALLY by the engine (canonical
    order first, then lexicographic for any remaining keys), never by
    unordered iteration.

    Attributes:

    dimension
        The :class:`BreakdownDimension`.

    groups
        Tuple of :class:`HistoricalPerformanceGroup`, deterministically
        ordered.
    """

    dimension: BreakdownDimension
    groups: tuple[HistoricalPerformanceGroup, ...] = field(
        default_factory=tuple,
    )


@dataclass(frozen=True, slots=True)
class HistoricalPerformanceAnalytics:
    """
    The aggregate historical performance analytics result.

    The result is DESCRIPTIVE. It aggregates already-computed
    historical outcomes (Sprint 11W). It never re-evaluates outcomes,
    never re-runs the pipeline, and never uses future information.

    Attributes:

    analytics_id
        Deterministic identifier (``"perf-"`` + sha256[:16] of the
        canonical analytics identity).

    overall
        The :class:`HistoricalPerformanceStatistics` across all
        outcomes.

    breakdowns
        Tuple of :class:`HistoricalPerformanceBreakdown`, one per
        supported :class:`BreakdownDimension`, deterministically
        ordered.

    outcome_count
        Total number of outcomes aggregated.

    label
        Optional descriptive label identifying the analytics run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).

    rationale
        Human-readable, descriptive summary. Descriptive only.
    """

    analytics_id: str
    overall: HistoricalPerformanceStatistics
    breakdowns: tuple[HistoricalPerformanceBreakdown, ...] = field(
        default_factory=tuple,
    )
    outcome_count: int = 0
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether the analytics aggregated no outcomes."""

        return self.outcome_count == 0


__all__ = [
    "BreakdownDimension",
    "HistoricalPerformanceAnalytics",
    "HistoricalPerformanceBreakdown",
    "HistoricalPerformanceGroup",
    "HistoricalPerformanceStatistics",
]
