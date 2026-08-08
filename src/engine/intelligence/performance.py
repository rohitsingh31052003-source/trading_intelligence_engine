"""
Performance & Trade Analytics Engine (Sprint 11E).

This engine consumes completed ``ValidationResult`` objects and
produces aggregate performance statistics via
``PerformanceAnalytics``.

It is deterministic:

- no randomness
- no external APIs
- no predictive logic
- no trade execution

It only answers:

    Given a collection of validated signals, what performance
    characteristics does the system demonstrate?
"""

from __future__ import annotations

from math import isinf
from typing import Any, Iterable

from engine.models.performance import (
    PerformanceAnalytics,
    PerformanceGrade,
)
from engine.models.validation import (
    ValidationResult,
    ValidationStatus,
)


# Outcomes that represent a completed trade with a realized
# result. AMBIGUOUS is deliberately excluded: although an
# ambiguous signal may have triggered entry, OHLC data cannot
# determine whether it resolved as a win or a loss, so it must
# not contribute to realized-R performance statistics.
_COMPLETED_STATUSES = frozenset(
    {
        ValidationStatus.WIN,
        ValidationStatus.LOSS,
    }
)

# Outcomes that indicate entry was triggered, even if the trade
# did not resolve to a clean WIN/LOSS (e.g. EXPIRED after entry).
_TRIGGERED_STATUSES = frozenset(
    {
        ValidationStatus.WIN,
        ValidationStatus.LOSS,
        ValidationStatus.AMBIGUOUS,
        ValidationStatus.EXPIRED,
    }
)

# Outcomes that break a WIN/LOSS streak when they appear between
# two trades in the supplied evaluation sequence.
_STREAK_BREAKING_STATUSES = frozenset(
    {
        ValidationStatus.AMBIGUOUS,
        ValidationStatus.EXPIRED,
        ValidationStatus.NOT_TRIGGERED,
        ValidationStatus.OPEN,
    }
)


