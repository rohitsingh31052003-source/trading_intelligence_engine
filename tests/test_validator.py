from datetime import UTC, datetime

import pytest

from engine.data.validator import DataValidator
from engine.models.ohlcv import OHLCVCandle


def create_candle(hour: int) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=datetime(2026, 7, 31, hour, 0, tzinfo=UTC),
        open=100,
        high=105,
        low=95,
        close=102,
        volume=1000,
    )


def test_validate_dataset_success():
    candles = [
        create_candle(9),
        create_candle(10),
        create_candle(11),
    ]

    DataValidator.validate_dataset(candles)


def test_empty_dataset():
    with pytest.raises(ValueError):
        DataValidator.validate_dataset([])


def test_duplicate_timestamp():
    candle = create_candle(9)

    with pytest.raises(ValueError):
        DataValidator.validate_dataset([candle, candle])


def test_unsorted_dataset():
    candles = [
        create_candle(10),
        create_candle(9),
    ]

    with pytest.raises(ValueError):
        DataValidator.validate_dataset(candles)
