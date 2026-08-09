"""
Demo script for the research robustness layer (Sprint 11H + 11I).

Runs the historical evaluation pipeline (Sprint 11F) against
the deterministic trending dataset, then builds a structured,
immutable ``ResearchReport`` and prints a readable research
robustness report covering:

    Overall performance
    Regime performance
    Segmentation
    Parameter sensitivity
    Parameter robustness (Sprint 11I)
    Walk-forward parameter selection (Sprint 11I)
    Out-of-sample evaluation
    Data sufficiency (Sprint 11I)
    Leakage audit (Sprint 11I: structured + NOT_VERIFIED)
    Research conclusion

The demo explicitly shows:
    - the development period vs the evaluation period
    - the candidate parameter configurations
    - the selected configuration
    - that selection was based only on development data
    - the robustness / stability result
    - data sufficiency
    - the leakage audit (including NOT VERIFIED items)
    - the final research conclusion

Every printed value is derived from the actual
``ResearchReport``; nothing is hardcoded into the output. The
conclusions are descriptive and never claim the strategy "is
profitable" on the small demo dataset.
"""

import os
import sys

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
    ),
)

from engine.config.swing_config import SwingConfig
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.research import (
    ResearchConfig,
    ResearchEngine,
)


def _bar(title: str) -> None:
    print()
    print("-" * 60)
    print(title)
    print("-" * 60)


def _fmt(value, suffix: str = "", precision: str = ".2f") -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if numeric == float("inf"):
        return "INF" + suffix
    if numeric == float("-inf"):
        return "-INF" + suffix

    return f"{numeric:{precision}}{suffix}"


