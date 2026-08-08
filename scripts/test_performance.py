"""
Demo script for the Performance & Trade Analytics layer
(Sprint 11E).

Constructs a deterministic set of validation results and
prints aggregate performance analytics.

All printed values are derived from the sample data via the
``PerformanceAnalyticsEngine``; nothing is hardcoded into the
output.
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

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.validation import (
    ExitReason,
    ValidationResult,
    ValidationStatus,
)


def make_result(
    status,
    *,
    exit_reason=ExitReason.NONE,
    realized_r=None,
    mfe_r=0.0,
    mae_r=0.0,
    duration_candles=0,
    entry_triggered=False,
):
    return ValidationResult(
        status=status,
        exit_reason=exit_reason,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        entry_triggered=entry_triggered,
        exit_price=None,
        candles_evaluated=duration_candles,
        duration_candles=duration_candles,
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        validation_timestamp=None,
        reason="",
        details=(),
    )


def sample_results():
    """
    Deterministic sample of 10 validation results.

    Evaluation sequence (in order):

        1. NOT_TRIGGERED
        2. EXPIRED
        3. LOSS  (-1.0R)
        4. WIN   (+3.0R)
        5. WIN   (+2.0R)
        6. WIN   (+1.5R)
        7. AMBIGUOUS
        8. LOSS  (-1.0R)
        9. WIN   (+0.5R)
        10. OPEN

    Completed-trade R order: -1, +3, +2, +1.5, -1, +0.5
    Equity curve:            -1, 2, 4, 5.5, 4.5, 5.0
    Maximum drawdown:        1.0R  (peak 5.5 -> trough 4.5)
    Maximum winning streak:  3
    Maximum losing streak:   1
    """

    return [
        make_result(
            ValidationStatus.NOT_TRIGGERED,
            exit_reason=ExitReason.NOT_TRIGGERED,
        ),
        make_result(
            ValidationStatus.EXPIRED,
            exit_reason=ExitReason.EXPIRY,
            entry_triggered=True,
            realized_r=None,
            mfe_r=4.0,
            mae_r=-1.4,
            duration_candles=8,
        ),
        make_result(
            ValidationStatus.LOSS,
            exit_reason=ExitReason.STOP_LOSS,
            entry_triggered=True,
            realized_r=-1.0,
            mfe_r=0.8,
            mae_r=-1.0,
            duration_candles=2,
        ),
        make_result(
            ValidationStatus.WIN,
            exit_reason=ExitReason.TAKE_PROFIT,
            entry_triggered=True,
            realized_r=3.0,
            mfe_r=3.5,
            mae_r=-0.3,
            duration_candles=4,
        ),
        make_result(
            ValidationStatus.WIN,
            exit_reason=ExitReason.TAKE_PROFIT,
            entry_triggered=True,
            realized_r=2.0,
            mfe_r=2.5,
            mae_r=-0.2,
            duration_candles=3,
        ),
        make_result(
            ValidationStatus.WIN,
            exit_reason=ExitReason.TAKE_PROFIT,
            entry_triggered=True,
            realized_r=1.5,
            mfe_r=1.8,
            mae_r=-0.1,
            duration_candles=2,
        ),
        make_result(
            ValidationStatus.AMBIGUOUS,
            exit_reason=ExitReason.BOTH_TOUCHED,
            entry_triggered=True,
            realized_r=None,
            mfe_r=4.0,
            mae_r=-0.5,
            duration_candles=1,
        ),
        make_result(
            ValidationStatus.LOSS,
            exit_reason=ExitReason.STOP_LOSS,
            entry_triggered=True,
            realized_r=-1.0,
            mfe_r=0.4,
            mae_r=-1.0,
            duration_candles=3,
        ),
        make_result(
            ValidationStatus.WIN,
            exit_reason=ExitReason.TAKE_PROFIT,
            entry_triggered=True,
            realized_r=0.5,
            mfe_r=1.0,
            mae_r=0.0,
            duration_candles=1,
        ),
        make_result(
            ValidationStatus.OPEN,
            exit_reason=ExitReason.NONE,
        ),
    ]


def _format_r(value):
    return f"{value:+.2f}R"


def main():

    engine = PerformanceAnalyticsEngine()

    analytics = engine.analyze(sample_results())

    print()
    print("=" * 60)
    print()
    print("Performance Analytics")
    print()
    print(
        f"Total Results        : "
        f"{analytics.total_results}"
    )
    print(
        f"Completed Trades     : "
        f"{analytics.completed_trades}"
    )
    print(
        f"Wins                 : "
        f"{analytics.wins}"
    )
    print(
        f"Losses               : "
        f"{analytics.losses}"
    )
    print()
    print(
        f"Win Rate             : "
        f"{analytics.win_rate:.2f}%"
    )
    print(
        f"Total R              : "
        f"{analytics.total_r:.2f}R"
    )
    print(
        f"Average R            : "
        f"{analytics.average_r:.2f}R"
    )
    print(
        f"Expectancy           : "
        f"{analytics.expectancy:.2f}R"
    )
    print(
        f"Profit Factor        : "
        f"{analytics.profit_factor_display}"
    )
    print()
    print(
        f"Average MFE          : "
        f"{analytics.average_mfe_r:.2f}R"
    )
    print(
        f"Average MAE          : "
        f"{analytics.average_mae_r:.2f}R"
    )
    print()
    print(
        f"Best Trade           : "
        f"{analytics.best_trade_r:.2f}R"
    )
    print(
        f"Worst Trade          : "
        f"{analytics.worst_trade_r:.2f}R"
    )
    print()
    print(
        f"Max Winning Streak   : "
        f"{analytics.maximum_winning_streak}"
    )
    print(
        f"Max Losing Streak    : "
        f"{analytics.maximum_losing_streak}"
    )
    print()
    print(
        f"Max Drawdown         : "
        f"{analytics.max_drawdown_r:.2f}R"
    )
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
