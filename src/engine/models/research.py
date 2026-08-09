"""
Domain models for the research / robustness layer (Sprint 11H).

This layer sits ABOVE the historical evaluation pipeline
(Sprint 11F) and the evaluation reporting layer (Sprint 11G).
It answers research questions about an existing strategy:

* when does the strategy work / fail (regime segmentation)
* is performance stable across market regimes
* is performance sensitive to parameters
* does apparent performance survive out-of-sample evaluation
* does the evaluation pipeline contain look-ahead / leakage

The models are immutable frozen+slots dataclasses consistent
with the rest of the project. No analytics logic lives here;
the research engines delegate computation to the existing
``PerformanceAnalyticsEngine`` wherever practical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# ============================================================
# REGIME
# ============================================================


class MarketRegime(Enum):
    """
    Coarse market regime label used for research segmentation.

    The classification is intentionally simple and
    deterministic; it is NOT a sophisticated statistical regime
    classifier. It exists so performance can be grouped by
    broad market condition.

    TRENDING
        Directional movement is clearly dominant.

    FLAT
        Movement is weak / range-bound with no clear direction.

    HIGH_VOLATILITY
        Realized volatility exceeds a configurable threshold.

    LOW_VOLATILITY
        Realized volatility is below a configurable threshold.

    UNKNOWN
        Insufficient information to classify (e.g. too few
        candles at the evaluation point).
    """

    TRENDING = "TRENDING"
    FLAT = "FLAT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


# ============================================================
# SEGMENTATION DIMENSIONS
# ============================================================


class SegmentationDimension(Enum):
    """
    Dimension along which performance is grouped.

    DIRECTION
        LONG / SHORT (and NONE for non-directional signals).

    SETUP_QUALITY
        The setup quality carried by the signal
        (WEAK / MODERATE / STRONG / EXCELLENT / INVALID).

    CONFIDENCE
        Deterministic confidence buckets
        (LOW / MEDIUM / HIGH / VERY_HIGH).

    RISK_REWARD
        Deterministic risk/reward buckets
        (LOW_RR / MEDIUM_RR / HIGH_RR).

    REGIME
        The market regime at the evaluation point.
    """

    DIRECTION = "DIRECTION"
    SETUP_QUALITY = "SETUP_QUALITY"
    CONFIDENCE = "CONFIDENCE"
    RISK_REWARD = "RISK_REWARD"
    REGIME = "REGIME"


class ConfidenceBucket(Enum):
    """Deterministic confidence bucket."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class RiskRewardBucket(Enum):
    """Deterministic risk/reward bucket."""

    LOW_RR = "LOW_RR"
    MEDIUM_RR = "MEDIUM_RR"
    HIGH_RR = "HIGH_RR"


# ============================================================
# REGIME STATISTICS
# ============================================================


@dataclass(frozen=True)
class RegimeStatistics:
    """
    Performance statistics for a single market regime.

    Mirrors the core completed-trade metrics produced by
    ``PerformanceAnalyticsEngine`` so regime views are directly
    comparable to the overall run. All counts default to zero
    and ratios default to ``0.0`` when no completed trades
    exist for the regime.
    """

    regime: MarketRegime

    total_results: int
    completed_trades: int

    wins: int
    losses: int
    ambiguous: int
    expired: int
    not_triggered: int

    win_rate: float
    total_r: float
    average_r: float
    expectancy: float
    profit_factor: float
    max_drawdown: float

    average_mfe: float
    average_mae: float

    @property
    def has_completed_trades(self) -> bool:
        return self.completed_trades > 0


# ============================================================
# SEGMENTED PERFORMANCE
# ============================================================


@dataclass(frozen=True)
class SegmentStatistics:
    """
    Performance statistics for one segment value of a chosen
    segmentation dimension.

    The ``segment_label`` is the human-readable value of the
    segment (e.g. ``"LONG"``, ``"STRONG"``, ``"TRENDING"``).
    """

    dimension: SegmentationDimension
    segment_label: str

    total_results: int
    completed_trades: int

    wins: int
    losses: int

    win_rate: float
    total_r: float
    average_r: float
    expectancy: float
    profit_factor: float
    max_drawdown: float

    @property
    def has_completed_trades(self) -> bool:
        return self.completed_trades > 0


@dataclass(frozen=True)
class SegmentedPerformance:
    """
    Performance grouped by a chosen dimension.

    Each entry in ``segments`` is a ``SegmentStatistics`` for
    one value of the dimension. ``dimension`` records which
    dimension was used.
    """

    dimension: SegmentationDimension
    segments: tuple[SegmentStatistics, ...] = field(
        default_factory=tuple,
    )

    @property
    def is_empty(self) -> bool:
        return not self.segments


# ============================================================
# PARAMETER SENSITIVITY
# ============================================================


@dataclass(frozen=True)
class ParameterResult:
    """
    Performance of one parameter configuration.

    The value is stored as ``Any`` so the engine is generic
    across numeric, string and tuple parameter values. The
    descriptive ``best_value_label`` is only ever used for
    reporting and is explicitly labelled descriptive (not
    predictive) by the sensitivity report.
    """

    parameter_name: str
    parameter_value: Any

    total_trades: int
    completed_trades: int
    win_rate: float
    expectancy: float
    profit_factor: float
    total_r: float
    max_drawdown: float


