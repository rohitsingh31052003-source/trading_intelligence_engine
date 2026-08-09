"""
Parameter sensitivity engine (Sprint 11H).

The ``ParameterSensitivityEngine`` evaluates a supplied
parameter across multiple values and reports how stable
historical performance is across those values.

Design rules:

* Generic.
  The caller supplies the parameter name, the candidate values
  and an ``evaluator`` callable that, given one parameter
  value, returns the performance analytics (or a pipeline
  result) for that configuration. The engine performs no
  strategy-specific optimisation.

* No automatic overfitting.
  The engine never selects the highest-return parameter as
  "optimal". It reports descriptive stability metrics and
  explicitly labels any best historical value as descriptive,
  not predictive.

* Deterministic.
  Results are produced in the supplied parameter order.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, isinf
from typing import Any, Callable, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.research import (
    ParameterResult,
    ParameterSensitivityReport,
)


# A parameter evaluator takes a single parameter value and
# returns an object carrying completed-trade performance. It
# may return a ``PerformanceAnalytics`` directly, or a
# ``PipelineResult`` (the engine projects the latter via its
# embedded analytics).
ParameterEvaluator = Callable[[Any], Any]


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class SensitivityConfig:
    """
    Mutable configuration for ``ParameterSensitivityEngine``.

    Field semantics:

    min_configurations
        Minimum number of configurations required before
        ``sufficient_data`` is True.

    stability_epsilon
        Small constant added to the expectancy range when
        computing the stability ratio to avoid division by
        zero.
    """

    min_configurations: int = 2
    stability_epsilon: float = 1e-9


# ============================================================
# ENGINE
# ============================================================


class ParameterSensitivityEngine:
    """
    Evaluate a parameter across multiple values and report
    performance stability.

    Public API:

        analyze(
            parameter_name,
            parameter_values,
            evaluator,
        ) -> ParameterSensitivityReport
    """

    def __init__(
        self,
        config: SensitivityConfig | None = None,
    ) -> None:
        self.config = config or SensitivityConfig()
        self._performance_engine = PerformanceAnalyticsEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def analyze(
        self,
        parameter_name: str,
        parameter_values: Sequence[Any],
        evaluator: ParameterEvaluator,
    ) -> ParameterSensitivityReport:
        """
        Run the evaluator for each parameter value and collect
        a ``ParameterResult`` plus stability metrics.
        """

        values = list(parameter_values)

        results: list[ParameterResult] = []

        for value in values:
            raw = evaluator(value)
            performance = self._performance_of(raw)
            results.append(self._to_result(parameter_name, value, performance))

        return self._build_report(parameter_name, results)

    # ========================================================
    # REPORT BUILDING
    # ========================================================

    def _build_report(
        self,
        parameter_name: str,
        results: list[ParameterResult],
    ) -> ParameterSensitivityReport:
        if not results:
            return ParameterSensitivityReport(
                parameter_name=parameter_name,
                results=tuple(),
                sufficient_data=False,
            )

        expectancies = [r.expectancy for r in results]

        best = max(results, key=lambda r: r.expectancy)

        median_expectancy = self._median(expectancies)
        expectancy_range = max(expectancies) - min(expectancies)

        profitable = sum(1 for r in results if r.total_r > 0.0)

        stability_ratio = self._stability_ratio(
            median_expectancy,
            expectancy_range,
            len(results),
        )

        sufficient = len(results) >= self.config.min_configurations

        return ParameterSensitivityReport(
            parameter_name=parameter_name,
            results=tuple(results),
            best_value_by_expectancy=best.parameter_value,
            best_value_descriptive=True,
            median_expectancy=median_expectancy,
            expectancy_range=expectancy_range,
            profitable_configurations=profitable,
            stability_ratio=stability_ratio,
            sufficient_data=sufficient,
        )

    # ========================================================
    # PERFORMANCE PROJECTION
    # ========================================================

    def _performance_of(self, raw: Any) -> Any:
        """
        Project a raw evaluator result onto a
        ``PerformanceAnalytics``.

        The evaluator may return either a ``PerformanceAnalytics``
        directly or a ``PipelineResult`` (which carries an
        embedded ``performance``).
        """

        # Already a PerformanceAnalytics-like object.
        if hasattr(raw, "expectancy") and hasattr(raw, "completed_trades"):
            return raw

        # PipelineResult-like object.
        performance = getattr(raw, "performance", None)
        if performance is not None:
            return performance

        # Fall back to recomputing from validation results if
        # the object exposes them.
        validation_results = getattr(raw, "validation_results", None)
        if validation_results is not None:
            return self._performance_engine.analyze(validation_results)

        return self._performance_engine.analyze([])

    @staticmethod
    def _to_result(
        parameter_name: str,
        value: Any,
        performance: Any,
    ) -> ParameterResult:
        return ParameterResult(
            parameter_name=parameter_name,
            parameter_value=value,
            total_trades=getattr(performance, "total_results", 0),
            completed_trades=getattr(performance, "completed_trades", 0),
            win_rate=getattr(performance, "win_rate", 0.0),
            expectancy=getattr(performance, "expectancy", 0.0),
            profit_factor=getattr(performance, "profit_factor", 0.0),
            total_r=getattr(performance, "total_r", 0.0),
            max_drawdown=getattr(performance, "max_drawdown_r", 0.0),
        )

    # ========================================================
    # HELPERS
    # ========================================================

    def _median(self, values: list[float]) -> float:
        if not values:
            return 0.0

        ordered = sorted(values)
        n = len(ordered)
        mid = n // 2

        if n % 2 == 1:
            return ordered[mid]

        return (ordered[mid - 1] + ordered[mid]) / 2.0

    def _stability_ratio(
        self,
        median_expectancy: float,
        expectancy_range: float,
        count: int,
    ) -> float:
        if count < self.config.min_configurations:
            return 0.0

        denominator = expectancy_range + self.config.stability_epsilon

        if denominator <= self.config.stability_epsilon:
            # No variance at all: perfectly stable historically.
            return float("inf") if median_expectancy != 0.0 else 0.0

        ratio = median_expectancy / denominator

        if isinf(ratio) or not isfinite(ratio):
            return float("inf")

        return ratio
