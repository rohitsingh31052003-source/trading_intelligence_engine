"""
Demo script for the evaluation reporting layer (Sprint 11G).

Runs the historical evaluation pipeline (Sprint 11F) against
the deterministic trending dataset, then builds a structured,
immutable ``EvaluationReport`` (Sprint 11G) and prints the
three independent statistical views:

    Pipeline statistics  (walk-forward funnel)
    Signal statistics    (signal-generation characteristics)
    Trade statistics     (completed-trade performance, delegated)

Every printed value is derived from the actual
``EvaluationReport``; nothing is hardcoded into the output.

The report retains the raw ``PipelineResult`` by reference, so
downstream comparison / robustness / Monte Carlo layers (future
sprints) can access the raw evaluation points, signals and
validation results without re-running the pipeline.
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
from engine.reporting import EvaluationReportEngine


def _bar(title: str) -> None:
    print()
    print("-" * 60)
    print(title)
    print("-" * 60)


def main() -> None:

    pipeline = HistoricalEvaluationPipeline()
    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    report = EvaluationReportEngine().analyze(
        result,
        label="trending-demo",
        metadata={"dataset": "trending", "sprint": "11G"},
    )

    print()
    print("=" * 60)
    print()
    print(f"Evaluation Report  :  {report.label}")
    print(
        f"Metadata           :  "
        f"{', '.join(f'{k}={v}' for k, v in report.metadata.items())}"
    )

    # -----------------------------------------------------
    # PIPELINE-LEVEL
    # -----------------------------------------------------

    p = report.pipeline

    _bar("Pipeline Statistics (walk-forward funnel)")

    print(f"Candles Processed     :  {p.candles_processed}")
    print(f"Evaluation Points     :  {p.evaluation_points}")
    print(f"Decisions Generated   :  {p.decisions_generated}")
    print(f"Eligible Decisions    :  {p.eligible_decisions}")
    print(f"Signals Generated     :  {p.signals_generated}")
    print(f"Signals Suppressed    :  {p.signals_suppressed}")
    print(f"Signals Validated     :  {p.signals_validated}")
    print(f"Validations Completed :  {p.validations_completed}")
    print(f"Completed Trades      :  {p.completed_trades}")

    print()

    print(f"Signal Generation Rate :  {p.signal_generation_rate:.2f}%")
    print(f"Suppression Rate       :  {p.suppression_rate:.2f}%")
    print(
        f"Validation Completion  :  "
        f"{p.validation_completion_rate:.2f}%"
    )

    # -----------------------------------------------------
    # SIGNAL-LEVEL
    # -----------------------------------------------------

    s = report.signals

    _bar("Signal Statistics (signal generation)")

    print(f"Total Signals         :  {s.total_signals}")
    print(f"Long Signals          :  {s.long_signals}")
    print(f"Short Signals         :  {s.short_signals}")
    print(f"Eligible Signals      :  {s.eligible_signals}")
    print(f"Suppressed Signals    :  {s.suppressed_signals}")
    print(f"No-Signal Points      :  {s.no_signal_points}")
    print(f"Invalid Signals       :  {s.invalid_signals}")

    print()

    print(f"Directional Balance   :  {s.directional_balance:+d}")
    print(f"Long Share            :  {s.long_share:.2f}%")
    print(f"Average Confidence    :  {s.average_confidence:.2f}")
    print(f"Average Risk/Reward   :  {s.average_risk_reward:.2f}")

    # -----------------------------------------------------
    # TRADE-LEVEL (delegated from PerformanceAnalytics)
    # -----------------------------------------------------

    t = report.trades

    _bar("Trade Statistics (completed trades)")

    print(f"Total Results         :  {t.total_results}")
    print(f"Completed Trades      :  {t.completed_trades}")
    print(f"Wins                  :  {t.wins}")
    print(f"Losses                :  {t.losses}")
    print(f"Ambiguous             :  {t.ambiguous}")
    print(f"Expired               :  {t.expired}")
    print(f"Not Triggered         :  {t.not_triggered}")
    print(f"Open                  :  {t.open}")

    print()

    print(f"Win Rate              :  {t.win_rate:.2f}%")
    print(f"Total R               :  {t.total_r:+.2f}R")
    print(f"Average R             :  {t.average_r:+.2f}R")
    print(f"Expectancy            :  {t.expectancy:+.2f}R")
    print(f"Profit Factor         :  {t.profit_factor_display}")

    print()

    print(f"Average MFE           :  {t.average_mfe_r:.2f}R")
    print(f"Average MAE           :  {t.average_mae_r:.2f}R")
    print(f"Max Drawdown          :  {t.max_drawdown_r:.2f}R")
    print(f"Max Winning Streak    :  {t.maximum_winning_streak}")
    print(f"Max Losing Streak     :  {t.maximum_losing_streak}")

    print()

    print(
        f"Profitable            :  "
        f"{'YES' if t.is_profitable else 'NO'}"
    )
    print(
        f"Has Completed Trades  :  "
        f"{'YES' if t.has_completed_trades else 'NO'}"
    )

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
