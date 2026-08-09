"""
Parameter robustness engine (Sprint 11I).

The ``ParameterRobustnessEngine`` consumes a
``ParameterSensitivityReport`` (produced by the Sprint 11H
sensitivity engine) and answers a different question:

    Which configurations are ROBUST, as opposed to merely the
    DESCRIPTIVE BEST?

Design rules:

* Descriptive best != robust.
  The configuration with the highest historical expectancy is
  reported as ``descriptive_best`` but is NOT automatically
  robust. A configuration is robust only when it is both
  profitable AND close to the median expectancy (i.e. not an
  outlier).

* Transparent, configurable thresholds.
  The robustness band and minimum-trade threshold are
  configurable so callers can audit the decision boundary.

* Dependency detection.
  The engine flags when the entire sweep's positive result
  rests on a single configuration
  (``highly_dependent_on_single_config``).

* No recomputation of trading logic.
  The engine projects the existing ``ParameterResult`` values;
  it never re-runs the pipeline.

The engine is deterministic and stateless across calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, isinf
from typing import Any

from engine.models.research import (
    ConfigurationRobustness,
    ParameterResult,
    ParameterRobustnessReport,
    ParameterSensitivityReport,
)


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class RobustnessConfig:
    """
    Mutable configuration for ``ParameterRobustnessEngine``.

    Field semantics:

    min_completed_trades
        Minimum completed trades a configuration must have to be
        considered "profitable". Configurations with fewer
        trades are never robust regardless of expectancy.

    band_fraction
        Robustness band as a fraction of the median expectancy.
        A configuration is ``near_median`` when
        ``|expectancy - median| <= band`` where
        ``band = max(|median| * band_fraction, band_floor)``.

    band_floor
        Absolute minimum band so near-zero medians do not
        produce a zero-width band.

    min_configurations
        Minimum number of configurations required for
        ``sufficient_data`` to be True.
    """

    min_completed_trades: int = 3
    band_fraction: float = 0.25
    band_floor: float = 0.1
    min_configurations: int = 2


# ============================================================
# ENGINE
# ============================================================


class ParameterRobustnessEngine:
    """
    Analyse parameter robustness from a sensitivity report.

    Public API:

        analyze(
            sensitivity_report,
        ) -> ParameterRobustnessReport
    """

    def __init__(
        self,
        config: RobustnessConfig | None = None,
    ) -> None:
        self.config = config or RobustnessConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def analyze(
        self,
        sensitivity_report: ParameterSensitivityReport,
    ) -> ParameterRobustnessReport:
        """
        Build a ``ParameterRobustnessReport`` from a sensitivity
        report.
        """

        results = list(sensitivity_report.results)

        if not results:
            return ParameterRobustnessReport(
                parameter_name=sensitivity_report.parameter_name,
                configurations=tuple(),
                sufficient_data=False,
                robust=False,
            )

        expectancies = [
            self._finite(r.expectancy) for r in results
        ]
        median_expectancy = self._median(expectancies)
        band = self._band(median_expectancy)

        configs: list[ConfigurationRobustness] = []
        for result in results:
            expectancy = self._finite(result.expectancy)
            total_r = self._finite(result.total_r)
            completed = result.completed_trades

            profitable = (
                completed >= self.config.min_completed_trades
                and total_r > 0.0
            )
            near_median = abs(expectancy - median_expectancy) <= band
            stable = profitable and near_median

            configs.append(
                ConfigurationRobustness(
                    parameter_value=result.parameter_value,
                    expectancy=expectancy,
                    total_r=total_r,
                    completed_trades=completed,
                    profitable=profitable,
                    near_median=near_median,
                    stable=stable,
                )
            )

        # Descriptive best = highest expectancy (mirrors the
        # sensitivity report's own descriptive best).
        descriptive_best = sensitivity_report.best_value_by_expectancy

        best_config = self._config_for(
            configs, descriptive_best
        )
        descriptive_best_is_robust = (
            best_config.stable if best_config is not None else False
        )

        robust_values = tuple(
            c.parameter_value for c in configs if c.stable
        )
        unstable_values = tuple(
            c.parameter_value for c in configs if not c.stable
        )

        profitable_count = sum(1 for c in configs if c.profitable)
        robust_count = len(robust_values)

        sufficient = len(results) >= self.config.min_configurations

        # Highly dependent on a single configuration when:
        # - multiple configs exist, but only ONE is profitable, OR
        # - multiple configs exist, but only ONE is robust while
        #   others are profitable-but-unstable.
        highly_dependent = False
        if len(results) > 1:
            if profitable_count == 1:
                highly_dependent = True
            elif robust_count == 1 and profitable_count > 1:
                highly_dependent = True

        robust = sufficient and robust_count >= 1

        return ParameterRobustnessReport(
            parameter_name=sensitivity_report.parameter_name,
            configurations=tuple(configs),
            descriptive_best=descriptive_best,
            descriptive_best_is_robust=descriptive_best_is_robust,
            robust_configurations=robust_values,
            unstable_configurations=unstable_values,
            median_expectancy=median_expectancy,
            expectancy_band=band,
            highly_dependent_on_single_config=highly_dependent,
            sufficient_data=sufficient,
            robust=robust,
        )

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _config_for(
        configs: list[ConfigurationRobustness],
        value: Any,
    ) -> ConfigurationRobustness | None:
        for c in configs:
            if c.parameter_value == value:
                return c
        return None

    def _band(self, median_expectancy: float) -> float:
        scaled = abs(median_expectancy) * self.config.band_fraction
        return max(scaled, self.config.band_floor)

    @staticmethod
    def _median(values: list[float]) -> float:
        if not values:
            return 0.0

        ordered = sorted(values)
        n = len(ordered)
        mid = n // 2

        if n % 2 == 1:
            return ordered[mid]

        return (ordered[mid - 1] + ordered[mid]) / 2.0

    @staticmethod
    def _finite(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0

        if isinf(numeric) or not isfinite(numeric):
            return 0.0

        return numeric


# ============================================================
# CONVENIENCE: build from raw ParameterResult list
# ============================================================


def build_sensitivity_for_robustness(
    parameter_name: str,
    results: list[ParameterResult],
) -> ParameterSensitivityReport:
    """
    Build a minimal ``ParameterSensitivityReport`` from a list of
    ``ParameterResult`` so the robustness engine can be used
    standalone (without running the full sensitivity engine).

    The stability metrics are computed with the same descriptive
    semantics as the sensitivity engine. This is a convenience
    helper; callers that already have a sensitivity report
    should pass it directly to ``analyze``.
    """

    if not results:
        return ParameterSensitivityReport(
            parameter_name=parameter_name,
            results=tuple(),
            sufficient_data=False,
        )

    expectancies = [r.expectancy for r in results]
    best = max(results, key=lambda r: r.expectancy)

    ordered = sorted(expectancies)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0

    expectancy_range = max(expectancies) - min(expectancies)
    profitable = sum(1 for r in results if r.total_r > 0.0)

    return ParameterSensitivityReport(
        parameter_name=parameter_name,
        results=tuple(results),
        best_value_by_expectancy=best.parameter_value,
        best_value_descriptive=True,
        median_expectancy=median,
        expectancy_range=expectancy_range,
        profitable_configurations=profitable,
        stability_ratio=0.0,
        sufficient_data=len(results) >= 2,
    )
