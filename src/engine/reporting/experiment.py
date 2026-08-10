"""
Experiment report formatter (Sprint 11J).

Produces a deterministic, human-readable experiment report from
an ``ExperimentResult``. The formatter reads the reused
``EvaluationReport`` and ``ResearchReport`` and presents the
required sections:

    Experiment ID
    Experiment Label
    Dataset
    Configuration
    Pipeline Summary
    Research Summary
    Robustness Summary
    Walk-Forward Summary
    OOS Summary
    Data Sufficiency
    Leakage Audit
    Reproducibility
    Research Conclusion

The formatter makes NO claims beyond what the underlying
reports support. Insufficient evidence is reported explicitly.
No print() inside the formatter; it returns a string.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any

from engine.models.experiment import ExperimentResult


# ============================================================
# FORMATTER
# ============================================================


class ExperimentReportFormatter:
    """
    Format an ``ExperimentResult`` into a readable report string.

    Public API:

        format(result) -> str

    The formatter is stateless and deterministic.
    """

    SEPARATOR = "=" * 60
    SUB_SEPARATOR = "-" * 60

    # ========================================================
    # PUBLIC API
    # ========================================================

    def format(self, result: ExperimentResult) -> str:
        """
        Produce the full experiment report as a string.
        """

        lines: list[str] = []

        lines.append(self.SEPARATOR)
        lines.append("Research Experiment Report (Sprint 11J)")
        lines.append(self.SEPARATOR)

        self._identity(result, lines)
        self._dataset(result, lines)
        self._configuration(result, lines)
        self._pipeline_summary(result, lines)
        self._research_summary(result, lines)
        self._robustness_summary(result, lines)
        self._walk_forward_summary(result, lines)
        self._oos_summary(result, lines)
        self._data_sufficiency(result, lines)
        self._leakage_audit(result, lines)
        self._reproducibility(result, lines)
        self._research_conclusion(result, lines)

        lines.append("")
        lines.append(self.SEPARATOR)

        return "\n".join(lines)

    # ========================================================
    # SECTIONS
    # ========================================================

    def _identity(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Experiment Identity")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Experiment ID        : {result.experiment_id}")
        lines.append(f"Experiment Label     : {result.label}")
        lines.append(
            f"Evidence Status      : {result.summary.evidence_status.value}"
        )

    def _dataset(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Dataset")
        lines.append(self.SUB_SEPARATOR)
        lines.append(f"Dataset Name         : {result.dataset.name}")
        lines.append(f"Dataset Size         : {result.dataset_size}")
        hash_label = (
            result.dataset.content_hash
            if result.dataset.content_hash
            else "N/A"
        )
        lines.append(f"Dataset Content Hash : {hash_label}")

    def _configuration(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Configuration")
        lines.append(self.SUB_SEPARATOR)

        config = result.config

        lines.append(f"Configuration Hash   : {config.configuration_hash}")
        lines.append(
            f"Pipeline min_history : {config.pipeline.min_history}"
        )
        lines.append(
            f"Swing lookback       : {config.pipeline.swing_config.lookback}"
        )

        evaluation = config.evaluation
        lines.append(
            f"Run OOS              : {'YES' if evaluation.run_out_of_sample else 'NO'}"
        )
        lines.append(
            f"Run Walk-Forward     : {'YES' if evaluation.run_walk_forward else 'NO'}"
        )
        lines.append(
            f"Run Sensitivity      : {'YES' if evaluation.run_sensitivity else 'NO'}"
        )

        if evaluation.parameter_name is not None:
            lines.append(
                f"Sweep Parameter      : {evaluation.parameter_name}"
            )
            lines.append(
                f"Sweep Values         : {list(evaluation.parameter_values)}"
            )

        if config.strategy_parameters:
            lines.append("Strategy Parameters  :")
            for key, value in config.strategy_parameters.items():
                lines.append(f"  {key:<20} = {value}")

        if config.seed is not None:
            lines.append(f"Random Seed          : {config.seed}")

    def _pipeline_summary(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Pipeline Summary")
        lines.append(self.SUB_SEPARATOR)

        report = result.evaluation_report
        pipeline = report.pipeline
        trades = report.trades

        lines.append(f"Candles Processed    : {pipeline.candles_processed}")
        lines.append(f"Evaluation Points    : {pipeline.evaluation_points}")
        lines.append(f"Eligible Decisions   : {pipeline.eligible_decisions}")
        lines.append(f"Signals Generated    : {pipeline.signals_generated}")
        lines.append(f"Signals Suppressed   : {pipeline.signals_suppressed}")
        lines.append(f"Signals Validated    : {pipeline.signals_validated}")
        lines.append(
            f"Validations Completed: {pipeline.validations_completed}"
        )
        lines.append(f"Completed Trades     : {trades.completed_trades}")
        lines.append(f"Win Rate             : {_fmt(trades.win_rate, '%')}")
        lines.append(f"Expectancy           : {_fmt(trades.expectancy, 'R')}")
        lines.append(f"Total R              : {_fmt(trades.total_r, 'R')}")
        lines.append(
            f"Profit Factor        : {trades.profit_factor_display}"
        )
        lines.append(
            f"Max Drawdown         : {_fmt(trades.max_drawdown_r, 'R')}"
        )

    def _research_summary(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Research Summary")
        lines.append(self.SUB_SEPARATOR)

        research = result.research_report

        regime_count = len(
            [rs for rs in research.regime_statistics if rs.completed_trades > 0]
        )
        lines.append(f"Regimes With Trades  : {regime_count}")

        segmentation = research.segmentation
        if segmentation is not None and not segmentation.is_empty:
            lines.append(
                f"Segmentation         : {segmentation.dimension.name} "
                f"({len(segmentation.segments)} segments)"
            )
        else:
            lines.append("Segmentation         : none")

        if research.conclusions:
            lines.append("Research Conclusions :")
            for c in research.conclusions:
                lines.append(f"  - {c}")
        else:
            lines.append("Research Conclusions : none")

    def _robustness_summary(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Robustness Summary")
        lines.append(self.SUB_SEPARATOR)

        rob = result.research_report.parameter_robustness

        if rob is None or rob.is_empty:
            lines.append("No parameter robustness analysis performed.")
            return

        lines.append(f"Parameter            : {rob.parameter_name}")
        lines.append(
            f"Configurations       : {rob.configuration_count}"
        )
        lines.append(
            f"Robust               : {'YES' if rob.robust else 'NO'}"
        )
        lines.append(
            f"Robust Configs       : {list(rob.robust_configurations)}"
        )
        lines.append(
            f"Descriptive Best     : {rob.descriptive_best}"
        )
        lines.append(
            f"Descriptive Best Robust: "
            f"{'YES' if rob.descriptive_best_is_robust else 'NO'}"
        )
        lines.append(
            f"Highly Dependent     : "
            f"{'YES' if rob.highly_dependent_on_single_config else 'NO'}"
        )

    def _walk_forward_summary(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Walk-Forward Summary")
        lines.append(self.SUB_SEPARATOR)

        wf = result.research_report.walk_forward_selection

        if wf is None:
            lines.append("No walk-forward parameter selection performed.")
            return

        lines.append(f"Parameter            : {wf.parameter_name}")
        lines.append(
            f"Development Window   : [{wf.development_window[0]}, "
            f"{wf.development_window[1]}) "
            f"({wf.development_candle_count} candles)"
        )
        lines.append(
            f"Evaluation Window    : [{wf.evaluation_window[0]}, "
            f"{wf.evaluation_window[1]}) "
            f"({wf.evaluation_candle_count} candles)"
        )
        lines.append(
            f"Windows Overlap      : {'YES' if wf.windows_overlap else 'NO'}"
        )

        if wf.has_selected:
            sel = wf.selected
            lines.append(f"Selected Value       : {sel.parameter_value}")
            lines.append(f"Selection Basis      : {sel.selection_basis}")
            lines.append(
                f"Selection Isolated   : "
                f"{'YES' if wf.selection_isolated_from_evaluation else 'NO'}"
            )
            lines.append(
                f"Selection Verified   : "
                f"{'YES' if wf.selection_verified else 'NO'}"
            )
        else:
            lines.append("No configuration was selected.")

        lines.append(f"OOS Trades           : {wf.out_of_sample_completed_trades}")

    def _oos_summary(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("OOS Summary")
        lines.append(self.SUB_SEPARATOR)

        oos = result.research_report.out_of_sample

        if oos is None:
            lines.append("No out-of-sample evaluation performed.")
            return

        in_perf = oos.in_sample_performance
        oos_perf = oos.out_of_sample_performance

        lines.append(f"Split Ratio          : {oos.split_ratio:.2f}")
        lines.append(f"In-Sample Candles    : {oos.in_sample_count}")
        lines.append(f"OOS Candles          : {oos.out_of_sample_count}")
        lines.append(
            f"In-Sample Expectancy : {_fmt(getattr(in_perf, 'expectancy', 0.0), 'R')}"
        )
        lines.append(
            f"OOS Expectancy       : {_fmt(getattr(oos_perf, 'expectancy', 0.0), 'R')}"
        )
        lines.append(
            f"Expectancy Degrade   : {_fmt(oos.expectancy_degradation, 'R')}"
        )
        lines.append(
            f"OOS Trades           : {getattr(oos_perf, 'completed_trades', 0)}"
        )
        lines.append(
            f"Sufficient Data      : {'YES' if oos.sufficient_data else 'NO'}"
        )

        if oos.parameter_selection_isolated is None:
            isolated_label = "NOT VERIFIED"
        else:
            isolated_label = (
                "YES" if oos.parameter_selection_isolated else "NO"
            )
        lines.append(f"Selection Isolated   : {isolated_label}")

    def _data_sufficiency(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Data Sufficiency")
        lines.append(self.SUB_SEPARATOR)

        ds = result.research_report.data_sufficiency

        if ds is None:
            lines.append("No data sufficiency report.")
            return

        lines.append(f"Completed Trades     : {ds.completed_trades}")
        lines.append(f"Min Trades (infer)   : {ds.min_trades_for_inference}")
        lines.append(
            f"Sufficient Trades    : {'YES' if ds.sufficient_trades else 'NO'}"
        )
        lines.append(
            f"Insufficient Regimes : {'YES' if ds.insufficient_regime_samples else 'NO'}"
        )
        lines.append(
            f"Insufficient OOS     : {'YES' if ds.insufficient_oos_trades else 'NO'}"
        )
        lines.append(
            f"Insufficient Params  : "
            f"{'YES' if ds.insufficient_parameter_observations else 'NO'}"
        )
        lines.append(
            f"Sufficient Inference : {'YES' if ds.sufficient_for_inference else 'NO'}"
        )
        lines.append(f"Summary              : {ds.summary}")

    def _leakage_audit(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Leakage Audit")
        lines.append(self.SUB_SEPARATOR)

        leak = result.research_report.leakage

        if leak is None:
            lines.append("No leakage audit performed.")
            return

        lines.append(f"Checks Performed     : {leak.checks_performed}")
        lines.append(f"Passed               : {'YES' if leak.passed else 'NO'}")
        lines.append(
            f"Has NOT VERIFIED     : {'YES' if leak.has_not_verified else 'NO'}"
        )

        if leak.checks:
            lines.append("Structured checks:")
            for chk in leak.checks:
                lines.append(
                    f"  - {chk.name:<28} {chk.severity.name:<12} "
                    f"passed={chk.passed}"
                )

        if leak.failures:
            lines.append("Failures:")
            for f in leak.failures:
                lines.append(f"  - {f}")

        if leak.not_verified:
            lines.append("NOT VERIFIED (not PASS):")
            for nv in leak.not_verified:
                lines.append(f"  - {nv}")

    def _reproducibility(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Reproducibility")
        lines.append(self.SUB_SEPARATOR)

        repro = result.reproducibility

        lines.append(f"Experiment ID        : {repro.experiment_id}")
        lines.append(f"Configuration Hash   : {repro.configuration_hash}")
        lines.append(f"Dataset Identity     : {repro.dataset_identity}")
        lines.append(f"Dataset Content Hash : {repro.dataset_content_hash}")
        lines.append(f"Dataset Size         : {repro.dataset_size}")
        lines.append(f"Code Version         : {repro.code_version}")
        lines.append(f"Random Seed          : {repro.random_seed}")
        lines.append(f"Reproducible         : {'YES' if repro.reproducible else 'NO'}")

        if repro.parameter_values:
            lines.append("Parameter Values     :")
            for key, value in repro.parameter_values.items():
                lines.append(f"  {key:<32} = {value}")

    def _research_conclusion(
        self,
        result: ExperimentResult,
        lines: list[str],
    ) -> None:
        lines.append("")
        lines.append(self.SUB_SEPARATOR)
        lines.append("Research Conclusion")
        lines.append(self.SUB_SEPARATOR)

        status = result.summary.evidence_status

        if status.value == "INSUFFICIENT":
            lines.append(
                "Evidence is INSUFFICIENT: fewer completed trades "
                "than the configured minimum. No reliable inference "
                "is possible; results are raw/descriptive only."
            )
        elif status.value == "PARTIAL":
            lines.append(
                "Evidence is PARTIAL: enough trades for a basic "
                "overall inference, but at least one secondary "
                "evidence source is insufficient. Conclusions are "
                "provisional."
            )
        else:
            lines.append(
                "Evidence is SUFFICIENT by the configured thresholds. "
                "Results remain descriptive, not predictive."
            )

        if result.research_report.conclusions:
            lines.append("")
            for c in result.research_report.conclusions:
                lines.append(f"- {c}")
        else:
            lines.append("- No research conclusions were derived.")


# ============================================================
# HELPERS
# ============================================================


def _fmt(value: Any, suffix: str = "", precision: str = ".2f") -> str:
    """
    Format a numeric value, returning ``N/A`` for non-numeric
    or infinite values (``INF`` is shown explicitly for profit
    factor-style fields elsewhere).
    """

    if value is None:
        return "N/A"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if isinf(numeric):
        return ("INF" if numeric > 0 else "-INF") + suffix

    if not isfinite(numeric):
        return "N/A"

    return f"{numeric:{precision}}{suffix}"
