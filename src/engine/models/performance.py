"""
Domain model for the Performance & Trade Analytics layer
(Sprint 11E).

This model represents aggregate performance statistics derived
from a collection of completed ``ValidationResult`` objects.

It does NOT execute trades.
It does NOT predict future prices.
It does NOT modify signals or validation results.

It answers a single question:

    Given a collection of validated signals, what performance
    characteristics does the system demonstrate?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isinf


class PerformanceGrade(Enum):
    """
    Explainable, deterministic tier derived from aggregate
    performance characteristics.

    The grade is a convenience label only. The raw numeric
    statistics remain the authoritative source of truth.

    INSUFFICIENT:
        No completed trades exist, so a grade cannot be
        meaningfully assigned.

    STRONG:
        Expectancy is clearly positive.

    POSITIVE:
        Expectancy is non-negative but modest.

    MARGINAL:
        Expectancy is slightly negative.

    WEAK:
        Expectancy is clearly negative.
    """

    INSUFFICIENT = "INSUFFICIENT"
    STRONG = "STRONG"
    POSITIVE = "POSITIVE"
    MARGINAL = "MARGINAL"
    WEAK = "WEAK"


@dataclass(frozen=True)
class PerformanceAnalytics:
    """
    Immutable aggregate analytics produced by
    ``PerformanceAnalyticsEngine``.

    All R-multiple statistics preserve the sign of R unless an
    attribute is explicitly described as a positive magnitude
    (for example ``max_drawdown_r``).

    Conventions:

    - Completed trades are WIN and LOSS outcomes only.
    - AMBIGUOUS, EXPIRED, NOT_TRIGGERED and OPEN outcomes are
      never silently treated as losses.
    - Win rate is computed over completed trades.
    - When no completed trades exist, ratio-based statistics
      default to ``0.0`` rather than raising.
    """

    # -----------------------------------------------------
    # COUNTS
    # -----------------------------------------------------

    total_results: int
    completed_trades: int
    triggered_trades: int

    wins: int
    losses: int
    ambiguous: int
    expired: int
    not_triggered: int
    open: int

    # -----------------------------------------------------
    # PERFORMANCE
    # -----------------------------------------------------

    win_rate: float

    total_r: float
    average_r: float
    average_winning_r: float
    average_losing_r: float

    best_trade_r: float
    worst_trade_r: float

    profit_factor: float
    expectancy: float

    grade: PerformanceGrade

    # -----------------------------------------------------
    # EXCURSION
    # -----------------------------------------------------

    average_mfe_r: float
    average_mae_r: float
    maximum_mfe_r: float
    maximum_mae_r: float

    # -----------------------------------------------------
    # DURATION
    # -----------------------------------------------------

    average_duration_candles: float
    minimum_duration_candles: int
    maximum_duration_candles: int

    # -----------------------------------------------------
    # STREAKS
    # -----------------------------------------------------

    maximum_winning_streak: int
    maximum_losing_streak: int

    # -----------------------------------------------------
    # RISK / DRAWDOWN
    # -----------------------------------------------------

    cumulative_r: float
    max_drawdown_r: float

    equity_curve: tuple[float, ...] = field(
        default_factory=tuple
    )

    # -----------------------------------------------------
    # HELPERS
    # -----------------------------------------------------

    @property
    def has_completed_trades(self) -> bool:
        """
        Whether any completed (WIN/LOSS) trades exist.
        """

        return self.completed_trades > 0

    @property
    def is_profitable(self) -> bool:
        """
        Whether the system has produced a positive total
        realized R across completed trades.
        """

        return self.total_r > 0.0

    @property
    def profit_factor_display(self) -> str:
        """
        Human-readable profit factor.

        Renders ``inf`` as ``INF`` so callers never need to
        special-case infinite values for display.
        """

        if isinf(self.profit_factor):
            return "INF"

        return f"{self.profit_factor:.2f}"