class PerformanceAnalyticsEngine:
    """
    Produce aggregate performance analytics from a collection of
    validated signals.

    The engine treats the supplied order as the evaluation
    sequence. It does not reorder, sort by timestamp, or
    otherwise rearrange results unless explicitly instructed.
    """

    # =========================================================
    # PUBLIC API
    # =========================================================

    def analyze(
        self,
        results: Iterable[ValidationResult],
    ) -> PerformanceAnalytics:
        """
        Analyze an iterable of ``ValidationResult`` objects.

        Returns an immutable ``PerformanceAnalytics`` instance.

        The engine never raises on empty or partial input.
        """

        ordered = list(results)

        counts = self._count_outcomes(ordered)

        completed = self._completed_results(ordered)

        realized_r = self._realized_r_values(completed)

        win_r = [r for r in realized_r if r > 0.0]
        loss_r = [r for r in realized_r if r < 0.0]

        durations = self._duration_values(completed)

        mfe_values = self._valid_numeric(
            ordered,
            "mfe_r",
        )
        mae_values = self._valid_numeric(
            ordered,
            "mae_r",
        )

        equity_curve = self._equity_curve(realized_r)

        max_winning_streak, max_losing_streak = (
            self._streaks(ordered)
        )

        max_drawdown = self._max_drawdown(equity_curve)

        total_r = sum(realized_r)

        average_r = self._safe_mean(realized_r)
        average_winning_r = self._safe_mean(win_r)
        average_losing_r = self._safe_mean(loss_r)

        best_trade_r = max(realized_r) if realized_r else 0.0
        worst_trade_r = min(realized_r) if realized_r else 0.0

        profit_factor = self._profit_factor(win_r, loss_r)

        expectancy = self._expectancy(realized_r)

        win_rate = self._win_rate(
            counts[ValidationStatus.WIN],
            counts[ValidationStatus.LOSS],
        )

        grade = self._grade(
            expectancy,
            completed_trades=len(completed),
        )

        average_mfe_r = self._safe_mean(mfe_values)
        average_mae_r = self._safe_mean(mae_values)

        maximum_mfe_r = max(mfe_values) if mfe_values else 0.0
        maximum_mae_r = min(mae_values) if mae_values else 0.0

        average_duration = self._safe_mean(
            [float(d) for d in durations]
        )

        minimum_duration = min(durations) if durations else 0
        maximum_duration = max(durations) if durations else 0

        return PerformanceAnalytics(
            total_results=len(ordered),
            completed_trades=len(completed),
            triggered_trades=self._count_triggered(ordered),
            wins=counts[ValidationStatus.WIN],
            losses=counts[ValidationStatus.LOSS],
            ambiguous=counts[ValidationStatus.AMBIGUOUS],
            expired=counts[ValidationStatus.EXPIRED],
            not_triggered=counts[ValidationStatus.NOT_TRIGGERED],
            open=counts[ValidationStatus.OPEN],
            win_rate=win_rate,
            total_r=total_r,
            average_r=average_r,
            average_winning_r=average_winning_r,
            average_losing_r=average_losing_r,
            best_trade_r=best_trade_r,
            worst_trade_r=worst_trade_r,
            profit_factor=profit_factor,
            expectancy=expectancy,
            grade=grade,
            average_mfe_r=average_mfe_r,
            average_mae_r=average_mae_r,
            maximum_mfe_r=maximum_mfe_r,
            maximum_mae_r=maximum_mae_r,
            average_duration_candles=average_duration,
            minimum_duration_candles=minimum_duration,
            maximum_duration_candles=maximum_duration,
            maximum_winning_streak=max_winning_streak,
            maximum_losing_streak=max_losing_streak,
            cumulative_r=equity_curve[-1] if equity_curve else 0.0,
            max_drawdown_r=max_drawdown,
            equity_curve=tuple(equity_curve),
        )

    # =========================================================
    # COUNTS
    # =========================================================

    def _count_outcomes(
        self,
        results: list[ValidationResult],
    ) -> dict[ValidationStatus, int]:
        """
        Count results by validation status.

        Every known status is initialized to zero so callers can
        safely read any status even when absent from the input.
        """

        counts: dict[ValidationStatus, int] = {
            status: 0
            for status in ValidationStatus
        }

        for result in results:
            status = self._status_of(result)
            if status in counts:
                counts[status] += 1

        return counts

    def _count_triggered(
        self,
        results: list[ValidationResult],
    ) -> int:
        """
        Count results whose entry was triggered.

        Triggered trades include WIN, LOSS, AMBIGUOUS and EXPIRED
        outcomes, all of which indicate that entry activation
        occurred.
        """

        return sum(
            1
            for result in results
            if self._status_of(result) in _TRIGGERED_STATUSES
        )

    # =========================================================
    # COMPLETED TRADES
    # =========================================================

    def _completed_results(
        self,
        results: list[ValidationResult],
    ) -> list[ValidationResult]:
        """
        Return only WIN and LOSS results, preserving order.
        """

        return [
            result
            for result in results
            if self._status_of(result) in _COMPLETED_STATUSES
        ]

    def _realized_r_values(
        self,
        completed: list[ValidationResult],
    ) -> list[float]:
        """
        Extract realized R from completed trades.

        Results with missing or ``None`` realized R are skipped
        so they do not corrupt R-multiple statistics.
        """

        values: list[float] = []

        for result in completed:
            realized = getattr(
                result,
                "realized_r",
                None,
            )

            if realized is None:
                continue

            try:
                value = float(realized)
            except (TypeError, ValueError):
                continue

            if isinf(value):
                continue

            values.append(value)

        return values

    def _duration_values(
        self,
        completed: list[ValidationResult],
    ) -> list[int]:
        """
        Extract duration (in candles) from completed trades.
        """

        values: list[int] = []

        for result in completed:
            duration = getattr(
                result,
                "duration_candles",
                0,
            )

            try:
                values.append(int(duration))
            except (TypeError, ValueError):
                values.append(0)

        return values

    def _valid_numeric(
        self,
        results: list[ValidationResult],
        attribute: str,
    ) -> list[float]:
        """
        Extract finite numeric values for an attribute across all
        results.

        Used for MFE/MAE excursions. Missing or non-finite
        values are skipped rather than treated as zero, so
        averages are not artificially flattened.
        """

        values: list[float] = []

        for result in results:
            value = getattr(
                result,
                attribute,
                None,
            )

            if value is None:
                continue

            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            if isinf(numeric):
                continue

            values.append(numeric)

        return values

    # =========================================================
    # RATIOS
    # =========================================================

    def _win_rate(
        self,
        wins: int,
        losses: int,
    ) -> float:
        """
        Win rate over completed trades only.

        wins / (wins + losses) * 100

        Returns 0.0 when there are no completed trades rather
        than raising.
        """

        completed = wins + losses

        if completed <= 0:
            return 0.0

        return (wins / completed) * 100.0

    def _profit_factor(
        self,
        win_r: list[float],
        loss_r: list[float],
    ) -> float:
        """
        profit_factor = gross_profit / abs(gross_loss)

        - No loss and positive profit -> inf
        - No profit and no loss -> 0.0
        """

        gross_profit = sum(win_r)
        gross_loss = sum(loss_r)

        abs_loss = abs(gross_loss)

        if abs_loss == 0.0:
            return float("inf") if gross_profit > 0.0 else 0.0

        return gross_profit / abs_loss

    def _expectancy(
        self,
        realized_r: list[float],
    ) -> float:
        """
        Expectancy per completed trade.

        total_realized_R / completed_trades

        Returns 0.0 when there are no completed trades.
        """

        return self._safe_mean(realized_r)

    # =========================================================
    # STREAKS
    # =========================================================

    def _streaks(
        self,
        results: list[ValidationResult],
    ) -> tuple[int, int]:
        """
        Compute maximum winning and losing streaks over the
        supplied evaluation sequence.

        Only WIN and LOSS extend a streak. Any other outcome
        (AMBIGUOUS, EXPIRED, NOT_TRIGGERED, OPEN) breaks both
        streaks.
        """

        max_winning = 0
        max_losing = 0

        current_winning = 0
        current_losing = 0

        for result in results:
            status = self._status_of(result)

            if status == ValidationStatus.WIN:
                current_winning += 1
                current_losing = 0
                max_winning = max(max_winning, current_winning)

            elif status == ValidationStatus.LOSS:
                current_losing += 1
                current_winning = 0
                max_losing = max(max_losing, current_losing)

            else:
                current_winning = 0
                current_losing = 0

        return max_winning, max_losing

    # =========================================================
    # EQUITY CURVE & DRAWDOWN
    # =========================================================

    def _equity_curve(
        self,
        realized_r: list[float],
    ) -> list[float]:
        """
        Cumulative realized R in supplied order.
        """

        curve: list[float] = []
        cumulative = 0.0

        for value in realized_r:
            cumulative += value
            curve.append(cumulative)

        return curve

    def _max_drawdown(
        self,
        equity_curve: list[float],
    ) -> float:
        """
        Maximum drawdown as a positive magnitude.

        Drawdown is the largest decline from a previous equity
        peak. Returned as a non-negative number.
        """

        if not equity_curve:
            return 0.0

        peak = equity_curve[0]
        max_dd = 0.0

        for value in equity_curve:
            if value > peak:
                peak = value

            drawdown = peak - value

            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    # =========================================================
    # GRADE
    # =========================================================

    def _grade(
        self,
        expectancy: float,
        completed_trades: int,
    ) -> PerformanceGrade:
        """
        Derive an explainable grade from expectancy.

        Deterministic thresholds. When no completed trades exist
        the grade is INSUFFICIENT.
        """

        if completed_trades <= 0:
            return PerformanceGrade.INSUFFICIENT

        if expectancy >= 1.0:
            return PerformanceGrade.STRONG

        if expectancy >= 0.0:
            return PerformanceGrade.POSITIVE

        if expectancy >= -0.5:
            return PerformanceGrade.MARGINAL

        return PerformanceGrade.WEAK

    # =========================================================
    # HELPERS
    # =========================================================

    def _safe_mean(
        self,
        values: list[float],
    ) -> float:
        """
        Arithmetic mean that defaults to 0.0 on empty input.
        """

        if not values:
            return 0.0

        return sum(values) / len(values)

    def _status_of(
        self,
        result: Any,
    ) -> ValidationStatus:
        """
        Extract the validation status defensively.
        """

        status = getattr(
            result,
            "status",
            None,
        )

        if isinstance(status, ValidationStatus):
            return status

        return ValidationStatus.OPEN
