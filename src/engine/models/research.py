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

    # Sprint 11I: regime robustness metadata. Defaults preserve
    # backward compatibility. ``sufficient_observations`` is
    # False for regimes with fewer than the configured minimum
    # completed trades; it is independent of ``completed_trades``
    # so a regime with zero trades is never reported as "zero
    # performance" -- it is reported as insufficient.
    sufficient_observations: bool = False
    min_observations_for_inference: int = 0

    @property
    def has_completed_trades(self) -> bool:
        return self.completed_trades > 0

    @property
    def has_no_completed_trades(self) -> bool:
        """
        A regime with no completed trades. This is distinct from
        a regime whose observed expectancy happens to be zero:
        zero trades means no evidence, not zero performance.
        """

        return self.completed_trades == 0

    @property
    def is_profitable(self) -> bool:
        """
        A regime is profitable only when it has completed trades
        AND a positive total R. Zero-trade regimes are NOT
        profitable (nor unprofitable) -- they are unobserved.
        """

        return self.completed_trades > 0 and self.total_r > 0.0

    @property
    def is_unprofitable(self) -> bool:
        return self.completed_trades > 0 and self.total_r < 0.0


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

    # Sprint 11I: explicit development / evaluation window
    # metadata so the leakage audit can verify separation.
    # ``None`` means the windows were not declared; the audit
    # then reports the separation as NOT VERIFIED rather than
    # PASS. ``parameter_selection_isolated`` is True only when
    # the caller can prove parameter selection touched only the
    # development window.
    #
    # Consistency note: the legacy ``OutOfSampleEngine`` performs
    # NO parameter selection, so it leaves
    # ``parameter_selection_isolated`` as ``None`` (NOT VERIFIED
    # by the legacy OOS path). When a Sprint 11I walk-forward
    # selection supplies a structural proof, the orchestrator
    # (``ResearchEngine``) upgrades ``parameter_selection_isolated``
    # to ``True`` AND records the proof source in
    # ``selection_isolation_verified_by`` so the report never
    # silently claims verification it did not perform itself.
    development_window: tuple[int, int] | None = None
    evaluation_window: tuple[int, int] | None = None
    parameter_selection_isolated: bool | None = None
    selection_isolation_verified_by: str | None = None

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


class LeakageSeverity(Enum):
    """
    Severity of a single leakage audit check.

    PASS
        The invariant was evaluated and held.

    WARNING
        The invariant held but a non-critical concern was
        surfaced (e.g. limited data made the check weak).

    NOT_VERIFIED
        The audit could not verify the invariant. The audit
        NEVER reports PASS for a property it could not prove;
        it reports NOT_VERIFIED instead.

    FAILURE
        The invariant was evaluated and violated.
    """

    PASS = "PASS"
    WARNING = "WARNING"
    NOT_VERIFIED = "NOT_VERIFIED"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class LeakageCheck:
    """
    A single structured leakage-audit check.

    Each check has an explainable ``name``, a ``severity``, a
    ``reason`` and a ``passed`` flag. ``passed`` is True only
    when the check was evaluated and held (severity PASS or
    WARNING). NOT_VERIFIED and FAILURE checks are not ``passed``.

    The structured form lets downstream tooling present the
    audit deterministically without parsing free-text strings.
    """

    name: str
    severity: LeakageSeverity
    reason: str
    passed: bool

    @property
    def is_failure(self) -> bool:
        return self.severity is LeakageSeverity.FAILURE

    @property
    def is_not_verified(self) -> bool:
        return self.severity is LeakageSeverity.NOT_VERIFIED


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
        True only when ``failures`` is empty. NOT_VERIFIED
        items do not fail the audit, but they are surfaced so
        no property is falsely reported as safe.

    checks_performed
        Number of invariants that were actually evaluated. Checks
        that could not be performed (because the required
        context was not supplied) are counted in
        ``not_verified`` instead.

    failures
        Human-readable descriptions of failed checks
        (severity FAILURE).

    warnings
        Human-readable warnings for checks that could not be
        fully verified or that surfaced a non-critical concern.

    not_verified
        Human-readable descriptions of properties the audit
        could not verify. These are never silently promoted to
        PASS.

    checks
        Structured per-check results. Backward-compatible:
        older callers can continue to use ``failures`` /
        ``warnings`` / ``passed``.
    """

    passed: bool
    checks_performed: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    not_verified: tuple[str, ...] = field(default_factory=tuple)
    checks: tuple[LeakageCheck, ...] = field(default_factory=tuple)

    @property
    def has_failures(self) -> bool:
        return bool(self.failures)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    @property
    def has_not_verified(self) -> bool:
        return bool(self.not_verified)


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

    parameter_robustness
        Robustness analysis distinguishing the descriptive best
        configuration from stable / robust configurations
        (Sprint 11I).

    walk_forward_selection
        Walk-forward parameter selection result that explicitly
        separates candidate evaluation (development data only),
        selected configuration, and out-of-sample evaluation of
        the selected configuration (Sprint 11I).

    out_of_sample
        In-sample vs out-of-sample comparison.

    data_sufficiency
        Sample-size awareness across trades, regimes, OOS and
        parameter observations (Sprint 11I).

    leakage
        Data-leakage audit result.

    conclusions
        Descriptive, non-predictive conclusions. The engine
        never claims the strategy "is profitable"; it reports
        what was observed. Descriptive findings are labelled
        separately from validated findings.
    """

    label: str

    overall_performance: Any = None

    regime_statistics: tuple[RegimeStatistics, ...] = field(
        default_factory=tuple,
    )

    segmentation: SegmentedPerformance | None = None

    parameter_sensitivity: ParameterSensitivityReport | None = None

    parameter_robustness: "ParameterRobustnessReport | None" = None

    walk_forward_selection: "WalkForwardSelectionReport | None" = None

    out_of_sample: OutOfSampleReport | None = None

    data_sufficiency: "DataSufficiencyReport | None" = None

    leakage: LeakageCheckResult | None = None

    conclusions: tuple[str, ...] = field(default_factory=tuple)

    result: Any = None

    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_regime_data(self) -> bool:
        return any(
            rs.total_results > 0 for rs in self.regime_statistics
        )