def main() -> None:

    candles = trending_dataset()

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(candles)

    def pipeline_evaluator(cs):
        return HistoricalEvaluationPipeline().evaluate(cs)

    def parameter_evaluator(value):
        config = PipelineConfig(
            swing_config=SwingConfig(lookback=value),
        )
        return HistoricalEvaluationPipeline(config).evaluate(candles)

    # Sprint 11I: walk-forward evaluator takes (candles, value).
    def walk_forward_evaluator(cs, value):
        config = PipelineConfig(
            swing_config=SwingConfig(lookback=value),
        )
        return HistoricalEvaluationPipeline(config).evaluate(cs)

    config = ResearchConfig(
        sensitivity_parameter_name="swing_lookback",
        sensitivity_parameter_values=(2, 3, 4),
    )

    report = ResearchEngine(config).analyze(
        result,
        candles,
        pipeline_evaluator=pipeline_evaluator,
        parameter_evaluator=parameter_evaluator,
        walk_forward_evaluator=walk_forward_evaluator,
        label="trending-demo",
        metadata={"dataset": "trending", "sprint": "11I"},
    )

    print()
    print("=" * 60)
    print("Research Robustness Report (Sprint 11I)")
    print("=" * 60)

    print()
    print(f"Dataset              : {report.label}")

    # -----------------------------------------------------
    # OVERALL PERFORMANCE
    # -----------------------------------------------------

    perf = report.overall_performance

    _bar("Overall Performance")

    print(f"Completed Trades     : {perf.completed_trades}")
    print(f"Win Rate             : {_fmt(perf.win_rate, '%')}")
    print(f"Expectancy           : {_fmt(perf.expectancy, 'R')}")
    print(f"Profit Factor        : {_fmt(perf.profit_factor)}")
    print(f"Total R              : {_fmt(perf.total_r, 'R')}")
    print(f"Max Drawdown         : {_fmt(perf.max_drawdown_r, 'R')}")

    # -----------------------------------------------------
    # REGIME PERFORMANCE
    # -----------------------------------------------------

    _bar("Regime Performance")

    for rs in report.regime_statistics:
        suff = "SUFFICIENT" if rs.sufficient_observations else (
            "UNOBSERVED" if rs.has_no_completed_trades else "INSUFFICIENT"
        )
        print(
            f"{rs.regime.name:<20} : "
            f"trades={rs.total_results:>2} "
            f"completed={rs.completed_trades:>2} "
            f"win_rate={_fmt(rs.win_rate, '%')} "
            f"expectancy={_fmt(rs.expectancy, 'R')} "
            f"[{suff}]"
        )

    # -----------------------------------------------------
    # SEGMENTATION
    # -----------------------------------------------------

    _bar("Segmentation")

    if report.segmentation is not None and not report.segmentation.is_empty:
        print(f"Dimension            : {report.segmentation.dimension.name}")
        for s in report.segmentation.segments:
            print(
                f"{s.segment_label:<20} : "
                f"trades={s.total_results:>2} "
                f"completed={s.completed_trades:>2} "
                f"win_rate={_fmt(s.win_rate, '%')} "
                f"expectancy={_fmt(s.expectancy, 'R')}"
            )
    else:
        print("No segmentation data available.")

    # -----------------------------------------------------
    # PARAMETER SENSITIVITY
    # -----------------------------------------------------

    _bar("Parameter Sensitivity")

    sens = report.parameter_sensitivity

    if sens is not None and not sens.is_empty:
        print(f"Parameter            : {sens.parameter_name}")
        print(f"Configurations       : {sens.configuration_count}")
        print(f"Median Expectancy    : {_fmt(sens.median_expectancy, 'R')}")
        print(f"Expectancy Range     : {_fmt(sens.expectancy_range, 'R')}")
        print(f"Stable Configs       : {sens.profitable_configurations}")
        print(f"Stability Ratio      : {_fmt(sens.stability_ratio)}")
        print(f"Sufficient Data      : {'YES' if sens.sufficient_data else 'NO'}")
        print(
            f"Best (descriptive)   : {sens.best_value_by_expectancy}"
        )
    else:
        print("No parameter sensitivity analysis performed.")

    # -----------------------------------------------------
    # PARAMETER ROBUSTNESS (Sprint 11I)
    # -----------------------------------------------------

    _bar("Parameter Robustness (Sprint 11I)")

    rob = report.parameter_robustness

    if rob is not None and not rob.is_empty:
        print(f"Parameter            : {rob.parameter_name}")
        print(f"Median Expectancy    : {_fmt(rob.median_expectancy, 'R')}")
        print(f"Robustness Band      : {_fmt(rob.expectancy_band, 'R')}")
        print(f"Robust Configs       : {rob.robust_configurations}")
        print(f"Unstable Configs     : {rob.unstable_configurations}")
        print(f"Descriptive Best     : {rob.descriptive_best}")
        print(
            f"Descriptive Best Robust: "
            f"{'YES' if rob.descriptive_best_is_robust else 'NO'}"
        )
        print(
            f"Highly Dependent     : "
            f"{'YES' if rob.highly_dependent_on_single_config else 'NO'}"
        )
        print(f"Robust               : {'YES' if rob.robust else 'NO'}")
        print()
        print("Per-configuration robustness:")
        for c in rob.configurations:
            tags = []
            if c.stable:
                tags.append("STABLE")
            else:
                if c.profitable:
                    tags.append("PROFITABLE-OUTLIER")
                else:
                    tags.append("UNSTABLE")
            if c.near_median:
                tags.append("near-median")
            print(
                f"  value={c.parameter_value} "
                f"completed={c.completed_trades} "
                f"expectancy={_fmt(c.expectancy, 'R')} "
                f"total_r={_fmt(c.total_r, 'R')} "
                f"[{', '.join(tags)}]"
            )
    else:
        print("No parameter robustness analysis performed.")

    # -----------------------------------------------------
    # WALK-FORWARD PARAMETER SELECTION (Sprint 11I)
    # -----------------------------------------------------

    _bar("Walk-Forward Parameter Selection (Sprint 11I)")

    wf = report.walk_forward_selection

    if wf is not None:
        print(f"Parameter            : {wf.parameter_name}")
        print(
            f"Development Window   : candles[{wf.development_window[0]}:"
            f"{wf.development_window[1]}] "
            f"({wf.development_candle_count} candles)"
        )
        print(
            f"Evaluation Window    : candles[{wf.evaluation_window[0]}:"
            f"{wf.evaluation_window[1]}] "
            f"({wf.evaluation_candle_count} candles)"
        )
        print(f"Windows Overlap      : {'YES' if wf.windows_overlap else 'NO'}")
        print()
        print("Candidate configurations (development window only):")
        for c in wf.candidates:
            print(
                f"  value={c.parameter_value} "
                f"completed={c.development_completed_trades} "
                f"expectancy={_fmt(c.development_expectancy, 'R')} "
                f"total_r={_fmt(c.development_total_r, 'R')} "
                f"sufficient="
                f"{'YES' if c.sufficient_development_trades else 'NO'}"
            )

        if wf.has_selected:
            sel = wf.selected
            print()
            print(f"Selected             : {sel.parameter_value}")
            print(f"Selection Basis      : {sel.selection_basis}")
            print(
                f"Selected From Dev    : "
                f"{'YES' if sel.selected_from_development_data else 'NO'}"
            )
            print(
                f"Selection Verified   : "
                f"{'YES' if wf.selection_verified else 'NO'}"
            )
            print(
                f"Selection Isolated   : "
                f"{'YES' if wf.selection_isolated_from_evaluation else 'NO'}"
            )
        else:
            print("No configuration was selected.")

        print()
        print(
            f"OOS Trades           : {wf.out_of_sample_completed_trades}"
        )
        oos_perf = wf.out_of_sample_performance
        print(
            f"OOS Expectancy       : "
            f"{_fmt(getattr(oos_perf, 'expectancy', 0.0), 'R')}"
        )
        print(
            f"Sufficient Dev Window: "
            f"{'YES' if wf.sufficient_development_window else 'NO'} "
            f"({wf.development_candle_count} candles)"
        )
        print(
            f"Sufficient Eval Window: "
            f"{'YES' if wf.sufficient_evaluation_window else 'NO'} "
            f"({wf.evaluation_candle_count} candles)"
        )
        print(
            f"Sufficient OOS Trades: "
            f"{'YES' if wf.has_out_of_sample_trades else 'NO'} "
            f"({wf.out_of_sample_completed_trades} trades)"
        )
    else:
        print("No walk-forward parameter selection performed.")

    # -----------------------------------------------------
    # OUT-OF-SAMPLE
    # -----------------------------------------------------

    _bar("Out-of-Sample")

    oos = report.out_of_sample

    if oos is not None:
        in_perf = oos.in_sample_performance
        oos_perf = oos.out_of_sample_performance

        print(f"Split Ratio          : {oos.split_ratio:.2f}")
        print(f"In-Sample Candles    : {oos.in_sample_count}")
        print(f"OOS Candles          : {oos.out_of_sample_count}")
        print(
            f"In-Sample Expectancy : {_fmt(getattr(in_perf, 'expectancy', 0.0), 'R')}"
        )
        print(
            f"OOS Expectancy       : {_fmt(getattr(oos_perf, 'expectancy', 0.0), 'R')}"
        )
        print(
            f"Degradation          : {_fmt(oos.expectancy_degradation, 'R')}"
        )
        print(
            f"In-Sample Trades     : {getattr(in_perf, 'completed_trades', 0)}"
        )
        print(
            f"OOS Trades           : {getattr(oos_perf, 'completed_trades', 0)}"
        )
        print(f"Sufficient Data      : {'YES' if oos.sufficient_data else 'NO'}")
        if oos.parameter_selection_isolated is None:
            sel_iso_label = "NOT VERIFIED"
        else:
            sel_iso_label = "YES" if oos.parameter_selection_isolated else "NO"
        print(
            f"Selection Isolated   : {sel_iso_label}"
        )
        if oos.selection_isolation_verified_by is not None:
            print(
                f"  Verified By        : "
                f"{oos.selection_isolation_verified_by}"
            )
        else:
            print(
                f"  Verified By        : "
                f"not independently verified by legacy OOS report"
            )
    else:
        print("No out-of-sample evaluation performed.")

    # -----------------------------------------------------
    # DATA SUFFICIENCY (Sprint 11I)
    # -----------------------------------------------------

    _bar("Data Sufficiency (Sprint 11I)")

    ds = report.data_sufficiency

    if ds is not None:
        print(f"Completed Trades     : {ds.completed_trades}")
        print(f"Min Trades (inference): {ds.min_trades_for_inference}")
        print(f"Sufficient Trades    : {'YES' if ds.sufficient_trades else 'NO'}")
        print(
            f"Insufficient Regimes : {'YES' if ds.insufficient_regime_samples else 'NO'}"
        )
        print(
            f"Insufficient OOS     : {'YES' if ds.insufficient_oos_trades else 'NO'}"
        )
        print(
            f"Insufficient Params  : {'YES' if ds.insufficient_parameter_observations else 'NO'}"
        )
        print(f"OOS Trades           : {ds.oos_completed_trades}")
        print(f"Parameter Configs    : {ds.parameter_configurations}")
        print(f"Regimes With Trades  : {ds.regimes_with_trades}")
        print(f"Regimes Sufficient   : {ds.regimes_sufficient}")
        print(
            f"Sufficient Inference : {'YES' if ds.sufficient_for_inference else 'NO'}"
        )
        print(f"Summary              : {ds.summary}")
    else:
        print("No data sufficiency report.")

    # -----------------------------------------------------
    # LEAKAGE AUDIT (Sprint 11I: structured)
    # -----------------------------------------------------

    _bar("Leakage Audit (Sprint 11I: structured)")

    leak = report.leakage

    if leak is not None:
        print(f"Checks Performed     : {leak.checks_performed}")
        print(f"Passed               : {'YES' if leak.passed else 'NO'}")
        print()
        print("Structured checks:")
        for chk in leak.checks:
            print(
                f"  - {chk.name:<24} {chk.severity.name:<12} "
                f"passed={chk.passed}"
            )
            # Wrap the reason for readability.
            reason = chk.reason
            while reason:
                line = reason[: 56]
                rest = reason[56:]
                print(f"      {line}")
                reason = rest

        if leak.failures:
            print()
            print("Failures:")
            for f in leak.failures:
                print(f"  - {f}")
        if leak.not_verified:
            print()
            print("NOT VERIFIED (not PASS):")
            for nv in leak.not_verified:
                print(f"  - {nv}")
        if leak.warnings and not leak.not_verified:
            print()
            print("Warnings:")
            for w in leak.warnings:
                print(f"  - {w}")
    else:
        print("No leakage audit performed.")

    # -----------------------------------------------------
    # RESEARCH CONCLUSION
    # -----------------------------------------------------

    _bar("Research Conclusion")

    if report.conclusions:
        for c in report.conclusions:
            print(f"- {c}")
    else:
        print("No conclusions derived.")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
