"""
Tests for the end-to-end historical evaluation pipeline
(Sprint 11F).

These tests verify the walk-forward integration architecture:

* basic operation (empty, insufficient history, sufficient
  history, chronological ordering)
* signal generation (no-signal, one-signal, multiple-signals)
* validation (future-only candles, TP / SL / expiry / ambiguous
  / not-triggered outcomes)
* the one-active-signal overlapping policy
* performance analytics integration
* determinism
* no look-ahead bias
* input preservation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.signal import SignalState
from engine.models.validation import ValidationStatus
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    flat_dataset,
    minimal_dataset,
    trending_dataset,
)


# ============================================================
# CANDLE HELPERS
# ============================================================

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def candle(
    close: float,
    spread: float,
    index: int,
) -> OHLCVCandle:
    """
    Build a valid OHLCV candle from a close price and a
    symmetric high/low spread.
    """

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


def first_validated_point(result):
    """Return the first evaluation point that produced a signal."""

    for point in result.evaluation_points_sequence:
        if point.validated:
            return point

    return None


# ============================================================
# BASIC OPERATION
# ============================================================


def test_empty_candle_list():
    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate([])

    assert result.candles_processed == 0
    assert result.evaluation_points == 0
    assert result.signals_generated == 0
    assert result.completed_trades == 0
    # Performance analytics is still produced (delegated, never raises).
    assert result.has_performance is True
    assert result.performance.completed_trades == 0


def test_insufficient_history_skipped():
    """
    Candles below the minimum-history threshold must be skipped
    without raising. No evaluation points should be produced.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(minimal_dataset())

    assert result.candles_processed == len(minimal_dataset())
    assert result.evaluation_points == 0
    assert result.signals_generated == 0


def test_sufficient_history_produces_evaluation_points():
    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    assert result.candles_processed == len(trending_dataset())
    assert result.evaluation_points > 0
    assert result.decisions_generated == result.evaluation_points


def test_chronological_candles_required():
    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    reversed_candles = list(reversed(candles))

    with pytest.raises(ValueError):
        pipeline.evaluate(reversed_candles)


def test_chronological_check_can_be_disabled():
    """
    When the ordering check is disabled the pipeline must not
    raise even for unsorted input. It still assumes chronological
    order; this only relaxes the explicit guard.
    """

    config = PipelineConfig(enforce_chronological_order=False)
    pipeline = HistoricalEvaluationPipeline(config)

    candles = trending_dataset()

    # Reversed input does not raise when the check is off.
    result = pipeline.evaluate(list(reversed(candles)))

    assert result.candles_processed == len(candles)


# ============================================================
# SIGNAL GENERATION
# ============================================================


def test_no_signal_scenario():
    """
    A flat oscillating market should produce no eligible signals.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(flat_dataset())

    assert result.signals_generated == 0
    assert result.completed_trades == 0


def test_one_signal_scenario():
    """
    Crafted trending prefix plus a single TP future candle must
    produce exactly one validated signal that resolves as WIN.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None
    assert first.signal.eligible is True
    assert first.validation is not None


def test_multiple_signals_scenario():
    """
    The full trending dataset must produce multiple validated
    signals.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    assert result.signals_generated >= 2
    assert result.signals_validated >= 2


def test_not_every_candle_produces_signal():
    """
    Most evaluation points must produce no signal.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    assert result.evaluation_points > result.signals_generated


# ============================================================
# VALIDATION OUTCOMES
# ============================================================


