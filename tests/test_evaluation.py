"""
Tests for the evaluation reporting layer (Sprint 11G).

These tests verify the structured, immutable reporting layer
that sits above the historical evaluation pipeline:

* pipeline-level funnel statistics (counts, suppression,
  validation completion, derived rates)
* signal-level statistics (directional split, eligibility,
  suppression, invalid / no-signal counts, averages)
* trade-level statistics (delegated from PerformanceAnalytics
  without recomputation)
* the top-level EvaluationReport (label, metadata, raw result
  reference, derived helpers)
* immutability (frozen+slots)
* determinism
* empty / no-signal scenarios
* a real end-to-end run against the trending dataset
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.evaluation import (
    EvaluationReport,
    PipelineStatistics,
    SignalStatistics,
    TradeStatistics,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.performance import PerformanceAnalytics
from engine.models.signal import SignalState
from engine.models.validation import (
    ExitReason,
    ValidationResult,
    ValidationStatus,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    flat_dataset,
    minimal_dataset,
    trending_dataset,
)
from engine.reporting import EvaluationReportEngine


# ============================================================
# HELPERS
# ============================================================

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def candle(close: float, spread: float, index: int) -> OHLCVCandle:
    low = round(close - spread, 2)
    high = round(close + spread, 2)
    open_ = round((low + high) / 2, 2)
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def make_validation(
    status=ValidationStatus.WIN,
    *,
    exit_reason=ExitReason.TAKE_PROFIT,
    entry_triggered=True,
    realized_r=None,
    mfe_r=0.0,
    mae_r=0.0,
    duration_candles=3,
    candles_evaluated=10,
) -> ValidationResult:
    return ValidationResult(
        status=status,
        exit_reason=exit_reason,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        entry_triggered=entry_triggered,
        exit_price=None,
        candles_evaluated=candles_evaluated,
        duration_candles=duration_candles,
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        validation_timestamp=None,
        reason="",
        details=(),
    )


def report_for(result, **kwargs):
    return EvaluationReportEngine().analyze(result, **kwargs)


# ============================================================
# EMPTY / MINIMAL INPUT
# ============================================================


def test_empty_pipeline_result_yields_zeroed_report():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate([])

    report = report_for(result, label="empty")

    assert report.label == "empty"
    assert report.pipeline.candles_processed == 0
    assert report.pipeline.evaluation_points == 0
    assert report.pipeline.signals_generated == 0
    assert report.pipeline.signals_suppressed == 0
    assert report.pipeline.validations_completed == 0
    assert report.pipeline.completed_trades == 0

    assert report.signals.total_signals == 0
    assert report.signals.long_signals == 0
    assert report.signals.short_signals == 0
    assert report.signals.no_signal_points == 0

    assert report.trades.completed_trades == 0
    assert report.trades.wins == 0
    assert report.trades.losses == 0
    assert report.has_completed_trades is False


def test_minimal_history_report_has_no_evaluation_points():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(minimal_dataset())

    report = report_for(result)

    assert report.pipeline.candles_processed == len(minimal_dataset())
    assert report.pipeline.evaluation_points == 0
    assert report.pipeline.signal_generation_rate == 0.0
    assert report.pipeline.suppression_rate == 0.0
    assert report.pipeline.validation_completion_rate == 0.0


# ============================================================
# PIPELINE-LEVEL STATISTICS
# ============================================================


def test_pipeline_statistics_match_result_counts():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    assert report.pipeline.candles_processed == result.candles_processed
    assert report.pipeline.evaluation_points == result.evaluation_points
    assert (
        report.pipeline.decisions_generated
        == result.decisions_generated
    )
    assert (
        report.pipeline.eligible_decisions
        == result.eligible_decisions
    )
    assert report.pipeline.signals_generated == result.signals_generated
    assert report.pipeline.signals_validated == result.signals_validated
    assert report.pipeline.completed_trades == result.completed_trades


def test_suppressed_count_derived_from_points():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    expected = sum(
        1 for p in result.evaluation_points_sequence if p.suppressed
    )
    assert report.pipeline.signals_suppressed == expected


def test_validations_completed_excludes_open():
    """
    validations_completed counts only terminal statuses (WIN,
    LOSS, EXPIRED, AMBIGUOUS, NOT_TRIGGERED). OPEN is excluded.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    terminal = {
        ValidationStatus.WIN,
        ValidationStatus.LOSS,
        ValidationStatus.EXPIRED,
        ValidationStatus.AMBIGUOUS,
        ValidationStatus.NOT_TRIGGERED,
    }

    expected = sum(
        1
        for p in result.evaluation_points_sequence
        if p.validation is not None and p.validation.status in terminal
    )

    assert report.pipeline.validations_completed == expected
    assert report.pipeline.validations_completed <= report.pipeline.signals_validated


