"""
Demo script for the end-to-end historical evaluation pipeline
(Sprint 11F).

Runs the entire pipeline against the deterministic synthetic
historical dataset and prints the walk-forward funnel together
with the aggregate performance statistics produced by the
existing ``PerformanceAnalyticsEngine``.

Every printed value is derived from the actual
``PipelineResult``; nothing is hardcoded into the output.
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

from engine.pipeline import (
    HistoricalEvaluationPipeline,
    trending_dataset,
)


def main() -> None:

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()

    result = pipeline.evaluate(candles)

    performance = result.performance

    print()
    print("=" * 60)
    print()
    print("Historical Pipeline Evaluation")
    print()
    print(
        f"Candles Processed      : "
        f"{result.candles_processed}"
    )
    print(
        f"Evaluation Points      : "
        f"{result.evaluation_points}"
    )
    print(
        f"Decisions Generated    : "
        f"{result.decisions_generated}"
    )
    print(
        f"Eligible Decisions     : "
        f"{result.eligible_decisions}"
    )
    print(
        f"Signals Generated      : "
        f"{result.signals_generated}"
    )
    print(
        f"Validated Signals      : "
        f"{result.signals_validated}"
    )
    print(
        f"Completed Trades       : "
        f"{result.completed_trades}"
    )

    print()

    if performance is not None:
        print(
            f"Wins                   : "
            f"{performance.wins}"
        )
        print(
            f"Losses                 : "
            f"{performance.losses}"
        )
        print(
            f"Ambiguous              : "
            f"{performance.ambiguous}"
        )
        print(
            f"Expired                : "
            f"{performance.expired}"
        )
        print(
            f"Not Triggered          : "
            f"{performance.not_triggered}"
        )

        print()

        print(
            f"Win Rate               : "
            f"{performance.win_rate:.2f}%"
        )
        print(
            f"Total R                : "
            f"{performance.total_r:.2f}R"
        )
        print(
            f"Average R              : "
            f"{performance.average_r:.2f}R"
        )
        print(
            f"Expectancy             : "
            f"{performance.expectancy:.2f}R"
        )
        print(
            f"Profit Factor          : "
            f"{performance.profit_factor_display}"
        )

        print()

        print(
            f"Maximum Drawdown       : "
            f"{performance.max_drawdown_r:.2f}R"
        )

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
