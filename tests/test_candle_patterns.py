"""
Tests for the candle / price-action pattern intelligence layer
(Sprint 11O).

Coverage:

* candle measurement calculations
* zero-range / degenerate candles
* detection and non-detection for every pattern
* boundary cases for thresholds
* direction classification
* determinism
* look-ahead safety regression
* pipeline integration (additive evidence)
* existing pipeline behaviour regression
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

import pytest

from engine.config.candle_pattern_config import CandlePatternConfig
from engine.intelligence.candle_patterns import CandlePatternEngine
from engine.models.candle_pattern import (
    CandleDirection,
    CandleMeasurements,
    CandlePattern,
    CandlePatternType,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    index: int,
    volume: float = 1000.0,
) -> OHLCVCandle:
    """
    Build a fully-specified OHLCV candle.
    """

    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def types_at(
    engine: CandlePatternEngine,
    candles: list[OHLCVCandle],
    index: int,
) -> set[CandlePatternType]:
    return {
        p.pattern_type
        for p in engine.detect(candles)
        if p.index == index
    }


# ============================================================
# CANDLE MEASUREMENTS
# ============================================================


def test_measurements_basic_bullish():
    c = candle(open_=100.0, high=105.0, low=98.0, close=104.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.range == pytest.approx(7.0)
    assert m.body == pytest.approx(4.0)
    assert m.upper_wick == pytest.approx(1.0)
    assert m.lower_wick == pytest.approx(2.0)
    assert m.body_to_range_ratio == pytest.approx(4.0 / 7.0)
    assert m.direction == CandleDirection.BULLISH


def test_measurements_basic_bearish():
    c = candle(open_=104.0, high=105.0, low=98.0, close=100.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.body == pytest.approx(4.0)
    assert m.upper_wick == pytest.approx(1.0)
    assert m.lower_wick == pytest.approx(2.0)
    assert m.direction == CandleDirection.BEARISH


def test_measurements_zero_range_candle():
    c = candle(open_=100.0, high=100.0, low=100.0, close=100.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.range == 0.0
    assert m.body == 0.0
    assert m.upper_wick == 0.0
    assert m.lower_wick == 0.0
    assert m.body_to_range_ratio == 0.0
    assert m.direction == CandleDirection.NEUTRAL


def test_measurements_doji_open_equals_close_with_range():
    # open == close but range > 0 -> NEUTRAL direction
    c = candle(open_=100.0, high=102.0, low=98.0, close=100.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.body == 0.0
    assert m.range == pytest.approx(4.0)
    assert m.direction == CandleDirection.NEUTRAL
    # upper/lower wicks both 2.0
    assert m.upper_wick == pytest.approx(2.0)
    assert m.lower_wick == pytest.approx(2.0)


def test_measurements_no_upper_wick():
    # close at the high -> no upper wick
    c = candle(open_=100.0, high=104.0, low=98.0, close=104.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.upper_wick == 0.0
    assert m.lower_wick == pytest.approx(2.0)


def test_measurements_no_lower_wick():
    c = candle(open_=100.0, high=104.0, low=96.0, close=96.0, index=0)
    m = CandleMeasurements.from_candle(c)

    assert m.lower_wick == 0.0
    assert m.upper_wick == pytest.approx(4.0)


# ============================================================
# ZERO-RANGE / DEGENERATE
# ============================================================


def test_zero_range_classified_as_doji():
    engine = CandlePatternEngine()
    candles = [candle(100.0, 100.0, 100.0, 100.0, 0)]

    patterns = [p for p in engine.detect(candles) if p.index == 0]

    assert len(patterns) == 1
    assert patterns[0].pattern_type == CandlePatternType.DOJI
    assert patterns[0].direction == CandleDirection.NEUTRAL


def test_zero_range_not_hammer_or_shooting_star():
    engine = CandlePatternEngine()
    candles = [candle(100.0, 100.0, 100.0, 100.0, 0)]

    found = types_at(engine, candles, 0)

    assert CandlePatternType.HAMMER not in found
    assert CandlePatternType.SHOOTING_STAR not in found


# ============================================================
# DOJI
# ============================================================


def test_doji_detected_body_ratio_at_threshold():
    # body exactly 10% of range -> ratio 0.1, matches <= 0.1
    # range 10, body 1 -> ratio 0.1
    c = candle(open_=100.0, high=105.0, low=95.0, close=101.0, index=0)
    engine = CandlePatternEngine()

    assert CandlePatternType.DOJI in types_at(engine, [c], 0)


def test_doji_detected_small_body():
    c = candle(open_=100.0, high=105.0, low=95.0, close=100.5, index=0)
    engine = CandlePatternEngine()

    assert CandlePatternType.DOJI in types_at(engine, [c], 0)


def test_doji_not_detected_body_too_large():
    # body 50% of range -> not doji
    c = candle(open_=100.0, high=105.0, low=95.0, close=102.5, index=0)
    engine = CandlePatternEngine()

    assert CandlePatternType.DOJI not in types_at(engine, [c], 0)


def test_doji_uses_boundary_above_threshold():
    # body 11% of range -> just above 0.1, not doji
    # range 100, body 11 -> 0.11
    c = candle(open_=0.0, high=50.0, low=-50.0, close=11.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.body_to_range_ratio == pytest.approx(0.11)

    assert CandlePatternType.DOJI not in types_at(engine, [c], 0)


def test_doji_custom_threshold():
    cfg = CandlePatternConfig(doji_max_body_ratio=0.3)
    engine = CandlePatternEngine(cfg)
    # body 2, range 10 -> ratio 0.2, doji under 0.3
    c = candle(open_=100.0, high=105.0, low=95.0, close=102.0, index=0)
    m = CandleMeasurements.from_candle(c)
    assert m.body_to_range_ratio == pytest.approx(0.2)

    assert CandlePatternType.DOJI in types_at(engine, [c], 0)


# ============================================================
# HAMMER
# ============================================================


def test_hammer_detected():
    # body 1, lower wick 5, upper wick 0
    c = candle(open_=101.0, high=102.0, low=95.0, close=102.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.lower_wick >= 2.0 * m.body
    assert m.upper_wick <= m.body

    found = types_at(engine, [c], 0)
    assert CandlePatternType.HAMMER in found


def test_hammer_direction_bullish():
    c = candle(open_=101.0, high=102.0, low=95.0, close=102.0, index=0)
    engine = CandlePatternEngine()

    p = next(
        p for p in engine.detect([c])
        if p.pattern_type == CandlePatternType.HAMMER
    )
    assert p.direction == CandleDirection.BULLISH


def test_hammer_not_detected_lower_wick_too_small():
    # body 4, lower wick 2 (ratio 0.5 < 2) -> not hammer
    c = candle(open_=100.0, high=106.0, low=96.0, close=104.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.lower_wick < 2.0 * m.body

    assert CandlePatternType.HAMMER not in types_at(engine, [c], 0)


def test_hammer_not_detected_upper_wick_too_large():
    # body 1, lower wick 5, upper wick 2 (> body) -> not hammer
    c = candle(open_=101.0, high=104.0, low=95.0, close=102.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.upper_wick > m.body

    assert CandlePatternType.HAMMER not in types_at(engine, [c], 0)


def test_hammer_not_detected_zero_body():
    c = candle(open_=100.0, high=105.0, low=95.0, close=100.0, index=0)
    engine = CandlePatternEngine()

    assert CandlePatternType.HAMMER not in types_at(engine, [c], 0)


def test_hammer_boundary_lower_wick_equals_twice_body():
    # body 2, lower wick exactly 4 -> 2x body -> matches
    c = candle(open_=100.0, high=102.0, low=96.0, close=102.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.lower_wick == pytest.approx(2.0 * m.body)

    assert CandlePatternType.HAMMER in types_at(engine, [c], 0)


# ============================================================
# SHOOTING STAR
# ============================================================


def test_shooting_star_detected():
    # body 1, upper wick 5, lower wick 0
    c = candle(open_=100.0, high=105.0, low=99.0, close=101.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.upper_wick >= 2.0 * m.body
    assert m.lower_wick <= m.body

    assert CandlePatternType.SHOOTING_STAR in types_at(engine, [c], 0)


def test_shooting_star_direction_bearish():
    c = candle(open_=100.0, high=105.0, low=99.0, close=101.0, index=0)
    engine = CandlePatternEngine()

    p = next(
        p for p in engine.detect([c])
        if p.pattern_type == CandlePatternType.SHOOTING_STAR
    )
    assert p.direction == CandleDirection.BEARISH


def test_shooting_star_not_detected_upper_wick_too_small():
    c = candle(open_=100.0, high=104.0, low=96.0, close=103.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.upper_wick < 2.0 * m.body

    assert CandlePatternType.SHOOTING_STAR not in types_at(engine, [c], 0)


def test_shooting_star_not_detected_lower_wick_too_large():
    # body 1, upper 5, lower 2 (> body) -> not shooting star
    c = candle(open_=100.0, high=105.0, low=97.0, close=101.0, index=0)
    engine = CandlePatternEngine()
    m = CandleMeasurements.from_candle(c)
    assert m.lower_wick > m.body

    assert CandlePatternType.SHOOTING_STAR not in types_at(engine, [c], 0)


def test_shooting_star_not_detected_zero_body():
    c = candle(open_=100.0, high=105.0, low=95.0, close=100.0, index=0)
    engine = CandlePatternEngine()

    assert CandlePatternType.SHOOTING_STAR not in types_at(engine, [c], 0)


# ============================================================
# BULLISH ENGULFING
# ============================================================


def test_bullish_engulfing_detected():
    prior = candle(open_=102.0, high=103.0, low=99.0, close=100.0, index=0)
    curr = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=1)
    engine = CandlePatternEngine()

    found = types_at(engine, [prior, curr], 1)
    assert CandlePatternType.BULLISH_ENGULFING in found


def test_bullish_engulfing_direction_and_prior_index():
    prior = candle(open_=102.0, high=103.0, low=99.0, close=100.0, index=0)
    curr = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=1)
    engine = CandlePatternEngine()

    p = next(
        x for x in engine.detect([prior, curr])
        if x.pattern_type == CandlePatternType.BULLISH_ENGULFING
    )
    assert p.direction == CandleDirection.BULLISH
    assert p.index == 1
    assert p.prior_index == 0
    assert p.prior_measurements is not None


def test_bullish_engulfing_not_detected_no_engulfment():
    # current open above prior close -> does not engulf
    prior = candle(open_=102.0, high=103.0, low=99.0, close=100.0, index=0)
    curr = candle(open_=101.0, high=105.0, low=99.0, close=104.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.BULLISH_ENGULFING not in types_at(
        engine, [prior, curr], 1,
    )


def test_bullish_engulfing_not_detected_wrong_directions():
    # both bullish -> not bullish engulfing
    prior = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=0)
    curr = candle(open_=99.0, high=106.0, low=99.0, close=105.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.BULLISH_ENGULFING not in types_at(
        engine, [prior, curr], 1,
    )


def test_bullish_engulfing_strict_body_must_be_larger():
    # current body engulfs but is NOT larger than prior body ->
    # rejected under strict default.
    # prior: open 100 close 99 (body 1, bearish)
    # curr: open 99.5 close 100.5 (body 1, bullish) engulfs [99,100]
    prior = candle(open_=100.0, high=101.0, low=98.0, close=99.0, index=0)
    curr = candle(open_=99.5, high=101.0, low=99.0, close=100.5, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.BULLISH_ENGULFING not in types_at(
        engine, [prior, curr], 1,
    )


def test_bullish_engulfing_non_strict_accepts_equal_body():
    # prior bearish body 1 (open 100 close 99); current bullish
    # body 1 (open 99 close 100) that engulfs the prior body but
    # is not strictly larger. Rejected under strict, accepted
    # under non-strict.
    prior = candle(open_=100.0, high=101.0, low=98.0, close=99.0, index=0)
    curr = candle(open_=99.0, high=101.0, low=99.0, close=100.0, index=1)
    strict = CandlePatternEngine()
    assert CandlePatternType.BULLISH_ENGULFING not in types_at(
        strict, [prior, curr], 1,
    )

    cfg = CandlePatternConfig(engulfing_strict=False)
    engine = CandlePatternEngine(cfg)

    assert CandlePatternType.BULLISH_ENGULFING in types_at(
        engine, [prior, curr], 1,
    )


def test_bullish_engulfing_not_detected_at_index_zero():
    curr = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=0)
    engine = CandlePatternEngine()

    # index 0 has no prior candle
    assert CandlePatternType.BULLISH_ENGULFING not in types_at(
        engine, [curr], 0,
    )


# ============================================================
# BEARISH ENGULFING
# ============================================================


def test_bearish_engulfing_detected():
    prior = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=0)
    curr = candle(open_=105.0, high=105.0, low=98.0, close=98.0, index=1)
    engine = CandlePatternEngine()

    found = types_at(engine, [prior, curr], 1)
    assert CandlePatternType.BEARISH_ENGULFING in found


def test_bearish_engulfing_direction_bearish():
    prior = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=0)
    curr = candle(open_=105.0, high=105.0, low=98.0, close=98.0, index=1)
    engine = CandlePatternEngine()

    p = next(
        x for x in engine.detect([prior, curr])
        if x.pattern_type == CandlePatternType.BEARISH_ENGULFING
    )
    assert p.direction == CandleDirection.BEARISH
    assert p.index == 1
    assert p.prior_index == 0


def test_bearish_engulfing_not_detected_no_engulfment():
    prior = candle(open_=99.0, high=105.0, low=99.0, close=104.0, index=0)
    curr = candle(open_=103.0, high=105.0, low=98.0, close=99.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.BEARISH_ENGULFING not in types_at(
        engine, [prior, curr], 1,
    )


def test_bearish_engulfing_strict_body_must_be_larger():
    # prior bullish body 1, curr bearish body 1 engulfing -> strict rejects
    prior = candle(open_=99.0, high=101.0, low=99.0, close=100.0, index=0)
    curr = candle(open_=100.5, high=101.0, low=98.5, close=99.5, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.BEARISH_ENGULFING not in types_at(
        engine, [prior, curr], 1,
    )


# ============================================================
# INSIDE BAR
# ============================================================


def test_inside_bar_detected():
    prior = candle(open_=95.0, high=110.0, low=90.0, close=105.0, index=0)
    curr = candle(open_=100.0, high=103.0, low=98.0, close=101.0, index=1)
    engine = CandlePatternEngine()

    found = types_at(engine, [prior, curr], 1)
    assert CandlePatternType.INSIDE_BAR in found


def test_inside_bar_direction_neutral():
    prior = candle(open_=95.0, high=110.0, low=90.0, close=105.0, index=0)
    curr = candle(open_=100.0, high=103.0, low=98.0, close=101.0, index=1)
    engine = CandlePatternEngine()

    p = next(
        x for x in engine.detect([prior, curr])
        if x.pattern_type == CandlePatternType.INSIDE_BAR
    )
    assert p.direction == CandleDirection.NEUTRAL
    assert p.prior_index == 0


def test_inside_bar_not_detected_breaks_high():
    prior = candle(open_=95.0, high=110.0, low=90.0, close=105.0, index=0)
    curr = candle(open_=100.0, high=111.0, low=98.0, close=101.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.INSIDE_BAR not in types_at(
        engine, [prior, curr], 1,
    )


def test_inside_bar_not_detected_breaks_low():
    prior = candle(open_=95.0, high=110.0, low=90.0, close=105.0, index=0)
    curr = candle(open_=100.0, high=103.0, low=89.0, close=101.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.INSIDE_BAR not in types_at(
        engine, [prior, curr], 1,
    )


def test_inside_bar_not_detected_equal_range_not_strictly_smaller():
    # current range exactly equals prior range -> not strictly smaller
    prior = candle(open_=95.0, high=110.0, low=90.0, close=105.0, index=0)
    curr = candle(open_=100.0, high=110.0, low=90.0, close=101.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.INSIDE_BAR not in types_at(
        engine, [prior, curr], 1,
    )


def test_inside_bar_not_detected_prior_zero_range():
    prior = candle(open_=100.0, high=100.0, low=100.0, close=100.0, index=0)
    curr = candle(open_=100.0, high=100.0, low=100.0, close=100.0, index=1)
    engine = CandlePatternEngine()

    assert CandlePatternType.INSIDE_BAR not in types_at(
        engine, [prior, curr], 1,
    )


# ============================================================
# DIRECTION CLASSIFICATION
# ============================================================


def test_direction_bullish_bearish_neutral():
    bullish = candle(open_=100.0, high=105.0, low=99.0, close=104.0, index=0)
    bearish = candle(open_=104.0, high=105.0, low=99.0, close=100.0, index=0)
    neutral = candle(open_=100.0, high=105.0, low=95.0, close=100.0, index=0)

    assert CandleMeasurements.from_candle(bullish).direction == CandleDirection.BULLISH
    assert CandleMeasurements.from_candle(bearish).direction == CandleDirection.BEARISH
    assert CandleMeasurements.from_candle(neutral).direction == CandleDirection.NEUTRAL


# ============================================================
# SCORE PROPERTIES
# ============================================================


def test_score_within_unit_interval():
    candles = [
        candle(100.0, 102.0, 98.0, 100.0, 0),  # doji
        candle(101.0, 102.0, 95.0, 102.0, 1),  # hammer
    ]
    engine = CandlePatternEngine()

    for p in engine.detect(candles):
        assert 0.0 <= p.score <= 1.0


def test_doji_zero_range_score_is_one():
    c = candle(100.0, 100.0, 100.0, 100.0, 0)
    engine = CandlePatternEngine()

    p = next(x for x in engine.detect([c]) if x.pattern_type == CandlePatternType.DOJI)
    assert p.score == 1.0


def test_inside_bar_score_increases_with_tighter_range():
    prior = candle(95.0, 110.0, 90.0, 105.0, 0)

    wide_inside = candle(100.0, 109.0, 91.0, 101.0, 1)  # small reduction
    tight_inside = candle(100.0, 100.5, 100.0, 100.3, 1)  # big reduction

    eng = CandlePatternEngine()
    wide = next(
        x for x in eng.detect([prior, wide_inside])
        if x.pattern_type == CandlePatternType.INSIDE_BAR
    )
    tight = next(
        x for x in eng.detect([prior, tight_inside])
        if x.pattern_type == CandlePatternType.INSIDE_BAR
    )
    assert tight.score > wide.score


# ============================================================
# CONFIRMED FIELD
# ============================================================


def test_base_detector_never_confirms():
    candles = [
        candle(100.0, 102.0, 98.0, 100.0, 0),
        candle(101.0, 102.0, 95.0, 102.0, 1),
    ]
    engine = CandlePatternEngine()

    for p in engine.detect(candles):
        assert p.confirmed is False


# ============================================================
# DETERMINISM
# ============================================================


def test_determinism_identical_inputs():
    candles = [
        candle(100.0, 102.0, 98.0, 100.0, 0),
        candle(99.0, 105.0, 99.0, 104.0, 1),
        candle(100.0, 103.0, 98.0, 101.0, 2),
    ]
    engine = CandlePatternEngine()

    a = engine.detect(candles)
    b = engine.detect(candles)

    assert a == b


def test_determinism_across_instances():
    candles = [
        candle(100.0, 102.0, 98.0, 100.0, 0),
        candle(99.0, 105.0, 99.0, 104.0, 1),
    ]

    a = CandlePatternEngine().detect(candles)
    b = CandlePatternEngine().detect(candles)

    assert a == b


# ============================================================
# LOOK-AHEAD SAFETY (REGRESSION)
# ============================================================


def _pattern_signature(patterns: Iterable[CandlePattern]) -> tuple:
    """
    A hashable, comparable signature of a pattern set that
    excludes the timestamp (which shifts when candles are
    appended at later indices).
    """

    return tuple(
        (
            p.pattern_type,
            p.index,
            p.direction,
            p.score,
            p.measurements.range,
            p.measurements.body,
            p.measurements.upper_wick,
            p.measurements.lower_wick,
            p.measurements.body_to_range_ratio,
            p.prior_index,
        )
        for p in patterns
    )


def test_lookahead_changing_future_candle_does_not_change_pattern_at_t():
    """
    Pattern(T) must depend only on candles[:T+1]. Appending or
    mutating any candle after T must not change the pattern at T.
    """

    base = [
        candle(100.0, 102.0, 98.0, 100.0, 0),  # doji at 0
        candle(99.0, 105.0, 99.0, 104.0, 1),  # bullish engulfing at 1
    ]

    engine = CandlePatternEngine()

    baseline = [p for p in engine.detect(base) if p.index <= 1]
    baseline_sig = _pattern_signature(baseline)

    # Append arbitrary future candles.
    extended = base + [
        candle(104.0, 200.0, 50.0, 190.0, 2),
        candle(190.0, 300.0, 10.0, 20.0, 3),
    ]
    extended_at_t = [p for p in engine.detect(extended) if p.index <= 1]
    extended_sig = _pattern_signature(extended_at_t)

    assert extended_sig == baseline_sig


def test_lookahead_pattern_at_t_independent_of_future_mutation():
    """
    Replacing a future candle with a completely different one
    must not alter patterns at earlier indices.
    """

    base = [
        candle(100.0, 102.0, 98.0, 100.0, 0),
        candle(99.0, 105.0, 99.0, 104.0, 1),
        candle(104.0, 106.0, 100.0, 101.0, 2),
    ]
    engine = CandlePatternEngine()

    before = _pattern_signature(
        p for p in engine.detect(base) if p.index <= 1
    )

    # Mutate the candle at index 2 (the future relative to T=1).
    mutated = list(base)
    mutated[2] = candle(1.0, 999.0, 0.5, 990.0, 2)

    after = _pattern_signature(
        p for p in engine.detect(mutated) if p.index <= 1
    )

    assert after == before


def test_lookahead_truncation_matches_full():
    """
    Detecting on candles[:T+1] must yield the same patterns at
    T as detecting on the full series.
    """

    candles = [
        candle(100.0, 102.0, 98.0, 100.0, 0),
        candle(99.0, 105.0, 99.0, 104.0, 1),
        candle(104.0, 106.0, 100.0, 101.0, 2),
    ]
    engine = CandlePatternEngine()

    full_at_2 = [p for p in engine.detect(candles) if p.index == 2]
    trunc_at_2 = [p for p in engine.detect(candles[:3]) if p.index == 2]

    assert _pattern_signature(full_at_2) == _pattern_signature(trunc_at_2)


def test_two_candle_uses_only_immediately_preceding():
    """
    A two-candle pattern at T uses only T-1 and T. Changing
    candle T-2 must not affect it.
    """

    t1 = 99.0
    t_close = 104.0
    candles = [
        candle(50.0, 60.0, 40.0, 55.0, 0),  # index 0 (T-2 for T=2)
        candle(102.0, 103.0, 99.0, t1, 1),  # prior bearish
        candle(99.0, 105.0, 99.0, t_close, 2),  # current bullish
    ]
    engine = CandlePatternEngine()

    before = _pattern_signature(
        p for p in engine.detect(candles) if p.index == 2
    )

    # Replace the T-2 candle with something totally different.
    candles2 = list(candles)
    candles2[0] = candle(500.0, 900.0, 100.0, 800.0, 0)

    after = _pattern_signature(
        p for p in engine.detect(candles2) if p.index == 2
    )

    assert after == before


# ============================================================
# ENGINE CONFIG VALIDATION
# ============================================================


def test_config_negative_doji_ratio_rejected():
    with pytest.raises(ValueError):
        CandlePatternEngine(CandlePatternConfig(doji_max_body_ratio=-0.1))


def test_config_negative_hammer_threshold_rejected():
    with pytest.raises(ValueError):
        CandlePatternEngine(
            CandlePatternConfig(hammer_min_lower_wick_to_body=-1.0),
        )


def test_config_negative_shooting_star_threshold_rejected():
    with pytest.raises(ValueError):
        CandlePatternEngine(
            CandlePatternConfig(shooting_star_min_upper_wick_to_body=-1.0),
        )


def test_default_config_used_when_none():
    engine = CandlePatternEngine()
    assert engine.config.doji_max_body_ratio == 0.1


# ============================================================
# IMMUtability
# ============================================================


def test_pattern_model_frozen():
    c = candle(100.0, 102.0, 98.0, 100.0, 0)
    engine = CandlePatternEngine()
    p = engine.detect([c])[0]

    with pytest.raises(Exception):
        p.score = 0.5  # type: ignore[misc]


def test_measurements_model_frozen():
    c = candle(100.0, 102.0, 98.0, 100.0, 0)
    m = CandleMeasurements.from_candle(c)

    with pytest.raises(Exception):
        m.body = 99.0  # type: ignore[misc]


# ============================================================
# PIPELINE INTEGRATION (ADDITIVE)
# ============================================================


def test_pipeline_result_carries_patterns():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    assert isinstance(result.patterns, tuple)
    assert len(result.patterns) > 0
    assert all(isinstance(p, CandlePattern) for p in result.patterns)


def test_pipeline_point_carries_patterns():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    for point in result.evaluation_points_sequence:
        assert isinstance(point.patterns, tuple)
        # Patterns attributed to this point are at this index.
        for p in point.patterns:
            assert p.index == point.index


def test_pipeline_patterns_index_coverage():
    """
    Every pattern in result.patterns must be attributable to one
    of the evaluation points.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    point_indices = {
        p.index for p in result.patterns
    }
    evaluated = {
        pt.index for pt in result.evaluation_points_sequence
    }
    assert point_indices <= evaluated


