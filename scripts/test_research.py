"""
Demo script for the research robustness layer (Sprint 11H).

Runs the historical evaluation pipeline (Sprint 11F) against
the deterministic trending dataset, then builds a structured,
immutable ``ResearchReport`` (Sprint 11H) and prints a readable
research robustness report covering:

    Overall performance
    Regime performance
    Segmentation
    Parameter sensitivity
    Out-of-sample evaluation
    Leakage audit
    Research conclusion

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
        metadata={"dataset": "trending", "sprint": "11H"},
    )

    print()
    print("=" * 60)
    print("Research Robustness Report")
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
        print(
            f"{rs.regime.name:<20} : "
            f"trades={rs.total_results:>2} "
            f"completed={rs.completed_trades:>2} "
            f"win_rate={_fmt(rs.win_rate, '%')} "
            f"expectancy={_fmt(rs.expectancy, 'R')}"
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
    else:
        print("No out-of-sample evaluation performed.")

    # -----------------------------------------------------
    # LEAKAGE AUDIT
    # -----------------------------------------------------

    _bar("Leakage Audit")

    leak = report.leakage

    if leak is not None:
        print(f"Checks               : {leak.checks_performed}")
        print(f"Passed               : {'YES' if leak.passed else 'NO'}")
        if leak.failures:
            print("Failures             :")
            for f in leak.failures:
                print(f"  - {f}")
        else:
            print("Failures             : none")
        if leak.warnings:
            print("Warnings             :")
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
