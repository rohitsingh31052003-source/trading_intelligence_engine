"""
Tests for the Performance & Trade Analytics layer (Sprint 11E).
"""

from dataclasses import FrozenInstanceError

import pytest

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.performance import (
    PerformanceAnalytics,
    PerformanceGrade,
)
from engine.models.validation import (
    ExitReason,
    ValidationResult,
    ValidationStatus,
)


# ============================================================
# RESULT BUILDERS
# ============================================================


def make_result(
    status=ValidationStatus.WIN,
    *,
    exit_reason=ExitReason.TAKE_PROFIT,
    entry_price=100.0,
    stop_loss=98.0,
    take_profit=104.0,
    entry_triggered=True,
    exit_price=None,
    candles_evaluated=10,
    duration_candles=3,
    realized_r=None,
    mfe_r=0.0,
    mae_r=0.0,
    validation_timestamp=None,
    reason="",
    details=(),
):
    return ValidationResult(
        status=status,
        exit_reason=exit_reason,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        entry_triggered=entry_triggered,
        exit_price=exit_price,
        candles_evaluated=candles_evaluated,
        duration_candles=duration_candles,
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        validation_timestamp=validation_timestamp,
        reason=reason,
        details=details,
    )


def win(realized_r, mfe_r=0.0, mae_r=0.0, duration=3):
    return make_result(
        status=ValidationStatus.WIN,
        exit_reason=ExitReason.TAKE_PROFIT,
        exit_price=104.0,
        realized_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        duration_candles=duration,
    )


def loss(mfe_r=0.0, mae_r=-1.0, duration=2):
    return make_result(
        status=ValidationStatus.LOSS,
        exit_reason=ExitReason.STOP_LOSS,
        exit_price=98.0,
        realized_r=-1.0,
        mfe_r=mfe_r,
        mae_r=mae_r,
        duration_candles=duration,
    )


def ambiguous(mfe_r=0.0, mae_r=0.0, duration=1):
    return make_result(
        status=ValidationStatus.AMBIGUOUS,
        exit_reason=ExitReason.BOTH_TOUCHED,
        realized_r=None,
        mfe_r=mfe_r,
        mae_r=mae_r,
        duration_candles=duration,
    )


def expired(mfe_r=0.0, mae_r=0.0, duration=5):
    return make_result(
        status=ValidationStatus.EXPIRED,
        exit_reason=ExitReason.EXPIRY,
        entry_triggered=True,
        realized_r=None,
        mfe_r=mfe_r,
        mae_r=mae_r,
        duration_candles=duration,
    )


def not_triggered():
    return make_result(
        status=ValidationStatus.NOT_TRIGGERED,
        exit_reason=ExitReason.NOT_TRIGGERED,
        entry_triggered=False,
        realized_r=None,
        mfe_r=0.0,
        mae_r=0.0,
        duration_candles=0,
    )


def open_result():
    return make_result(
        status=ValidationStatus.OPEN,
        exit_reason=ExitReason.NONE,
        entry_triggered=False,
        realized_r=None,
        mfe_r=0.0,
        mae_r=0.0,
        duration_candles=0,
    )


@pytest.fixture()
def engine():
    return PerformanceAnalyticsEngine()


# ============================================================
# COUNTS
# ============================================================