# ============================================================
# PARAMETER ROBUSTNESS (Sprint 11I)
# ============================================================


@dataclass(frozen=True)
class ConfigurationRobustness:
    """
    Robustness assessment for a single parameter configuration.

    Distinguishes DESCRIPTIVE BEST from ROBUST / STABLE:

    profitable
        The configuration produced positive total R AND at
        least ``min_completed_trades`` completed trades. A
        configuration with no trades is never "profitable".

    near_median
        The configuration's expectancy lies within the
        robustness band of the median expectancy. The band is
        configurable and intentionally transparent.

    stable
        ``profitable AND near_median``. A stable configuration
        is one that is both economically positive and not an
        outlier relative to the rest of the sweep.

    The descriptive best (highest expectancy) is NOT
    automatically stable; that distinction is captured by
    ``ParameterRobustnessReport.descriptive_best_is_robust``.
    """

    parameter_value: Any
    expectancy: float
    total_r: float
    completed_trades: int
    profitable: bool
    near_median: bool
    stable: bool


@dataclass(frozen=True)
class ParameterRobustnessReport:
    """
    Robustness analysis across a swept parameter (Sprint 11I).

    This report is deliberately distinct from
    ``ParameterSensitivityReport``: sensitivity describes how
    much performance varies; robustness describes which
    configurations are reliable enough to trust, and whether the
    result is overly dependent on a single configuration.

    Field semantics:

    descriptive_best
        The parameter value with the highest historical
        expectancy. DESCRIPTIVE ONLY -- never predictive.

    descriptive_best_is_robust
        Whether the descriptive best is also a stable
        configuration. When False, the best historical result
        is an outlier and must not be treated as a robust choice.

    robust_configurations
        Parameter values that are stable (profitable AND
        near the median). These are the only configurations
        the report is willing to call "robust".

    unstable_configurations
        Parameter values that are NOT stable.

    highly_dependent_on_single_config
        True when the entire sweep's positive result rests on a
        single configuration (e.g. only one profitable config,
        or only one stable config in a multi-config sweep).

    robust
        Whether at least one robust configuration exists AND
        there is sufficient data to make the statement.
    """

    parameter_name: str
    configurations: tuple[ConfigurationRobustness, ...] = field(
        default_factory=tuple,
    )

    descriptive_best: Any = None
    descriptive_best_is_robust: bool = False

    robust_configurations: tuple[Any, ...] = field(default_factory=tuple)
    unstable_configurations: tuple[Any, ...] = field(default_factory=tuple)

    median_expectancy: float = 0.0
    expectancy_band: float = 0.0

    highly_dependent_on_single_config: bool = False
    sufficient_data: bool = False
    robust: bool = False

    @property
    def configuration_count(self) -> int:
        return len(self.configurations)

    @property
    def is_empty(self) -> bool:
        return not self.configurations


# ============================================================
# WALK-FORWARD PARAMETER SELECTION (Sprint 11I)
# ============================================================


@dataclass(frozen=True)
class CandidateResult:
    """
    Development-window performance for one candidate parameter
    configuration.

    The performance is computed EXCLUSIVELY from the development
    window; the evaluation (out-of-sample) window is never
    supplied during candidate evaluation.
    """

    parameter_value: Any
    development_performance: Any
    development_completed_trades: int
    development_expectancy: float
    development_total_r: float
    sufficient_development_trades: bool


@dataclass(frozen=True)
class SelectedConfiguration:
    """
    The configuration selected for out-of-sample evaluation.

    Selection is performed using DEVELOPMENT data only. The
    ``selection_basis`` documents the (descriptive) criterion
    used; the report never claims the selected configuration is
    predictive.
    """

    parameter_value: Any
    selection_basis: str
    development_expectancy: float
    selected_from_development_data: bool
    selected_index: int