@dataclass(frozen=True)
class ParameterSensitivityReport:
    """
    Collection of ``ParameterResult`` values plus stability
    information.

    Stability metrics describe how much performance varies
    across parameter values. They are DESCRIPTIVE: a high
    stability ratio means historical performance was similar
    across configurations, NOT that future performance will be.

    Field semantics:

    parameter_name
        Name of the parameter being swept.

    results
        One ``ParameterResult`` per supplied value, in the
        supplied order.

    best_value_by_expectancy
        The parameter value with the highest historical
        expectancy. Descriptive only.

    median_expectancy
        Median expectancy across all configurations.

    expectancy_range
        max(expectancy) - min(expectancy) across configurations.

    profitable_configurations
        Number of configurations with positive total R.

    stability_ratio
        median_expectancy / (expectancy_range + epsilon).
        Higher means more stable. ``0.0`` when there are fewer
        than two configurations or no variance.

    sufficient_data
        Whether enough configurations exist to make any
        stability statement (>= 2 by default).
    """

    parameter_name: str
    results: tuple[ParameterResult, ...] = field(
        default_factory=tuple,
    )

    best_value_by_expectancy: Any = None
    best_value_descriptive: bool = True

    median_expectancy: float = 0.0
    expectancy_range: float = 0.0
    profitable_configurations: int = 0
    stability_ratio: float = 0.0
    sufficient_data: bool = False

    @property
    def configuration_count(self) -> int:
        return len(self.results)

    @property
    def is_empty(self) -> bool:
        return not self.results


# ============================================================
# OUT-OF-SAMPLE
# ============================================================


@dataclass(frozen=True)
class OutOfSampleReport:
    """
    Development (in-sample) vs evaluation (out-of-sample)
    comparison.

    The split is always chronological. Degradation metrics are
    reported as the out-of-sample value minus the in-sample
    value (a negative number means performance degraded).

    Field semantics:

    split_ratio
        Fraction of data used for development / in-sample.

    in_sample_count / out_of_sample_count
        Number of candles in each split.

    in_sample_performance / out_of_sample_performance
        The ``PerformanceAnalytics`` for each split (may be the
        engine's empty analytics when no trades occurred).

    expectancy_degradation
        oos.expectancy - in_sample.expectancy.

    profit_factor_degradation
        oos.profit_factor - in_sample.profit_factor.

    win_rate_degradation
        oos.win_rate - in_sample.win_rate.

    drawdown_change
        oos.max_drawdown_r - in_sample.max_drawdown_r.

    trade_count_change
        oos.completed_trades - in_sample.completed_trades.

    sufficient_data
        Whether both splits had enough candles / trades to make
        a meaningful comparison.
    """

    split_ratio: float

    in_sample_count: int
    out_of_sample_count: int

    in_sample_results: tuple[Any, ...] = field(default_factory=tuple)
    out_of_sample_results: tuple[Any, ...] = field(default_factory=tuple)

    in_sample_performance: Any = None
    out_of_sample_performance: Any = None

    expectancy_degradation: float = 0.0
    profit_factor_degradation: float = 0.0
    win_rate_degradation: float = 0.0
    drawdown_change: float = 0.0
    trade_count_change: int = 0

    sufficient_data: bool = False

    @property
    def has_in_sample_trades(self) -> bool:
        perf = self.in_sample_performance
        return perf is not None and getattr(
            perf, "completed_trades", 0
        ) > 0

    @property
    def has_out_of_sample_trades(self) -> bool:
        perf = self.out_of_sample_performance
        return perf is not None and getattr(
            perf, "completed_trades", 0
        ) > 0


# ============================================================
# LEAKAGE AUDIT
# ============================================================


@dataclass(frozen=True)
class LeakageCheckResult:
    """
    Result of a data-leakage audit over a walk-forward pipeline
    run.

    The audit verifies a set of explicit, deterministic
    invariants. It does NOT claim a mathematical guarantee of
    zero leakage; it reports exactly what was checked.

    Field semantics:

    passed
        True only when ``failures`` is empty.

    checks_performed
        Number of invariants that were evaluated.

    failures
        Human-readable descriptions of failed checks.

    warnings
        Human-readable warnings for checks that could not be
        fully verified (e.g. insufficient data).
    """

    passed: bool
    checks_performed: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_failures(self) -> bool:
        return bool(self.failures)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


# ============================================================
# RESEARCH REPORT
# ============================================================


@dataclass(frozen=True)
class ResearchReport:
    """
    Top-level immutable research robustness report.

    Bundles the independent research views together with an
    identifying label and metadata. The raw ``PipelineResult``
    is retained by reference for downstream layers.

    Field semantics:

    label / metadata
        Identify the research run.

    overall_performance
        The overall completed-trade ``PerformanceAnalytics``
        (delegated from the pipeline result).

    regime_statistics
        Per-regime performance statistics.

    segmentation
        Performance grouped by a chosen segmentation
        dimension (direction / setup quality / confidence /
        risk-reward / regime).

    parameter_sensitivity
        Stability information across a swept parameter.

    out_of_sample
        In-sample vs out-of-sample comparison.

    leakage
        Data-leakage audit result.

    conclusions
        Descriptive, non-predictive conclusions. The engine
        never claims the strategy "is profitable"; it reports
        what was observed.
    """

    label: str

    overall_performance: Any = None

    regime_statistics: tuple[RegimeStatistics, ...] = field(
        default_factory=tuple,
    )

    segmentation: SegmentedPerformance | None = None

    parameter_sensitivity: ParameterSensitivityReport | None = None

    out_of_sample: OutOfSampleReport | None = None

    leakage: LeakageCheckResult | None = None

    conclusions: tuple[str, ...] = field(default_factory=tuple)

    result: Any = None

    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_regime_data(self) -> bool:
        return any(
            rs.total_results > 0 for rs in self.regime_statistics
        )