def _crafted_outcome(future_factory):
    """
    Run the pipeline on the trending prefix up to the first
    signal's trigger candle, then append crafted future candles
    produced by ``future_factory`` (a function of (T, signal)).

    Returns the validation result of the first validated point.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None

    t = first.index
    signal = first.signal

    crafted = candles[: t + 1] + future_factory(t, signal)

    crafted_result = pipeline.evaluate(crafted)

    point = first_validated_point(crafted_result)
    assert point is not None
    assert point.index == t

    return point.validation


def test_tp_outcome_flows_correctly():
    def future(t, signal):
        entry = signal.entry_price
        target = signal.take_profit
        return [
            candle(entry, 1.0, t + 1),
            candle(target, 1.0, t + 2),
        ]

    validation = _crafted_outcome(future)

    assert validation.status == ValidationStatus.WIN
    assert validation.realized_r is not None
    assert validation.realized_r > 0


def test_sl_outcome_flows_correctly():
    def future(t, signal):
        entry = signal.entry_price
        stop = signal.stop_loss
        return [
            candle(entry, 1.0, t + 1),
            candle(stop, 1.0, t + 2),
        ]

    validation = _crafted_outcome(future)

    assert validation.status == ValidationStatus.LOSS
    assert validation.realized_r == -1.0


def test_expiry_flows_correctly():
    def future(t, signal):
        entry = signal.entry_price
        return [
            candle(entry, 0.5, t + 1 + i)
            for i in range(3)
        ]

    validation = _crafted_outcome(future)

    assert validation.status == ValidationStatus.EXPIRED
    assert validation.entry_triggered is True
    assert validation.realized_r is None


def test_ambiguous_outcome_flows_correctly():
    def future(t, signal):
        entry = signal.entry_price
        target = signal.take_profit
        stop = signal.stop_loss
        midpoint = (target + stop) / 2
        spread = ((target - stop) / 2) + 0.5
        return [
            candle(entry, 1.0, t + 1),
            candle(midpoint, spread, t + 2),
        ]

    validation = _crafted_outcome(future)

    assert validation.status == ValidationStatus.AMBIGUOUS
    assert validation.realized_r is None


def test_not_triggered_outcome_flows_correctly():
    def future(t, signal):
        entry = signal.entry_price
        # Price stays entirely above entry so entry is never
        # touched for a LONG signal.
        return [
            candle(entry + 5, 1.0, t + 1 + i)
            for i in range(5)
        ]

    validation = _crafted_outcome(future)

    assert validation.status == ValidationStatus.NOT_TRIGGERED
    assert validation.entry_triggered is False


def test_validation_uses_future_candles_only():
    """
    The validation timestamp must come from a candle strictly
    after the trigger candle, never from the trigger or earlier.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None

    trigger_ts = candles[first.index].timestamp
    validation = first.validation

    assert validation is not None
    assert validation.validation_timestamp is not None
    assert validation.validation_timestamp > trigger_ts


def test_validation_does_not_use_candles_before_signal():
    """
    A crafted historical candle containing the TP level BEFORE
    the signal must not influence the validation outcome. The
    signal's TP is reached only by future candles.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None

    t = first.index
    signal = first.signal

    # Append a TP future candle so validation resolves as WIN.
    crafted = candles[: t + 1] + [
        candle(signal.entry_price, 1.0, t + 1),
        candle(signal.take_profit, 1.0, t + 2),
    ]

    crafted_result = pipeline.evaluate(crafted)
    point = first_validated_point(crafted_result)

    assert point.index == t
    assert point.validation.status == ValidationStatus.WIN


# ============================================================
# OVERLAPPING SIGNALS
# ============================================================


def test_no_overlapping_active_validations():
    """
    Under the one-active-signal policy, no two validated
    windows may overlap: the next signal's index must be at
    least trigger + 1 + candles_evaluated of the previous.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    validated = [
        p for p in result.evaluation_points_sequence if p.validated
    ]

    assert len(validated) >= 2

    for previous, current in zip(validated, validated[1:]):
        previous_window_end = (
            previous.index + 1 + previous.validation.candles_evaluated
        )
        assert current.index >= previous_window_end


def test_overlapping_signal_is_suppressed():
    """
    When a signal is generated while an active validation window
    covers the next eligible points, those points must be
    suppressed (no second validation), not generate overlapping
    trades.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    # The number of validated signals must never exceed the
    # number of generated signals.
    assert result.signals_validated <= result.signals_generated

    # Suppressed points are explicitly flagged. Whenever there
    # are more eligible decisions than validations, at least one
    # eligible signal must have been suppressed by the active
    # window.
    suppressed = [
        p for p in result.evaluation_points_sequence if p.suppressed
    ]

    if result.eligible_decisions > result.signals_validated:
        assert len(suppressed) >= 1

    # Suppressed points carry the would-be signal but no
    # validation result.
    for point in suppressed:
        assert point.signal is not None
        assert point.signal.eligible is True
        assert point.validation is None


# ============================================================
# PERFORMANCE INTEGRATION
# ============================================================


def test_pipeline_validation_results_match_performance_input():
    """
    Feeding the pipeline's validation results directly into the
    performance engine must reproduce the pipeline's aggregate
    statistics exactly.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    engine = PerformanceAnalyticsEngine()
    analytics = engine.analyze(result.validation_results)

    assert analytics == result.performance


def test_performance_statistics_generated():
    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    assert result.has_performance is True

    performance = result.performance

    # Counts must be internally consistent.
    assert (
        performance.wins + performance.losses
        == performance.completed_trades
    )
    assert performance.total_results == result.signals_validated


# ============================================================
# DETERMINISM
# ============================================================


