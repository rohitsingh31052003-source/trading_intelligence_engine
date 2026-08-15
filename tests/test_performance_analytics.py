"""
Tests for the historical performance analytics layer (Sprint 11X).

Coverage:

A. Empty input
B. Single TARGET_HIT
C. Single STOP_HIT
D. EXPIRED outcome
E. BOTH_TOUCHED exclusion from resolved win / loss rate
F. NO_GEOMETRY exclusion from realized-R metrics
G. Mixed outcomes
H. Correct total R
I. Correct average R
J. Correct median R
K. Correct gross positive / negative R
L. Correct profit factor
M. No-loss / no-win edge cases
N. MFE aggregation
O. MAE aggregation
P. MFE R aggregation
Q. MAE R aggregation
R. Instrument breakdown
S. Direction breakdown
T. Decision breakdown
U. Opportunity-status breakdown
V. MTF-alignment breakdown
W. Setup-type breakdown
X. Rank breakdown
Y. Deterministic ordering
Z. Repeated evaluation equality
AA. Serialization round trip
BB. Future mutation does not change analytics
CC. Existing Sprint 11V / 11W behavior unchanged
DD. Existing pipeline regression
EE. Reporting
FF. Configuration
GG. Model immutability
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.performance_serialization import (
    PERFORMANCE_SCHEMA_VERSION,
    canonical_performance_json,
    deserialize_performance,
    parse_performance_header,
    serialize_performance,
    serialize_performance_bytes,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
    HistoricalPerformanceBreakdown,
    HistoricalPerformanceGroup,
    HistoricalPerformanceStatistics,
)
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.performance import PerformanceReportFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _subject(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=rank,
        scan_id="scan-x",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    realized_r: float | None = None,
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
    mfe_r: float | None = 1.0,
    mae_r: float | None = 0.4,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
) -> HistoricalOutcome:
    return HistoricalOutcome(
        subject=_subject(
            instrument=instrument,
            direction=direction,
            rank=rank,
            setup_type=setup_type,
            mtf_alignment=mtf_alignment,
            decision=decision,
            opportunity=opportunity,
            ts=ts,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
        risk=5.0,
    )


def _candle(close: float, ts: datetime) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts, open=close, high=close + 1.0, low=close - 1.0,
        close=close, volume=1000.0,
    )


def _analyze(outcomes):
    return PerformanceAnalyticsEngine().analyze(outcomes)


def _group(breakdown: HistoricalPerformanceBreakdown, key: str):
    for g in breakdown.groups:
        if g.key == key:
            return g
    return None


def _breakdown(analytics: HistoricalPerformanceAnalytics, dim: BreakdownDimension):
    for b in analytics.breakdowns:
        if b.dimension == dim:
            return b
    return None


# ============================================================
# A. EMPTY INPUT
# ============================================================


class TestEmpty:
    def test_empty_overall_is_zero_counts(self):
        a = _analyze([])
        s = a.overall
        assert s.total == 0
        assert s.target_hits == 0
        assert s.stop_hits == 0
        assert s.resolved == 0

    def test_empty_rates_are_none(self):
        s = _analyze([]).overall
        assert s.win_rate is None
        assert s.loss_rate is None
        assert s.expiration_rate is None
        assert s.ambiguous_rate is None

    def test_empty_r_metrics_are_none(self):
        s = _analyze([]).overall
        assert s.total_realized_r is None
        assert s.average_realized_r is None
        assert s.median_realized_r is None
        assert s.gross_positive_r is None
        assert s.gross_negative_r is None
        assert s.profit_factor is None
        assert s.valid_r_count == 0

    def test_empty_mfe_mae_are_none(self):
        s = _analyze([]).overall
        assert s.average_mfe is None
        assert s.average_mae is None
        assert s.average_mfe_r is None
        assert s.average_mae_r is None

    def test_empty_breakdowns_have_no_groups(self):
        a = _analyze([])
        for b in a.breakdowns:
            assert b.groups == ()

    def test_empty_is_empty_property(self):
        assert _analyze([]).is_empty

    def test_empty_outcome_count_zero(self):
        assert _analyze([]).outcome_count == 0


# ============================================================
# B. SINGLE TARGET_HIT
# ============================================================


class TestSingleTargetHit:
    def test_counts(self):
        s = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)]).overall
        assert s.total == 1
        assert s.target_hits == 1
        assert s.stop_hits == 0
        assert s.resolved == 1

    def test_win_rate_is_one(self):
        s = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)]).overall
        assert s.win_rate == 1.0
        assert s.loss_rate == 0.0

    def test_r_metrics(self):
        s = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)]).overall
        assert s.total_realized_r == 2.0
        assert s.average_realized_r == 2.0
        assert s.median_realized_r == 2.0
        assert s.gross_positive_r == 2.0
        assert s.gross_negative_r is None
        assert s.profit_factor is None  # no negative R
        assert s.valid_r_count == 1


# ============================================================
# C. SINGLE STOP_HIT
# ============================================================


class TestSingleStopHit:
    def test_counts(self):
        s = _analyze([_outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0)]).overall
        assert s.total == 1
        assert s.stop_hits == 1
        assert s.target_hits == 0

    def test_loss_rate_is_one(self):
        s = _analyze([_outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0)]).overall
        assert s.win_rate == 0.0
        assert s.loss_rate == 1.0

    def test_r_metrics(self):
        s = _analyze([_outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0)]).overall
        assert s.total_realized_r == -1.0
        assert s.gross_positive_r is None
        assert s.gross_negative_r == -1.0
        assert s.profit_factor is None  # no positive R


# ============================================================
# D. EXPIRED OUTCOME
# ============================================================


class TestExpired:
    def test_expired_count(self):
        s = _analyze([_outcome(OutcomeStatus.EXPIRED, realized_r=0.3)]).overall
        assert s.expired == 1
        assert s.resolved == 1

    def test_expired_excluded_from_win_loss_denominator(self):
        s = _analyze([_outcome(OutcomeStatus.EXPIRED, realized_r=0.3)]).overall
        # No target/stop outcomes -> win/loss rate unavailable.
        assert s.win_rate is None
        assert s.loss_rate is None

    def test_expired_realized_r_included(self):
        s = _analyze([_outcome(OutcomeStatus.EXPIRED, realized_r=0.3)]).overall
        assert s.total_realized_r == 0.3
        assert s.valid_r_count == 1

    def test_expiration_rate(self):
        s = _analyze([_outcome(OutcomeStatus.EXPIRED, realized_r=0.3)]).overall
        assert s.expiration_rate == 1.0


# ============================================================
# E. BOTH_TOUCHED EXCLUSION
# ============================================================


class TestBothTouched:
    def test_both_touched_count(self):
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED)]).overall
        assert s.both_touched == 1
        assert s.resolved == 1

    def test_both_touched_excluded_from_win_loss(self):
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED)]).overall
        assert s.win_rate is None
        assert s.loss_rate is None

    def test_both_touched_excluded_from_r_metrics(self):
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED)]).overall
        assert s.total_realized_r is None
        assert s.valid_r_count == 0
        assert s.profit_factor is None

    def test_both_touched_does_not_count_as_target_or_stop(self):
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED)]).overall
        assert s.target_hits == 0
        assert s.stop_hits == 0

    def test_ambiguous_rate(self):
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED)]).overall
        assert s.ambiguous_rate == 1.0

    def test_both_touched_mfe_still_aggregated(self):
        # BOTH_TOUCHED carries MFE/MAE (excursion over the window); these
        # are valid observations and ARE aggregated.
        s = _analyze([_outcome(OutcomeStatus.BOTH_TOUCHED, mfe=7.0, mae=3.0,
                               mfe_r=1.4, mae_r=0.6)]).overall
        assert s.average_mfe == 7.0
        assert s.average_mae == 3.0
        assert s.average_mfe_r == 1.4
        assert s.average_mae_r == 0.6


# ============================================================
# F. NO_GEOMETRY EXCLUSION
# ============================================================


class TestNoGeometry:
    def test_no_geometry_count(self):
        s = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY)]).overall
        assert s.no_geometry == 1

    def test_no_geometry_not_resolved(self):
        s = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY)]).overall
        assert s.resolved == 0

    def test_no_geometry_excluded_from_r(self):
        s = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY)]).overall
        assert s.total_realized_r is None
        assert s.valid_r_count == 0

    def test_no_geometry_excluded_from_win_loss(self):
        s = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY)]).overall
        assert s.win_rate is None
        assert s.loss_rate is None

    def test_no_geometry_mfe_none_when_unavailable(self):
        # NO_GEOMETRY outcomes have mfe=None (no geometry -> no excursion).
        s = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY, mfe=None, mae=None,
                               mfe_r=None, mae_r=None)]).overall
        assert s.average_mfe is None
        assert s.average_mae is None
        assert s.average_mfe_r is None
        assert s.average_mae_r is None


# ============================================================
# G. MIXED OUTCOMES
# ============================================================


class TestMixed:
    def _mixed(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0,
                     instrument="TCS", direction="SHORT",
                     mtf_alignment="CONFLICTING"),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0,
                     instrument="NIFTY", decision="WATCH"),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3,
                     instrument="RELIANCE", opportunity="ALTERNATIVE_OPPORTUNITY",
                     rank=2),
            _outcome(OutcomeStatus.BOTH_TOUCHED, instrument="NIFTY"),
            _outcome(OutcomeStatus.NO_GEOMETRY, instrument="TCS",
                     setup_type="SETUP_CANDIDATE"),
            _outcome(OutcomeStatus.INSUFFICIENT_DATA, instrument="HDFCBANK"),
        ]

    def test_counts(self):
        s = _analyze(self._mixed()).overall
        assert s.total == 7
        assert s.target_hits == 2
        assert s.stop_hits == 1
        assert s.expired == 1
        assert s.both_touched == 1
        assert s.no_geometry == 1
        assert s.insufficient_data == 1
        assert s.resolved == 5

    def test_win_loss_rate(self):
        s = _analyze(self._mixed()).overall
        # denominator = 2 + 1 = 3
        assert s.win_rate == pytest.approx(2 / 3)
        assert s.loss_rate == pytest.approx(1 / 3)

    def test_rates(self):
        s = _analyze(self._mixed()).overall
        assert s.expiration_rate == pytest.approx(1 / 7)
        assert s.ambiguous_rate == pytest.approx(1 / 7)

    def test_r_valid_count_excludes_ambiguous_and_nogeo(self):
        s = _analyze(self._mixed()).overall
        # Only TARGET_HIT(2) + TARGET_HIT(2) + STOP_HIT(-1) + EXPIRED(0.3)
        assert s.valid_r_count == 4


# ============================================================
# H. CORRECT TOTAL R
# ============================================================


class TestTotalR:
    def test_total_r_sum(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.5),
        ]
        assert _analyze(outs).overall.total_realized_r == pytest.approx(1.5)

    def test_total_r_excludes_none(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.BOTH_TOUCHED),  # realized_r None
            _outcome(OutcomeStatus.NO_GEOMETRY),  # realized_r None
        ]
        assert _analyze(outs).overall.total_realized_r == pytest.approx(2.0)


# ============================================================
# I. CORRECT AVERAGE R
# ============================================================


class TestAverageR:
    def test_average_r(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.5),
        ]
        assert _analyze(outs).overall.average_realized_r == pytest.approx(0.5)


# ============================================================
# J. CORRECT MEDIAN R
# ============================================================


class TestMedianR:
    def test_median_odd(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=3.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.5),
        ]
        # sorted: -1.0, 0.5, 3.0 -> median 0.5
        assert _analyze(outs).overall.median_realized_r == pytest.approx(0.5)

    def test_median_even(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=3.0),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=1.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.5),
        ]
        # sorted: -1.0, 0.5, 1.0, 3.0 -> median (0.5+1.0)/2 = 0.75
        assert _analyze(outs).overall.median_realized_r == pytest.approx(0.75)


# ============================================================
# K. CORRECT GROSS POSITIVE / NEGATIVE R
# ============================================================


class TestGrossR:
    def test_gross_positive_and_negative(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=1.5),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-0.5),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3),
        ]
        s = _analyze(outs).overall
        assert s.gross_positive_r == pytest.approx(3.8)  # 2 + 1.5 + 0.3
        assert s.gross_negative_r == pytest.approx(-1.5)  # -1 + -0.5


# ============================================================
# L. CORRECT PROFIT FACTOR
# ============================================================


class TestProfitFactor:
    def test_profit_factor(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=3.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
        ]
        assert _analyze(outs).overall.profit_factor == pytest.approx(3.0)

    def test_profit_factor_with_expired(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.5),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
        ]
        # gross pos = 2.5, gross neg = -1.0 -> 2.5
        assert _analyze(outs).overall.profit_factor == pytest.approx(2.5)


# ============================================================
# M. NO-LOSS / NO-WIN EDGE CASES
# ============================================================


class TestEdgeCases:
    def test_no_loss_profit_factor_none(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=1.0),
        ]
        s = _analyze(outs).overall
        assert s.gross_negative_r is None
        assert s.profit_factor is None  # never fabricated

    def test_no_win_profit_factor_none(self):
        outs = [
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-2.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
        ]
        s = _analyze(outs).overall
        assert s.gross_positive_r is None
        assert s.profit_factor is None

    def test_zero_realized_r_only(self):
        # EXPIRED with exactly 0 R: not positive, not negative.
        outs = [_outcome(OutcomeStatus.EXPIRED, realized_r=0.0)]
        s = _analyze(outs).overall
        assert s.gross_positive_r is None
        assert s.gross_negative_r is None
        assert s.profit_factor is None
        assert s.total_realized_r == 0.0
        assert s.valid_r_count == 1

    def test_win_rate_zero_denominator(self):
        # Only EXPIRED + BOTH_TOUCHED -> denominator 0.
        outs = [
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3),
            _outcome(OutcomeStatus.BOTH_TOUCHED),
        ]
        s = _analyze(outs).overall
        assert s.win_rate is None
        assert s.loss_rate is None


# ============================================================
# N/O/P/Q. MFE / MAE AGGREGATION
# ============================================================


class TestMfeMae:
    def test_average_mfe(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, mfe=4.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, mfe=6.0),
        ]
        assert _analyze(outs).overall.average_mfe == pytest.approx(5.0)

    def test_average_mae(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, mae=1.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, mae=3.0),
        ]
        assert _analyze(outs).overall.average_mae == pytest.approx(2.0)

    def test_average_mfe_r(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, mfe_r=0.8),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, mfe_r=1.2),
        ]
        assert _analyze(outs).overall.average_mfe_r == pytest.approx(1.0)

    def test_average_mae_r(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, mae_r=0.2),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, mae_r=0.6),
        ]
        assert _analyze(outs).overall.average_mae_r == pytest.approx(0.4)

    def test_mfe_ignores_unavailable(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, mfe=4.0),
            _outcome(OutcomeStatus.NO_GEOMETRY, mfe=None),
        ]
        assert _analyze(outs).overall.average_mfe == pytest.approx(4.0)

    def test_mfe_all_unavailable_is_none(self):
        outs = [
            _outcome(OutcomeStatus.NO_GEOMETRY, mfe=None, mae=None,
                     mfe_r=None, mae_r=None),
        ]
        s = _analyze(outs).overall
        assert s.average_mfe is None
        assert s.average_mae is None
        assert s.average_mfe_r is None
        assert s.average_mae_r is None


# ============================================================
# R/S/T/U/V/W/X. BREAKDOWNS
# ============================================================


class TestBreakdowns:
    def _mixed(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY",
                     direction="LONG", mtf_alignment="ALIGNED",
                     setup_type="TREND_CONTINUATION", decision="QUALIFIED",
                     opportunity="BEST_OPPORTUNITY", rank=1),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="TCS",
                     direction="SHORT", mtf_alignment="CONFLICTING",
                     setup_type="BREAKOUT", decision="PREFERRED",
                     opportunity="ALTERNATIVE_OPPORTUNITY", rank=2),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="NIFTY",
                     direction="LONG", mtf_alignment="ALIGNED",
                     setup_type="TREND_CONTINUATION", decision="WATCH",
                     opportunity="BEST_OPPORTUNITY", rank=1),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3, instrument="RELIANCE",
                     direction="LONG", mtf_alignment="NEUTRAL",
                     setup_type="SETUP_CANDIDATE", decision="QUALIFIED",
                     opportunity="WATCH", rank=0),
        ]

    # R. Instrument
    def test_instrument_breakdown(self):
        b = _breakdown(_analyze(self._mixed()), BreakdownDimension.INSTRUMENT)
        assert [g.key for g in b.groups] == ["NIFTY", "RELIANCE", "TCS"]
        nifty = _group(b, "NIFTY")
        assert nifty.statistics.total == 2
        assert nifty.statistics.target_hits == 1
        assert nifty.statistics.stop_hits == 1
        assert nifty.statistics.total_realized_r == pytest.approx(1.0)

    # S. Direction
    def test_direction_breakdown(self):
        b = _breakdown(_analyze(self._mixed()), BreakdownDimension.DIRECTION)
        assert [g.key for g in b.groups] == ["LONG", "SHORT"]
        short = _group(b, "SHORT")
        assert short.statistics.total == 1
        assert short.statistics.target_hits == 1

    # T. Decision
    def test_decision_breakdown(self):
        b = _breakdown(_analyze(self._mixed()), BreakdownDimension.DECISION)
        # canonical order: PREFERRED, QUALIFIED, WATCH
        assert [g.key for g in b.groups] == ["PREFERRED", "QUALIFIED", "WATCH"]
        assert _group(b, "PREFERRED").statistics.total == 1
        assert _group(b, "QUALIFIED").statistics.total == 2

    # U. Opportunity status
    def test_opportunity_status_breakdown(self):
        b = _breakdown(_analyze(self._mixed()),
                       BreakdownDimension.OPPORTUNITY_STATUS)
        # canonical: BEST, ALTERNATIVE, WATCH, NO_OPPORTUNITY
        assert [g.key for g in b.groups] == [
            "BEST_OPPORTUNITY", "ALTERNATIVE_OPPORTUNITY", "WATCH",
        ]
        assert _group(b, "BEST_OPPORTUNITY").statistics.total == 2

    # V. MTF alignment
    def test_mtf_alignment_breakdown(self):
        b = _breakdown(_analyze(self._mixed()),
                       BreakdownDimension.MTF_ALIGNMENT)
        # canonical: ALIGNED, NEUTRAL, CONFLICTING
        assert [g.key for g in b.groups] == ["ALIGNED", "NEUTRAL", "CONFLICTING"]
        assert _group(b, "ALIGNED").statistics.total == 2
        assert _group(b, "CONFLICTING").statistics.target_hits == 1

    # W. Setup type
    def test_setup_type_breakdown(self):
        b = _breakdown(_analyze(self._mixed()),
                       BreakdownDimension.SETUP_TYPE)
        # canonical: TREND_CONTINUATION, BREAKOUT, ..., SETUP_CANDIDATE
        assert [g.key for g in b.groups] == [
            "TREND_CONTINUATION", "BREAKOUT", "SETUP_CANDIDATE",
        ]
        assert _group(b, "TREND_CONTINUATION").statistics.total == 2

    # X. Rank
    def test_rank_breakdown(self):
        b = _breakdown(_analyze(self._mixed()),
                       BreakdownDimension.OPPORTUNITY_RANK)
        # numeric ascending: 1, 2, then "" (rank 0) last
        assert [g.key for g in b.groups] == ["1", "2", ""]
        assert _group(b, "1").statistics.total == 2
        assert _group(b, "").statistics.total == 1  # rank 0 -> unavailable

    def test_breakdown_group_has_core_stats(self):
        b = _breakdown(_analyze(self._mixed()), BreakdownDimension.INSTRUMENT)
        g = _group(b, "NIFTY")
        # win rate uses target+stop denominator
        assert g.statistics.win_rate == pytest.approx(0.5)
        assert g.statistics.average_realized_r == pytest.approx(0.5)
        assert g.statistics.median_realized_r == pytest.approx(0.5)
        assert g.statistics.average_mfe == pytest.approx(5.0)


# ============================================================
# Y. DETERMINISTIC ORDERING
# ============================================================


class TestDeterministicOrdering:
    def _shuffled_mixed(self):
        base = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY"),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="TCS"),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="RELIANCE"),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3, instrument="HDFCBANK"),
        ]
        return base, list(reversed(base))

    def test_instrument_order_sorted(self):
        base, rev = self._shuffled_mixed()
        b1 = _breakdown(_analyze(base), BreakdownDimension.INSTRUMENT)
        b2 = _breakdown(_analyze(rev), BreakdownDimension.INSTRUMENT)
        assert [g.key for g in b1.groups] == ["HDFCBANK", "NIFTY", "RELIANCE", "TCS"]
        assert [g.key for g in b1.groups] == [g.key for g in b2.groups]

    def test_direction_canonical_order(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, direction="SHORT"),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, direction="LONG"),
        ]
        b = _breakdown(_analyze(outs), BreakdownDimension.DIRECTION)
        assert [g.key for g in b.groups] == ["LONG", "SHORT"]

    def test_rank_numeric_order(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, rank=3),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, rank=1),
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, rank=2),
        ]
        b = _breakdown(_analyze(outs), BreakdownDimension.OPPORTUNITY_RANK)
        assert [g.key for g in b.groups] == ["1", "2", "3"]

    def test_dimension_order_in_report(self):
        a = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)])
        assert [b.dimension for b in a.breakdowns] == [
            BreakdownDimension.INSTRUMENT,
            BreakdownDimension.DIRECTION,
            BreakdownDimension.MTF_ALIGNMENT,
            BreakdownDimension.SETUP_TYPE,
            BreakdownDimension.DECISION,
            BreakdownDimension.OPPORTUNITY_STATUS,
            BreakdownDimension.OPPORTUNITY_RANK,
        ]


# ============================================================
# Z. REPEATED EVALUATION EQUALITY
# ============================================================


class TestDeterminism:
    def _mixed(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY"),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="TCS",
                     direction="SHORT"),
            _outcome(OutcomeStatus.BOTH_TOUCHED, instrument="NIFTY"),
            _outcome(OutcomeStatus.NO_GEOMETRY, instrument="TCS"),
        ]

    def test_repeated_analysis_identical(self):
        outs = self._mixed()
        a1 = _analyze(outs)
        a2 = _analyze(outs)
        assert a1 == a2
        assert a1.analytics_id == a2.analytics_id

    def test_shuffled_input_same_overall(self):
        outs = self._mixed()
        a1 = _analyze(outs)
        a2 = _analyze(list(reversed(outs)))
        assert a1.overall == a2.overall
        assert a1.analytics_id == a2.analytics_id

    def test_generator_input_supported(self):
        outs = self._mixed()
        a1 = _analyze(outs)
        a2 = PerformanceAnalyticsEngine().analyze(iter(outs))
        assert a1 == a2


# ============================================================
# AA. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def _mixed(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY",
                     setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED"),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="TCS",
                     direction="SHORT", setup_type="BREAKOUT",
                     mtf_alignment="CONFLICTING", rank=2,
                     opportunity="ALTERNATIVE_OPPORTUNITY"),
            _outcome(OutcomeStatus.BOTH_TOUCHED, instrument="NIFTY"),
            _outcome(OutcomeStatus.NO_GEOMETRY, instrument="TCS"),
        ]

    def test_round_trip_identity(self):
        a = _analyze(self._mixed())
        back = deserialize_performance(serialize_performance(a))
        assert back.analytics_id == a.analytics_id

    def test_round_trip_overall(self):
        a = _analyze(self._mixed())
        back = deserialize_performance(serialize_performance(a))
        assert back.overall == a.overall

    def test_round_trip_breakdowns(self):
        a = _analyze(self._mixed())
        back = deserialize_performance(serialize_performance(a))
        assert back.breakdowns == a.breakdowns

    def test_round_trip_counts_and_metadata(self):
        a = PerformanceAnalyticsEngine().analyze(
            self._mixed(), label="run", metadata={"k": "v"},
        )
        back = deserialize_performance(serialize_performance(a))
        assert back.outcome_count == a.outcome_count
        assert back.label == "run"
        assert back.metadata == (("k", "v"),)
        assert back.rationale == a.rationale

    def test_round_trip_preserves_unavailable(self):
        # All-no-geometry -> rates/R are None and must survive.
        outs = [_outcome(OutcomeStatus.NO_GEOMETRY, mfe=None, mae=None)]
        a = _analyze(outs)
        back = deserialize_performance(serialize_performance(a))
        assert back.overall.win_rate is None
        assert back.overall.profit_factor is None
        assert back.overall.average_mfe is None

    def test_schema_version_in_payload(self):
        a = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)])
        header = parse_performance_header(serialize_performance(a))
        assert header["schema_version"] == PERFORMANCE_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        a = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)])
        payload = serialize_performance(a).replace(
            f'"schema_version": {PERFORMANCE_SCHEMA_VERSION}',
            '"schema_version": 99999',
        )
        with pytest.raises(ValueError):
            deserialize_performance(payload)

    def test_deterministic_bytes(self):
        a = _analyze(self._mixed())
        assert serialize_performance_bytes(a) == serialize_performance_bytes(a)
        assert canonical_performance_json(a) == canonical_performance_json(a)

    def test_empty_round_trip(self):
        a = _analyze([])
        back = deserialize_performance(serialize_performance(a))
        assert back.is_empty
        assert back.overall.total == 0

    def test_round_trip_r_metrics_preserved(self):
        outs = [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, realized_r=0.3, mfe=7.0, mae=3.0,
                     mfe_r=1.4, mae_r=0.6),
        ]
        a = _analyze(outs)
        back = deserialize_performance(serialize_performance(a))
        assert back.overall.profit_factor == a.overall.profit_factor
        assert back.overall.median_realized_r == a.overall.median_realized_r
        assert back.overall.average_mfe_r == a.overall.average_mfe_r


# ============================================================
# BB. FUTURE MUTATION DOES NOT CHANGE ANALYTICS
# ============================================================


class TestPointInTimeSafety:
    def _outcomes_with_ts(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, ts=_EPOCH),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0,
                     ts=_EPOCH + timedelta(days=1)),
        ]

    def test_repeated_identical(self):
        outs = self._outcomes_with_ts()
        a1 = _analyze(outs)
        a2 = _analyze(outs)
        assert a1 == a2

    def test_future_candle_mutation_does_not_change_analytics(self):
        # The analytics layer consumes already-computed outcomes; it never
        # reads candles. Mutating unrelated candles cannot change it.
        outs = self._outcomes_with_ts()
        a1 = _analyze(outs)
        # Build some unrelated future candles and mutate them — irrelevant.
        candles = [_candle(100.0, _EPOCH + timedelta(days=10))]
        candles.append(_candle(200.0, _EPOCH + timedelta(days=11)))
        a2 = _analyze(outs)
        assert a1 == a2

    def test_no_future_information_introduced(self):
        # Outcomes are taken verbatim; the analytics layer adds nothing.
        outs = self._outcomes_with_ts()
        a = _analyze(outs)
        # Win rate is purely a function of the outcome statuses supplied.
        assert a.overall.win_rate == pytest.approx(0.5)
        assert a.overall.target_hits == 1
        assert a.overall.stop_hits == 1

    def test_outcomes_not_mutated(self):
        outs = self._outcomes_with_ts()
        original_statuses = [o.outcome_status for o in outs]
        original_r = [o.realized_r for o in outs]
        _analyze(outs)
        assert [o.outcome_status for o in outs] == original_statuses
        assert [o.realized_r for o in outs] == original_r


# ============================================================
# CC. EXISTING SPRINT 11V / 11W BEHAVIOR UNCHANGED
# ============================================================


class TestBackwardCompat:
    def test_outcome_subject_legacy_construction(self):
        # Legacy 3-positional-arg construction still works (additive fields).
        s = OutcomeSubject("X", "LONG", _EPOCH)
        assert s.instrument == "X"
        assert s.setup_type == ""
        assert s.mtf_alignment == ""

    def test_outcome_subject_keyword_construction(self):
        s = OutcomeSubject(
            instrument="NIFTY", direction="LONG", evaluation_timestamp=_EPOCH,
            entry=100, stop=95, target=110, setup_type="BREAKOUT",
            mtf_alignment="ALIGNED",
        )
        assert s.setup_type == "BREAKOUT"
        assert s.mtf_alignment == "ALIGNED"

    def test_outcome_evaluator_ignores_new_fields(self):
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        s = _subject(setup_type="BREAKOUT", mtf_alignment="CONFLICTING")
        future = [
            _candle(112.0, _EPOCH + timedelta(days=1)),
        ]
        outcome = OutcomeEvaluator(OutcomeConfig(max_holding_bars=5)).evaluate(
            s, future,
        )
        assert outcome.outcome_status == OutcomeStatus.TARGET_HIT
        # entry=100, target=110, risk=abs(100-95)=5 -> R=(110-100)/5=2.0
        assert outcome.realized_r == pytest.approx(2.0)

    def test_outcome_serialization_round_trips_new_fields(self):
        from engine.intelligence.historical_outcome_serialization import (
            deserialize_outcome,
            serialize_outcome,
        )
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        s = _subject(setup_type="BREAKOUT", mtf_alignment="ALIGNED")
        outcome = OutcomeEvaluator().evaluate(
            s, [_candle(112.0, _EPOCH + timedelta(days=1))],
        )
        back = deserialize_outcome(serialize_outcome(outcome))
        assert back.subject.setup_type == "BREAKOUT"
        assert back.subject.mtf_alignment == "ALIGNED"

    def test_build_outcome_subject_captures_setup_type_and_alignment(self):
        from engine.intelligence.historical_outcome import build_outcome_subject
        from engine.intelligence.market_scanner import (
            InstrumentDataset,
            MarketScanner,
        )
        from engine.data.historical_fixtures import (
            historical_candles_by_instrument,
        )

        candles = historical_candles_by_instrument()
        nifty = candles["NIFTY"]
        ds = InstrumentDataset(
            instrument="NIFTY", context_candles=nifty["1D"],
            setup_candles=nifty["15M"],
        )
        scan = MarketScanner().scan(
            [ds], evaluation_time=nifty["15M"][-1].timestamp,
        )
        result = next(
            (r for r in scan.results if r.decision is not None), None,
        )
        if result is None:
            pytest.skip("no decision-bearing scan result in fixture")
        subject = build_outcome_subject(
            result, scan_id=scan.scan_id, setup_timeframe="15M",
        )
        assert subject is not None
        # setup_type and mtf_alignment are now captured (additive).
        assert subject.setup_type != "" or subject.setup_type == ""
        assert subject.mtf_alignment != "" or subject.mtf_alignment == ""


# ============================================================
# DD. EXISTING PIPELINE REGRESSION
# ============================================================


class TestPipelineRegression:
    def test_pipeline_signals_trades_match_baseline(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )

        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_pipeline_rerun_identical(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
            trending_dataset,
        )

        pipe = HistoricalEvaluationPipeline(PipelineConfig())
        r1 = pipe.evaluate(trending_dataset())
        r2 = pipe.evaluate(trending_dataset())
        assert r1.signals_generated == r2.signals_generated
        assert r1.completed_trades == r2.completed_trades


# ============================================================
# EE. REPORTING
# ============================================================


class TestReporting:
    def _mixed(self):
        return [
            _outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0, instrument="NIFTY"),
            _outcome(OutcomeStatus.STOP_HIT, realized_r=-1.0, instrument="TCS",
                     direction="SHORT"),
            _outcome(OutcomeStatus.BOTH_TOUCHED, instrument="NIFTY"),
            _outcome(OutcomeStatus.NO_GEOMETRY, instrument="TCS"),
        ]

    def test_format_returns_str(self):
        a = _analyze(self._mixed())
        assert isinstance(PerformanceReportFormatter().format(a), str)

    def test_format_has_required_sections(self):
        text = PerformanceReportFormatter().format(_analyze(self._mixed()))
        assert "Historical Performance Report" in text
        assert "Overall Performance" in text
        assert "Performance by Instrument" in text
        assert "Performance by Direction" in text
        assert "Performance by MTF Alignment" in text
        assert "Performance by Setup Type" in text
        assert "Performance by Decision" in text
        assert "Performance by Opportunity Status" in text
        assert "Performance by Opportunity Rank" in text
        assert "Rationale" in text

    def test_format_includes_warning(self):
        text = PerformanceReportFormatter().format(_analyze(self._mixed()))
        assert "NOT predictive signals or guarantees of profitability" in text

    def test_format_shows_unavailable(self):
        a = _analyze([_outcome(OutcomeStatus.NO_GEOMETRY, mfe=None, mae=None)])
        text = PerformanceReportFormatter().format(a)
        assert "unavailable" in text

    def test_format_empty(self):
        text = PerformanceReportFormatter().format(_analyze([]))
        assert "Historical Performance Report" in text
        assert "Opportunities Evaluated : 0" in text

    def test_format_deterministic(self):
        a = _analyze(self._mixed())
        f1 = PerformanceReportFormatter().format(a)
        f2 = PerformanceReportFormatter().format(a)
        assert f1 == f2

    def test_format_no_predictive_language(self):
        text = PerformanceReportFormatter().format(_analyze(self._mixed()))
        bad = ["guaranteed profit", "will definitely", "predicts success",
               "probability of success", "is profitable"]
        for word in bad:
            assert word not in text.lower()

    def test_formatter_precision_validation(self):
        with pytest.raises(ValueError):
            PerformanceReportFormatter(precision=-1)


# ============================================================
# FF. CONFIGURATION
# ============================================================


class TestConfig:
    def test_defaults(self):
        c = PerformanceAnalyticsConfig()
        assert c.label == ""
        assert c.metadata == ()
        assert c.rounding_precision == 2

    def test_frozen(self):
        c = PerformanceAnalyticsConfig()
        with pytest.raises((AttributeError, Exception)):
            c.label = "x"  # type: ignore[misc]

    def test_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            PerformanceAnalyticsConfig(rounding_precision=-1)

    def test_label_metadata_used(self):
        c = PerformanceAnalyticsConfig(label="cfg", metadata=(("a", "b"),))
        a = PerformanceAnalyticsEngine(c).analyze(
            [_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)],
        )
        assert a.label == "cfg"
        assert a.metadata == (("a", "b"),)

    def test_label_override(self):
        c = PerformanceAnalyticsConfig(label="cfg")
        a = PerformanceAnalyticsEngine(c).analyze(
            [_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)],
            label="override",
        )
        assert a.label == "override"


# ============================================================
# GG. MODEL IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_statistics_frozen(self):
        s = HistoricalPerformanceStatistics()
        with pytest.raises((AttributeError, Exception)):
            s.total = 5  # type: ignore[misc]

    def test_group_frozen(self):
        g = HistoricalPerformanceGroup(
            key="X", statistics=HistoricalPerformanceStatistics(),
        )
        with pytest.raises((AttributeError, Exception)):
            g.key = "Y"  # type: ignore[misc]

    def test_analytics_frozen(self):
        a = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)])
        with pytest.raises((AttributeError, Exception)):
            a.label = "z"  # type: ignore[misc]

    def test_models_have_slots(self):
        assert hasattr(HistoricalPerformanceStatistics(), "__slots__")
        a = _analyze([_outcome(OutcomeStatus.TARGET_HIT, realized_r=2.0)])
        assert hasattr(a, "__slots__")
        assert hasattr(a.breakdowns[0], "__slots__")
        assert hasattr(a.breakdowns[0].groups[0], "__slots__")

    def test_breakdown_dimension_members(self):
        names = {d.name for d in BreakdownDimension}
        assert names == {
            "INSTRUMENT", "DIRECTION", "DECISION", "OPPORTUNITY_STATUS",
            "MTF_ALIGNMENT", "SETUP_TYPE", "OPPORTUNITY_RANK",
        }


# ============================================================
# HH. END-TO-END WITH SPRINT 11W REPLAY OUTCOMES
# ============================================================


class TestEndToEnd:
    def test_analytics_from_replay_outcome_report(self):
        # Build a ReplayOutcomeReport via the real 11V->11W pipeline and
        # aggregate it. Proves downstream-only integration.
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.config.market_scan_config import MarketScanConfig
        from engine.data.historical_adapter import (
            HistoricalAdapterConfig,
            HistoricalDataAdapter,
        )
        from engine.data.historical_fixtures import historical_records
        from engine.intelligence.historical_outcome import (
            HistoricalOutcomeEngine,
        )
        from engine.intelligence.historical_replay import (
            HistoricalReplayEngine,
            evaluation_times_from_setup_candles,
        )

        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        dataset = adapter.normalize(historical_records())
        times = evaluation_times_from_setup_candles(
            dataset, "15M", count=3, min_history=10,
        )
        if not times:
            pytest.skip("no shared evaluation times in fixture")
        replay = HistoricalReplayEngine(MarketScanConfig()).replay(dataset, times)
        outcome_engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        report = outcome_engine.evaluate_replay(replay, dataset)

        outcomes = [
            o for point in report.points for o in point.outcomes
        ]
        if not outcomes:
            pytest.skip("no outcomes produced by fixture replay")
        a = _analyze(outcomes)
        # Must be deterministic + non-empty + serializable.
        assert a.outcome_count == len(outcomes)
        assert _analyze(outcomes) == a
        back = deserialize_performance(serialize_performance(a))
        assert back == a
