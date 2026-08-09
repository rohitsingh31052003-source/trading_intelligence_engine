"""
Domain models for the evaluation / reporting layer (Sprint 11G).

This layer sits ABOVE the historical evaluation pipeline
(Sprint 11F). It turns a ``PipelineResult`` into a structured,
reusable, immutable report suitable for comparing historical
runs and eventually supporting strategy research.

The reporting layer deliberately preserves three independent
statistical views:

    PipelineStatistics
        The walk-forward funnel: how many candles / evaluation
        points / decisions / signals / validations were
        processed and how many were suppressed.

    SignalStatistics
        The signal-generation view: directional split, eligible
        vs suppressed, and signal-production rates.

    TradeStatistics
        The completed-trade view: wins / losses / expired /
        ambiguous / not-triggered, win rate, R-multiples,
        expectancy, profit factor, MFE / MAE, drawdown and
        streaks.

Trade statistics are NOT recomputed here. They are delegated to
the existing ``PerformanceAnalytics`` (produced by
``PerformanceAnalyticsEngine`` inside the pipeline), so no
analytics logic is duplicated.

All models are immutable frozen+slots dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isinf
from typing import Any, Mapping

from engine.models.performance import PerformanceAnalytics


# ============================================================
# PIPELINE-LEVEL STATISTICS
# ============================================================


@dataclass(frozen=True)
class PipelineStatistics:
    """
    Immutable snapshot of the walk-forward pipeline funnel.

    These counters describe what the pipeline did with the
    historical candle sequence, independent of trade outcomes.

    Field semantics:

    candles_processed
        Total candles supplied to the pipeline.

    evaluation_points
        Indices actually evaluated (insufficient-history
        indices are skipped and not counted).

    decisions_generated
        Evaluation points that reached the decision engine.

    eligible_decisions
        Decisions whose downstream signal became eligible
        (including any that were later suppressed by the
        one-active-signal policy).

    signals_generated
        Eligible signals actually forwarded to validation
        (i.e. not suppressed).

    signals_suppressed
        Eligible signals suppressed because an active
        validation was already in progress.

    signals_validated
        Signals forwarded to the validation engine.

    validations_completed
        Signals that reached a terminal validation status
        (WIN / LOSS / EXPIRED / AMBIGUOUS / NOT_TRIGGERED).
        OPEN is not terminal because the future window ran out
        of candles before resolution.

    completed_trades
        Signals that resolved to a clean WIN or LOSS.
    """

    candles_processed: int
    evaluation_points: int
    decisions_generated: int
    eligible_decisions: int
    signals_generated: int
    signals_suppressed: int
    signals_validated: int
    validations_completed: int
    completed_trades: int

    # -----------------------------------------------------
    # DERIVED RATES
    # -----------------------------------------------------

    @property
    def signal_generation_rate(self) -> float:
        """
        signals_generated / evaluation_points * 100.

        Measures how often an evaluation point yields a
        validated signal. Returns 0.0 when no evaluation points
        exist.
        """

        if self.evaluation_points <= 0:
            return 0.0

        return (self.signals_generated / self.evaluation_points) * 100.0

    @property
    def suppression_rate(self) -> float:
        """
        signals_suppressed / eligible_decisions * 100.

        Measures how often an eligible signal was blocked by
        the one-active-signal policy. Returns 0.0 when no
        eligible decisions exist.
        """

        if self.eligible_decisions <= 0:
            return 0.0

        return (self.signals_suppressed / self.eligible_decisions) * 100.0

    @property
    def validation_completion_rate(self) -> float:
        """
        validations_completed / signals_validated * 100.

        Measures how many validated signals reached a terminal
        outcome. Returns 0.0 when no signals were validated.
        """

        if self.signals_validated <= 0:
            return 0.0

        return (
            self.validations_completed / self.signals_validated
        ) * 100.0


# ============================================================
# SIGNAL-LEVEL STATISTICS
# ============================================================


@dataclass(frozen=True)
class SignalStatistics:
    """
    Immutable snapshot of signal-generation characteristics.

    Directional counts are computed over the signals that were
    actually generated (forwarded to validation), not over
    suppressed signals.

    Field semantics:

    total_signals
        Number of signals generated (== pipeline
        ``signals_generated``).

    long_signals
        Generated LONG signals.

    short_signals
        Generated SHORT signals.

    eligible_signals
        Generated signals that were eligible.

    suppressed_signals
        Eligible signals suppressed by the one-active-signal
        policy.

    no_signal_points
        Evaluation points that produced no signal at all.

    invalid_signals
        Evaluation points whose signal was rejected as INVALID.

    average_confidence
        Mean confidence of generated signals (0.0 when none).

    average_risk_reward
        Mean risk/reward ratio of generated signals (0.0 when
        none).
    """

    total_signals: int
    long_signals: int
    short_signals: int
    eligible_signals: int
    suppressed_signals: int
    no_signal_points: int
    invalid_signals: int
    average_confidence: float
    average_risk_reward: float

    # -----------------------------------------------------
    # DERIVED RATES
    # -----------------------------------------------------

    @property
    def directional_balance(self) -> float:
        """
        long_signals - short_signals.

        Positive means the run leaned bullish; negative means
        bearish. Zero means perfectly balanced (or no signals).
        """

        return self.long_signals - self.short_signals

    @property
    def long_share(self) -> float:
        """
        long_signals / total_signals * 100.

        Returns 0.0 when no signals were generated.
        """

        if self.total_signals <= 0:
            return 0.0

        return (self.long_signals / self.total_signals) * 100.0


# ============================================================
# TRADE-LEVEL STATISTICS
# ============================================================


@dataclass(frozen=True)
class TradeStatistics:
    """
    Immutable completed-trade statistics view.

    This object is a curated, read-only projection of the
    existing ``PerformanceAnalytics`` produced by the pipeline.
    It does NOT recompute any statistic; it exposes the
    authoritative values produced by
    ``PerformanceAnalyticsEngine``.

    The distinction between ``TradeStatistics`` and
    ``PerformanceAnalytics`` is one of role:

    * ``PerformanceAnalytics`` is the engine output model and
      carries every field the engine computes.

    * ``TradeStatistics`` is the reporting-layer view that
      answers the specific set of completed-trade questions
      required by the evaluation report, and keeps the
      pipeline/signal/trade separation clean.
    """

    # -----------------------------------------------------
    # COUNTS
    # -----------------------------------------------------

    total_results: int
    completed_trades: int
    wins: int
    losses: int
    ambiguous: int
    expired: int
    not_triggered: int
    open: int

    # -----------------------------------------------------
    # R-MULTIPLE PERFORMANCE
    # -----------------------------------------------------

    win_rate: float
    total_r: float
    average_r: float
    expectancy: float
    profit_factor: float

    # -----------------------------------------------------
    # EXCURSION
    # -----------------------------------------------------

    average_mfe_r: float
    average_mae_r: float

    # -----------------------------------------------------
    # RISK / DRAWDOWN / STREAKS
    # -----------------------------------------------------

    max_drawdown_r: float
    maximum_winning_streak: int
    maximum_losing_streak: int

    # -----------------------------------------------------
    # RAW DELEGATED ANALYTICS
    # -----------------------------------------------------

    performance: PerformanceAnalytics | None = None

    # -----------------------------------------------------
    # DERIVED HELPERS
    # -----------------------------------------------------

    @property
    def has_completed_trades(self) -> bool:
        return self.completed_trades > 0

    @property
    def is_profitable(self) -> bool:
        return self.total_r > 0.0

    @property
    def profit_factor_display(self) -> str:
        """
        Human-readable profit factor (``inf`` -> ``INF``).
        """

        if isinf(self.profit_factor):
            return "INF"

        return f"{self.profit_factor:.2f}"

    @classmethod
    def from_performance(
        cls,
        performance: PerformanceAnalytics | None,
    ) -> "TradeStatistics":
        """
        Build a ``TradeStatistics`` view from an existing
        ``PerformanceAnalytics``.

        When ``performance`` is None (the pipeline never
        produces None in practice, but the model allows it),
        a zeroed view is returned so callers never need to
        special-case the absence.
        """

        if performance is None:
            return cls(
                total_results=0,
                completed_trades=0,
                wins=0,
                losses=0,
                ambiguous=0,
                expired=0,
                not_triggered=0,
                open=0,
                win_rate=0.0,
                total_r=0.0,
                average_r=0.0,
                expectancy=0.0,
                profit_factor=0.0,
                average_mfe_r=0.0,
                average_mae_r=0.0,
                max_drawdown_r=0.0,
                maximum_winning_streak=0,
                maximum_losing_streak=0,
                performance=None,
            )

        return cls(
            total_results=performance.total_results,
            completed_trades=performance.completed_trades,
            wins=performance.wins,
            losses=performance.losses,
            ambiguous=performance.ambiguous,
            expired=performance.expired,
            not_triggered=performance.not_triggered,
            open=performance.open,
            win_rate=performance.win_rate,
            total_r=performance.total_r,
            average_r=performance.average_r,
            expectancy=performance.expectancy,
            profit_factor=performance.profit_factor,
            average_mfe_r=performance.average_mfe_r,
            average_mae_r=performance.average_mae_r,
            max_drawdown_r=performance.max_drawdown_r,
            maximum_winning_streak=performance.maximum_winning_streak,
            maximum_losing_streak=performance.maximum_losing_streak,
            performance=performance,
        )


# ============================================================
# EVALUATION REPORT
# ============================================================


@dataclass(frozen=True)
class EvaluationReport:
    """
    Immutable, self-contained evaluation report for a single
    historical pipeline run.

    A report bundles the three statistical views together with
    an identifying label and free-form metadata so that future
    sprints can compare reports across runs, parameters or
    strategies without modifying this model.

    The original ``PipelineResult`` is retained by reference
    (``result``) so downstream research layers can access the
    raw evaluation points, signals and validation results. The
    report never copies or duplicates that data.

    Extensibility:

    * ``label`` identifies the run (strategy name, parameter
      set id, dataset name, ...).
    * ``metadata`` carries arbitrary string key/value context
      (config hashes, parameter snapshots, tags).
    * ``result`` gives future comparison / robustness / Monte
      Carlo layers direct access to raw pipeline output.
    """

    label: str

    pipeline: PipelineStatistics
    signals: SignalStatistics
    trades: TradeStatistics

    result: Any | None = None

    metadata: Mapping[str, str] = field(
        default_factory=dict,
    )

    # -----------------------------------------------------
    # DERIVED HELPERS
    # -----------------------------------------------------

    @property
    def has_signals(self) -> bool:
        return self.signals.total_signals > 0

    @property
    def has_completed_trades(self) -> bool:
        return self.trades.has_completed_trades

    @property
    def is_profitable(self) -> bool:
        return self.trades.is_profitable