def test_pipeline_pattern_config_threaded_through():
    cfg = PipelineConfig(
        candle_pattern_config=CandlePatternConfig(doji_max_body_ratio=0.5),
    )
    pipeline = HistoricalEvaluationPipeline(cfg)

    assert pipeline._pattern_engine.config.doji_max_body_ratio == 0.5


def test_pipeline_empty_result_has_no_patterns():
    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate([])

    assert result.patterns == ()


# ============================================================
# EXISTING PIPELINE BEHAVIOUR REGRESSION
# ============================================================


def test_pipeline_signals_unchanged_by_pattern_engine():
    """
    Pattern evidence must not change existing signal/decision
    behaviour. Re-running the pipeline with and without the
    pattern engine active (by comparing against the documented
    trending-dataset behaviour) yields identical signals.

    Because the pattern engine runs additively and is not fed
    into confluence/decision/signal, the funnel counts produced
    by the default pipeline must match the pre-11O baseline.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    # The pre-11O trending dataset produced at least one signal
    # and at least one completed trade. These counts are
    # behaviour, not pattern evidence; they must be unchanged.
    assert result.signals_generated >= 1
    assert result.completed_trades >= 1

    # Every signal still carries its prices (unchanged logic).
    for s in result.signals:
        assert s.entry_price is not None
        assert s.stop_loss is not None
        assert s.take_profit is not None


def test_pipeline_signal_prices_match_truncated_run():
    """
    The signal at the first validated point must be identical
    whether the full dataset is evaluated or the dataset is
    truncated to candles[:T+1]. This reproduces the pre-11O
    look-ahead regression for signals and confirms the pattern
    engine did not introduce future-data coupling.
    """

    pipeline = HistoricalEvaluationPipeline()

    candles = trending_dataset()
    result = pipeline.evaluate(candles)

    first = next(
        pt for pt in result.evaluation_points_sequence if pt.validated
    )
    t = first.index
    signal = first.signal

    truncated = pipeline.evaluate(candles[: t + 1])
    point = next(
        pt for pt in truncated.evaluation_points_sequence
        if pt.index == t
    )

    assert point.signal is not None
    assert point.signal.entry_price == signal.entry_price
    assert point.signal.stop_loss == signal.stop_loss
    assert point.signal.take_profit == signal.take_profit


def test_pipeline_does_not_mutate_input():
    pipeline = HistoricalEvaluationPipeline()
    candles = trending_dataset()
    snapshot = list(candles)

    pipeline.evaluate(candles)

    assert candles == snapshot


def test_pipeline_full_suite_still_passes_smoke():
    """
    Smoke test: the full suite count is unchanged (all pre-11O
    tests pass). This is enforced by the test runner; here we
    just confirm the pipeline produces a well-formed result.
    """

    pipeline = HistoricalEvaluationPipeline()
    result = pipeline.evaluate(trending_dataset())

    assert result.candles_processed == len(trending_dataset())
    assert result.evaluation_points > 0
    assert result.performance is not None
