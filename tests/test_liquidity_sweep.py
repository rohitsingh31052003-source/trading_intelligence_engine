from datetime import datetime, timedelta

from engine.intelligence.liquidity_sweep import LiquiditySweepEngine
from engine.models.liquidity import (
    LiquidityPool,
    LiquidityStatus,
    LiquidityType,
)
from engine.models.liquidity_sweep import LiquiditySweepType
from engine.models.ohlcv import OHLCVCandle
from engine.models.strength import StrengthCategory


def make_pool(
    *,
    price: float,
    liquidity_type: LiquidityType,
    created_at: datetime | None = None,
):

    return LiquidityPool(
        price=price,
        liquidity_type=liquidity_type,
        created_at=created_at or datetime(2025, 1, 1),
        swing_count=2,
        strength=25.0,
        category=StrengthCategory.WEAK,
        status=LiquidityStatus.ACTIVE,
    )


def make_candle(
    *,
    timestamp: datetime,
    high: float,
    low: float,
    open: float | None = None,
    close: float | None = None,
    volume: float = 1000,
):
    if open is None:
        open = (high + low) / 2

    if close is None:
        close = (high + low) / 2
    

    return OHLCVCandle(
        timestamp=timestamp,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_empty_pools():

    engine = LiquiditySweepEngine()

    results = engine.analyze([], [])

    assert results == []


def test_buy_side_pool_swept():

    engine = LiquiditySweepEngine()

    pool = make_pool(
        price=1325,
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1327,
            low=1320,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.sweep_type == LiquiditySweepType.BUY_SIDE


def test_buy_side_pool_not_swept():

    engine = LiquiditySweepEngine()

    pool = make_pool(
        price=1325,
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1324,
            low=1318,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.sweep_type == LiquiditySweepType.NONE


def test_sell_side_pool_swept():

    engine = LiquiditySweepEngine()

    pool = make_pool(
        price=1215,
        liquidity_type=LiquidityType.SELL_SIDE,
    )

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1220,
            low=1210,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is True
    assert result.sweep_type == LiquiditySweepType.SELL_SIDE


def test_sell_side_pool_not_swept():

    engine = LiquiditySweepEngine()

    pool = make_pool(
        price=1215,
        liquidity_type=LiquidityType.SELL_SIDE,
    )

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1220,
            low=1216,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False
    assert result.sweep_type == LiquiditySweepType.NONE


def test_equal_price_does_not_trigger_buy_side_sweep():

    engine = LiquiditySweepEngine()

    pool = make_pool(
        price=1325,
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1325,
            low=1320,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False


def test_multiple_pools_are_processed_independently():

    engine = LiquiditySweepEngine()

    pools = [
        make_pool(
            price=1325,
            liquidity_type=LiquidityType.BUY_SIDE,
        ),
        make_pool(
            price=1215,
            liquidity_type=LiquidityType.SELL_SIDE,
        ),
    ]

    candles = [
        make_candle(
            timestamp=datetime(2025, 1, 2),
            high=1327,
            low=1218,
        ),
        make_candle(
            timestamp=datetime(2025, 1, 3),
            high=1218,
            low=1212,
        ),
    ]

    results = engine.analyze(pools, candles)

    assert len(results) == 2
    assert results[0].detected is True
    assert results[1].detected is True


def test_timestamp_filtering():

    engine = LiquiditySweepEngine()

    created = datetime(2025, 1, 5)

    pool = make_pool(
        price=1325,
        liquidity_type=LiquidityType.BUY_SIDE,
        created_at=created,
    )

    candles = [
        make_candle(
            timestamp=created - timedelta(days=1),
            high=1330,
            low=1320,
        ),
        make_candle(
            timestamp=created + timedelta(days=1),
            high=1324,
            low=1318,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected is False