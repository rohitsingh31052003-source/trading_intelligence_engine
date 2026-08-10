"""
Demo script for the reproducible Research Experiment Framework
(Sprint 11J).

Runs two complete, reproducible experiments against the
deterministic trending dataset using two different strategy
configurations, then:

* prints a full experiment report for each, and
* prints an experiment comparison report.

Every printed value is derived from the actual
``ExperimentResult`` / ``ExperimentComparison``; nothing is
hardcoded. The conclusions are descriptive and never claim a
strategy "is profitable" or "is best" unless the evidence
supports it.
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
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentComparisonEngine,
    ExperimentConfig,
    ExperimentRunner,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.research.research import ResearchConfig
from engine.reporting import (
    ExperimentComparisonFormatter,
    ExperimentReportFormatter,
)


def _config(label: str, lookback: int) -> ExperimentConfig:
    """
    Build a deterministic experiment configuration.
    """

    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name="trending"),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback",
            parameter_values=(2, 3, 4),
        ),
        strategy_parameters={"swing_lookback": str(lookback)},
        seed=42,
        metadata={"dataset": "trending", "sprint": "11J"},
    )


def main() -> None:

    runner = ExperimentRunner()

    config_a = _config("trending-lookback-2", lookback=2)
    config_b = _config("trending-lookback-3", lookback=3)

    result_a = runner.run(config_a)
    result_b = runner.run(config_b)

    formatter = ExperimentReportFormatter()

    print(formatter.format(result_a))
    print()
    print(formatter.format(result_b))

    # -----------------------------------------------------
    # COMPARISON
    # -----------------------------------------------------

    comparison = ExperimentComparisonEngine().compare(
        [result_a, result_b]
    )

    comp_formatter = ExperimentComparisonFormatter()

    print()
    print(comp_formatter.format(comparison))


if __name__ == "__main__":
    main()
