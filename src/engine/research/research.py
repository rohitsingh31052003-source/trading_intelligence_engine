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

from dataclasses import dataclass, field
from math import isinf
from typing import Any, Mapping, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import PipelineResult
from engine.models.research import (
    LeakageCheckResult,
    MarketRegime,
    OutOfSampleReport,
    ParameterSensitivityReport,
    RegimeStatistics,
    ResearchReport,
    SegmentedPerformance,
    SegmentationDimension,
)
from engine.models.signal import SignalResult
from engine.models.validation import (
    ValidationResult,
)
from engine.research.leakage import (
    LeakageAuditEngine,
    LeakageAuditConfig,
)
from engine.research.out_of_sample import (
    OutOfSampleEngine,
    OutOfSampleConfig,
    PipelineEvaluator,
)
from engine.research.regime import MarketRegimeEngine, RegimeConfig
from engine.research.segmentation import (
    PerformanceSegmentationEngine,
    SegmentationConfig,
)
from engine.research.sensitivity import (
    ParameterEvaluator,
    ParameterSensitivityEngine,
    SensitivityConfig,
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
            label,
            metadata,
        ) -> ResearchReport
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

    # ========================================================
    # PUBLIC API
    # ========================================================

    def analyze(
        self,
        result: PipelineResult,
        candles: Sequence[OHLCVCandle] | None = None,
        pipeline_evaluator: PipelineEvaluator | None = None,
        parameter_evaluator: ParameterEvaluator | None = None,
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

        out_of_sample = self._out_of_sample(
            candle_list,
            pipeline_evaluator,
        )

        leakage = self._leakage_engine.audit(result, candle_list)

        conclusions = self._conclusions(
            overall=overall,
            regime_statistics=regime_statistics,
            segmentation=segmentation,
            sensitivity=sensitivity,
            out_of_sample=out_of_sample,
            leakage=leakage,
        )

        return ResearchReport(
            label=label,
            overall_performance=overall,
            regime_statistics=regime_statistics,
            segmentation=segmentation,
            parameter_sensitivity=sensitivity,
            out_of_sample=out_of_sample,
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

        return RegimeStatistics(
            regime=regime,
            total_results=performance.total_results,
            completed_trades=performance.completed_trades,
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
        )

    @staticmethod
    def _empty_regime_statistics(
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
    # CONCLUSIONS
    # ========================================================

    def _conclusions(
        self,
        overall: Any,
        regime_statistics: tuple[RegimeStatistics, ...],
        segmentation: SegmentedPerformance,
        sensitivity: ParameterSensitivityReport | None,
        out_of_sample: OutOfSampleReport | None,
        leakage: LeakageCheckResult | None,
    ) -> list[str]:
        conclusions: list[str] = []

        completed = getattr(overall, "completed_trades", 0)
        total_r = getattr(overall, "total_r", 0.0)

        if completed < self.config.min_trades_for_inference:
            conclusions.append(
                "Insufficient trades for reliable inference."
            )
        elif total_r > 0.0:
            conclusions.append(
                "Positive historical expectancy observed in this dataset."
            )
        elif total_r < 0.0:
            conclusions.append(
                "Negative historical expectancy observed in this dataset."
            )
        else:
            conclusions.append(
                "Neutral historical expectancy observed in this dataset."
            )

        # Regime conclusions.
        regimes_with_trades = [
            rs for rs in regime_statistics if rs.completed_trades > 0
        ]
        if len(regimes_with_trades) >= 2:
            conclusions.append(
                "Performance was evaluated across multiple market regimes."
            )
        elif regimes_with_trades:
            conclusions.append(
                "Performance was concentrated in a single market regime."
            )

        # Segmentation conclusion.
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

        # Sensitivity conclusion.
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
                    "Parameter sensitivity appears high."
                )
            else:
                conclusions.append(
                    "Parameter sensitivity appears moderate to stable."
                )
            conclusions.append(
                "Best historical parameter value is descriptive, "
                "not predictive."
            )

        # Out-of-sample conclusion.
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

        # Leakage conclusion.
        if leakage is not None:
            if leakage.passed:
                conclusions.append(
                    "No leakage violations were detected by the "
                    "implemented checks."
                )
            else:
                conclusions.append(
                    "Leakage violations were detected by the "
                    "implemented checks."
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