class TestCounts:
    def test_empty_input(self, engine):
        analytics = engine.analyze([])

        assert analytics.total_results == 0
        assert analytics.completed_trades == 0
        assert analytics.triggered_trades == 0
        assert analytics.wins == 0
        assert analytics.losses == 0
        assert analytics.ambiguous == 0
        assert analytics.expired == 0
        assert analytics.not_triggered == 0
        assert analytics.open == 0

    def test_all_wins(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5), win(3.0)]
        )

        assert analytics.wins == 3
        assert analytics.losses == 0
        assert analytics.completed_trades == 3

    def test_all_losses(self, engine):
        analytics = engine.analyze(
            [loss(), loss(), loss()]
        )

        assert analytics.losses == 3
        assert analytics.wins == 0
        assert analytics.completed_trades == 3

    def test_mixed_results(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                loss(),
                ambiguous(),
                expired(),
                not_triggered(),
                open_result(),
            ]
        )

        assert analytics.total_results == 6
        assert analytics.wins == 1
        assert analytics.losses == 1
        assert analytics.ambiguous == 1
        assert analytics.expired == 1
        assert analytics.not_triggered == 1
        assert analytics.open == 1
        assert analytics.completed_trades == 2
        assert analytics.triggered_trades == 4

    def test_triggered_includes_ambiguous_and_expired(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [
                win(2.0),
                ambiguous(),
                expired(),
                not_triggered(),
            ]
        )

        assert analytics.triggered_trades == 3

    def test_accepts_iterable_not_only_list(self, engine):
        analytics = engine.analyze(
            r for r in [win(2.0), loss()]
        )

        assert analytics.completed_trades == 2


# ============================================================
# WIN RATE
# ============================================================


class TestWinRate:
    def test_all_wins_100_percent(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5), win(3.0)]
        )

        assert analytics.win_rate == 100.0

    def test_all_losses_0_percent(self, engine):
        analytics = engine.analyze(
            [loss(), loss(), loss()]
        )

        assert analytics.win_rate == 0.0

    def test_mixed_win_rate(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5), loss(), loss()]
        )

        assert analytics.win_rate == 50.0

    def test_no_completed_trades_win_rate_zero(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [not_triggered(), expired(), ambiguous()]
        )

        assert analytics.win_rate == 0.0

    def test_untriggered_excluded_from_win_rate(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [
                win(2.0),
                win(1.5),
                not_triggered(),
                not_triggered(),
            ]
        )

        assert analytics.win_rate == 100.0


# ============================================================
# R STATISTICS
# ============================================================


class TestRStatistics:
    def test_total_r_preserves_sign(self, engine):
        results = [
            win(2.0),
            win(1.5),
            loss(),
            loss(),
        ]

        analytics = engine.analyze(results)

        assert analytics.total_r == pytest.approx(1.5)

    def test_average_r(self, engine):
        results = [
            win(2.0),
            win(1.5),
            loss(),
            loss(),
        ]

        analytics = engine.analyze(results)

        assert analytics.average_r == pytest.approx(0.375)

    def test_average_winning_r(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5), win(3.0), loss()]
        )

        assert analytics.average_winning_r == pytest.approx(
            (2.0 + 1.5 + 3.0) / 3
        )

    def test_average_losing_r(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                loss(),
                loss(),
            ]
        )

        assert analytics.average_losing_r == pytest.approx(-1.0)

    def test_best_trade_r(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(3.5), win(1.0), loss()]
        )

        assert analytics.best_trade_r == pytest.approx(3.5)

    def test_worst_trade_r(self, engine):
        analytics = engine.analyze(
            [win(2.0), loss(), loss()]
        )

        assert analytics.worst_trade_r == pytest.approx(-1.0)

    def test_losses_not_converted_to_positive(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [loss(), loss()]
        )

        assert analytics.total_r == pytest.approx(-2.0)
        assert analytics.average_losing_r == pytest.approx(-1.0)

    def test_missing_realized_r_skipped(self, engine):
        bad_win = make_result(
            status=ValidationStatus.WIN,
            realized_r=None,
        )
        analytics = engine.analyze(
            [bad_win, win(2.0), loss()]
        )

        # bad_win counts as a WIN by status, but its realized R
        # is missing and therefore excluded from R statistics.
        assert analytics.completed_trades == 3
        assert analytics.wins == 2
        assert analytics.total_r == pytest.approx(1.0)


# ============================================================
# PROFIT FACTOR
# ============================================================