def test_signal_generation_rate():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    expected = (
        result.signals_generated / result.evaluation_points * 100.0
    )
    assert report.pipeline.signal_generation_rate == pytest.approx(expected)
    assert 0.0 < report.pipeline.signal_generation_rate <= 100.0


def test_suppression_rate_uses_eligible_denominator():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    suppressed = report.pipeline.signals_suppressed
    eligible = report.pipeline.eligible_decisions

    expected = (suppressed / eligible * 100.0) if eligible else 0.0
    assert report.pipeline.suppression_rate == pytest.approx(expected)


def test_validation_completion_rate_uses_validated_denominator():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    expected = (
        report.pipeline.validations_completed
        / report.pipeline.signals_validated
        * 100.0
    )
    assert report.pipeline.validation_completion_rate == pytest.approx(expected)


# ============================================================
# SIGNAL-LEVEL STATISTICS
# ============================================================


def test_signal_directional_split_sums_to_total():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    assert report.signals.total_signals == result.signals_generated
    assert (
        report.signals.long_signals + report.signals.short_signals
        == report.signals.total_signals
    )


def test_signal_long_share_and_balance():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    if report.signals.total_signals > 0:
        expected = (
            report.signals.long_signals
            / report.signals.total_signals
            * 100.0
        )
        assert report.signals.long_share == pytest.approx(expected)

    assert report.signals.directional_balance == (
        report.signals.long_signals - report.signals.short_signals
    )


def test_no_signal_and_invalid_counts_consistent_with_points():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    no_signal = 0
    invalid = 0
    for p in result.evaluation_points_sequence:
        if p.signal is None or p.signal.state == SignalState.NO_SIGNAL:
            no_signal += 1
        elif p.signal.state == SignalState.INVALID:
            invalid += 1

    assert report.signals.no_signal_points == no_signal
    assert report.signals.invalid_signals == invalid


def test_signal_averages_zero_when_no_signals():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(flat_dataset())

    report = report_for(result)

    assert report.signals.total_signals == 0
    assert report.signals.average_confidence == 0.0
    assert report.signals.average_risk_reward == 0.0


def test_signal_averages_computed_from_generated_signals():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    if not result.signals:
        pytest.skip("trending dataset produced no signals")

    expected_conf = sum(
        float(s.confidence) for s in result.signals
    ) / len(result.signals)
    expected_rr = sum(
        float(s.risk_reward_ratio) for s in result.signals
    ) / len(result.signals)

    assert report.signals.average_confidence == pytest.approx(expected_conf)
    assert report.signals.average_risk_reward == pytest.approx(expected_rr)


# ============================================================
# TRADE-LEVEL STATISTICS (DELEGATED)
# ============================================================


def test_trade_statistics_delegate_to_performance():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    perf = result.performance
    assert perf is not None

    assert report.trades.total_results == perf.total_results
    assert report.trades.completed_trades == perf.completed_trades
    assert report.trades.wins == perf.wins
    assert report.trades.losses == perf.losses
    assert report.trades.ambiguous == perf.ambiguous
    assert report.trades.expired == perf.expired
    assert report.trades.not_triggered == perf.not_triggered
    assert report.trades.open == perf.open
    assert report.trades.win_rate == perf.win_rate
    assert report.trades.total_r == perf.total_r
    assert report.trades.average_r == perf.average_r
    assert report.trades.expectancy == perf.expectancy
    assert report.trades.profit_factor == perf.profit_factor
    assert report.trades.average_mfe_r == perf.average_mfe_r
    assert report.trades.average_mae_r == perf.average_mae_r
    assert report.trades.max_drawdown_r == perf.max_drawdown_r
    assert (
        report.trades.maximum_winning_streak
        == perf.maximum_winning_streak
    )
    assert (
        report.trades.maximum_losing_streak
        == perf.maximum_losing_streak
    )
    assert report.trades.performance is perf


