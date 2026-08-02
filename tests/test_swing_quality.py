from datetime import UTC, datetime, timedelta

from engine.config.swing_config import SwingConfig
from engine.intelligence.swings import SwingEngine
from engine.models.ohlcv import OHLCVCandle


def make_candle(
    high: float,
    low: float,
    index: int,
) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1000,
    )


def test_strong_move():
    candles = [
        make_candle(100, 95, 0),
        make_candle(105, 100, 1),
        make_candle(112, 107, 2),
        make_candle(104, 99, 3),
        make_candle(103, 98, 4),
    ]

    engine = SwingEngine(SwingConfig())

    swings = engine.detect(candles)

    assert len(swings) == 1

    swing = swings[0]

    assert swing.evidence.move_percent > 5


def test_weak_move():
    candles = [
        make_candle(100.0, 99.8, 0),
        make_candle(100.3, 100.0, 1),
        make_candle(100.5, 100.2, 2),
        make_candle(100.2, 99.9, 3),
        make_candle(100.1, 99.8, 4),
    ]

    engine = SwingEngine(SwingConfig())

    swings = engine.detect(candles)

    assert len(swings) == 1

    swing = swings[0]

    assert swing.evidence.move_percent < 1


def test_confidence_is_capped():
    candles = [
        make_candle(100, 95, 0),
        make_candle(150, 145, 1),
        make_candle(220, 215, 2),
        make_candle(150, 145, 3),
        make_candle(140, 135, 4),
    ]

    engine = SwingEngine(SwingConfig())

    swings = engine.detect(candles)

    assert len(swings) == 1

    swing = swings[0]

    assert swing.evidence.confidence <= 100