class TestProfitFactor:
    def test_profitable_system(self, engine):
        analytics = engine.analyze(
            [win(3.0), win(2.0), loss(), loss()]
        )

        assert analytics.profit_factor == pytest.approx(2.5)

    def test_losing_system(self, engine):
        analytics = engine.analyze(
            [win(1.0), loss(), loss(), loss()]
        )

        assert analytics.profit_factor == pytest.approx(
            1.0 / 3.0
        )

    def test_no_losses_returns_inf(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5)]
        )

        assert analytics.profit_factor == float("inf")

    def test_no_trades_returns_zero(self, engine):
        analytics = engine.analyze([])

        assert analytics.profit_factor == 0.0

    def test_no_profit_no_loss_returns_zero(
        self,
        engine,
    ):
        zero_win = make_result(
            status=ValidationStatus.WIN,
            realized_r=0.0,
        )
        analytics = engine.analyze([zero_win])

        assert analytics.profit_factor == 0.0


# ============================================================
# EXPECTANCY
# ============================================================


class TestExpectancy:
    def test_positive_expectancy(self, engine):
        analytics = engine.analyze(
            [win(3.0), win(2.0), loss()]
        )

        assert analytics.expectancy == pytest.approx(
            (3.0 + 2.0 - 1.0) / 3
        )

    def test_negative_expectancy(self, engine):
        analytics = engine.analyze(
            [win(1.0), loss(), loss()]
        )

        assert analytics.expectancy == pytest.approx(-0.333, abs=1e-3)

    def test_zero_completed_trades(self, engine):
        analytics = engine.analyze(
            [not_triggered(), expired()]
        )

        assert analytics.expectancy == 0.0


# ============================================================
# MFE / MAE
# ============================================================


class TestMfeMae:
    def test_average_mfe(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, mfe_r=2.5),
                win(1.5, mfe_r=1.5),
                loss(mfe_r=0.5),
            ]
        )

        assert analytics.average_mfe_r == pytest.approx(
            (2.5 + 1.5 + 0.5) / 3
        )

    def test_average_mae(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, mae_r=-0.5),
                win(1.5, mae_r=-0.25),
                loss(mae_r=-1.0),
            ]
        )

        assert analytics.average_mae_r == pytest.approx(
            (-0.5 + -0.25 + -1.0) / 3
        )

    def test_maximum_mfe(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, mfe_r=2.5),
                win(1.5, mfe_r=3.5),
                loss(mfe_r=0.5),
            ]
        )

        assert analytics.maximum_mfe_r == pytest.approx(3.5)

    def test_maximum_mae_is_most_negative(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, mae_r=-0.5),
                win(1.5, mae_r=-0.25),
                loss(mae_r=-1.5),
            ]
        )

        assert analytics.maximum_mae_r == pytest.approx(-1.5)

    def test_mae_sign_preserved(self, engine):
        analytics = engine.analyze(
            [win(2.0, mae_r=-0.5)]
        )

        assert analytics.average_mae_r == pytest.approx(-0.5)
        assert analytics.maximum_mae_r == pytest.approx(-0.5)

    def test_missing_mfe_mae_skipped(self, engine):
        missing_mfe = make_result(
            status=ValidationStatus.WIN,
            realized_r=2.0,
            mfe_r=None,
        )
        analytics = engine.analyze(
            [missing_mfe, win(2.0, mfe_r=2.0)]
        )

        assert analytics.average_mfe_r == pytest.approx(2.0)


# ============================================================
# DURATION
# ============================================================


class TestDuration:
    def test_average_duration(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, duration=3),
                win(1.5, duration=5),
                loss(duration=4),
            ]
        )

        assert analytics.average_duration_candles == pytest.approx(4.0)

    def test_min_duration(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, duration=3),
                win(1.5, duration=1),
                loss(duration=4),
            ]
        )

        assert analytics.minimum_duration_candles == 1

    def test_max_duration(self, engine):
        analytics = engine.analyze(
            [
                win(2.0, duration=3),
                win(1.5, duration=7),
                loss(duration=4),
            ]
        )

        assert analytics.maximum_duration_candles == 7

    def test_untriggered_excluded_from_duration(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [
                win(2.0, duration=3),
                not_triggered(),
                expired(duration=50),
            ]
        )

        assert analytics.average_duration_candles == pytest.approx(3.0)
        assert analytics.maximum_duration_candles == 3