def test_trade_statistics_answer_all_required_questions():
    """
    The trade view must expose every metric required by the
    Sprint 11G objectives.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)
    t = report.trades

    # Counts.
    assert isinstance(t.wins, int)
    assert isinstance(t.losses, int)
    assert isinstance(t.expired, int)
    assert isinstance(t.ambiguous, int)
    assert isinstance(t.not_triggered, int)

    # Ratios / R.
    assert isinstance(t.win_rate, float)
    assert isinstance(t.total_r, float)
    assert isinstance(t.average_r, float)
    assert isinstance(t.expectancy, float)
    assert isinstance(t.profit_factor, float)

    # Excursion.
    assert isinstance(t.average_mfe_r, float)
    assert isinstance(t.average_mae_r, float)

    # Drawdown / streaks.
    assert isinstance(t.max_drawdown_r, float)
    assert isinstance(t.maximum_winning_streak, int)
    assert isinstance(t.maximum_losing_streak, int)


def test_trade_profit_factor_display_inf():
    """
    A profit factor with no losses is infinite; display must
    render as INF.
    """

    perf = PerformanceAnalyticsEngine().analyze(
        [
            make_validation(
                ValidationStatus.WIN,
                realized_r=2.0,
            ),
        ]
    )

    trade = TradeStatistics.from_performance(perf)

    assert trade.profit_factor_display == "INF"


def test_trade_profit_factor_display_finite():
    perf = PerformanceAnalyticsEngine().analyze(
        [
            make_validation(
                ValidationStatus.WIN,
                realized_r=3.0,
            ),
            make_validation(
                ValidationStatus.LOSS,
                exit_reason=ExitReason.STOP_LOSS,
                realized_r=-1.0,
            ),
        ]
    )

    trade = TradeStatistics.from_performance(perf)

    assert trade.profit_factor_display == "3.00"


def test_trade_statistics_from_none_performance():
    trade = TradeStatistics.from_performance(None)

    assert trade.completed_trades == 0
    assert trade.wins == 0
    assert trade.total_r == 0.0
    assert trade.expectancy == 0.0
    assert trade.performance is None
    assert trade.has_completed_trades is False
    assert trade.is_profitable is False


def test_trade_is_profitable_flag():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    assert report.trades.is_profitable == (report.trades.total_r > 0.0)
    assert report.is_profitable == report.trades.is_profitable


# ============================================================
# EVALUATION REPORT
# ============================================================


def test_report_retains_raw_result_reference():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result, label="run-1")

    assert report.result is result
    assert report.result.evaluation_points == result.evaluation_points


def test_report_label_and_metadata():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    meta = {"dataset": "trending", "version": "11G"}
    report = report_for(
        result,
        label="trending-run",
        metadata=meta,
    )

    assert report.label == "trending-run"
    assert dict(report.metadata) == meta


def test_report_metadata_defaults_to_empty():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    assert dict(report.metadata) == {}


def test_report_has_signals_helper():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)
    assert report.has_signals == (report.signals.total_signals > 0)


def test_report_flat_dataset_has_no_signals():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(flat_dataset())

    report = report_for(result, label="flat")

    assert report.signals.total_signals == 0
    assert report.has_signals is False
    assert report.trades.completed_trades == 0


# ============================================================
# IMMUTABILITY
# ============================================================


def test_evaluation_report_is_frozen():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result, label="x")

    with pytest.raises(FrozenInstanceError):
        report.label = "y"  # type: ignore[misc]


def test_pipeline_statistics_is_frozen():
    stats = PipelineStatistics(
        candles_processed=10,
        evaluation_points=5,
        decisions_generated=5,
        eligible_decisions=2,
        signals_generated=2,
        signals_suppressed=0,
        signals_validated=2,
        validations_completed=2,
        completed_trades=1,
    )

    with pytest.raises(FrozenInstanceError):
        stats.signals_generated = 99  # type: ignore[misc]


def test_signal_statistics_is_frozen():
    stats = SignalStatistics(
        total_signals=2,
        long_signals=1,
        short_signals=1,
        eligible_signals=2,
        suppressed_signals=0,
        no_signal_points=3,
        invalid_signals=0,
        average_confidence=50.0,
        average_risk_reward=2.0,
    )

    with pytest.raises(FrozenInstanceError):
        stats.long_signals = 9  # type: ignore[misc]


def test_trade_statistics_is_frozen():
    trade = TradeStatistics.from_performance(None)

    with pytest.raises(FrozenInstanceError):
        trade.wins = 5  # type: ignore[misc]


def test_models_use_slots():
    """
    Frozen+slots dataclasses must not allow new attribute
    creation.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result)

    for obj in (
        report,
        report.pipeline,
        report.signals,
        report.trades,
    ):
        with pytest.raises(AttributeError):
            obj.new_attr = 1  # type: ignore[attr-defined]


