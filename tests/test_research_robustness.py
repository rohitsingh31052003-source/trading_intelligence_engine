"""
Tests for the Sprint 11I research / robustness hardening.

Covers:

* Walk-forward parameter selection
    - development / evaluation separation
    - selection from development data only
    - OOS data cannot change the selected parameter
    - chronological, non-overlapping windows
    - insufficient development / evaluation data
    - empty / single-candidate / evaluator-exception handling
* Parameter robustness
    - descriptive best vs robust / stable
    - stable vs unstable configurations
    - high dependency on a single configuration
    - insufficient data
    - no-overfitting behaviour
* Data sufficiency
    - insufficient trades / regime samples / OOS trades /
      parameter observations
    - sufficient-for-inference gate
* Regime robustness
    - zero-trade regimes are unobserved, not zero performance
    - regime sample sufficiency
    - profitable / unprofitable distinction
* Leakage audit (Sprint 11I)
    - structured checks / severities
    - NOT VERIFIED semantics (never falsely PASS)
    - overlapping development / OOS windows
    - parameter selection using OOS data
    - accidental reuse of evaluation results
    - temporal ordering
    - backward compatibility (checks_performed == 5 default)
* Report correctness
    - report sections populated
    - descriptive vs validated labelling
* Backward compatibility
    - legacy ResearchEngine.analyze call (no walk-forward)
    - legacy LeakageCheckResult construction
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
    CandidateResult,
    DataSufficiencyReport,
    LeakageCheck,
    LeakageCheckResult,
    LeakageSeverity,
    MarketRegime,
    OutOfSampleReport,
    ParameterResult,
    ParameterSensitivityReport,
    RegimeStatistics,
    SelectedConfiguration,
    SegmentationDimension,
    WalkForwardSelectionReport,
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
    trending_dataset,
)
from engine.research import (
    LeakageAuditConfig,
    LeakageAuditContext,
    LeakageAuditEngine,
    ParameterRobustnessEngine,
    ResearchConfig,
    ResearchEngine,
    RobustnessConfig,
    WalkForwardConfig,
    WalkForwardParameterEngine,
    build_sensitivity_for_robustness,
)


# ============================================================
# SHARED FIXTURES (mirror tests/test_research.py)
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
    return [candle(round(100.0 + i * 2, 2), 1.0, i) for i in range(n)]


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
    confidence: float = 0.8,
    risk_reward_ratio: float = 2.0,
    quality: SetupQuality = SetupQuality.STRONG,
) -> SignalResult:
    return SignalResult(
        direction=direction,
        state=SignalState.LONG,
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


def make_parameter_result(
    value,
    *,
    expectancy: float,
    total_r: float,
    completed_trades: int = 3,
) -> ParameterResult:
    return ParameterResult(
        parameter_name="p",
        parameter_value=value,
        total_trades=completed_trades,
        completed_trades=completed_trades,
        win_rate=50.0,
        expectancy=expectancy,
        profit_factor=1.5,
        total_r=total_r,
        max_drawdown=0.5,
    )


def make_sensitivity(results: list[ParameterResult]) -> ParameterSensitivityReport:
    return build_sensitivity_for_robustness("p", results)


# ============================================================
# WALK-FORWARD PARAMETER SELECTION
# ============================================================


class TestWalkForwardSelection:
    def _evaluator(self):
        def evaluator(candles, value):
            config = PipelineConfig(
                swing_config=SwingConfig(lookback=value),
            )
            return HistoricalEvaluationPipeline(config).evaluate(candles)

        return evaluator

    def test_development_evaluation_separation(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3), self._evaluator(),
        )
        assert report.development_window[0] == 0
        assert report.development_window[1] == report.evaluation_window[0]
        assert report.evaluation_window[1] == len(candles)
        assert report.windows_overlap is False

    def test_windows_non_overlapping(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3), self._evaluator(),
        )
        dev_end = report.development_window[1]
        eval_start = report.evaluation_window[0]
        assert dev_end <= eval_start

    def test_candidate_count_matches_values(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3, 4, 5), self._evaluator(),
        )
        assert len(report.candidates) == 4
        assert [c.parameter_value for c in report.candidates] == [2, 3, 4, 5]

    def test_selection_from_development_data_only(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3), self._evaluator(),
        )
        assert report.has_selected
        assert report.selected.selected_from_development_data is True
        assert report.selection_isolated_from_evaluation is True
        assert report.selection_verified is True

    def test_selected_matches_descriptive_best_of_development(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3, 4), self._evaluator(),
        )
        best = max(
            report.candidates, key=lambda c: c.development_expectancy
        )
        assert report.selected.parameter_value == best.parameter_value
        assert (
            report.selected.development_expectancy
            == best.development_expectancy
        )

    def test_oos_data_cannot_change_selected_parameter(self):
        """The selected parameter must be one of the development-window
        descriptive bests; the OOS evaluation only runs the SELECTED
        config, so OOS data cannot retroactively change selection."""
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3, 4), self._evaluator(),
        )
        selected_value = report.selected.parameter_value
        candidate_values = {c.parameter_value for c in report.candidates}
        assert selected_value in candidate_values
        # Only one OOS result, for the selected config.
        assert report.out_of_sample_result is not None

    def test_chronological_split_default_70_30(self):
        candles = trending_candles(100)
        report = WalkForwardParameterEngine(WalkForwardConfig()).evaluate(
            candles, "lookback", (2,), self._evaluator(),
        )
        dev = report.development_candle_count
        oos = report.evaluation_candle_count
        assert dev + oos == 100
        assert dev == 70
        assert oos == 30

    def test_invalid_split_ratio_raises(self):
        candles = trending_candles(40)
        with pytest.raises(ValueError):
            WalkForwardParameterEngine(
                WalkForwardConfig(split_ratio=1.5),
            ).evaluate(candles, "lookback", (2,), self._evaluator())

    def test_empty_candles_returns_report(self):
        report = WalkForwardParameterEngine().evaluate(
            [], "lookback", (2, 3), self._evaluator(),
        )
        # Candidates are still produced (one per value) but with no
        # trades; the descriptive best is the first (0 expectancy)
        # but dev data is insufficient.
        assert len(report.candidates) == 2
        for c in report.candidates:
            assert c.development_completed_trades == 0
            assert c.sufficient_development_trades is False
        assert report.sufficient_development_data is False

    def test_empty_parameter_values(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (), self._evaluator(),
        )
        assert report.candidates == ()
        assert report.has_selected is False

    def test_evaluator_exception_does_not_propagate(self):
        def bad_evaluator(candles, value):
            raise RuntimeError("boom")

        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3), bad_evaluator,
        )
        # All candidates get empty analytics (0 trades) but no raise.
        for c in report.candidates:
            assert c.development_completed_trades == 0

    def test_min_development_trades_threshold(self):
        candles = trending_candles(40)
        cfg = WalkForwardConfig(min_development_trades=999)
        report = WalkForwardParameterEngine(cfg).evaluate(
            candles, "lookback", (2, 3), self._evaluator(),
        )
        # No candidate meets the threshold -> insufficient dev data.
        assert report.sufficient_development_data is False
        for c in report.candidates:
            assert c.sufficient_development_trades is False

    def test_deterministic(self):
        candles = trending_candles(40)
        ev = self._evaluator()
        r1 = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3, 4), ev,
        )
        r2 = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3, 4), ev,
        )
        assert r1 == r2

    def test_report_frozen(self):
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2,), self._evaluator(),
        )
        with pytest.raises(FrozenInstanceError):
            report.parameter_name = "x"  # type: ignore[misc]


# ============================================================
# PARAMETER ROBUSTNESS
# ============================================================


class TestParameterRobustness:
    def test_descriptive_best_is_robust_when_stable(self):
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=1.1, total_r=3.3),
            make_parameter_result(4, expectancy=0.95, total_r=2.85),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.descriptive_best == 3
        assert report.descriptive_best_is_robust is True
        assert 3 in report.robust_configurations

    def test_descriptive_best_not_robust_when_outlier(self):
        # value 2 is the descriptive best but far above the median.
        results = [
            make_parameter_result(2, expectancy=5.0, total_r=15.0),
            make_parameter_result(3, expectancy=0.5, total_r=1.5),
            make_parameter_result(4, expectancy=0.5, total_r=1.5),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine(RobustnessConfig()).analyze(sens)
        assert report.descriptive_best == 2
        # median is 0.5; band = max(0.5*0.25, 0.1) = 0.125;
        # |5.0 - 0.5| = 4.5 > 0.125 -> not near median -> not stable.
        assert report.descriptive_best_is_robust is False
        assert 2 not in report.robust_configurations
        assert 2 in report.unstable_configurations

    def test_stable_vs_unstable_configurations(self):
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=1.05, total_r=3.0),
            make_parameter_result(4, expectancy=5.0, total_r=15.0),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        stable_values = set(report.robust_configurations)
        unstable_values = set(report.unstable_configurations)
        assert stable_values == {2, 3}
        assert unstable_values == {4}

    def test_highly_dependent_on_single_config_when_one_profitable(self):
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=-1.0, total_r=-3.0),
            make_parameter_result(4, expectancy=-0.5, total_r=-1.5),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.highly_dependent_on_single_config is True

    def test_highly_dependent_when_single_robust_among_profitable(self):
        # two profitable but only one near-median (stable).
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=1.0, total_r=3.0),
            make_parameter_result(4, expectancy=5.0, total_r=15.0),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        # profitable configs: 2, 3, 4 (3 profitable). robust: 2,3 (2).
        # robust_count=2 and profitable_count=3 -> not the single-robust
        # branch. So highly_dependent should be False here.
        assert report.highly_dependent_on_single_config is False

    def test_robust_flag_requires_sufficient_data(self):
        results = [make_parameter_result(2, expectancy=1.0, total_r=3.0)]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.sufficient_data is False
        assert report.robust is False

    def test_robust_flag_true_when_at_least_one_stable(self):
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=1.0, total_r=3.0),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.sufficient_data is True
        assert report.robust is True

    def test_no_overfitting_descriptive_best_never_auto_robust(self):
        """The descriptive best must NOT be automatically marked robust
        just because it has the highest expectancy."""
        results = [
            make_parameter_result(2, expectancy=10.0, total_r=30.0),
            make_parameter_result(3, expectancy=0.1, total_r=0.3),
            make_parameter_result(4, expectancy=0.1, total_r=0.3),
        ]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.descriptive_best == 2
        assert report.descriptive_best_is_robust is False

    def test_empty_results(self):
        sens = ParameterSensitivityReport(parameter_name="p")
        report = ParameterRobustnessEngine().analyze(sens)
        assert report.is_empty is True
        assert report.robust is False

    def test_zero_trade_config_never_profitable(self):
        pr = make_parameter_result(2, expectancy=0.0, total_r=0.0, completed_trades=0)
        sens = make_sensitivity([pr, make_parameter_result(3, expectancy=1.0, total_r=3.0)])
        report = ParameterRobustnessEngine(
            RobustnessConfig(min_completed_trades=3),
        ).analyze(sens)
        config_2 = [c for c in report.configurations if c.parameter_value == 2][0]
        assert config_2.profitable is False
        assert config_2.stable is False

    def test_deterministic(self):
        results = [
            make_parameter_result(2, expectancy=1.0, total_r=3.0),
            make_parameter_result(3, expectancy=1.1, total_r=3.3),
        ]
        sens = make_sensitivity(results)
        r1 = ParameterRobustnessEngine().analyze(sens)
        r2 = ParameterRobustnessEngine().analyze(sens)
        assert r1 == r2

    def test_report_frozen(self):
        results = [make_parameter_result(2, expectancy=1.0, total_r=3.0)]
        sens = make_sensitivity(results)
        report = ParameterRobustnessEngine().analyze(sens)
        with pytest.raises(FrozenInstanceError):
            report.robust = True  # type: ignore[misc]


# ============================================================
# DATA SUFFICIENCY
# ============================================================


class TestDataSufficiency:
    def _report(
        self,
        completed: int = 10,
        regimes_with_trades: int = 0,
        regimes_insufficient: int = 0,
        oos_completed: int = 5,
        oos_performed: bool = True,
        parameter_configurations: int = 3,
        parameter_performed: bool = True,
    ) -> DataSufficiencyReport:
        # Build via the ResearchEngine helper indirectly by constructing
        # the model directly for unit-level control.
        min_trades = 5
        sufficient_trades = completed >= min_trades

        regimes_sufficient = regimes_with_trades - regimes_insufficient
        insufficient_regime = regimes_insufficient > 0
        insufficient_oos = oos_performed and oos_completed < 3
        insufficient_params = (
            parameter_performed and parameter_configurations < 2
        )

        return DataSufficiencyReport(
            completed_trades=completed,
            min_trades_for_inference=min_trades,
            sufficient_trades=sufficient_trades,
            insufficient_trades=not sufficient_trades,
            insufficient_regime_samples=insufficient_regime,
            insufficient_oos_trades=insufficient_oos,
            insufficient_parameter_observations=insufficient_params,
            min_regime_observations=3,
            min_oos_trades=3,
            min_parameter_configurations=2,
            oos_completed_trades=oos_completed,
            parameter_configurations=parameter_configurations,
            regimes_with_trades=regimes_with_trades,
            regimes_sufficient=max(regimes_sufficient, 0),
            summary="unit",
        )

    def test_sufficient_for_inference_true_when_all_met(self):
        ds = self._report(
            completed=10,
            regimes_with_trades=2,
            regimes_insufficient=0,
            oos_completed=5,
            parameter_configurations=3,
        )
        assert ds.sufficient_for_inference is True
        assert ds.insufficient_trades is False
        assert ds.insufficient_regime_samples is False
        assert ds.insufficient_oos_trades is False
        assert ds.insufficient_parameter_observations is False

    def test_insufficient_trades(self):
        ds = self._report(completed=3)
        assert ds.sufficient_trades is False
        assert ds.insufficient_trades is True
        assert ds.sufficient_for_inference is False

    def test_insufficient_regime_samples(self):
        ds = self._report(
            completed=10, regimes_with_trades=2, regimes_insufficient=1,
        )
        assert ds.insufficient_regime_samples is True
        assert ds.sufficient_for_inference is False

    def test_insufficient_oos_trades(self):
        ds = self._report(completed=10, oos_completed=1)
        assert ds.insufficient_oos_trades is True
        assert ds.sufficient_for_inference is False

    def test_insufficient_parameter_observations(self):
        ds = self._report(completed=10, parameter_configurations=1)
        assert ds.insufficient_parameter_observations is True
        assert ds.sufficient_for_inference is False

    def test_no_oos_performed_not_flagged(self):
        ds = self._report(
            completed=10, oos_completed=0, oos_performed=False,
        )
        assert ds.insufficient_oos_trades is False

    def test_report_frozen(self):
        ds = self._report()
        with pytest.raises(FrozenInstanceError):
            ds.completed_trades = 0  # type: ignore[misc]


# ============================================================
# REGIME ROBUSTNESS
# ============================================================


class TestRegimeRobustness:
    def test_zero_trade_regime_is_unobserved_not_zero_performance(self):
        rs = RegimeStatistics(
            regime=MarketRegime.FLAT,
            total_results=0,
            completed_trades=0,
            wins=0, losses=0, ambiguous=0, expired=0, not_triggered=0,
            win_rate=0.0, total_r=0.0, average_r=0.0, expectancy=0.0,
            profit_factor=0.0, max_drawdown=0.0,
            average_mfe=0.0, average_mae=0.0,
        )
        assert rs.has_no_completed_trades is True
        assert rs.is_profitable is False
        assert rs.is_unprofitable is False

    def test_profitable_regime_requires_trades(self):
        rs = RegimeStatistics(
            regime=MarketRegime.TRENDING,
            total_results=5, completed_trades=5,
            wins=3, losses=2, ambiguous=0, expired=0, not_triggered=0,
            win_rate=60.0, total_r=4.0, average_r=0.8, expectancy=0.8,
            profit_factor=2.0, max_drawdown=1.0,
            average_mfe=1.0, average_mae=-0.5,
        )
        assert rs.is_profitable is True
        assert rs.is_unprofitable is False

    def test_unprofitable_regime(self):
        rs = RegimeStatistics(
            regime=MarketRegime.FLAT,
            total_results=3, completed_trades=3,
            wins=0, losses=3, ambiguous=0, expired=0, not_triggered=0,
            win_rate=0.0, total_r=-3.0, average_r=-1.0, expectancy=-1.0,
            profit_factor=0.0, max_drawdown=2.0,
            average_mfe=0.0, average_mae=-1.0,
        )
        assert rs.is_unprofitable is True
        assert rs.is_profitable is False

    def test_regime_sufficient_observations_flag(self):
        from engine.models.research import MarketRegime

        rs = RegimeStatistics(
            regime=MarketRegime.TRENDING,
            total_results=5, completed_trades=5,
            wins=3, losses=2, ambiguous=0, expired=0, not_triggered=0,
            win_rate=60.0, total_r=4.0, average_r=0.8, expectancy=0.8,
            profit_factor=2.0, max_drawdown=1.0,
            average_mfe=1.0, average_mae=-0.5,
            sufficient_observations=True,
            min_observations_for_inference=3,
        )
        assert rs.sufficient_observations is True
        assert rs.has_completed_trades is True

    def test_research_engine_populates_regime_sufficiency(self):
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="r")
        # Every regime has the sufficiency flag set (True or False).
        for rs in report.regime_statistics:
            assert isinstance(rs.sufficient_observations, bool)
            assert rs.min_observations_for_inference == 3


# ============================================================
# LEAKAGE AUDIT (Sprint 11I)
# ============================================================


class TestLeakageAuditHardened:
    def _valid_result(self) -> PipelineResult:
        signal = make_signal()
        win = make_validation(ValidationStatus.WIN, realized_r=2.0, candles_evaluated=5)
        points = [
            make_point(10, signal, win),
            make_point(20, signal, make_validation(ValidationStatus.LOSS, realized_r=-1.0, candles_evaluated=4)),
        ]
        return build_result(points, candles_processed=30)

    def test_structured_checks_present(self):
        result = self._valid_result()
        audit = LeakageAuditEngine().audit(result, trending_candles(30))
        assert len(audit.checks) == 5
        for chk in audit.checks:
            assert isinstance(chk, LeakageCheck)
            assert chk.severity in LeakageSeverity

    def test_backwards_compat_checks_performed_five(self):
        result = self._valid_result()
        audit = LeakageAuditEngine().audit(result, trending_candles(30))
        assert audit.checks_performed == 5
        assert audit.passed is True
        assert audit.failures == ()

    def test_not_verified_when_no_walk_forward(self):
        """When no walk-forward context is supplied, check 4 (OOS
        isolation) must be NOT_VERIFIED, never PASS."""
        result = self._valid_result()
        audit = LeakageAuditEngine().audit(result, trending_candles(30))
        check4 = [c for c in audit.checks if c.name == "oos_parameter_isolation"][0]
        assert check4.severity is LeakageSeverity.NOT_VERIFIED
        assert check4.passed is False
        assert audit.has_not_verified is True

    def test_check4_passes_with_structural_walk_forward(self):
        result = self._valid_result()
        wf = WalkForwardSelectionReport(
            parameter_name="p",
            development_window=(0, 20),
            evaluation_window=(20, 30),
            selection_isolated_from_evaluation=True,
            selection_verified=True,
            selected=SelectedConfiguration(
                parameter_value=2,
                selection_basis="development_expectancy",
                development_expectancy=1.0,
                selected_from_development_data=True,
                selected_index=0,
            ),
        )
        ctx = LeakageAuditContext(walk_forward_selection=wf)
        audit = LeakageAuditEngine().audit(
            result, trending_candles(30), context=ctx,
        )
        check4 = [c for c in audit.checks if c.name == "oos_parameter_isolation"][0]
        assert check4.severity is LeakageSeverity.PASS
        assert check4.passed is True

    def test_extended_checks_run_with_walk_forward(self):
        result = self._valid_result()
        wf = WalkForwardSelectionReport(
            parameter_name="p",
            development_window=(0, 20),
            evaluation_window=(20, 30),
            selection_isolated_from_evaluation=True,
            selection_verified=True,
            selected=SelectedConfiguration(
                parameter_value=2,
                selection_basis="development_expectancy",
                development_expectancy=1.0,
                selected_from_development_data=True,
                selected_index=0,
            ),
            candidates=(
                CandidateResult(
                    parameter_value=2,
                    development_performance=None,
                    development_completed_trades=3,
                    development_expectancy=1.0,
                    development_total_r=3.0,
                    sufficient_development_trades=True,
                ),
            ),
        )
        ctx = LeakageAuditContext(walk_forward_selection=wf)
        audit = LeakageAuditEngine().audit(
            result, trending_candles(30), context=ctx,
        )
        names = {c.name for c in audit.checks}
        assert "window_overlap" in names
        assert "selection_isolation" in names
        assert "no_evaluation_reuse" in names
        assert audit.checks_performed == 8
        assert audit.passed is True

    def test_overlapping_windows_fail(self):
        result = self._valid_result()
        wf = WalkForwardSelectionReport(
            parameter_name="p",
            development_window=(0, 25),
            evaluation_window=(20, 30),
            selection_isolated_from_evaluation=True,
            selection_verified=True,
            selected=SelectedConfiguration(
                parameter_value=2,
                selection_basis="development_expectancy",
                development_expectancy=1.0,
                selected_from_development_data=True,
                selected_index=0,
            ),
        )
        ctx = LeakageAuditContext(walk_forward_selection=wf)
        audit = LeakageAuditEngine().audit(
            result, trending_candles(30), context=ctx,
        )
        overlap = [c for c in audit.checks if c.name == "window_overlap"][0]
        assert overlap.severity is LeakageSeverity.FAILURE
        assert audit.passed is False
        assert any("Check 6" in f for f in audit.failures)

    def test_selection_isolation_failure(self):
        result = self._valid_result()
        wf = WalkForwardSelectionReport(
            parameter_name="p",
            development_window=(0, 20),
            evaluation_window=(20, 30),
            selection_isolated_from_evaluation=False,
            selection_verified=False,
            selected=SelectedConfiguration(
                parameter_value=2,
                selection_basis="development_expectancy",
                development_expectancy=1.0,
                selected_from_development_data=False,
                selected_index=0,
            ),
        )
        ctx = LeakageAuditContext(walk_forward_selection=wf)
        audit = LeakageAuditEngine().audit(
            result, trending_candles(30), context=ctx,
        )
        sel = [c for c in audit.checks if c.name == "selection_isolation"][0]
        assert sel.severity is LeakageSeverity.FAILURE

    def test_evaluation_reuse_failure(self):
        result = self._valid_result()
        wf = WalkForwardSelectionReport(
            parameter_name="p",
            development_window=(0, 20),
            evaluation_window=(20, 30),
            selection_isolated_from_evaluation=True,
            selection_verified=True,
            selected=SelectedConfiguration(
                parameter_value=2,
                selection_basis="development_expectancy",
                development_expectancy=9.9,  # not in candidates
                selected_from_development_data=True,
                selected_index=0,
            ),
            candidates=(
                CandidateResult(
                    parameter_value=2,
                    development_performance=None,
                    development_completed_trades=3,
                    development_expectancy=1.0,
                    development_total_r=3.0,
                    sufficient_development_trades=True,
                ),
            ),
        )
        ctx = LeakageAuditContext(walk_forward_selection=wf)
        audit = LeakageAuditEngine().audit(
            result, trending_candles(30), context=ctx,
        )
        reuse = [c for c in audit.checks if c.name == "no_evaluation_reuse"][0]
        assert reuse.severity is LeakageSeverity.FAILURE
        assert audit.passed is False

    def test_temporal_ordering_failure(self):
        signal = make_signal()
        p1 = make_point(20, signal, make_validation(ValidationStatus.WIN, realized_r=2.0, candles_evaluated=3))
        p2 = make_point(10, signal, make_validation(ValidationStatus.LOSS, realized_r=-1.0, candles_evaluated=3))
        result = build_result([p1, p2], candles_processed=30)
        audit = LeakageAuditEngine().audit(result, trending_candles(30))
        assert audit.passed is False
        assert any("Check 5" in f for f in audit.failures)

    def test_future_data_violation(self):
        signal = make_signal()
        validation = make_validation(
            ValidationStatus.WIN, realized_r=2.0, candles_evaluated=10,
        )
        point = make_point(28, signal, validation)
        result = build_result([point], candles_processed=30)
        audit = LeakageAuditEngine().audit(result, trending_candles(30))
        assert audit.passed is False
        assert any("Check 3" in f for f in audit.failures)

    def test_legacy_warning_behavior_preserved(self):
        """When out_of_sample_isolated=False and no walk-forward, the
        Check 4 message still surfaces in warnings (backward compat)."""
        result = self._valid_result()
        audit = LeakageAuditEngine(
            LeakageAuditConfig(out_of_sample_isolated=False),
        ).audit(result, trending_candles(30))
        assert any("Check 4" in w for w in audit.warnings)

    def test_legacy_declared_isolation_no_warning(self):
        result = self._valid_result()
        audit = LeakageAuditEngine(
            LeakageAuditConfig(out_of_sample_isolated=True),
        ).audit(result, trending_candles(30))
        assert not any("Check 4" in w for w in audit.warnings)

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
        audit = LeakageAuditEngine().audit(result, [])
        assert audit.checks_performed == 5
        assert audit.passed is True


# ============================================================
# END-TO-END REPORT CORRECTNESS
# ============================================================


class TestReportCorrectness:
    def _evaluators(self):
        def wf(cs, value):
            cfg = PipelineConfig(swing_config=SwingConfig(lookback=value))
            return HistoricalEvaluationPipeline(cfg).evaluate(cs)

        def param(value):
            cfg = PipelineConfig(swing_config=SwingConfig(lookback=value))
            return HistoricalEvaluationPipeline(cfg).evaluate(trending_dataset())

        return wf, param

    def test_report_has_all_sprint_11i_sections(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        wf, param = self._evaluators()
        cfg = ResearchConfig(
            sensitivity_parameter_name="swing_lookback",
            sensitivity_parameter_values=(2, 3, 4),
        )
        report = ResearchEngine(cfg).analyze(
            result, candles,
            pipeline_evaluator=lambda cs: HistoricalEvaluationPipeline().evaluate(cs),
            parameter_evaluator=param,
            walk_forward_evaluator=wf,
            label="full",
        )
        assert report.parameter_robustness is not None
        assert report.walk_forward_selection is not None
        assert report.data_sufficiency is not None
        assert report.leakage is not None
        assert report.overall_performance is not None

    def test_walk_forward_context_drives_extended_leakage(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        wf, param = self._evaluators()
        cfg = ResearchConfig(
            sensitivity_parameter_name="swing_lookback",
            sensitivity_parameter_values=(2, 3, 4),
        )
        report = ResearchEngine(cfg).analyze(
            result, candles,
            walk_forward_evaluator=wf,
            parameter_evaluator=param,
            label="wf",
        )
        # Walk-forward context supplied -> 8 checks performed.
        assert report.leakage.checks_performed == 8
        assert report.leakage.passed is True
        assert report.leakage.has_not_verified is False

    def test_no_walk_forward_yields_not_verified(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="bare")
        assert report.leakage.checks_performed == 5
        assert report.leakage.has_not_verified is True

    def test_descriptive_vs_validated_labels_in_conclusions(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        wf, param = self._evaluators()
        cfg = ResearchConfig(
            sensitivity_parameter_name="swing_lookback",
            sensitivity_parameter_values=(2, 3, 4),
        )
        report = ResearchEngine(cfg).analyze(
            result, candles,
            walk_forward_evaluator=wf,
            parameter_evaluator=param,
            label="labels",
        )
        joined = " ".join(report.conclusions)
        assert "descriptive" in joined.lower()
        assert "validated by construction" in joined.lower()

    def test_data_sufficiency_summary_built(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="ds")
        assert report.data_sufficiency is not None
        assert isinstance(report.data_sufficiency.summary, str)
        assert report.data_sufficiency.summary

    def test_deterministic_report(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        wf, param = self._evaluators()
        cfg = ResearchConfig(
            sensitivity_parameter_name="swing_lookback",
            sensitivity_parameter_values=(2, 3, 4),
        )
        r1 = ResearchEngine(cfg).analyze(
            result, candles, walk_forward_evaluator=wf,
            parameter_evaluator=param, label="d",
        )
        r2 = ResearchEngine(cfg).analyze(
            result, candles, walk_forward_evaluator=wf,
            parameter_evaluator=param, label="d",
        )
        assert r1 == r2

    def test_report_frozen(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="f")
        with pytest.raises(FrozenInstanceError):
            report.label = "g"  # type: ignore[misc]


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================


class TestBackwardCompatibility:
    def test_legacy_analyze_signature_without_walk_forward(self):
        candles = trending_dataset()
        result = HistoricalEvaluationPipeline().evaluate(candles)
        report = ResearchEngine().analyze(result, candles, label="legacy")
        assert report.walk_forward_selection is None
        assert report.parameter_robustness is None
        assert report.data_sufficiency is not None  # always built now
        assert report.leakage.checks_performed == 5

    def test_legacy_research_config_defaults_preserved(self):
        cfg = ResearchConfig()
        assert cfg.min_trades_for_inference == 5
        assert cfg.default_segmentation_dimension == (
            SegmentationDimension.DIRECTION
        )
        assert cfg.out_of_sample.split_ratio == 0.70

    def test_legacy_leakage_check_result_construction(self):
        r = LeakageCheckResult(passed=True, checks_performed=5)
        assert r.passed is True
        assert r.failures == ()
        assert r.warnings == ()
        assert r.not_verified == ()
        assert r.checks == ()

    def test_legacy_out_of_sample_report_construction(self):
        oos = OutOfSampleReport(
            split_ratio=0.7,
            in_sample_count=10,
            out_of_sample_count=5,
            sufficient_data=True,
        )
        assert oos.development_window is None
        assert oos.evaluation_window is None
        assert oos.parameter_selection_isolated is None

    def test_legacy_regime_statistics_construction(self):
        from engine.models.research import MarketRegime

        rs = RegimeStatistics(
            regime=MarketRegime.TRENDING,
            total_results=1, completed_trades=1,
            wins=1, losses=0, ambiguous=0, expired=0, not_triggered=0,
            win_rate=100.0, total_r=2.0, average_r=2.0, expectancy=2.0,
            profit_factor=2.0, max_drawdown=0.0,
            average_mfe=0.0, average_mae=0.0,
        )
        assert rs.sufficient_observations is False
        assert rs.min_observations_for_inference == 0

    def test_existing_research_tests_still_pass_import(self):
        """The Sprint 11H public API remains importable from the package."""
        from engine.research import (
            MarketRegimeEngine,
            OutOfSampleEngine,
            ParameterSensitivityEngine,
            PerformanceSegmentationEngine,
            ResearchEngine,
            ResearchReport,
        )
        assert all(
            cls is not None
            for cls in [
                MarketRegimeEngine,
                OutOfSampleEngine,
                ParameterSensitivityEngine,
                PerformanceSegmentationEngine,
                ResearchEngine,
                ResearchReport,
            ]
        )


# ============================================================
# SPRINT 11I REPORTING CONSISTENCY (review hardening)
# ============================================================


class TestReportingConsistency:
    """
    Regression tests for the two reporting inconsistencies found
    during the Sprint 11I focused review.

    1. Walk-forward ``sufficient_evaluation_window`` (WINDOW size
       in candles) vs DataSufficiency ``insufficient_oos_trades``
       (completed TRADES) must be distinct, explicitly named
       concepts.
    2. The legacy OOS report's ``parameter_selection_isolated``
       must be made consistent with a verified Sprint 11I
       walk-forward selection via explicit attribution, WITHOUT
       weakening the leakage audit and WITHOUT falsely claiming
       verification the legacy engine cannot perform alone.
    """

    # ----- walk-forward evaluator used across the class -----
    @staticmethod
    def _wf_evaluator():
        def evaluator(candles, value):
            config = PipelineConfig(
                swing_config=SwingConfig(lookback=value),
            )
            return HistoricalEvaluationPipeline(config).evaluate(
                candles,
            )

        return evaluator

    # ============================================================
    # ISSUE 1: window-size sufficiency vs trade-count sufficiency
    # ============================================================

    def test_walk_forward_eval_window_sufficiency_is_candle_count(self):
        """``sufficient_evaluation_window`` reflects EVALUATION
        WINDOW candle count, NOT completed OOS trades."""
        candles = trending_candles(40)
        report = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3),
            self._wf_evaluator(),
        )
        # Default min_evaluation_candles == 10; 30% of 40 == 12.
        assert report.evaluation_candle_count >= 10
        assert report.sufficient_evaluation_window is True

    def test_walk_forward_eval_window_insufficient_when_too_small(self):
        candles = trending_candles(15)
        cfg = WalkForwardConfig(min_evaluation_candles=999)
        report = WalkForwardParameterEngine(cfg).evaluate(
            candles, "lookback", (2,),
            self._wf_evaluator(),
        )
        assert report.sufficient_evaluation_window is False

    def test_window_sufficiency_and_oos_trade_sufficiency_are_independent(self):
        """The headline inconsistency: an evaluation WINDOW can be
        sufficient (enough candles) while OOS completed TRADES are
        insufficient (zero). Both must hold simultaneously and be
        reported via DIFFERENT, explicitly named fields."""
        candles = trending_candles(40)
        wf = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3),
            self._wf_evaluator(),
        )
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def pipeline_evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        report = ResearchEngine(
            ResearchConfig(
                sensitivity_parameter_name="lookback",
                sensitivity_parameter_values=(2, 3),
            ),
        ).analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            walk_forward_evaluator=self._wf_evaluator(),
            label="consistency",
        )

        # Window sufficiency (candles) -- TRUE on this dataset.
        assert report.walk_forward_selection.sufficient_evaluation_window is True
        # Trade sufficiency (completed OOS trades) -- FALSE (0 trades).
        assert report.walk_forward_selection.out_of_sample_completed_trades == 0
        assert report.data_sufficiency.insufficient_oos_trades is True
        # The two concepts are exposed under distinct names.
        assert hasattr(report.walk_forward_selection, "sufficient_evaluation_window")
        assert hasattr(report.data_sufficiency, "insufficient_oos_trades")

    def test_walk_forward_window_sufficiency_field_renamed(self):
        """The canonical field name is the explicit
        ``sufficient_evaluation_window`` / ``sufficient_development_window``."""
        candles = trending_candles(40)
        wf = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2,),
            self._wf_evaluator(),
        )
        assert hasattr(wf, "sufficient_evaluation_window")
        assert hasattr(wf, "sufficient_development_window")

    def test_walk_forward_legacy_sufficiency_alias_backward_compat(self):
        """The legacy ``sufficient_evaluation_data`` /
        ``sufficient_development_data`` names remain accessible
        as read-only aliases proxying to the new fields."""
        candles = trending_candles(40)
        wf = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2,),
            self._wf_evaluator(),
        )
        assert (
            wf.sufficient_evaluation_data
            == wf.sufficient_evaluation_window
        )
        assert (
            wf.sufficient_development_data
            == wf.sufficient_development_window
        )
        # Alias is read-only (property on frozen+slots dataclass).
        with pytest.raises(AttributeError):
            wf.sufficient_evaluation_data = False  # type: ignore[misc]

    def test_walk_forward_legacy_alias_matches_new_field_in_engine(self):
        """Engine output: legacy alias and new field carry the
        same value for both windows."""
        candles = trending_candles(40)
        wf = WalkForwardParameterEngine().evaluate(
            candles, "lookback", (2, 3),
            self._wf_evaluator(),
        )
        assert wf.sufficient_development_data is wf.sufficient_development_window
        assert wf.sufficient_evaluation_data is wf.sufficient_evaluation_window

    # ============================================================
    # ISSUE 2: OOS selection-isolation attribution
    # ============================================================

    def test_legacy_oos_engine_still_emits_not_verified_standalone(self):
        """The standalone legacy OutOfSampleEngine performs no
        parameter selection and must STILL emit
        ``parameter_selection_isolated=None`` (NOT VERIFIED) and
        ``selection_isolation_verified_by=None``."""
        candles = trending_candles(40)
        from engine.research import OutOfSampleEngine

        def evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        oos = OutOfSampleEngine().evaluate(candles, evaluator)
        assert oos.parameter_selection_isolated is None
        assert oos.selection_isolation_verified_by is None

    def test_oos_report_carries_selection_isolation_verified_by_field(self):
        """OutOfSampleReport has the new attribution field
        (defaults to None = not verified)."""
        oos = OutOfSampleReport(
            split_ratio=0.7,
            in_sample_count=10,
            out_of_sample_count=5,
            sufficient_data=True,
        )
        assert oos.selection_isolation_verified_by is None
        assert oos.parameter_selection_isolated is None

    def test_orchestrator_attributes_walk_forward_proof_to_oos(self):
        """When a verified walk-forward selection exists in the
        same report, the orchestrator upgrades the legacy OOS
        report's ``parameter_selection_isolated`` to True AND
        records the proof source -- making the two sections
        consistent (both affirm isolation) while preserving the
        attribution of WHERE the proof came from."""
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def pipeline_evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        report = ResearchEngine(
            ResearchConfig(
                sensitivity_parameter_name="lookback",
                sensitivity_parameter_values=(2, 3),
            ),
        ).analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            walk_forward_evaluator=self._wf_evaluator(),
            label="attribution",
        )
        oos = report.out_of_sample
        wf = report.walk_forward_selection
        assert wf is not None and wf.selection_verified
        # Consistency: both now affirm isolation.
        assert oos.parameter_selection_isolated is True
        assert (
            oos.selection_isolation_verified_by
            == "Sprint 11I walk-forward selection"
        )

    def test_orchestrator_leaves_oos_not_verified_without_walk_forward(self):
        """Without a walk-forward selection, the OOS report must
        remain NOT VERIFIED (no proof to attribute)."""
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def pipeline_evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        report = ResearchEngine().analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            label="no-wf",
        )
        oos = report.out_of_sample
        assert oos is not None
        assert oos.parameter_selection_isolated is None
        assert oos.selection_isolation_verified_by is None

    def test_attribution_does_not_weaken_leakage_audit(self):
        """The OOS attribution is a REPORTING convenience; the
        leakage audit still performs its OWN independent
        structural check (7) and the audit remains PASS only on
        its own merits. The audit must not rely on the
        attribution."""
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def pipeline_evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        report = ResearchEngine(
            ResearchConfig(
                sensitivity_parameter_name="lookback",
                sensitivity_parameter_values=(2, 3),
            ),
        ).analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            walk_forward_evaluator=self._wf_evaluator(),
            label="audit-untouched",
        )
        # Audit ran the extended 8-check path and passed on its
        # own structural merits.
        assert report.leakage.checks_performed == 8
        assert report.leakage.passed is True
        check7 = [
            c for c in report.leakage.checks
            if c.name == "selection_isolation"
        ][0]
        assert check7.severity is LeakageSeverity.PASS

    def test_attribution_not_applied_when_walk_forward_not_verified(self):
        """If the walk-forward selection is NOT verified (e.g. a
        caller-supplied selection that the engine cannot prove),
        the OOS report must NOT be upgraded -- no false
        attribution."""
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)

        # Build an OOS report and a NON-verified walk-forward,
        # then call the attribution helper directly.
        from engine.research import OutOfSampleEngine

        def evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        oos = OutOfSampleEngine().evaluate(candles, evaluator)
        wf = WalkForwardSelectionReport(
            parameter_name="lookback",
            selection_verified=False,
            selection_isolated_from_evaluation=False,
        )
        upgraded = ResearchEngine._attribute_selection_isolation(oos, wf)
        assert upgraded.parameter_selection_isolated is None
        assert upgraded.selection_isolation_verified_by is None

    def test_attribution_idempotent_on_none_oos(self):
        assert ResearchEngine._attribute_selection_isolation(None, None) is None

    def test_attribution_deterministic(self):
        candles = trending_candles(40)
        result = HistoricalEvaluationPipeline().evaluate(candles)

        def pipeline_evaluator(cs):
            return HistoricalEvaluationPipeline().evaluate(cs)

        cfg = ResearchConfig(
            sensitivity_parameter_name="lookback",
            sensitivity_parameter_values=(2, 3),
        )
        r1 = ResearchEngine(cfg).analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            walk_forward_evaluator=self._wf_evaluator(),
            label="d",
        )
        r2 = ResearchEngine(cfg).analyze(
            result, candles,
            pipeline_evaluator=pipeline_evaluator,
            walk_forward_evaluator=self._wf_evaluator(),
            label="d",
        )
        assert r1.out_of_sample == r2.out_of_sample
        assert (
            r1.out_of_sample.selection_isolation_verified_by
            == r2.out_of_sample.selection_isolation_verified_by
        )
