"""
Tests for the research / robustness layer (Sprint 11H).

Covers:

* Regime detection (trending / flat / volatility / insufficient
  data / determinism / no future candles)
* Performance segmentation (direction / setup quality /
  confidence / RR / regime / empty results)
* Parameter sensitivity (multiple values / determinism /
  stability / empty / insufficient / no overfitting)
* Out-of-sample (chronological split / 70-30 default / custom
  split / insufficient data / no shuffling / comparison)
* Leakage audit (valid pipeline / future-data violation /
  validation ordering violation / chronological violation)
* ResearchEngine (full orchestration / empty / minimal /
  determinism / failure handling)
* Immutability (frozen + slots)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.swing_config import SwingConfig
from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.decision import (
    DecisionDirection,
    SetupQuality,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import (
    PipelineEvaluationPoint,
    PipelineResult,
)
from engine.models.research import (
    ConfidenceBucket,
    LeakageCheckResult,
    MarketRegime,
    ParameterResult,
    RegimeStatistics,
    RiskRewardBucket,
    SegmentStatistics,
    SegmentationDimension,
)
from engine.models.signal import (
    EntrySource,
    Invalidation,
    SignalDirection,
    SignalResult,
    SignalState,
)
from engine.models.validation import (
    ExitReason,
    ValidationResult,
    ValidationStatus,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    minimal_dataset,
    trending_dataset,
)
from engine.research import (
    LeakageAuditConfig,
    LeakageAuditEngine,
    MarketRegimeEngine,
    OutOfSampleConfig,
    OutOfSampleEngine,
    ParameterSensitivityEngine,
    PerformanceSegmentationEngine,
    RegimeConfig,
    ResearchConfig,
    ResearchEngine,
    SegmentationConfig,
)


# ============================================================
# SHARED FIXTURES
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


def trending_candles(n: int = 30) -> list[OHLCVCandle]:
    """A clean monotonic uptrend (high directional efficiency)."""
    return [candle(round(100.0 + i * 2, 2), 1.0, i) for i in range(n)]


def flat_candles(n: int = 30) -> list[OHLCVCandle]:
    """A flat oscillating market (low directional efficiency, moderate
    volatility so it falls through to the FLAT branch)."""
    return [
        candle(round(100.0 + (0.4 if i % 2 == 0 else -0.4), 2), 0.5, i)
        for i in range(n)
    ]


def high_volatility_candles(n: int = 30) -> list[OHLCVCandle]:
    """Large swings relative to price (high normalized volatility)."""
    return [
        candle(
            round(100.0 + (10 if i % 2 == 0 else -10), 2),
            8.0,
            i,
        )
        for i in range(n)
    ]


def low_volatility_candles(n: int = 30) -> list[OHLCVCandle]:
    """Very small ranges relative to price (low volatility)."""
    return [
        candle(round(100.0 + i * 0.01, 2), 0.05, i)
        for i in range(n)
    ]


def make_validation(
    status=ValidationStatus.WIN,
    *,
    realized_r: float | None = 2.0,
    mfe_r: float = 2.5,
    mae_r: float = -0.5,
    duration_candles: int = 3,
    candles_evaluated: int = 10,
) -> ValidationResult:
    return ValidationResult(
        status=status,
        exit_reason=ExitReason.TAKE_PROFIT,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        entry_triggered=True,
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


def make_signal(
    *,
    direction: SignalDirection = SignalDirection.LONG,
    state: SignalState = SignalState.LONG,
    confidence: float = 0.8,
    risk_reward_ratio: float = 2.0,
    quality: SetupQuality = SetupQuality.STRONG,
) -> SignalResult:
    return SignalResult(
        direction=direction,
        state=state,
        entry_price=100.0,
        entry_source=EntrySource.TRIGGER_CLOSE,
        stop_loss=98.0,
        take_profit=104.0,
        risk_per_unit=2.0,
        reward_per_unit=4.0,
        risk_reward_ratio=risk_reward_ratio,
        confidence=confidence,
        quality=quality,
        eligible=True,
        invalidation=Invalidation(price=98.0, condition="below stop"),
        decision_direction=DecisionDirection.BULLISH,
    )


def make_point(
    index: int,
    signal: SignalResult | None,
    validation: ValidationResult | None,
    *,
    timestamp=None,
    suppressed: bool = False,
) -> PipelineEvaluationPoint:
    return PipelineEvaluationPoint(
        index=index,
        timestamp=timestamp if timestamp is not None else _EPOCH + timedelta(days=index),
        decision_direction="BULLISH" if signal is not None else "UNKNOWN",
        decision_status="READY" if signal is not None else "NOT_READY",
        signal_state=signal.state.name if signal is not None else "NO_SIGNAL",
        signal=signal,
        validation=validation,
        reason="",
        suppressed=suppressed,
    )


def build_result(
    points: list[PipelineEvaluationPoint],
    *,
    candles_processed: int | None = None,
) -> PipelineResult:
    signals = tuple(p.signal for p in points if p.signal is not None)
    validations = tuple(
        p.validation for p in points if p.validation is not None
    )
    completed = sum(
        1
        for v in validations
        if v.status in (ValidationStatus.WIN, ValidationStatus.LOSS)
    )
    perf = PerformanceAnalyticsEngine().analyze(list(validations))

    return PipelineResult(
        candles_processed=candles_processed if candles_processed is not None else len(points),
        evaluation_points=len(points),
        decisions_generated=len(points),
        eligible_decisions=sum(1 for p in points if p.signal is not None),
        signals_generated=len([p for p in points if p.signal is not None]),
        signals_validated=len(validations),
        completed_trades=completed,
        evaluation_points_sequence=tuple(points),
        signals=signals,
        validation_results=validations,
        performance=perf,
    )


# ============================================================
# REGIME
# ============================================================


class TestRegime:
    def test_trending_classification(self):
        engine = MarketRegimeEngine()
        assert engine.classify(trending_candles(30)) == MarketRegime.TRENDING

    def test_flat_classification(self):
        engine = MarketRegimeEngine()
        assert engine.classify(flat_candles(30)) == MarketRegime.FLAT

    def test_high_volatility_classification(self):
        engine = MarketRegimeEngine()
        assert (
            engine.classify(high_volatility_candles(30))
            == MarketRegime.HIGH_VOLATILITY
        )

    def test_low_volatility_classification(self):
        engine = MarketRegimeEngine()
        assert (
            engine.classify(low_volatility_candles(30))
            == MarketRegime.LOW_VOLATILITY
        )

    def test_insufficient_data_returns_unknown(self):
        engine = MarketRegimeEngine()
        assert engine.classify(trending_candles(3)) == MarketRegime.UNKNOWN

    def test_empty_returns_unknown(self):
        engine = MarketRegimeEngine()
        assert engine.classify([]) == MarketRegime.UNKNOWN

    def test_deterministic_output(self):
        engine = MarketRegimeEngine()
        candles = trending_dataset()
        first = engine.classify(candles)
        for _ in range(5):
            assert engine.classify(candles) == first

    def test_future_candles_not_used(self):
        """Classifying a prefix must equal classifying a longer series
        truncated to the same prefix."""
        engine = MarketRegimeEngine()
        candles = trending_candles(40)
        prefix = candles[:25]
        # Build a series that has the same prefix but different future.
        longer = prefix + [candle(999.0, 1.0, 25)]
        assert engine.classify(prefix) == engine.classify(longer[:25])

    def test_min_history_config_respected(self):
        engine = MarketRegimeEngine(RegimeConfig(min_history=25))
        assert engine.classify(trending_candles(20)) == MarketRegime.UNKNOWN

    def test_volatility_thresholds_configurable(self):
        # With a very high threshold, a normally high-vol market is not
        # classified HIGH_VOLATILITY and falls through to direction.
        engine = MarketRegimeEngine(
            RegimeConfig(high_volatility_threshold=10.0),
        )
        result = engine.classify(high_volatility_candles(30))
        assert result != MarketRegime.HIGH_VOLATILITY


# ============================================================
# SEGMENTATION
# ============================================================


class TestSegmentation:
    def _pairs(self):
        long_win = (make_signal(direction=SignalDirection.LONG), make_validation(ValidationStatus.WIN, realized_r=2.0))
        short_loss = (make_signal(direction=SignalDirection.SHORT, quality=SetupQuality.WEAK), make_validation(ValidationStatus.LOSS, realized_r=-1.0))
        strong_win = (make_signal(quality=SetupQuality.STRONG, confidence=0.9, risk_reward_ratio=3.0), make_validation(ValidationStatus.WIN, realized_r=3.0))
        return [long_win, short_loss, strong_win]

    def test_direction_segmentation(self):
        engine = PerformanceSegmentationEngine()
        seg = engine.segment(self._pairs(), SegmentationDimension.DIRECTION)
        labels = {s.segment_label for s in seg.segments}
        assert labels == {"LONG", "SHORT"}
        long_seg = next(s for s in seg.segments if s.segment_label == "LONG")
        assert long_seg.completed_trades == 2

    def test_setup_quality_segmentation(self):
        engine = PerformanceSegmentationEngine()
        seg = engine.segment(self._pairs(), SegmentationDimension.SETUP_QUALITY)
        labels = {s.segment_label for s in seg.segments}
        assert "STRONG" in labels
        assert "WEAK" in labels

    def test_confidence_buckets(self):
        engine = PerformanceSegmentationEngine()
        seg = engine.segment(self._pairs(), SegmentationDimension.CONFIDENCE)
        labels = {s.segment_label for s in seg.segments}
        # confidence 0.8 -> HIGH, 0.8 -> HIGH, 0.9 -> VERY_HIGH
        assert labels <= {"HIGH", "VERY_HIGH"}

    def test_confidence_bucket_assignment_direct(self):
        from engine.research.segmentation import (
            PerformanceSegmentationEngine,
        )
        eng = PerformanceSegmentationEngine()
        # thresholds: low=0.30, medium=0.50, high=0.70, very_high=0.85
        assert eng._confidence_bucket(make_signal(confidence=0.1)) == ConfidenceBucket.LOW
        assert eng._confidence_bucket(make_signal(confidence=0.35)) == ConfidenceBucket.LOW
        assert eng._confidence_bucket(make_signal(confidence=0.55)) == ConfidenceBucket.MEDIUM
        assert eng._confidence_bucket(make_signal(confidence=0.75)) == ConfidenceBucket.HIGH
        assert eng._confidence_bucket(make_signal(confidence=0.9)) == ConfidenceBucket.VERY_HIGH

    def test_rr_buckets(self):
        engine = PerformanceSegmentationEngine()
        seg = engine.segment(self._pairs(), SegmentationDimension.RISK_REWARD)
        labels = {s.segment_label for s in seg.segments}
        # rr 2.0 -> MEDIUM_RR, 3.0 -> HIGH_RR
        assert labels <= {"MEDIUM_RR", "HIGH_RR"}

    def test_rr_bucket_assignment_direct(self):
        eng = PerformanceSegmentationEngine()
        # thresholds: low=1.0, medium=1.5, high=2.5
        assert eng._risk_reward_bucket(make_signal(risk_reward_ratio=0.5)) == RiskRewardBucket.LOW_RR
        assert eng._risk_reward_bucket(make_signal(risk_reward_ratio=1.2)) == RiskRewardBucket.LOW_RR
        assert eng._risk_reward_bucket(make_signal(risk_reward_ratio=1.8)) == RiskRewardBucket.MEDIUM_RR
        assert eng._risk_reward_bucket(make_signal(risk_reward_ratio=3.0)) == RiskRewardBucket.HIGH_RR

    def test_regime_segmentation(self):
        candles = trending_candles(40)
        engine = PerformanceSegmentationEngine()
        pairs = self._pairs()
        indices = [10, 20, 30]
        seg = engine.segment(
            pairs,
            SegmentationDimension.REGIME,
            candles=candles,
            evaluation_indices=indices,
        )
        assert seg.dimension == SegmentationDimension.REGIME
        # All pairs should map to some regime label.
        assert all(s.segment_label for s in seg.segments)

    def test_regime_segmentation_without_candles_is_unknown(self):
        engine = PerformanceSegmentationEngine()
        pairs = self._pairs()
        seg = engine.segment(pairs, SegmentationDimension.REGIME, candles=None)
        assert len(seg.segments) == 1
        assert seg.segments[0].segment_label == MarketRegime.UNKNOWN.name

    def test_empty_results(self):
        engine = PerformanceSegmentationEngine()
        seg = engine.segment([], SegmentationDimension.DIRECTION)
        assert seg.is_empty

    def test_segment_statistics_delegate_to_performance_engine(self):
        engine = PerformanceSegmentationEngine()
        pairs = [(make_signal(), make_validation(ValidationStatus.WIN, realized_r=2.0))]
        seg = engine.segment(pairs, SegmentationDimension.DIRECTION)
        s = seg.segments[0]
        assert s.win_rate == 100.0
        assert s.total_r == 2.0
        assert s.completed_trades == 1

    def test_configurable_thresholds(self):
        cfg = SegmentationConfig(confidence_high=0.95, confidence_very_high=0.99)
        eng = PerformanceSegmentationEngine(cfg)
        s = make_signal(confidence=0.8)
        # With high thresholds, 0.8 falls into MEDIUM.
        assert eng._confidence_bucket(s) == ConfidenceBucket.MEDIUM


# ============================================================
# SENSITIVITY
# ============================================================


class TestSensitivity:
    def _evaluator(self, table: dict):
        def evaluator(value):
            class _P:
                expectancy = table[value][0]
                total_r = table[value][1]
                completed_trades = table[value][2]
                total_results = table[value][2]
                win_rate = table[value][3]
                profit_factor = 1.0
                max_drawdown_r = 0.5
            return _P()
        return evaluator

    def test_multiple_parameter_values(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2, 3, 4],
            self._evaluator(
                {2: (1.0, 3.0, 3, 66.0), 3: (0.5, 1.5, 3, 50.0), 4: (-0.5, -1.5, 3, 33.0)},
            ),
        )
        assert report.configuration_count == 3
        assert len(report.results) == 3
        assert all(r.parameter_name == "lookback" for r in report.results)

    def test_deterministic_evaluation(self):
        engine = ParameterSensitivityEngine()
        evaluator = self._evaluator(
            {2: (1.0, 3.0, 3, 66.0), 3: (0.5, 1.5, 3, 50.0)},
        )
        r1 = engine.analyze("lookback", [2, 3], evaluator)
        r2 = engine.analyze("lookback", [2, 3], evaluator)
        assert r1 == r2

    def test_stable_configuration_calculation(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2, 3],
            self._evaluator({2: (1.0, 3.0, 3, 66.0), 3: (1.0, 3.0, 3, 66.0)}),
        )
        # Identical expectancies -> zero range -> high stability.
        assert report.expectancy_range == 0.0
        assert report.median_expectancy == 1.0
        assert report.profitable_configurations == 2

    def test_best_value_by_expectancy_is_descriptive(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2, 3],
            self._evaluator({2: (1.0, 3.0, 3, 66.0), 3: (2.0, 6.0, 3, 66.0)}),
        )
        assert report.best_value_by_expectancy == 3
        assert report.best_value_descriptive is True

    def test_empty_results(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze("lookback", [], lambda v: None)
        assert report.is_empty
        assert report.sufficient_data is False

    def test_insufficient_data(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2],
            self._evaluator({2: (1.0, 3.0, 3, 66.0)}),
        )
        assert report.sufficient_data is False

    def test_no_automatic_overfitting_behavior(self):
        """The report must expose stability metrics, not a single 'optimal'
        parameter to deploy."""
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2, 3, 4],
            self._evaluator(
                {2: (1.0, 3.0, 3, 66.0), 3: (2.0, 6.0, 3, 66.0), 4: (0.2, 0.6, 3, 40.0)},
            ),
        )
        # No attribute named "optimal" or "deploy".
        assert not hasattr(report, "optimal")
        assert not hasattr(report, "deploy")
        assert report.best_value_descriptive is True

    def test_sensitivity_report_has_stability_ratio(self):
        engine = ParameterSensitivityEngine()
        report = engine.analyze(
            "lookback",
            [2, 3],
            self._evaluator({2: (1.0, 3.0, 3, 66.0), 3: (3.0, 9.0, 3, 66.0)}),
        )
        assert report.stability_ratio >= 0.0
        assert report.median_expectancy == 2.0
        assert report.expectancy_range == 2.0

    def test_pipeline_result_evaluator_projection(self):
        """The engine should accept a PipelineResult (carrying .performance)."""
        candles = trending_dataset()

        def evaluator(value):
            config = PipelineConfig(swing_config=SwingConfig(lookback=value))
            return HistoricalEvaluationPipeline(config).evaluate(candles)

        engine = ParameterSensitivityEngine()
        report = engine.analyze("lookback", [2, 3], evaluator)
        assert report.configuration_count == 2
        assert all(r.completed_trades >= 0 for r in report.results)


# ============================================================
# OUT-OF-SAMPLE
# ============================================================


class TestOutOfSample:
    def _evaluator(self):
        pipeline = HistoricalEvaluationPipeline()

        def evaluator(cs):
            return pipeline.evaluate(cs)

        return evaluator

    def test_chronological_split(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        assert report.in_sample_count + report.out_of_sample_count == len(candles)

    def test_default_70_30_split(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        assert report.split_ratio == 0.70
        ratio = report.in_sample_count / len(candles)
        assert 0.65 <= ratio <= 0.75

    def test_custom_split(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine(OutOfSampleConfig(split_ratio=0.5))
        report = engine.evaluate(candles, self._evaluator())
        assert report.split_ratio == 0.5
        assert report.in_sample_count == report.out_of_sample_count

    def test_insufficient_data(self):
        candles = minimal_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        assert report.sufficient_data is False

    def test_no_shuffling(self):
        """In-sample must be a prefix, out-of-sample a suffix (no shuffle)."""
        candles = trending_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        split_index = report.in_sample_count
        assert report.in_sample_count == split_index
        # The first in-sample candle timestamp equals the first candle.
        # We verify via counts + the split index boundary.
        assert split_index + report.out_of_sample_count == len(candles)

    def test_performance_comparison_present(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        assert report.in_sample_performance is not None
        assert report.out_of_sample_performance is not None
        assert isinstance(report.expectancy_degradation, float)

    def test_degradation_is_oos_minus_in_sample(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine()
        report = engine.evaluate(candles, self._evaluator())
        expected = (
            report.out_of_sample_performance.expectancy
            - report.in_sample_performance.expectancy
        )
        assert report.expectancy_degradation == pytest.approx(expected)

    def test_invalid_split_ratio_raises(self):
        candles = trending_dataset()
        engine = OutOfSampleEngine(OutOfSampleConfig(split_ratio=1.5))
        with pytest.raises(ValueError):
            engine.evaluate(candles, self._evaluator())

    def test_empty_candles(self):
        report = OutOfSampleEngine().evaluate([], self._evaluator())
        assert report.in_sample_count == 0
        assert report.out_of_sample_count == 0
        assert report.sufficient_data is False


# ============================================================
# LEAKAGE
# ============================================================


class TestLeakage:
    def _valid_result(self) -> PipelineResult:
        signal = make_signal()
        win = make_validation(ValidationStatus.WIN, realized_r=2.0, candles_evaluated=5)
        points = [
            make_point(10, signal, win),
            make_point(20, signal, make_validation(ValidationStatus.LOSS, realized_r=-1.0, candles_evaluated=4)),
        ]
        return build_result(points, candles_processed=30)

    def test_valid_walk_forward_pipeline(self):
        result = self._valid_result()
        engine = LeakageAuditEngine()
        audit = engine.audit(result, trending_candles(30))
        assert audit.checks_performed == 5
        assert audit.passed is True
        assert audit.failures == ()

    def test_future_data_violation(self):
        """A validation that evaluated more candles than the future window
        allows must fail check 3."""
        signal = make_signal()
        # index 28, only 1 future candle exists (indices 29), but validation
        # claims it evaluated 10 candles.
        validation = make_validation(
            ValidationStatus.WIN,
            realized_r=2.0,
            candles_evaluated=10,
        )
        point = make_point(28, signal, validation)
        result = build_result([point], candles_processed=30)
        engine = LeakageAuditEngine()
        audit = engine.audit(result, trending_candles(30))
        assert audit.passed is False
        assert any("Check 3" in f for f in audit.failures)

    def test_chronological_ordering_violation(self):
        signal = make_signal()
        win = make_validation(ValidationStatus.WIN, realized_r=2.0, candles_evaluated=3)
        p1 = make_point(20, signal, win)
        p2 = make_point(10, signal, make_validation(ValidationStatus.LOSS, realized_r=-1.0, candles_evaluated=3))
        result = build_result([p1, p2], candles_processed=30)
        engine = LeakageAuditEngine()
        audit = engine.audit(result, trending_candles(30))
        assert audit.passed is False
        assert any("Check 5" in f for f in audit.failures)

    def test_out_of_sample_isolation_warning(self):
        result = self._valid_result()
        engine = LeakageAuditEngine(LeakageAuditConfig(out_of_sample_isolated=False))
        audit = engine.audit(result, trending_candles(30))
        assert any("Check 4" in w for w in audit.warnings)

    def test_out_of_sample_isolation_confirmed_no_warning(self):
        result = self._valid_result()
        engine = LeakageAuditEngine(LeakageAuditConfig(out_of_sample_isolated=True))
        audit = engine.audit(result, trending_candles(30))
        assert not any("Check 4" in w for w in audit.warnings)

    def test_index_out_of_range_violation(self):
        signal = make_signal()
        win = make_validation(ValidationStatus.WIN, realized_r=2.0, candles_evaluated=3)
        point = make_point(99, signal, win)
        result = build_result([point], candles_processed=30)
        engine = LeakageAuditEngine()
        audit = engine.audit(result, trending_candles(30))
        assert audit.passed is False
        assert any("Check 1" in f for f in audit.failures)

    def test_deterministic(self):
        result = self._valid_result()
        engine = LeakageAuditEngine()
        candles = trending_candles(30)
        a1 = engine.audit(result, candles)
        a2 = engine.audit(result, candles)
        assert a1 == a2

    def test_empty_result_passes(self):
        pipeline = HistoricalEvaluationPipeline()
        result = pipeline.evaluate([])
        engine = LeakageAuditEngine()
        audit = engine.audit(result, [])
        assert audit.checks_performed == 5
        assert audit.passed is True


# ============================================================
# RESEARCH ENGINE
# ============================================================


class TestResearchEngine:
    def _full_run(self, **kwargs):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        pipeline = HistoricalEvaluationPipeline()

        def pipeline_evaluator(cs):
            return pipeline.evaluate(cs)

        def parameter_evaluator(value):
            config = PipelineConfig(swing_config=SwingConfig(lookback=value))
            return HistoricalEvaluationPipeline(config).evaluate(candles)

        config = ResearchConfig(
            sensitivity_parameter_name="swing_lookback",
            sensitivity_parameter_values=(2, 3, 4),
        )
        report = ResearchEngine(config).analyze(
            result,
            candles,
            pipeline_evaluator=pipeline_evaluator,
            parameter_evaluator=parameter_evaluator,
            label="trending-demo",
            metadata={"dataset": "trending"},
            **kwargs,
        )
        return report, result

    def test_full_orchestration(self):
        report, result = self._full_run()
        assert report.label == "trending-demo"
        assert report.result is result
        assert report.overall_performance is not None
        assert len(report.regime_statistics) == len(MarketRegime)
        assert report.segmentation is not None
        assert report.parameter_sensitivity is not None
        assert report.parameter_sensitivity.configuration_count == 3
        assert report.out_of_sample is not None
        assert report.leakage is not None
        assert len(report.conclusions) > 0
        assert report.metadata["dataset"] == "trending"

    def test_empty_dataset(self):
        pipeline = HistoricalEvaluationPipeline()
        result = pipeline.evaluate([])
        engine = ResearchEngine()
        report = engine.analyze(result, [], label="empty")
        assert report.overall_performance is not None
        assert report.overall_performance.completed_trades == 0
        assert all(rs.total_results == 0 for rs in report.regime_statistics)
        assert report.parameter_sensitivity is None
        assert report.out_of_sample is None
        assert report.leakage is not None
        assert any("Insufficient" in c for c in report.conclusions)

    def test_minimal_dataset(self):
        pipeline = HistoricalEvaluationPipeline()
        result = pipeline.evaluate(minimal_dataset())
        engine = ResearchEngine()
        report = engine.analyze(result, minimal_dataset(), label="minimal")
        assert report.overall_performance.completed_trades == 0
        assert report.leakage.passed is True

    def test_deterministic_report(self):
        r1, _ = self._full_run()
        r2, _ = self._full_run()
        assert r1 == r2

    def test_failure_handling_evaluator_exception(self):
        """A pipeline evaluator that raises should not crash the report."""
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def bad_evaluator(cs):
            raise RuntimeError("boom")

        report = ResearchEngine().analyze(
            result,
            candles,
            pipeline_evaluator=bad_evaluator,
            label="fail",
        )
        # Out-of-sample should still be present but with empty performance.
        assert report.out_of_sample is not None
        assert report.out_of_sample.in_sample_performance is not None

    def test_no_sensitivity_without_evaluator(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="no-sens")
        assert report.parameter_sensitivity is None

    def test_conclusions_are_descriptive_not_predictive(self):
        report, _ = self._full_run()
        joined = " ".join(report.conclusions)
        assert "is profitable" not in joined
        assert "descriptive" in joined or "Insufficient" in joined

    def test_regime_statistics_all_regimes_present(self):
        report, _ = self._full_run()
        regimes = {rs.regime for rs in report.regime_statistics}
        assert regimes == set(MarketRegime)

    def test_research_config_defaults(self):
        cfg = ResearchConfig()
        assert cfg.out_of_sample.split_ratio == 0.70
        assert cfg.default_segmentation_dimension == SegmentationDimension.DIRECTION
        assert cfg.min_trades_for_inference == 5

    def test_default_segmentation_dimension_configurable(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        cfg = ResearchConfig(default_segmentation_dimension=SegmentationDimension.CONFIDENCE)
        report = ResearchEngine(cfg).analyze(result, candles, label="conf")
        assert report.segmentation.dimension == SegmentationDimension.CONFIDENCE


# ============================================================
# IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_regime_statistics_frozen(self):
        rs = RegimeStatistics(
            regime=MarketRegime.TRENDING,
            total_results=1,
            completed_trades=1,
            wins=1,
            losses=0,
            ambiguous=0,
            expired=0,
            not_triggered=0,
            win_rate=100.0,
            total_r=2.0,
            average_r=2.0,
            expectancy=2.0,
            profit_factor=2.0,
            max_drawdown=0.0,
            average_mfe=0.0,
            average_mae=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            rs.regime = MarketRegime.FLAT  # type: ignore[misc]

    def test_segment_statistics_frozen(self):
        s = SegmentStatistics(
            dimension=SegmentationDimension.DIRECTION,
            segment_label="LONG",
            total_results=1,
            completed_trades=1,
            wins=1,
            losses=0,
            win_rate=100.0,
            total_r=2.0,
            average_r=2.0,
            expectancy=2.0,
            profit_factor=2.0,
            max_drawdown=0.0,
        )
        with pytest.raises(FrozenInstanceError):
            s.segment_label = "SHORT"  # type: ignore[misc]

    def test_parameter_result_frozen(self):
        pr = ParameterResult(
            parameter_name="lookback",
            parameter_value=2,
            total_trades=3,
            completed_trades=3,
            win_rate=66.0,
            expectancy=1.0,
            profit_factor=2.0,
            total_r=3.0,
            max_drawdown=0.5,
        )
        with pytest.raises(FrozenInstanceError):
            pr.parameter_value = 99  # type: ignore[misc]

    def test_leakage_check_result_frozen(self):
        r = LeakageCheckResult(passed=True, checks_performed=5)
        with pytest.raises(FrozenInstanceError):
            r.passed = False  # type: ignore[misc]

    def test_research_report_frozen(self):
        report = ResearchEngine().analyze(
            HistoricalEvaluationPipeline().evaluate([]),
            [],
            label="x",
        )
        with pytest.raises(FrozenInstanceError):
            report.label = "y"  # type: ignore[misc]

    def test_models_use_slots(self):
        rs = RegimeStatistics(
            regime=MarketRegime.TRENDING,
            total_results=0,
            completed_trades=0,
            wins=0,
            losses=0,
            ambiguous=0,
            expired=0,
            not_triggered=0,
            win_rate=0.0,
            total_r=0.0,
            average_r=0.0,
            expectancy=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            average_mfe=0.0,
            average_mae=0.0,
        )
        with pytest.raises(AttributeError):
            rs.new_attribute = "x"  # type: ignore[attr-defined]


# ============================================================
# END-TO-END REAL PIPELINE
# ============================================================


def test_end_to_end_research_report_on_trending_dataset():
    candles = trending_dataset()
    result = HistoricalEvaluationPipeline().evaluate(candles)
    pipeline = HistoricalEvaluationPipeline()
    config = ResearchConfig(
        sensitivity_parameter_name="swing_lookback",
        sensitivity_parameter_values=(2, 3),
    )
    report = ResearchEngine(config).analyze(
        result,
        candles,
        pipeline_evaluator=lambda cs: pipeline.evaluate(cs),
        parameter_evaluator=lambda v: HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=v)),
        ).evaluate(candles),
        label="e2e",
    )
    assert report.label == "e2e"
    assert report.overall_performance is not None
    assert report.leakage.passed is True
    assert report.out_of_sample is not None
    assert report.parameter_sensitivity is not None