# ============================================================
# DETERMINISM
# ============================================================


def test_engine_is_deterministic():
    pipeline = HistoricalEvaluationPipeline()
    engine = EvaluationReportEngine()

    result = pipeline.evaluate(trending_dataset())

    r1 = engine.analyze(result, label="a")
    r2 = engine.analyze(result, label="a")

    assert r1 == r2
    assert r1.pipeline == r2.pipeline
    assert r1.signals == r2.signals
    assert r1.trades == r2.trades


def test_different_labels_produce_different_reports():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    engine = EvaluationReportEngine()
    r1 = engine.analyze(result, label="a")
    r2 = engine.analyze(result, label="b")

    assert r1 != r2
    assert r1.pipeline == r2.pipeline


# ============================================================
# END-TO-END INTEGRITY
# ============================================================


def test_full_trending_run_integrity():
    """
    End-to-end sanity: every required Sprint 11G question is
    answerable from the report and consistent with the raw
    pipeline result.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    report = report_for(result, label="integrity")

    # Pipeline-level questions.
    assert report.pipeline.evaluation_points == result.evaluation_points
    assert report.pipeline.signals_generated == result.signals_generated
    assert report.pipeline.signals_suppressed == sum(
        1 for p in result.evaluation_points_sequence if p.suppressed
    )
    assert (
        report.pipeline.eligible_decisions == result.eligible_decisions
    )
    assert report.pipeline.signals_validated == result.signals_validated

    # Signal-level: eligible + suppressed relationship.
    assert (
        report.signals.eligible_signals
        == report.signals.total_signals + report.signals.suppressed_signals
    )

    # Trade-level consistency with performance.
    perf = result.performance
    assert perf is not None
    assert report.trades.wins + report.trades.losses == report.trades.completed_trades
    assert report.trades.completed_trades == perf.completed_trades
    assert report.trades.wins == perf.wins
    assert report.trades.losses == perf.losses


def test_engine_handles_synthetic_result_directly():
    """
    The engine must work on a hand-built PipelineResult, not
    only on pipeline output. This proves it consumes
    PipelineResult / PipelineEvaluationPoint / ValidationResult
    objects rather than reimplementing pipeline logic.
    """

    from engine.models.pipeline import (
        PipelineEvaluationPoint,
        PipelineResult,
    )

    validations = [
        make_validation(
            ValidationStatus.WIN,
            realized_r=2.0,
            mfe_r=2.5,
            mae_r=-0.2,
        ),
        make_validation(
            ValidationStatus.LOSS,
            exit_reason=ExitReason.STOP_LOSS,
            realized_r=-1.0,
            mfe_r=0.4,
            mae_r=-1.0,
        ),
    ]

    perf = PerformanceAnalyticsEngine().analyze(validations)

    result = PipelineResult(
        candles_processed=50,
        evaluation_points=40,
        decisions_generated=40,
        eligible_decisions=3,
        signals_generated=2,
        signals_validated=2,
        completed_trades=2,
        evaluation_points_sequence=(),
        signals=(),
        validation_results=tuple(validations),
        performance=perf,
    )

    report = EvaluationReportEngine().analyze(
        result,
        label="synthetic",
        metadata={"source": "unit-test"},
    )

    assert report.pipeline.signals_generated == 2
    assert report.pipeline.signals_suppressed == 0
    assert report.trades.wins == 1
    assert report.trades.losses == 1
    assert report.trades.total_r == pytest.approx(1.0)
    assert report.trades.expectancy == pytest.approx(0.5)
    assert report.trades.maximum_winning_streak == 1
    assert report.trades.maximum_losing_streak == 1
    assert dict(report.metadata) == {"source": "unit-test"}


def test_engine_counts_open_as_non_terminal_validation():
    """
    A validation that is still OPEN must NOT count toward
    validations_completed.
    """

    from engine.models.pipeline import (
        PipelineEvaluationPoint,
        PipelineResult,
    )
    from engine.models.signal import (
        SignalDirection,
        SignalResult,
    )
    from engine.models.decision import (
        DecisionDirection,
        SetupQuality,
    )
    from engine.models.signal import (
        EntrySource,
        Invalidation,
    )

    open_validation = make_validation(
        ValidationStatus.OPEN,
        exit_reason=ExitReason.NONE,
        entry_triggered=False,
        realized_r=None,
    )
    win_validation = make_validation(
        ValidationStatus.WIN,
        realized_r=2.0,
    )

    perf = PerformanceAnalyticsEngine().analyze(
        [open_validation, win_validation]
    )

    signal = SignalResult(
        direction=SignalDirection.LONG,
        state=SignalState.LONG,
        entry_price=100.0,
        entry_source=EntrySource.TRIGGER_CLOSE,
        stop_loss=98.0,
        take_profit=104.0,
        risk_per_unit=2.0,
        reward_per_unit=4.0,
        risk_reward_ratio=2.0,
        confidence=80.0,
        quality=SetupQuality.MODERATE,
        eligible=True,
        invalidation=Invalidation(price=98.0, condition="below stop"),
        decision_direction=DecisionDirection.BULLISH,
    )

    point_open = PipelineEvaluationPoint(
        index=10,
        timestamp=None,
        decision_direction="BULLISH",
        decision_status="READY",
        signal_state="LONG",
        signal=signal,
        validation=open_validation,
        reason="",
        suppressed=False,
    )
    point_win = PipelineEvaluationPoint(
        index=20,
        timestamp=None,
        decision_direction="BULLISH",
        decision_status="READY",
        signal_state="LONG",
        signal=signal,
        validation=win_validation,
        reason="",
        suppressed=False,
    )

    result = PipelineResult(
        candles_processed=30,
        evaluation_points=2,
        decisions_generated=2,
        eligible_decisions=2,
        signals_generated=2,
        signals_validated=2,
        completed_trades=1,
        evaluation_points_sequence=(point_open, point_win),
        signals=(signal, signal),
        validation_results=(open_validation, win_validation),
        performance=perf,
    )

    report = EvaluationReportEngine().analyze(result, label="open-test")

    # Only the WIN is terminal; OPEN is not.
    assert report.pipeline.validations_completed == 1
    assert report.pipeline.completed_trades == 1
    assert report.trades.open == 1


def test_no_print_in_production_modules():
    """
    Production engine/model modules must not contain print()
    statements.
    """

    import os

    root = os.path.join("src", "engine")
    targets = [
        os.path.join(root, "models", "evaluation.py"),
        os.path.join(root, "reporting", "evaluation.py"),
        os.path.join(root, "reporting", "__init__.py"),
    ]

    for path in targets:
        with open(path) as fh:
            content = fh.read()
        assert "print(" not in content, f"print() found in {path}"
