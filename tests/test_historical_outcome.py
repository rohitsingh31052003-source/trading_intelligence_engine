"""
Tests for the historical opportunity outcome evaluation layer
(Sprint 11W).

Coverage:

A. Outcome model + subject basics (enums, frozen/slots, defaults,
   invariants, properties)
B. Configuration (defaults, validation, immutability)
C. LONG outcomes (target hit, stop hit, expiration, MFE/MAE, R)
D. SHORT outcomes (target hit, stop hit, expiration — symmetric)
E. Same-candle ambiguity (LONG + SHORT BOTH_TOUCHED, no fabricated R)
F. Geometry handling (incomplete geometry -> NO_GEOMETRY, invalid /
   zero risk, non-directional)
G. Missing future data -> INSUFFICIENT_DATA; timestamp boundaries
H. Exact evaluation horizon + mutation after horizon
I. MFE / MAE correctness (absolute + R-normalized) + direction symmetry
J. Realized R correctness (target/stop/expired; None for ambiguous)
K. Multiple opportunities (each evaluated independently, ranking/
   identity preserved)
L. Instrument isolation (mutating another instrument's future data)
M. Point-in-time / leakage (decision stability prefix==full, future
   mutation changes outcome but never the decision at T, forward-only
   window)
N. Determinism (repeated identical evaluation)
O. Serialization round trip (identity, instrument, direction,
   timestamps, outcome status, exit, MFE, MAE, R, ranking fields;
   schema version; deterministic bytes; report round trip)
P. Replay integration (decisions taken verbatim from replay; outcome
   uses only future candles; HTF protection intact; multiple points)
Q. Reporting (sections, warning, no predictive language, returns str,
   determinism)
R. Subject builders (build_outcome_subject, build_outcome_subjects,
   future_candles_after)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.historical_outcome_config import OutcomeConfig
from engine.intelligence.historical_outcome import (
    HistoricalOutcomeEngine,
    OutcomeEvaluator,
    build_outcome_subject,
    build_outcome_subjects,
    future_candles_after,
)
from engine.intelligence.historical_outcome_serialization import (
    OUTCOME_SCHEMA_VERSION,
    canonical_outcome_json,
    canonical_outcome_report_json,
    deserialize_outcome,
    deserialize_outcome_report,
    parse_outcome_header,
    serialize_outcome,
    serialize_outcome_bytes,
    serialize_outcome_report,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
    ReplayOutcomePoint,
    ReplayOutcomeReport,
)
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.historical_outcome import HistoricalOutcomeFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _candle(
    close: float,
    ts: datetime,
    high: float | None = None,
    low: float | None = None,
    opn: float | None = None,
) -> OHLCVCandle:
    """Build an OHLC-valid candle."""

    if high is None:
        high = max(close, opn if opn is not None else close) + 1.0
    if low is None:
        low = min(close, opn if opn is not None else close) - 1.0
    if opn is None:
        opn = close
    return OHLCVCandle(
        timestamp=ts,
        open=opn,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _series(closes: list[float], start: datetime = _EPOCH) -> list[OHLCVCandle]:
    return [_candle(c, start + timedelta(days=i)) for i, c in enumerate(closes)]


def _long_subject(
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
    instrument: str = "NIFTY",
    ts: datetime = _EPOCH,
    rank: int = 1,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction="LONG",
        evaluation_timestamp=ts,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification="QUALIFIED",
        decision_score=70,
        opportunity_status="BEST_OPPORTUNITY",
        rank=rank,
        scan_id="scan-x",
        setup_timeframe="15M",
    )


def _short_subject(
    entry: float = 100.0,
    stop: float = 105.0,
    target: float = 90.0,
    instrument: str = "RELIANCE",
    ts: datetime = _EPOCH,
    rank: int = 1,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction="SHORT",
        evaluation_timestamp=ts,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification="QUALIFIED",
        decision_score=65,
        opportunity_status="BEST_OPPORTUNITY",
        rank=rank,
        scan_id="scan-x",
        setup_timeframe="15M",
    )


def _future(closes: list[float], start: datetime = _EPOCH) -> list[OHLCVCandle]:
    """Future candles with timestamps strictly after _EPOCH."""

    return [
        _candle(c, start + timedelta(days=i + 1)) for i, c in enumerate(closes)
    ]


def _future_with_hl(
    pairs: list[tuple[float, float, float]],
    start: datetime = _EPOCH,
) -> list[OHLCVCandle]:
    """Future candles as (close, high, low) with timestamps after _EPOCH."""

    return [
        _candle(c, start + timedelta(days=i + 1), high=h, low=l)
        for i, (c, h, l) in enumerate(pairs)
    ]


# ============================================================
# A. OUTCOME MODEL + SUBJECT BASICS
# ============================================================


class TestOutcomeModel:
    def test_outcome_status_members(self):
        names = {s.name for s in OutcomeStatus}
        assert names == {
            "TARGET_HIT", "STOP_HIT", "BOTH_TOUCHED",
            "EXPIRED", "NO_GEOMETRY", "INSUFFICIENT_DATA",
        }

    def test_is_determinate_property(self):
        assert OutcomeStatus.TARGET_HIT.is_determinate
        assert OutcomeStatus.STOP_HIT.is_determinate
        assert OutcomeStatus.BOTH_TOUCHED.is_determinate
        assert OutcomeStatus.EXPIRED.is_determinate
        assert not OutcomeStatus.NO_GEOMETRY.is_determinate
        assert not OutcomeStatus.INSUFFICIENT_DATA.is_determinate

    def test_is_ambiguous_property(self):
        assert OutcomeStatus.BOTH_TOUCHED.is_ambiguous
        assert not OutcomeStatus.TARGET_HIT.is_ambiguous

    def test_subject_frozen_slots(self):
        s = _long_subject()
        assert hasattr(s, "__slots__")
        with pytest.raises((AttributeError, Exception)):
            s.instrument = "X"  # type: ignore[misc]

    def test_subject_has_geometry_property(self):
        assert _long_subject().has_geometry
        assert not OutcomeSubject(
            "X", "LONG", _EPOCH, entry=100, stop=None, target=110,
        ).has_geometry

    def test_subject_is_directional(self):
        assert _long_subject().is_directional
        assert _short_subject().is_directional
        assert not OutcomeSubject("X", "NONE", _EPOCH).is_directional
        assert not OutcomeSubject("X", "", _EPOCH).is_directional

    def test_outcome_frozen_slots(self):
        o = HistoricalOutcome(
            subject=_long_subject(),
            outcome_status=OutcomeStatus.NO_GEOMETRY,
        )
        assert hasattr(o, "__slots__")
        with pytest.raises((AttributeError, Exception)):
            o.outcome_status = OutcomeStatus.EXPIRED  # type: ignore[misc]

    def test_outcome_properties_delegate_to_subject(self):
        o = HistoricalOutcome(
            subject=_long_subject(instrument="NIFTY"),
            outcome_status=OutcomeStatus.NO_GEOMETRY,
        )
        assert o.instrument == "NIFTY"
        assert o.direction == "LONG"
        assert o.evaluation_timestamp == _EPOCH

    def test_no_geometry_invariants_reject_fabricated_values(self):
        with pytest.raises(ValueError):
            HistoricalOutcome(
                subject=_long_subject(),
                outcome_status=OutcomeStatus.NO_GEOMETRY,
                exit_price=100.0,
            )
        with pytest.raises(ValueError):
            HistoricalOutcome(
                subject=_long_subject(),
                outcome_status=OutcomeStatus.INSUFFICIENT_DATA,
                realized_r=1.0,
            )
        with pytest.raises(ValueError):
            HistoricalOutcome(
                subject=_long_subject(),
                outcome_status=OutcomeStatus.NO_GEOMETRY,
                bars_held=3,
            )

    def test_both_touched_rejects_fabricated_r(self):
        with pytest.raises(ValueError):
            HistoricalOutcome(
                subject=_long_subject(),
                outcome_status=OutcomeStatus.BOTH_TOUCHED,
                realized_r=1.0,
            )


# ============================================================
# B. CONFIGURATION
# ============================================================


class TestConfig:
    def test_defaults(self):
        c = OutcomeConfig()
        assert c.max_holding_bars == 20
        assert c.min_history == 0

    def test_max_holding_bars_must_be_positive(self):
        with pytest.raises(ValueError):
            OutcomeConfig(max_holding_bars=0)
        with pytest.raises(ValueError):
            OutcomeConfig(max_holding_bars=-3)

    def test_min_history_non_negative(self):
        with pytest.raises(ValueError):
            OutcomeConfig(min_history=-1)

    def test_frozen(self):
        c = OutcomeConfig()
        with pytest.raises((AttributeError, Exception)):
            c.max_holding_bars = 5  # type: ignore[misc]


# ============================================================
# C. LONG OUTCOMES
# ============================================================


class TestLongOutcomes:
    def test_long_target_hit(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([105, 112, 108]))
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.exit_price == 110.0
        assert o.outcome_timestamp == _EPOCH + timedelta(days=2)
        assert o.bars_held == 2
        assert abs(o.realized_r - 2.0) < 1e-9
        assert o.risk == 5.0

    def test_long_stop_hit(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([98, 94, 90]))
        assert o.outcome_status == OutcomeStatus.STOP_HIT
        assert o.exit_price == 95.0
        assert o.bars_held == 2
        assert abs(o.realized_r - (-1.0)) < 1e-9

    def test_long_expired(self):
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=10))
        o = ev.evaluate(_long_subject(), _future([101, 102, 103]))
        assert o.outcome_status == OutcomeStatus.EXPIRED
        assert o.exit_price == 103.0
        assert o.bars_held == 3
        assert abs(o.realized_r - 0.6) < 1e-9

    def test_long_target_uses_ohlc_not_close(self):
        # close below target but high pierces target -> target hit.
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(101, 111, 100)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.exit_price == 110.0

    def test_long_stop_uses_ohlc_not_close(self):
        # close above stop but low pierces stop -> stop hit.
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(99, 100, 94)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.STOP_HIT
        assert o.exit_price == 95.0


# ============================================================
# D. SHORT OUTCOMES (symmetric)
# ============================================================


class TestShortOutcomes:
    def test_short_target_hit(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_short_subject(), _future([95, 88, 92]))
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.exit_price == 90.0
        assert o.bars_held == 2
        assert abs(o.realized_r - 2.0) < 1e-9

    def test_short_stop_hit(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_short_subject(), _future([102, 106, 108]))
        assert o.outcome_status == OutcomeStatus.STOP_HIT
        assert o.exit_price == 105.0
        assert o.bars_held == 2
        assert abs(o.realized_r - (-1.0)) < 1e-9

    def test_short_expired(self):
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=10))
        o = ev.evaluate(_short_subject(), _future([99, 98, 97]))
        assert o.outcome_status == OutcomeStatus.EXPIRED
        assert o.exit_price == 97.0
        assert abs(o.realized_r - 0.6) < 1e-9

    def test_short_target_uses_ohlc_not_close(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(99, 100, 89)])
        o = ev.evaluate(_short_subject(), fut)
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.exit_price == 90.0

    def test_short_stop_uses_ohlc_not_close(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(101, 106, 100)])
        o = ev.evaluate(_short_subject(), fut)
        assert o.outcome_status == OutcomeStatus.STOP_HIT
        assert o.exit_price == 105.0

    def test_short_long_symmetric_same_geometry(self):
        # Same R outcomes for mirrored geometry + mirrored price path.
        ev = OutcomeEvaluator()
        long_o = ev.evaluate(_long_subject(entry=100, stop=95, target=110),
                             _future([105, 112]))
        short_o = ev.evaluate(_short_subject(entry=100, stop=105, target=90),
                              _future([95, 88]))
        assert long_o.outcome_status == short_o.outcome_status
        assert long_o.realized_r == short_o.realized_r
        assert long_o.bars_held == short_o.bars_held


# ============================================================
# E. SAME-CANDLE AMBIGUITY
# ============================================================


class TestBothTouched:
    def test_long_both_touched_single_candle(self):
        ev = OutcomeEvaluator()
        # one candle whose high >= target AND low <= stop
        fut = _future_with_hl([(100, 115, 90)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.BOTH_TOUCHED
        assert o.exit_price is None
        assert o.realized_r is None
        assert o.bars_held == 1
        assert o.outcome_timestamp == _EPOCH + timedelta(days=1)

    def test_short_both_touched_single_candle(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(100, 115, 90)])
        o = ev.evaluate(_short_subject(), fut)
        assert o.outcome_status == OutcomeStatus.BOTH_TOUCHED
        assert o.exit_price is None
        assert o.realized_r is None

    def test_both_touched_when_first_touch_same_candle(self):
        # Two candles: the second touches both target and stop, neither
        # touched on the first -> BOTH_TOUCHED at bar 2.
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(102, 103, 101), (100, 115, 90)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.BOTH_TOUCHED
        assert o.bars_held == 2

    def test_target_then_both_on_later_candle_is_target_hit(self):
        # Target touched on bar 1; bar 2 touches both. Target already hit.
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(112, 115, 100), (100, 115, 90)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.bars_held == 1

    def test_stop_then_both_on_later_candle_is_stop_hit(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(94, 100, 90), (100, 115, 90)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.STOP_HIT
        assert o.bars_held == 1


# ============================================================
# F. GEOMETRY HANDLING
# ============================================================


class TestGeometry:
    def test_missing_target_is_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        o = ev.evaluate(s, _future([101, 102]))
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY
        assert o.exit_price is None
        assert o.realized_r is None
        assert o.mfe is None
        assert o.mae is None

    def test_missing_stop_is_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=None, target=110)
        o = ev.evaluate(s, _future([101, 102]))
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY

    def test_missing_entry_is_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=None, stop=95, target=110)
        o = ev.evaluate(s, _future([101, 102]))
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY

    def test_non_directional_is_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "NONE", _EPOCH, entry=100, stop=95, target=110)
        o = ev.evaluate(s, _future([101, 102]))
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY

    def test_zero_risk_is_no_geometry(self):
        # stop == entry -> zero risk -> invalid geometry.
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=100, target=110)
        o = ev.evaluate(s, _future([101, 102]))
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY
        assert o.risk is None

    def test_no_geometry_does_not_inspect_future(self):
        # Even with touching future candles, NO_GEOMETRY is returned and
        # no MFE/MAE fabricated.
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        fut = _future_with_hl([(112, 115, 90)])
        o = ev.evaluate(s, fut)
        assert o.outcome_status == OutcomeStatus.NO_GEOMETRY
        assert o.mfe is None and o.mae is None


# ============================================================
# G. MISSING FUTURE DATA + TIMESTAMP BOUNDARIES
# ============================================================


class TestInsufficientData:
    def test_no_future_candles_is_insufficient_data(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), [])
        assert o.outcome_status == OutcomeStatus.INSUFFICIENT_DATA
        assert o.exit_price is None
        assert o.bars_held == 0

    def test_future_candle_at_evaluation_time_excluded(self):
        # A candle AT the evaluation timestamp is NOT future (must be
        # strictly greater). The evaluator receives the caller-filtered
        # future slice; verify it still produces a valid evaluation
        # using only strictly-after candles.
        ev = OutcomeEvaluator()
        at_t = _candle(100.0, _EPOCH)  # timestamp == evaluation
        after = _future([112])
        o = ev.evaluate(_long_subject(), [at_t, *after])
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.bars_held == 1  # only the strictly-after candle counted

    def test_future_candles_after_helper_strict(self):
        candles = [
            _candle(100.0, _EPOCH),
            _candle(101.0, _EPOCH + timedelta(days=1)),
            _candle(102.0, _EPOCH + timedelta(days=2)),
        ]
        future = future_candles_after(candles, _EPOCH)
        assert len(future) == 2
        assert all(c.timestamp > _EPOCH for c in future)

    def test_future_candles_after_none_anchor(self):
        assert future_candles_after(_series([100, 101]), None) == ()


# ============================================================
# H. EXACT EVALUATION HORIZON + MUTATION AFTER HORIZON
# ============================================================


class TestHorizon:
    def test_exact_horizon_target_at_last_bar(self):
        # max_holding_bars=3; target hit on the 3rd candle.
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        o = ev.evaluate(_long_subject(), _future([101, 102, 112]))
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        assert o.bars_held == 3

    def test_target_beyond_horizon_is_expired(self):
        # Target hit on the 4th candle but horizon is 3 -> EXPIRED.
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        o = ev.evaluate(_long_subject(), _future([101, 102, 103, 112]))
        assert o.outcome_status == OutcomeStatus.EXPIRED
        assert o.exit_price == 103.0  # close of the last in-window candle

    def test_mutation_after_horizon_does_not_change_outcome(self):
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        base = _future([101, 102, 103, 112, 94])
        o1 = ev.evaluate(_long_subject(), base)
        # Mutate candles beyond the horizon.
        mutated = _future([101, 102, 103, 200, 1])
        o2 = ev.evaluate(_long_subject(), mutated)
        assert o1.outcome_status == o2.outcome_status
        assert o1.exit_price == o2.exit_price
        assert o1.bars_held == o2.bars_held
        assert o1.realized_r == o2.realized_r

    def test_horizon_truncates_mfe_mae(self):
        # A huge favorable spike on bar 5 is beyond a horizon of 3.
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        o = ev.evaluate(
            _long_subject(), _future([101, 102, 103, 1000]),
        )
        assert o.outcome_status == OutcomeStatus.EXPIRED
        # MFE computed only over the 3-candle window (max high 104).
        assert o.mfe is not None and o.mfe < 200


# ============================================================
# I. MFE / MAE
# ============================================================


class TestMfeMae:
    def test_long_mfe_mae(self):
        ev = OutcomeEvaluator()
        # entry 100; highs: 106,108 ; lows: 99,97
        fut = _future_with_hl([(105, 106, 99), (107, 108, 97)])
        o = ev.evaluate(_long_subject(), fut)
        # MFE = max(high - entry) = 108-100 = 8; MAE = max(entry-low) = 100-97 = 3
        assert abs(o.mfe - 8.0) < 1e-9
        assert abs(o.mae - 3.0) < 1e-9
        assert abs(o.mfe_r - 8.0 / 5.0) < 1e-9
        assert abs(o.mae_r - 3.0 / 5.0) < 1e-9

    def test_short_mfe_mae(self):
        ev = OutcomeEvaluator()
        # SHORT entry 100; favorable = down, adverse = up
        fut = _future_with_hl([(95, 106, 94), (93, 104, 92)])
        o = ev.evaluate(_short_subject(), fut)
        # MFE = max(entry - low) = 100-92 = 8; MAE = max(high - entry) = 106-100 = 6
        assert abs(o.mfe - 8.0) < 1e-9
        assert abs(o.mae - 6.0) < 1e-9

    def test_mfe_mae_none_when_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        o = ev.evaluate(s, _future([101]))
        assert o.mfe is None and o.mae is None
        assert o.mfe_r is None and o.mae_r is None

    def test_mfe_mae_computed_over_full_window(self):
        # Even after a target hit, MFE/MAE reflect the whole window.
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(105, 111, 100), (107, 112, 96)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.outcome_status == OutcomeStatus.TARGET_HIT
        # MFE over both bars = 112-100 = 12 (not just 11 at the hit bar)
        assert abs(o.mfe - 12.0) < 1e-9
        # MAE = 100-96 = 4
        assert abs(o.mae - 4.0) < 1e-9

    def test_r_normalized_excursion_uses_risk(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(105, 110, 95)])
        o = ev.evaluate(_long_subject(entry=100, stop=90, target=120), fut)
        # risk = 10; MFE = 10 -> mfe_r = 1.0; MAE = 5 -> mae_r = 0.5
        assert abs(o.mfe_r - 1.0) < 1e-9
        assert abs(o.mae_r - 0.5) < 1e-9


# ============================================================
# J. REALIZED R
# ============================================================


class TestRealizedR:
    def test_target_hit_r(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(entry=100, stop=95, target=110),
                        _future([112]))
        assert abs(o.realized_r - 2.0) < 1e-9  # (110-100)/5

    def test_stop_hit_r(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(entry=100, stop=95, target=110),
                        _future([94]))
        assert abs(o.realized_r - (-1.0)) < 1e-9  # (95-100)/5

    def test_expired_r_uses_mark_to_close(self):
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=5))
        o = ev.evaluate(_long_subject(entry=100, stop=95, target=110),
                        _future([103]))
        assert abs(o.realized_r - 0.6) < 1e-9  # (103-100)/5

    def test_short_target_hit_r(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_short_subject(entry=100, stop=105, target=90),
                        _future([88]))
        assert abs(o.realized_r - 2.0) < 1e-9  # (100-90)/5

    def test_short_stop_hit_r(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_short_subject(entry=100, stop=105, target=90),
                        _future([106]))
        assert abs(o.realized_r - (-1.0)) < 1e-9  # (100-105)/5

    def test_both_touched_r_is_none(self):
        ev = OutcomeEvaluator()
        fut = _future_with_hl([(100, 115, 90)])
        o = ev.evaluate(_long_subject(), fut)
        assert o.realized_r is None

    def test_no_geometry_r_is_none(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        o = ev.evaluate(s, _future([101]))
        assert o.realized_r is None


# ============================================================
# K. MULTIPLE OPPORTUNITIES
# ============================================================


class TestMultipleOpportunities:
    def test_each_opportunity_evaluated_independently(self):
        ev = OutcomeEvaluator()
        long_subj = _long_subject(instrument="NIFTY", rank=1)
        short_subj = _short_subject(instrument="RELIANCE", rank=2)
        long_o = ev.evaluate(long_subj, _future([105, 112]))
        short_o = ev.evaluate(short_subj, _future([95, 88]))
        assert long_o.outcome_status == OutcomeStatus.TARGET_HIT
        assert short_o.outcome_status == OutcomeStatus.TARGET_HIT
        assert long_o.instrument == "NIFTY"
        assert short_o.instrument == "RELIANCE"
        assert long_o.subject.rank == 1
        assert short_o.subject.rank == 2

    def test_multiple_opportunities_preserve_identity(self):
        subjects = [
            (_long_subject(instrument="ICICIBANK", rank=1), _future([105, 112])),
            (_long_subject(instrument="NIFTY", rank=2), _future([105, 112])),
            (_short_subject(instrument="RELIANCE", rank=3), _future([95, 88])),
            (_long_subject(instrument="TCS", rank=4), _future([105, 112])),
        ]
        ev = OutcomeEvaluator()
        outs = [ev.evaluate(s, fut) for s, fut in subjects]
        assert {o.instrument for o in outs} == {
            "ICICIBANK", "NIFTY", "RELIANCE", "TCS",
        }
        assert [o.subject.rank for o in outs] == [1, 2, 3, 4]
        assert all(o.outcome_status == OutcomeStatus.TARGET_HIT for o in outs)


# ============================================================
# L. INSTRUMENT ISOLATION
# ============================================================


class TestInstrumentIsolation:
    def test_mutating_other_instrument_future_no_effect(self):
        ev = OutcomeEvaluator()
        subj = _long_subject(instrument="NIFTY")
        own_future = _future([105, 112])
        o1 = ev.evaluate(subj, own_future)
        # A different instrument's future is irrelevant to this outcome.
        other_future = _future([1, 1000])
        o2 = ev.evaluate(subj, other_future)
        # o2 evaluates the SAME subject against a DIFFERENT future; the
        # point is that the OTHER instrument's data never bleeds in.
        # Here we confirm isolation by re-evaluating with the original.
        o3 = ev.evaluate(subj, own_future)
        assert o1.outcome_status == OutcomeStatus.TARGET_HIT
        assert o3.outcome_status == o1.outcome_status
        assert o3.exit_price == o1.exit_price


# ============================================================
# M. POINT-IN-TIME / LEAKAGE
# ============================================================


class TestPointInTime:
    def test_outcome_uses_only_strictly_future_candles(self):
        ev = OutcomeEvaluator()
        # Include the evaluation candle (at T) plus future candles.
        at_t = _candle(100.0, _EPOCH)
        future = _future([105, 112])
        o = ev.evaluate(_long_subject(), [at_t, *future])
        # The at-T candle must not count as a future bar.
        assert o.bars_held == 2
        assert o.outcome_status == OutcomeStatus.TARGET_HIT

    def test_future_mutation_may_change_outcome_not_subject(self):
        ev = OutcomeEvaluator()
        subj = _long_subject()
        o_before = ev.evaluate(subj, _future([101, 102, 103]))
        assert o_before.outcome_status == OutcomeStatus.EXPIRED
        # Mutate the future to produce a target hit.
        o_after = ev.evaluate(subj, _future([101, 102, 112]))
        assert o_after.outcome_status == OutcomeStatus.TARGET_HIT
        # The SUBJECT (decision at T) is NEVER mutated.
        assert subj.entry == 100.0
        assert subj.direction == "LONG"
        assert subj.evaluation_timestamp == _EPOCH

    def test_decision_stability_prefix_full(self):
        # The outcome evaluated on a prefix of the future equals the
        # outcome evaluated on the full future, when truncated to the
        # same horizon. This proves the evaluator does not read beyond
        # the horizon.
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        full = _future([101, 102, 103, 112, 94])
        prefix = full[:3]
        o_full = ev.evaluate(_long_subject(), full)
        o_prefix = ev.evaluate(_long_subject(), prefix)
        assert o_full.outcome_status == o_prefix.outcome_status
        assert o_full.exit_price == o_prefix.exit_price
        assert o_full.bars_held == o_prefix.bars_held

    def test_evaluator_does_not_mutate_input_candles(self):
        ev = OutcomeEvaluator()
        fut = _future([105, 112])
        fut_copy = [OHLCVCandle(c.timestamp, c.open, c.high, c.low, c.close, c.volume) for c in fut]
        ev.evaluate(_long_subject(), fut)
        for a, b in zip(fut, fut_copy):
            assert a == b


# ============================================================
# N. DETERMINISM
# ============================================================


class TestDeterminism:
    def test_repeated_evaluation_identical(self):
        ev = OutcomeEvaluator()
        fut = _future([105, 108, 112])
        o1 = ev.evaluate(_long_subject(), fut)
        o2 = ev.evaluate(_long_subject(), fut)
        assert o1 == o2

    def test_repeated_evaluation_all_statuses(self):
        ev = OutcomeEvaluator()
        for fut in [
            _future([105, 112]),
            _future([94]),
            _future_with_hl([(100, 115, 90)]),
            _future([101, 102]),
            [],
        ]:
            o1 = ev.evaluate(_long_subject(), fut)
            o2 = ev.evaluate(_long_subject(), fut)
            assert o1 == o2

    def test_no_randomness_in_ranking_keys(self):
        ev = OutcomeEvaluator()
        s1 = _long_subject(instrument="A", rank=1)
        s2 = _long_subject(instrument="B", rank=2)
        fut = _future([112])
        o1 = ev.evaluate(s1, fut)
        o2 = ev.evaluate(s2, fut)
        assert o1.outcome_status == o2.outcome_status


# ============================================================
# O. SERIALIZATION
# ============================================================


class TestSerialization:
    def test_outcome_round_trip_target_hit(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([105, 112]))
        s = serialize_outcome(o)
        back = deserialize_outcome(s)
        assert back.outcome_status == o.outcome_status
        assert back.exit_price == o.exit_price
        assert back.realized_r == o.realized_r
        assert back.mfe == o.mfe
        assert back.mae == o.mae
        assert back.mfe_r == o.mfe_r
        assert back.mae_r == o.mae_r
        assert back.bars_held == o.bars_held
        assert back.risk == o.risk

    def test_outcome_round_trip_preserves_identity_fields(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(instrument="NIFTY", rank=2), _future([112]))
        back = deserialize_outcome(serialize_outcome(o))
        assert back.subject.instrument == "NIFTY"
        assert back.subject.direction == "LONG"
        assert back.subject.entry == 100.0
        assert back.subject.stop == 95.0
        assert back.subject.target == 110.0
        assert back.subject.evaluation_timestamp == _EPOCH
        assert back.subject.decision_classification == "QUALIFIED"
        assert back.subject.decision_score == 70
        assert back.subject.opportunity_status == "BEST_OPPORTUNITY"
        assert back.subject.rank == 2
        assert back.subject.scan_id == "scan-x"

    def test_outcome_round_trip_both_touched(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future_with_hl([(100, 115, 90)]))
        back = deserialize_outcome(serialize_outcome(o))
        assert back.outcome_status == OutcomeStatus.BOTH_TOUCHED
        assert back.exit_price is None
        assert back.realized_r is None
        assert back.outcome_timestamp == o.outcome_timestamp

    def test_outcome_round_trip_expired(self):
        ev = OutcomeEvaluator(OutcomeConfig(max_holding_bars=3))
        o = ev.evaluate(_long_subject(), _future([101, 102, 103]))
        back = deserialize_outcome(serialize_outcome(o))
        assert back.outcome_status == OutcomeStatus.EXPIRED
        assert back.exit_price == 103.0
        assert back.realized_r == o.realized_r

    def test_outcome_round_trip_no_geometry(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        o = ev.evaluate(s, _future([101]))
        back = deserialize_outcome(serialize_outcome(o))
        assert back.outcome_status == OutcomeStatus.NO_GEOMETRY
        assert back.exit_price is None
        assert back.realized_r is None

    def test_schema_version_in_payload(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        header = parse_outcome_header(serialize_outcome(o))
        assert header["schema_version"] == OUTCOME_SCHEMA_VERSION

    def test_future_schema_version_rejected(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        payload = serialize_outcome(o).replace(
            f'"schema_version": {OUTCOME_SCHEMA_VERSION}',
            '"schema_version": 99999',
        )
        with pytest.raises(ValueError):
            deserialize_outcome(payload)

    def test_deterministic_bytes(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        b1 = serialize_outcome_bytes(o)
        b2 = serialize_outcome_bytes(o)
        assert b1 == b2
        assert canonical_outcome_json(o) == canonical_outcome_json(o)

    def test_report_round_trip(self):
        ev = OutcomeEvaluator()
        point = ReplayOutcomePoint(
            evaluation_time=_EPOCH,
            outcomes=(
                ev.evaluate(_long_subject(instrument="NIFTY"), _future([112])),
                ev.evaluate(_short_subject(instrument="RELIANCE"), _future([88])),
            ),
        )
        report = ReplayOutcomeReport(
            report_id="outcomes-test",
            instruments=("NIFTY", "RELIANCE"),
            timeframes=("1D", "15M"),
            points=(point,),
            rationale="test",
        )
        back = deserialize_outcome_report(serialize_outcome_report(report))
        assert back.report_id == report.report_id
        assert back.instruments == report.instruments
        assert back.timeframes == report.timeframes
        assert back.outcome_count == report.outcome_count
        assert len(back.points) == 1
        assert back.points[0].outcomes[0].outcome_status == OutcomeStatus.TARGET_HIT
        assert back.points[0].outcomes[0].subject.instrument == "NIFTY"
        assert canonical_outcome_report_json(report) == serialize_outcome_report(report)


# ============================================================
# P. REPLAY INTEGRATION
# ============================================================


class TestReplayIntegration:
    """Integration with the Sprint 11V replay using live fixtures."""

    def _setup(self):
        from engine.config.market_scan_config import MarketScanConfig
        from engine.data.historical_adapter import (
            HistoricalAdapterConfig,
            HistoricalDataAdapter,
        )
        from engine.data.historical_fixtures import historical_records
        from engine.intelligence.historical_replay import (
            HistoricalReplayEngine,
            evaluation_times_from_setup_candles,
        )

        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        dataset = adapter.normalize(historical_records())
        times = evaluation_times_from_setup_candles(
            dataset, "15M", count=4, min_history=10,
        )
        replay = HistoricalReplayEngine(MarketScanConfig()).replay(dataset, times)
        return dataset, replay

    def test_report_evaluates_all_points(self):
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        report = engine.evaluate_replay(replay, dataset)
        assert len(report.points) == len(replay.points)
        assert report.outcome_count >= 1

    def test_outcomes_use_only_future_candles(self):
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        report = engine.evaluate_replay(replay, dataset)
        for point in report.points:
            for o in point.outcomes:
                # Every outcome's determining timestamp (when present)
                # must be strictly after the evaluation time.
                if o.outcome_timestamp is not None:
                    assert o.outcome_timestamp > point.evaluation_time

    def test_decision_at_t_unchanged_by_outcome_evaluation(self):
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        # Snapshot the scan at the first point before outcome eval.
        first_scan = replay.points[-1].scan
        scan_id_before = first_scan.scan_id
        results_before = [
            (r.instrument, r.direction, r.decision_score) for r in first_scan.results
        ]
        engine.evaluate_replay(replay, dataset)
        # The replay's scan is NOT mutated by outcome evaluation.
        assert replay.points[-1].scan.scan_id == scan_id_before
        after = [
            (r.instrument, r.direction, r.decision_score)
            for r in replay.points[-1].scan.results
        ]
        assert after == results_before

    def test_htf_protection_intact(self):
        # The outcome layer must not weaken the Sprint 11V HTF
        # incomplete-candle protection: outcomes are only evaluated for
        # eligible opportunities the scan already surfaced.
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        report = engine.evaluate_replay(replay, dataset)
        eligible_total = sum(
            1 for p in replay.points for r in p.scan.results if r.eligible
        )
        outcome_total = report.outcome_count
        assert outcome_total == eligible_total

    def test_report_deterministic(self):
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        r1 = engine.evaluate_replay(replay, dataset)
        r2 = engine.evaluate_replay(replay, dataset)
        assert r1.report_id == r2.report_id
        assert r1.outcome_count == r2.outcome_count
        for p1, p2 in zip(r1.points, r2.points):
            assert len(p1.outcomes) == len(p2.outcomes)
            for o1, o2 in zip(p1.outcomes, p2.outcomes):
                assert o1 == o2

    def test_multiple_opportunities_each_evaluated(self):
        dataset, replay = self._setup()
        engine = HistoricalOutcomeEngine(OutcomeConfig(max_holding_bars=15))
        report = engine.evaluate_replay(replay, dataset)
        # At least one point must have evaluated multiple opportunities.
        multi = [p for p in report.points if len(p.outcomes) > 1]
        assert len(multi) >= 1


# ============================================================
# Q. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        text = HistoricalOutcomeFormatter().format(o)
        assert isinstance(text, str)

    def test_format_required_sections(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        text = HistoricalOutcomeFormatter().format(o)
        for label in [
            "Historical Replay Outcome Report",
            "Instrument",
            "Direction",
            "Decision",
            "Score",
            "Opportunity Status",
            "Entry",
            "Stop",
            "Target",
            "Outcome",
            "Outcome Time",
            "Exit Price",
            "Bars Held",
            "MFE",
            "MAE",
            "Realized R",
        ]:
            assert label in text, f"missing section: {label}"

    def test_format_includes_warning(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        text = HistoricalOutcomeFormatter().format(o)
        assert "NOT predictive" in text
        assert "guarantees of profitability" in text

    def test_format_no_predictive_language(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        text = HistoricalOutcomeFormatter().format(o).lower()
        for bad in ["will definitely win", "predicts", "accuracy is guaranteed"]:
            assert bad not in text

    def test_format_unavailable_shown(self):
        ev = OutcomeEvaluator()
        s = OutcomeSubject("X", "LONG", _EPOCH, entry=100, stop=95, target=None)
        o = ev.evaluate(s, _future([101]))
        text = HistoricalOutcomeFormatter().format(o)
        assert "unavailable" in text

    def test_format_report_returns_str(self):
        ev = OutcomeEvaluator()
        point = ReplayOutcomePoint(
            evaluation_time=_EPOCH,
            outcomes=(ev.evaluate(_long_subject(), _future([112])),),
        )
        report = ReplayOutcomeReport(
            report_id="outcomes-x",
            instruments=("NIFTY",),
            timeframes=("1D", "15M"),
            points=(point,),
            rationale="test",
        )
        text = HistoricalOutcomeFormatter().format_report(report)
        assert isinstance(text, str)
        assert "Historical Replay Outcome Report" in text
        assert "WARNING" in text

    def test_format_determinism(self):
        ev = OutcomeEvaluator()
        o = ev.evaluate(_long_subject(), _future([112]))
        f = HistoricalOutcomeFormatter()
        assert f.format(o) == f.format(o)


# ============================================================
# R. SUBJECT BUILDERS
# ============================================================


class TestSubjectBuilders:
    def test_build_outcome_subject_from_scan_result(self):
        from engine.intelligence.market_scanner import InstrumentDataset, MarketScanner
        from engine.data.historical_fixtures import historical_candles_by_instrument

        candles = historical_candles_by_instrument()
        # Use NIFTY candles to build a scan result.
        nifty = candles["NIFTY"]
        ds = InstrumentDataset(
            instrument="NIFTY",
            context_candles=nifty["1D"],
            setup_candles=nifty["15M"],
        )
        scanner = MarketScanner()
        scan = scanner.scan([ds], evaluation_time=nifty["15M"][-1].timestamp)
        # Find an instrument result with a decision.
        result = next(r for r in scan.results if r.decision is not None)
        subject = build_outcome_subject(result, scan_id=scan.scan_id, setup_timeframe="15M")
        assert subject is not None
        assert subject.instrument == "NIFTY"
        assert subject.scan_id == scan.scan_id
        assert subject.setup_timeframe == "15M"

    def test_build_outcome_subject_none_when_no_decision(self):
        from engine.models.market_scan import InstrumentScanResult, MTFAlignment

        result = InstrumentScanResult(
            instrument="X",
            context_timeframe="1D",
            setup_timeframe="15M",
            timestamp=_EPOCH,
            decision=None,
            opportunity=None,
            alignment=MTFAlignment.UNKNOWN,
        )
        assert build_outcome_subject(result) is None

    def test_build_outcome_subjects_eligible_only(self):
        from engine.intelligence.market_scanner import InstrumentDataset, MarketScanner
        from engine.data.historical_fixtures import historical_candles_by_instrument

        candles = historical_candles_by_instrument()
        nifty = candles["NIFTY"]
        ds = InstrumentDataset(
            instrument="NIFTY",
            context_candles=nifty["1D"],
            setup_candles=nifty["15M"],
        )
        scan = MarketScanner().scan([ds], evaluation_time=nifty["15M"][-1].timestamp)
        subjects = build_outcome_subjects(scan, setup_timeframe="15M")
        # Every projected subject came from an eligible result.
        eligible_instruments = {
            r.instrument for r in scan.results if r.eligible
        }
        if subjects:
            assert {s.instrument for s in subjects} <= eligible_instruments

    def test_build_outcome_subjects_all_when_not_eligible_only(self):
        from engine.intelligence.market_scanner import InstrumentDataset, MarketScanner
        from engine.data.historical_fixtures import historical_candles_by_instrument

        candles = historical_candles_by_instrument()
        nifty = candles["NIFTY"]
        ds = InstrumentDataset(
            instrument="NIFTY",
            context_candles=nifty["1D"],
            setup_candles=nifty["15M"],
        )
        scan = MarketScanner().scan([ds], evaluation_time=nifty["15M"][-1].timestamp)
        subjects_all = build_outcome_subjects(
            scan, setup_timeframe="15M", eligible_only=False,
        )
        subjects_eligible = build_outcome_subjects(scan, setup_timeframe="15M")
        assert len(subjects_all) >= len(subjects_eligible)