@dataclass(frozen=True)
class WalkForwardSelectionReport:
    """
    Full walk-forward parameter selection + evaluation result.

    This model makes the development / evaluation separation
    EXPLICIT and auditable:

    candidates
        Per-candidate development-window results. The evaluation
        window was never seen during candidate evaluation.

    selected
        The selected configuration, chosen from development data
        only.

    out_of_sample_result
        The pipeline result of evaluating the SELECTED
        configuration on the evaluation window only.

    development_window / evaluation_window
        Half-open index ranges ``[start, end)`` of the two
        non-overlapping windows.

    selection_isolated_from_evaluation
        True by construction: the engine never passes evaluation
        candles to candidate evaluation. This is a structural
        guarantee, not a statistical one.

    selection_verified
        Whether the audit can PROVE the selection was isolated.
        True when the engine performed the selection itself
        (structural proof); may be False/NOT_VERIFIED when
        selection was supplied externally.
    """

    parameter_name: str
    candidates: tuple[CandidateResult, ...] = field(
        default_factory=tuple,
    )
    selected: SelectedConfiguration | None = None

    out_of_sample_result: Any = None
    out_of_sample_performance: Any = None
    out_of_sample_completed_trades: int = 0

    development_window: tuple[int, int] = (0, 0)
    evaluation_window: tuple[int, int] = (0, 0)

    development_candle_count: int = 0
    evaluation_candle_count: int = 0

    selection_isolated_from_evaluation: bool = True
    selection_verified: bool = True

    # Sprint 11I consistency: these fields describe WINDOW SIZE
    # sufficiency (enough candles in the development / evaluation
    # window), NOT completed-trade sufficiency. The names are
    # explicit to avoid confusion with DataSufficiencyReport's
    # trade-count thresholds (``insufficient_oos_trades`` etc.).
    sufficient_development_window: bool = False
    sufficient_evaluation_window: bool = False

    # Backward-compatible aliases (Sprint 11I rename). The legacy
    # ``sufficient_development_data`` / ``sufficient_evaluation_data``
    # names were ambiguous about whether "data" meant candles or
    # trades; they now proxy to the explicit window-sufficiency
    # fields. Read-only.
    @property
    def sufficient_development_data(self) -> bool:
        return self.sufficient_development_window

    @property
    def sufficient_evaluation_data(self) -> bool:
        return self.sufficient_evaluation_window

    @property
    def has_selected(self) -> bool:
        return self.selected is not None

    @property
    def windows_overlap(self) -> bool:
        """Whether the declared windows overlap (should be False)."""

        dev_start, dev_end = self.development_window
        eval_start, _ = self.evaluation_window
        return dev_end > eval_start and dev_end > dev_start

    @property
    def has_out_of_sample_trades(self) -> bool:
        return self.out_of_sample_completed_trades > 0


# ============================================================
# DATA SUFFICIENCY (Sprint 11I)
# ============================================================


@dataclass(frozen=True)
class DataSufficiencyReport:
    """
    Sample-size awareness for a research run (Sprint 11I).

    The system must never imply statistical confidence from tiny
    samples. This report makes the sufficiency of each evidence
    source explicit and configurable. All thresholds are stored
    on the report so callers can audit the decision boundaries.

    Field semantics:

    sufficient_trades
        Whether the overall run had at least
        ``min_trades_for_inference`` completed trades.

    insufficient_regime_samples
        Whether ANY observed regime had fewer than
        ``min_regime_observations`` completed trades (when that
        regime had any results at all). Zero-trade regimes are
        reported as insufficient, not as zero-performance.

    insufficient_oos_trades
        Whether the out-of-sample window had fewer than
        ``min_oos_trades`` completed trades (when an OOS
        evaluation was performed).

    insufficient_parameter_observations
        Whether the parameter sweep had fewer than
        ``min_parameter_configurations`` configurations.

    sufficient_for_inference
        Overall: True only when ``sufficient_trades`` is True AND
        no insufficiency flag is set. This is the single flag a
        cautious caller should gate on before drawing any
        conclusion.
    """

    completed_trades: int
    min_trades_for_inference: int

    sufficient_trades: bool
    insufficient_trades: bool

    insufficient_regime_samples: bool
    insufficient_oos_trades: bool
    insufficient_parameter_observations: bool

    min_regime_observations: int
    min_oos_trades: int
    min_parameter_configurations: int

    oos_completed_trades: int
    parameter_configurations: int
    regimes_with_trades: int
    regimes_sufficient: int

    summary: str

    @property
    def sufficient_for_inference(self) -> bool:
        return (
            self.sufficient_trades
            and not self.insufficient_regime_samples
            and not self.insufficient_oos_trades
            and not self.insufficient_parameter_observations
        )