# ============================================================
# STREAKS
# ============================================================


class TestStreaks:
    def test_consecutive_wins(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                win(1.5),
                win(3.0),
                loss(),
            ]
        )

        assert analytics.maximum_winning_streak == 3

    def test_consecutive_losses(self, engine):
        analytics = engine.analyze(
            [
                loss(),
                loss(),
                loss(),
                win(2.0),
            ]
        )

        assert analytics.maximum_losing_streak == 3

    def test_alternating_outcomes(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                loss(),
                win(1.5),
                loss(),
            ]
        )

        assert analytics.maximum_winning_streak == 1
        assert analytics.maximum_losing_streak == 1

    def test_ambiguous_breaks_streak(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                win(1.5),
                ambiguous(),
                win(3.0),
            ]
        )

        assert analytics.maximum_winning_streak == 2

    def test_expired_breaks_streak(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                win(1.5),
                expired(),
                win(3.0),
            ]
        )

        assert analytics.maximum_winning_streak == 2

    def test_not_triggered_breaks_streak(self, engine):
        analytics = engine.analyze(
            [
                loss(),
                loss(),
                not_triggered(),
                loss(),
            ]
        )

        assert analytics.maximum_losing_streak == 2

    def test_open_breaks_streak(self, engine):
        analytics = engine.analyze(
            [
                win(2.0),
                win(1.5),
                open_result(),
                win(3.0),
            ]
        )

        assert analytics.maximum_winning_streak == 2

    def test_long_winning_streak(self, engine):
        analytics = engine.analyze(
            [
                win(1.0),
                win(1.0),
                win(1.0),
                win(1.0),
                win(1.0),
                loss(),
            ]
        )

        assert analytics.maximum_winning_streak == 5

    def test_long_losing_streak(self, engine):
        analytics = engine.analyze(
            [
                loss(),
                loss(),
                loss(),
                loss(),
                loss(),
                loss(),
            ]
        )

        assert analytics.maximum_losing_streak == 6


# ============================================================
# DRAWDOWN
# ============================================================


class TestDrawdown:
    def test_known_equity_sequence(self, engine):
        # Exact spec example: +2 -1 +3 -2 -1
        # cumulative: 2, 1, 4, 2, 1
        # peak 4 -> trough 1 -> max drawdown 3
        results = [
            make_result(
                status=ValidationStatus.WIN,
                realized_r=2.0,
            ),
            make_result(
                status=ValidationStatus.LOSS,
                realized_r=-1.0,
            ),
            make_result(
                status=ValidationStatus.WIN,
                realized_r=3.0,
            ),
            make_result(
                status=ValidationStatus.LOSS,
                realized_r=-2.0,
            ),
            make_result(
                status=ValidationStatus.LOSS,
                realized_r=-1.0,
            ),
        ]

        analytics = engine.analyze(results)

        assert analytics.cumulative_r == pytest.approx(1.0)
        assert analytics.equity_curve == pytest.approx(
            [2.0, 1.0, 4.0, 2.0, 1.0]
        )
        assert analytics.max_drawdown_r == pytest.approx(3.0)

    def test_drawdown_is_positive_magnitude(
        self,
        engine,
    ):
        # +1 -1 -1 -1 -> cumulative 1, 0, -1, -2
        # peak 1 -> trough -2 -> drawdown 3
        analytics = engine.analyze(
            [win(1.0), loss(), loss(), loss()]
        )

        assert analytics.max_drawdown_r >= 0.0
        assert analytics.max_drawdown_r == pytest.approx(3.0)

    def test_no_drawdown_when_only_wins(self, engine):
        analytics = engine.analyze(
            [win(1.0), win(2.0), win(3.0)]
        )

        assert analytics.max_drawdown_r == 0.0

    def test_empty_results_no_drawdown(self, engine):
        analytics = engine.analyze([])

        assert analytics.max_drawdown_r == 0.0

    def test_single_trade_no_drawdown(self, engine):
        analytics = engine.analyze([win(2.0)])

        assert analytics.max_drawdown_r == 0.0
        assert analytics.equity_curve == (2.0,)