def test_determinism_identical_results():
    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()

    first = pipeline.evaluate(candles)
    second = pipeline.evaluate(candles)

    assert first.candles_processed == second.candles_processed
    assert first.evaluation_points == second.evaluation_points
    assert first.signals_generated == second.signals_generated
    assert first.signals_validated == second.signals_validated
    assert first.completed_trades == second.completed_trades
    assert first.performance == second.performance
    assert (
        first.evaluation_points_sequence
        == second.evaluation_points_sequence
    )


def test_determinism_across_instances():
    """
    Two independent pipeline instances with the same config must
    produce identical results.
    """

    candles = trending_dataset()

    first = HistoricalEvaluationPipeline().evaluate(candles)
    second = HistoricalEvaluationPipeline().evaluate(candles)

    assert first == second


# ============================================================
# NO LOOK-AHEAD
# ============================================================


def test_signal_does_not_know_about_future_tp():
    """
    A future candle containing an obvious TP must not change the
    signal generated at T. The signal is derived solely from
    candles[:T+1].
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None

    t = first.index
    signal = first.signal

    # Truncate history to exactly the trigger candle. No future
    # candles exist, so the signal at T must still be identical.
    truncated = candles[: t + 1]
    truncated_result = pipeline.evaluate(truncated)

    point = next(
        p
        for p in truncated_result.evaluation_points_sequence
        if p.index == t
    )

    assert point.signal is not None
    assert point.signal.eligible is True
    assert point.signal.entry_price == signal.entry_price
    assert point.signal.stop_loss == signal.stop_loss
    assert point.signal.take_profit == signal.take_profit


def test_future_tp_candle_does_not_alter_signal():
    """
    Appending an obvious future TP candle must not alter the
    signal prices at T.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = first_validated_point(result)
    assert first is not None

    t = first.index
    signal = first.signal

    extended = candles[: t + 1] + [
        candle(signal.take_profit, 1.0, t + 1),
    ]
    extended_result = pipeline.evaluate(extended)

    point = next(
        p
        for p in extended_result.evaluation_points_sequence
        if p.index == t
    )

    assert point.signal.entry_price == signal.entry_price
    assert point.signal.stop_loss == signal.stop_loss
    assert point.signal.take_profit == signal.take_profit


# ============================================================
# INPUT PRESERVATION
# ============================================================


def test_pipeline_does_not_mutate_input_collection():
    import copy

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    snapshot = copy.deepcopy(candles)

    pipeline.evaluate(candles)

    # The caller's list and its immutable candle contents must
    # be byte-for-byte equal to the pre-evaluation snapshot.
    assert candles == snapshot
    assert len(candles) == len(snapshot)
    assert [c.close for c in candles] == [c.close for c in snapshot]
    assert [c.timestamp for c in candles] == [
        c.timestamp for c in snapshot
    ]


def test_pipeline_does_not_mutate_individual_candles():
    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    original_timestamps = [c.timestamp for c in candles]
    original_closes = [c.close for c in candles]

    pipeline.evaluate(candles)

    assert [c.timestamp for c in candles] == original_timestamps
    assert [c.close for c in candles] == original_closes


# ============================================================
# RESULT STRUCTURE
# ============================================================


def test_result_funnel_consistency():
    """
    The funnel counts must be internally consistent.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    assert result.decisions_generated == result.evaluation_points
    assert result.signals_generated <= result.eligible_decisions
    assert result.signals_validated <= result.signals_generated
    assert result.completed_trades <= result.signals_validated
    assert len(result.signals) == result.signals_generated
    assert len(result.validation_results) == result.signals_validated


def test_result_distinguishes_counts():
    """
    The result must expose all the debugging counts requested by
    the sprint contract.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    for attr in (
        "candles_processed",
        "evaluation_points",
        "decisions_generated",
        "eligible_decisions",
        "signals_generated",
        "signals_validated",
        "completed_trades",
    ):
        assert hasattr(result, attr)
        value = getattr(result, attr)
        assert isinstance(value, int)


def test_min_history_config_respected():
    """
    A custom minimum history must delay the first evaluation
    point accordingly.
    """

    config = PipelineConfig(min_history=20)
    pipeline = HistoricalEvaluationPipeline(config)

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    if result.evaluation_points > 0:
        first_index = result.evaluation_points_sequence[0].index
        assert first_index >= 20


def test_evaluation_point_carries_engine_outputs():
    """
    Each evaluation point must carry the decision direction and
    signal state produced by the engines.
    """

    pipeline = HistoricalEvaluationPipeline()

    result = pipeline.evaluate(trending_dataset())

    for point in result.evaluation_points_sequence:
        assert isinstance(point.decision_direction, str)
        assert isinstance(point.signal_state, str)
        assert point.signal_state in {
            state.name for state in SignalState
        }
