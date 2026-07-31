from datetime import UTC, datetime

import pytest

from engine.models.ohlcv import OHLCVCandle


def test_valid_candle_creation():
    candle = OHLCVCandle(
        timestamp=datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
        open=100.0,
        high=105.0,
        low=99.0,
        close=104.0,
        volume=1000.0,
    )

    assert candle.open == 100.0
    assert candle.high == 105.0
    assert candle.low == 99.0
    assert candle.close == 104.0
    assert candle.volume == 1000.0


def test_bullish_candle():
    candle = OHLCVCandle(
        timestamp=datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
        open=100,
        high=110,
        low=95,
        close=105,
        volume=100,
    )

    assert candle.is_bullish is True
    assert candle.is_bearish is False


def test_bearish_candle():
    candle = OHLCVCandle(
        timestamp=datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
        open=105,
        high=110,
        low=95,
        close=100,
        volume=100,
    )

    assert candle.is_bullish is False
    assert candle.is_bearish is True


def test_invalid_high_low():
    with pytest.raises(ValueError):
        OHLCVCandle(
            timestamp=datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
            open=100,
            high=90,
            low=100,
            close=95,
            volume=100,
        )


def test_negative_volume():
    with pytest.raises(ValueError):
        OHLCVCandle(
            timestamp=datetime(2026, 7, 31, 9, 15, tzinfo=UTC),
            open=100,
            high=110,
            low=95,
            close=105,
            volume=-1,
        )