# ============================================================
# IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_analytics_is_immutable(self, engine):
        analytics = engine.analyze([win(2.0), loss()])

        with pytest.raises(FrozenInstanceError):
            analytics.win_rate = 99.0

    def test_equity_curve_is_tuple(self, engine):
        analytics = engine.analyze(
            [win(2.0), loss()]
        )

        assert isinstance(analytics.equity_curve, tuple)

    def test_returns_performance_analytics_type(
        self,
        engine,
    ):
        analytics = engine.analyze([])

        assert isinstance(analytics, PerformanceAnalytics)


# ============================================================
# GRADE
# ============================================================


class TestGrade:
    def test_no_completed_trades_insufficient(
        self,
        engine,
    ):
        analytics = engine.analyze([not_triggered()])

        assert analytics.grade == PerformanceGrade.INSUFFICIENT

    def test_strong_grade(self, engine):
        analytics = engine.analyze([win(3.0), win(2.0)])

        assert analytics.expectancy == pytest.approx(2.5)
        assert analytics.grade == PerformanceGrade.STRONG

    def test_positive_grade(self, engine):
        analytics = engine.analyze(
            [win(0.5), loss()]
        )

        assert analytics.expectancy == pytest.approx(-0.25)
        assert analytics.grade == PerformanceGrade.MARGINAL

    def test_weak_grade(self, engine):
        analytics = engine.analyze(
            [win(0.5), loss(), loss(), loss()]
        )

        assert analytics.expectancy == pytest.approx(-0.625)
        assert analytics.grade == PerformanceGrade.WEAK


# ============================================================
# EDGE CASES
# ============================================================


class TestEdgeCases:
    def test_empty_results_no_crash(self, engine):
        analytics = engine.analyze([])

        assert analytics.total_r == 0.0
        assert analytics.expectancy == 0.0
        assert analytics.win_rate == 0.0

    def test_only_not_triggered(self, engine):
        analytics = engine.analyze(
            [not_triggered(), not_triggered()]
        )

        assert analytics.completed_trades == 0
        assert analytics.win_rate == 0.0
        assert analytics.expectancy == 0.0

    def test_only_expired(self, engine):
        analytics = engine.analyze(
            [expired(), expired()]
        )

        assert analytics.expired == 2
        assert analytics.completed_trades == 0
        assert analytics.total_r == 0.0

    def test_only_ambiguous(self, engine):
        analytics = engine.analyze(
            [ambiguous(), ambiguous()]
        )

        assert analytics.ambiguous == 2
        assert analytics.completed_trades == 0
        assert analytics.total_r == 0.0

    def test_single_completed_trade(self, engine):
        analytics = engine.analyze([win(2.0)])

        assert analytics.completed_trades == 1
        assert analytics.total_r == pytest.approx(2.0)
        assert analytics.average_r == pytest.approx(2.0)
        assert analytics.win_rate == 100.0

    def test_zero_wins(self, engine):
        analytics = engine.analyze(
            [loss(), loss(), expired()]
        )

        assert analytics.wins == 0
        assert analytics.win_rate == 0.0

    def test_zero_losses_profit_factor_inf(self, engine):
        analytics = engine.analyze(
            [win(2.0), win(1.5)]
        )

        assert analytics.profit_factor == float("inf")

    def test_missing_realized_r_handled(self, engine):
        no_r = make_result(
            status=ValidationStatus.WIN,
            realized_r=None,
        )
        analytics = engine.analyze([no_r])

        assert analytics.total_r == 0.0

    def test_does_not_treat_unresolved_as_loss(
        self,
        engine,
    ):
        analytics = engine.analyze(
            [expired(), open_result(), not_triggered()]
        )

        assert analytics.losses == 0
        assert analytics.total_r == 0.0

    def test_deterministic_repeatable(self, engine):
        results = [win(2.0), loss(), win(1.5), loss()]

        first = engine.analyze(results)
        second = engine.analyze(results)

        assert first == second
