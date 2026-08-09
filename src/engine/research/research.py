"""
Research orchestrator engine (Sprint 11H).

The ``ResearchEngine`` orchestrates the Sprint 11H research
sub-engines into a single ``ResearchReport``:

1. regime classification
2. performance segmentation
3. parameter sensitivity
4. out-of-sample evaluation
5. leakage audit

Design rules:

* No duplication of core trading logic.
  The engine orchestrates existing engines (pipeline,
  performance analytics, regime, segmentation, sensitivity,
  out-of-sample, leakage). It does NOT re-implement any
  strategy logic.

* Descriptive conclusions only.
  The engine never claims the strategy "is profitable". It
  reports what was observed in the supplied dataset.

* Deterministic.
  Identical inputs always produce identical reports.

* Graceful.
  Missing / empty inputs yield a report with empty views and
  descriptive conclusions rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isinf
from typing import Any, Mapping, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import PipelineResult
from engine.models.research import (
    DataSufficiencyReport,
    LeakageCheckResult,
    MarketRegime,
    OutOfSampleReport,
    ParameterRobustnessReport,
    ParameterSensitivityReport,
    RegimeStatistics,
    ResearchReport,
    SegmentedPerformance,
    SegmentationDimension,
    WalkForwardSelectionReport,
)
from engine.models.signal import SignalResult
from engine.models.validation import (
    ValidationResult,
)
from engine.research.leakage import (
    LeakageAuditConfig,
    LeakageAuditContext,
    LeakageAuditEngine,
)
from engine.research.out_of_sample import (
    OutOfSampleConfig,
    OutOfSampleEngine,
    PipelineEvaluator,
)
from engine.research.regime import MarketRegimeEngine, RegimeConfig
from engine.research.robustness import (
    ParameterRobustnessEngine,
    RobustnessConfig,
)
from engine.research.segmentation import (
    PerformanceSegmentationEngine,
    SegmentationConfig,
)
from engine.research.sensitivity import (
    ParameterEvaluator,
    ParameterSensitivityEngine,
    SensitivityConfig,
)
from engine.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardEvaluator,
    WalkForwardParameterEngine,
)


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class ResearchConfig:
    """
    Mutable configuration for ``ResearchEngine``.

    Bundles the sub-engine configurations and the
    parameter-sensitivity sweep the orchestrator should run.

    Sprint 11I additions:

    robustness
        Configuration for the ``ParameterRobustnessEngine``.

    walk_forward
        Configuration for the ``WalkForwardParameterEngine``.
        Walk-forward analysis runs only when a
        ``walk_forward_evaluator`` is supplied to
        ``ResearchEngine.analyze``.

    min_regime_observations
        Minimum completed trades a regime must have to be
        considered "sufficient" for regime-level inference.

    min_oos_trades
        Minimum completed trades in the out-of-sample window
        for OOS inference.

    min_parameter_configurations
        Minimum parameter configurations for parameter
        inference.
    """

    regime: RegimeConfig = field(default_factory=RegimeConfig)
    segmentation: SegmentationConfig = field(
        default_factory=SegmentationConfig,
    )
    sensitivity: SensitivityConfig = field(
        default_factory=SensitivityConfig,
    )
    out_of_sample: OutOfSampleConfig = field(
        default_factory=OutOfSampleConfig,
    )
    leakage: LeakageAuditConfig = field(
        default_factory=LeakageAuditConfig,
    )
    robustness: RobustnessConfig = field(
        default_factory=RobustnessConfig,
    )
    walk_forward: WalkForwardConfig = field(
        default_factory=WalkForwardConfig,
    )

    # Which segmentation dimension to report by default.
    default_segmentation_dimension: SegmentationDimension = (
        SegmentationDimension.DIRECTION
    )

    # Optional parameter sensitivity sweep. When None, no
    # sensitivity analysis is run.
    sensitivity_parameter_name: str | None = None
    sensitivity_parameter_values: tuple[Any, ...] = field(
        default_factory=tuple,
    )

    # Minimum completed trades for any "sufficient data"
    # conclusion.
    min_trades_for_inference: int = 5

    # Sprint 11I data-sufficiency thresholds.
    min_regime_observations: int = 3
    min_oos_trades: int = 3
    min_parameter_configurations: int = 2


# ============================================================
# ENGINE
# ============================================================


class ResearchEngine:
    """
    Orchestrate the research sub-engines into a
    ``ResearchReport``.

    Public API:

        analyze(
            result,
            candles,
            pipeline_evaluator=None,
            parameter_evaluator=None,
            walk_forward_evaluator=None,
            label,
            metadata,
        ) -> ResearchReport

    Sprint 11I additions:

    * ``walk_forward_evaluator`` -- when supplied (together with
      the sensitivity sweep config), the engine runs the
      ``WalkForwardParameterEngine`` to produce an explicit
      development / evaluation separation and a
      ``WalkForwardSelectionReport``. The robustness engine
      then turns the sensitivity report into a
      ``ParameterRobustnessReport``.

    * The leakage audit receives a ``LeakageAuditContext`` so it
      can structurally verify the development / evaluation
      separation and parameter-selection isolation (checks 6-8).

    * A ``DataSufficiencyReport`` is built from the available
      evidence sources so the report never implies statistical
      confidence from tiny samples.

    All new arguments are optional and backward-compatible.
    """

    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()
        self._performance_engine = PerformanceAnalyticsEngine()
        self._regime_engine = MarketRegimeEngine(self.config.regime)
        self._segmentation_engine = PerformanceSegmentationEngine(
            self.config.segmentation,
        )
        self._sensitivity_engine = ParameterSensitivityEngine(
            self.config.sensitivity,
        )
        self._out_of_sample_engine = OutOfSampleEngine(
            self.config.out_of_sample,
        )
        self._leakage_engine = LeakageAuditEngine(self.config.leakage)
        self._robustness_engine = ParameterRobustnessEngine(
            self.config.robustness,
        )
        self._walk_forward_engine = WalkForwardParameterEngine(
            self.config.walk_forward,
        )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def analyze(
        self,
        result: PipelineResult,
        candles: Sequence[OHLCVCandle] | None = None,
        pipeline_evaluator: PipelineEvaluator | None = None,
        parameter_evaluator: ParameterEvaluator | None = None,
        walk_forward_evaluator: WalkForwardEvaluator | None = None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> ResearchReport:
        """
        Build a full ``ResearchReport`` from a pipeline result.
        """

        candle_list = list(candles) if candles is not None else []

        overall = result.performance or self._performance_engine.analyze([])

        regime_statistics = self._regime_statistics(
            result,
            candle_list,
        )

        segmentation = self._segmentation(
            result,
            candle_list,
        )

        sensitivity = self._sensitivity(parameter_evaluator)

        robustness = self._robustness(sensitivity)

        walk_forward = self._walk_forward(
            candle_list,
            walk_forward_evaluator,
        )

        out_of_sample = self._out_of_sample(
            candle_list,
            pipeline_evaluator,
        )

        # Sprint 11I consistency: when a walk-forward selection
        # supplies a STRUCTURAL proof that parameter selection
        # was isolated from the evaluation window, attribute that
        # proof to the legacy OOS report. The legacy
        # ``OutOfSampleEngine`` cannot prove this itself (it
        # performs no parameter selection), so without this the
        # OOS section would read "NOT VERIFIED" even though a
        # verified walk-forward selection exists in the same
        # report. We do NOT weaken the leakage audit: the audit
        # still performs its own structural check (7) and the
        # legacy engine still emits ``None`` when run standalone.
        out_of_sample = self._attribute_selection_isolation(
            out_of_sample,
            walk_forward,
        )

        leakage_context = self._leakage_context(walk_forward)
        leakage = self._leakage_engine.audit(
            result,
            candle_list,
            context=leakage_context,
        )

        data_sufficiency = self._data_sufficiency(
            overall=overall,
            regime_statistics=regime_statistics,
            out_of_sample=out_of_sample,
            walk_forward=walk_forward,
            sensitivity=sensitivity,
        )

        conclusions = self._conclusions(
            overall=overall,
            regime_statistics=regime_statistics,
            segmentation=segmentation,
            sensitivity=sensitivity,
            robustness=robustness,
            walk_forward=walk_forward,
            out_of_sample=out_of_sample,
            data_sufficiency=data_sufficiency,
            leakage=leakage,
        )

        return ResearchReport(
            label=label,
            overall_performance=overall,
            regime_statistics=regime_statistics,
            segmentation=segmentation,
            parameter_sensitivity=sensitivity,
            parameter_robustness=robustness,
            walk_forward_selection=walk_forward,
            out_of_sample=out_of_sample,
            data_sufficiency=data_sufficiency,
            leakage=leakage,
            conclusions=tuple(conclusions),
            result=result,
            metadata=dict(metadata) if metadata is not None else {},
        )

    # ========================================================
    # REGIME STATISTICS
    # ========================================================

    def _regime_statistics(
        self,
        result: PipelineResult,
        candles: list[OHLCVCandle],
    ) -> tuple[RegimeStatistics, ...]:
        """
        Group validation results by the market regime at their
        generation point.
        """

        pairs, indices = self._validated_pairs(result)

        if not pairs:
            return tuple(
                self._empty_regime_statistics(regime)
                for regime in MarketRegime
            )

        # Classify regime per validated pair.
        groups: dict[MarketRegime, list[ValidationResult]] = {
            regime: [] for regime in MarketRegime
        }

        for (signal, validation), idx in zip(pairs, indices):
            regime = self._regime_at(candles, idx)
            groups[regime].append(validation)

        return tuple(
            self._regime_statistics_for(regime, results)
            for regime in MarketRegime
            for results in [groups.get(regime, [])]
        )

    def _regime_at(
        self,
        candles: list[OHLCVCandle],
        index: int,
    ) -> MarketRegime:
        if not candles or index < 0 or index >= len(candles):
            return MarketRegime.UNKNOWN

        visible = candles[: index + 1]
        return self._regime_engine.classify(visible)

    def _regime_statistics_for(
        self,
        regime: MarketRegime,
        results: list[ValidationResult],
    ) -> RegimeStatistics:
        performance = self._performance_engine.analyze(results)

        completed = performance.completed_trades

        return RegimeStatistics(
            regime=regime,
            total_results=performance.total_results,
            completed_trades=completed,
            wins=performance.wins,
            losses=performance.losses,
            ambiguous=performance.ambiguous,
            expired=performance.expired,
            not_triggered=performance.not_triggered,
            win_rate=performance.win_rate,
            total_r=performance.total_r,
            average_r=performance.average_r,
            expectancy=performance.expectancy,
            profit_factor=performance.profit_factor,
            max_drawdown=performance.max_drawdown_r,
            average_mfe=performance.average_mfe_r,
            average_mae=performance.average_mae_r,
            sufficient_observations=(
                completed >= self.config.min_regime_observations
            ),
            min_observations_for_inference=self.config.min_regime_observations,
        )

    def _empty_regime_statistics(
        self,
        regime: MarketRegime,
    ) -> RegimeStatistics:
        return RegimeStatistics(
            regime=regime,
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
            sufficient_observations=False,
            min_observations_for_inference=self.config.min_regime_observations,
        )

    # ========================================================
    # SEGMENTATION
    # ========================================================

    def _segmentation(
        self,
        result: PipelineResult,
        candles: list[OHLCVCandle],
    ) -> SegmentedPerformance:
        pairs, indices = self._validated_pairs(result)

        return self._segmentation_engine.segment(
            pairs,
            self.config.default_segmentation_dimension,
            candles=candles,
            evaluation_indices=indices,
        )

    # ========================================================
    # SENSITIVITY
    # ========================================================

    def _sensitivity(
        self,
        parameter_evaluator: ParameterEvaluator | None,
    ) -> ParameterSensitivityReport | None:
        name = self.config.sensitivity_parameter_name
        values = self.config.sensitivity_parameter_values

        if (
            parameter_evaluator is None
            or name is None
            or not values
        ):
            return None

        return self._sensitivity_engine.analyze(
            parameter_name=name,
            parameter_values=values,
            evaluator=parameter_evaluator,
        )

    # ========================================================
    # ROBUSTNESS (Sprint 11I)
    # ========================================================

    def _robustness(
        self,
        sensitivity: ParameterSensitivityReport | None,
    ) -> ParameterRobustnessReport | None:
        if sensitivity is None or sensitivity.is_empty:
            return None

        return self._robustness_engine.analyze(sensitivity)

    # ========================================================
    # WALK-FORWARD SELECTION (Sprint 11I)
    # ========================================================

    def _walk_forward(
        self,
        candles: list[OHLCVCandle],
        walk_forward_evaluator: WalkForwardEvaluator | None,
    ) -> WalkForwardSelectionReport | None:
        name = self.config.sensitivity_parameter_name
        values = self.config.sensitivity_parameter_values

        if (
            walk_forward_evaluator is None
            or name is None
            or not values
            or not candles
        ):
            return None

        return self._walk_forward_engine.evaluate(
            candles=candles,
            parameter_name=name,
            parameter_values=values,
            evaluator=walk_forward_evaluator,
        )

    # ========================================================
    # OUT-OF-SAMPLE
    # ========================================================

    def _out_of_sample(
        self,
        candles: list[OHLCVCandle],
        pipeline_evaluator: PipelineEvaluator | None,
    ) -> OutOfSampleReport | None:
        if pipeline_evaluator is None or not candles:
            return None

        return self._out_of_sample_engine.evaluate(
            candles,
            pipeline_evaluator,
        )

    # ========================================================
    # SELECTION-ISOLATION ATTRIBUTION (Sprint 11I consistency)
    # ========================================================

    @staticmethod
    def _attribute_selection_isolation(
        out_of_sample: OutOfSampleReport | None,
        walk_forward: WalkForwardSelectionReport | None,
    ) -> OutOfSampleReport | None:
        """
        Make the legacy OOS report consistent with the Sprint 11I
        walk-forward selection regarding parameter-selection
        isolation.

        The legacy ``OutOfSampleEngine`` performs no parameter
        selection and therefore cannot independently prove
        isolation (it emits ``parameter_selection_isolated=None``
        = NOT VERIFIED). When a walk-forward selection in the
        SAME report supplies a structural proof
        (``selection_verified`` AND
        ``selection_isolated_from_evaluation``), attribute that
        proof to the OOS report: set
        ``parameter_selection_isolated=True`` and record the
        proof source in ``selection_isolation_verified_by``.

        This NEVER weakens the leakage audit (which performs its
        own independent structural check) and NEVER claims
        verification the orchestrator does not have. When no
        walk-forward proof is available, the OOS report is
        returned unchanged (still NOT VERIFIED).
        """

        if out_of_sample is None:
            return None

        if walk_forward is None:
            return out_of_sample

        if not (
            walk_forward.selection_verified
            and walk_forward.selection_isolated_from_evaluation
        ):
            return out_of_sample

        return replace(
            out_of_sample,
            parameter_selection_isolated=True,
            selection_isolation_verified_by=(
                "Sprint 11I walk-forward selection"
            ),
        )

    # ========================================================
    # LEAKAGE CONTEXT (Sprint 11I)
    # ========================================================

    @staticmethod
    def _leakage_context(
        walk_forward: WalkForwardSelectionReport | None,
    ) -> LeakageAuditContext:
        if walk_forward is None:
            return LeakageAuditContext()

        return LeakageAuditContext(
            walk_forward_selection=walk_forward,
            development_window=walk_forward.development_window,
            evaluation_window=walk_forward.evaluation_window,
        )

    # ========================================================
    # DATA SUFFICIENCY (Sprint 11I)
    # ========================================================

    def _data_sufficiency(
        self,
        overall: Any,
        regime_statistics: tuple[RegimeStatistics, ...],
        out_of_sample: OutOfSampleReport | None,
        walk_forward: WalkForwardSelectionReport | None,
        sensitivity: ParameterSensitivityReport | None,
    ) -> DataSufficiencyReport:
        completed = getattr(overall, "completed_trades", 0)
        min_trades = self.config.min_trades_for_inference

        sufficient_trades = completed >= min_trades
        insufficient_trades = not sufficient_trades

        # Regime sufficiency: a regime with ANY results but fewer
        # than the minimum completed trades is insufficient.
        # Zero-trade regimes are NOT counted as insufficient
        # samples here (they are unobserved, not under-sampled);
        # they are surfaced via ``has_no_completed_trades``.
        regimes_with_trades = [
            rs for rs in regime_statistics if rs.completed_trades > 0
        ]
        regimes_sufficient = [
            rs for rs in regimes_with_trades
            if rs.completed_trades >= self.config.min_regime_observations
        ]
        insufficient_regime_samples = any(
            0 < rs.completed_trades < self.config.min_regime_observations
            for rs in regime_statistics
        )

        # OOS trades.
        if walk_forward is not None:
            oos_completed = walk_forward.out_of_sample_completed_trades
            oos_performed = True
        elif out_of_sample is not None:
            oos_perf = out_of_sample.out_of_sample_performance
            oos_completed = getattr(oos_perf, "completed_trades", 0)
            oos_performed = True
        else:
            oos_completed = 0
            oos_performed = False

        insufficient_oos_trades = (
            oos_performed and oos_completed < self.config.min_oos_trades
        )

        # Parameter observations.
        if sensitivity is not None:
            parameter_configurations = sensitivity.configuration_count
            parameter_performed = True
        else:
            parameter_configurations = 0
            parameter_performed = False

        insufficient_parameter_observations = (
            parameter_performed
            and parameter_configurations
            < self.config.min_parameter_configurations
        )

        summary_parts: list[str] = []
        if sufficient_trades:
            summary_parts.append(
                f"{completed} completed trades (>= {min_trades} threshold)"
            )
        else:
            summary_parts.append(
                f"{completed} completed trades (< {min_trades} threshold)"
            )

        if insufficient_regime_samples:
            summary_parts.append(
                "some observed regimes have insufficient trades"
            )

        if insufficient_oos_trades:
            summary_parts.append(
                f"OOS trades {oos_completed} (< "
                f"{self.config.min_oos_trades} threshold)"
            )

        if insufficient_parameter_observations:
            summary_parts.append(
                f"{parameter_configurations} parameter configurations "
                f"(< {self.config.min_parameter_configurations} threshold)"
            )

        return DataSufficiencyReport(
            completed_trades=completed,
            min_trades_for_inference=min_trades,
            sufficient_trades=sufficient_trades,
            insufficient_trades=insufficient_trades,
            insufficient_regime_samples=insufficient_regime_samples,
            insufficient_oos_trades=insufficient_oos_trades,
            insufficient_parameter_observations=(
                insufficient_parameter_observations
            ),
            min_regime_observations=self.config.min_regime_observations,
            min_oos_trades=self.config.min_oos_trades,
            min_parameter_configurations=(
                self.config.min_parameter_configurations
            ),
            oos_completed_trades=oos_completed,
            parameter_configurations=parameter_configurations,
            regimes_with_trades=len(regimes_with_trades),
            regimes_sufficient=len(regimes_sufficient),
            summary="; ".join(summary_parts),
        )

    # ========================================================
    # CONCLUSIONS
    # ========================================================

    def _conclusions(
        self,
        overall: Any,
        regime_statistics: tuple[RegimeStatistics, ...],
        segmentation: SegmentedPerformance,
        sensitivity: ParameterSensitivityReport | None,
        robustness: ParameterRobustnessReport | None,
        walk_forward: WalkForwardSelectionReport | None,
        out_of_sample: OutOfSampleReport | None,
        data_sufficiency: DataSufficiencyReport | None,
        leakage: LeakageCheckResult | None,
    ) -> list[str]:
        conclusions: list[str] = []

        # ----- Overall (descriptive) -----
        completed = getattr(overall, "completed_trades", 0)
        total_r = getattr(overall, "total_r", 0.0)

        if completed < self.config.min_trades_for_inference:
            conclusions.append(
                "Insufficient trades for reliable inference."
            )
        elif total_r > 0.0:
            conclusions.append(
                "Positive historical expectancy observed in this "
                "dataset (descriptive, not predictive)."
            )
        elif total_r < 0.0:
            conclusions.append(
                "Negative historical expectancy observed in this "
                "dataset (descriptive, not predictive)."
            )
        else:
            conclusions.append(
                "Neutral historical expectancy observed in this "
                "dataset (descriptive, not predictive)."
            )

        # ----- Regime robustness -----
        regimes_with_trades = [
            rs for rs in regime_statistics if rs.completed_trades > 0
        ]
        zero_trade_regimes = [
            rs for rs in regime_statistics
            if rs.total_results == 0
        ]
        if len(regimes_with_trades) >= 2:
            conclusions.append(
                "Performance was evaluated across multiple market regimes."
            )
        elif regimes_with_trades:
            conclusions.append(
                "Performance was concentrated in a single market regime."
            )
        if any(
            0 < rs.completed_trades < self.config.min_regime_observations
            for rs in regime_statistics
        ):
            conclusions.append(
                "Some observed regimes have insufficient trades for "
                "regime-level inference."
            )
        if zero_trade_regimes and regimes_with_trades:
            conclusions.append(
                "Zero-trade regimes are reported as unobserved, not as "
                "zero performance."
            )

        # ----- Segmentation -----
        if segmentation and not segmentation.is_empty:
            directional = any(
                s.segment_label in ("LONG", "SHORT")
                and s.completed_trades > 0
                for s in segmentation.segments
            )
            if directional:
                conclusions.append(
                    "Directional segmentation is available for inspection."
                )

        # ----- Sensitivity (descriptive) -----
        if sensitivity is not None and not sensitivity.is_empty:
            if not sensitivity.sufficient_data:
                conclusions.append(
                    "Parameter sensitivity could not be assessed "
                    "due to insufficient configurations."
                )
            elif sensitivity.stability_ratio < 0.5 and not self._is_inf(
                sensitivity.stability_ratio
            ):
                conclusions.append(
                    "Parameter sensitivity appears high (descriptive)."
                )
            else:
                conclusions.append(
                    "Parameter sensitivity appears moderate to stable "
                    "(descriptive)."
                )
            conclusions.append(
                "Best historical parameter value is descriptive, "
                "not predictive."
            )

        # ----- Robustness (Sprint 11I) -----
        if robustness is not None and not robustness.is_empty:
            if not robustness.sufficient_data:
                conclusions.append(
                    "Parameter robustness could not be assessed due to "
                    "insufficient configurations."
                )
            elif robustness.robust:
                conclusions.append(
                    "At least one robust parameter configuration was "
                    "identified (descriptive)."
                )
            else:
                conclusions.append(
                    "No robust parameter configuration was identified."
                )

            if robustness.highly_dependent_on_single_config:
                conclusions.append(
                    "Results appear highly dependent on a single "
                    "parameter configuration."
                )

            if (
                robustness.descriptive_best is not None
                and not robustness.descriptive_best_is_robust
            ):
                conclusions.append(
                    "The descriptive best configuration is NOT robust; "
                    "treat it as an outlier, not a deployable choice."
                )

        # ----- Walk-forward selection (validated) -----
        if walk_forward is not None and walk_forward.has_selected:
            conclusions.append(
                "Parameter selection was performed on development data "
                "only; the evaluation window was held out (validated by "
                "construction)."
            )
            if not walk_forward.sufficient_development_window:
                conclusions.append(
                    "Development window had insufficient trades for a "
                    "reliable selection."
                )
            if walk_forward.has_out_of_sample_trades:
                conclusions.append(
                    "Selected configuration was evaluated out-of-sample."
                )
            else:
                conclusions.append(
                    "Selected configuration produced no out-of-sample "
                    "trades; OOS evidence is insufficient."
                )

        # ----- Out-of-sample -----
        if out_of_sample is not None:
            if not out_of_sample.sufficient_data:
                conclusions.append(
                    "Out-of-sample evaluation lacked sufficient data."
                )
            elif out_of_sample.expectancy_degradation < 0.0:
                conclusions.append(
                    "Out-of-sample performance degraded relative to "
                    "development performance."
                )
            elif out_of_sample.expectancy_degradation > 0.0:
                conclusions.append(
                    "Out-of-sample performance improved relative to "
                    "development performance."
                )
            else:
                conclusions.append(
                    "Out-of-sample performance was stable relative to "
                    "development performance."
                )

        # ----- Data sufficiency -----
        if data_sufficiency is not None:
            if not data_sufficiency.sufficient_for_inference:
                conclusions.append(
                    "Data sufficiency: at least one evidence source is "
                    "insufficient; do not draw confident conclusions."
                )
            else:
                conclusions.append(
                    "Data sufficiency: all evidence sources meet the "
                    "configured minimum thresholds."
                )

        # ----- Leakage -----
        if leakage is not None:
            if leakage.has_failures:
                conclusions.append(
                    "Leakage violations were detected by the "
                    "implemented checks."
                )
            else:
                conclusions.append(
                    "No leakage violations were detected by the "
                    "implemented checks."
                )
            if leakage.has_not_verified:
                conclusions.append(
                    "Some leakage properties could not be verified and "
                    "are reported as NOT VERIFIED, not PASS."
                )

        return conclusions

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _validated_pairs(
        result: PipelineResult,
    ) -> tuple[
        list[tuple[SignalResult, ValidationResult]],
        list[int],
    ]:
        """
        Extract ``(signal, validation)`` pairs for validated
        evaluation points, together with their generation
        indices, in chronological order.

        ``result.signals`` and ``result.validation_results`` are
        aligned tuples, but the generation index is carried on
        the evaluation points. We pair via the validated points
        to preserve the index.
        """

        pairs: list[tuple[SignalResult, ValidationResult]] = []
        indices: list[int] = []

        for point in result.evaluation_points_sequence:
            if point.validated and point.signal is not None:
                pairs.append((point.signal, point.validation))
                indices.append(point.index)

        return pairs, indices

    @staticmethod
    def _is_inf(value: float) -> bool:
        try:
            return isinf(float(value))
        except (TypeError, ValueError):
            return False
