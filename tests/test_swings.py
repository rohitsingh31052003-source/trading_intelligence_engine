from datetime import datetime, timedelta

from engine.intelligence.swings import SwingEngine
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingType


def make_candle(
    high: float,
    low: float,
    index: int,
) -> OHLCVCandle:
    """
    Create a simple candle for testing.
    """

    return OHLCVCandle(
        timestamp=datetime(2025, 1, 1) + timedelta(days=index),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1000,
    )

def test_invalid_lookback():
    import pytest

    with pytest.raises(ValueError):
        SwingEngine(lookback=0)

def test_valid_lookback():
    engine = SwingEngine(lookback=2)

    assert engine.lookback == 2

def test_detect_single_swing_high():
    candles = [
        make_candle(10, 5, 0),
        make_candle(12, 6, 1),
        make_candle(15, 7, 2),
        make_candle(11, 6, 3),
        make_candle(9, 5, 4),
    ]

    engine = SwingEngine(lookback=2)

    swings = engine.detect(candles)

    highs = [
        s
        for s in swings
        if s.swing_type == SwingType.HIGH
    ]

    assert len(highs) == 1
    assert highs[0].price == 15

def test_detect_single_swing_low():
    candles = [
        make_candle(15, 9, 0),
        make_candle(14, 8, 1),
        make_candle(13, 4, 2),
        make_candle(14, 7, 3),
        make_candle(15, 9, 4),
    ]

    engine = SwingEngine()

    swings = engine.detect(candles)

    lows = [
        s
        for s in swings
        if s.swing_type == SwingType.LOW
    ]

    assert len(lows) == 1
    assert lows[0].price == 4

def test_no_swings():
    candles = [
        make_candle(10, 5, 0),
        make_candle(11, 6, 1),
        make_candle(12, 7, 2),
        make_candle(13, 8, 3),
        make_candle(14, 9, 4),
    ]

    engine = SwingEngine()

    swings = engine.detect(candles)

    assert len(swings) == 0

def test_small_dataset():
    candles = [
        make_candle(10, 5, 0),
        make_candle(11, 6, 1),
    ]

    engine = SwingEngine()

    swings = engine.detect(candles)

    assert swings == []

    def test_large_lookback():
        candles = [
        make_candle(10, 5, i)
        for i in range(5)
    ]

    engine = SwingEngine(lookback=5)

    swings = engine.detect(candles)

    assert swings == []

def test_equal_highs_are_not_swing():
    candles = [
        make_candle(10, 5, 0),
        make_candle(15, 6, 1),
        make_candle(15, 7, 2),
        make_candle(11, 6, 3),
        make_candle(9, 5, 4),
    ]

    engine = SwingEngine()

    swings = engine.detect(candles)

    highs = [
        s
        for s in swings
        if s.swing_type == SwingType.HIGH
    ]

    assert len(highs) == 0

def test_equal_lows_are_not_swing():
    candles = [
        make_candle(15, 9, 0),
        make_candle(14, 4, 1),
        make_candle(13, 4, 2),
        make_candle(14, 7, 3),
        make_candle(15, 9, 4),
    ]

    engine = SwingEngine()

    swings = engine.detect(candles)

    lows = [
        s
        for s in swings
        if s.swing_type == SwingType.LOW
    ]

    assert len(lows) == 0