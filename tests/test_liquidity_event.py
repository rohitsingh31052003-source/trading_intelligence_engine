from datetime import datetime, timedelta

from engine.intelligence.liquidity_event import LiquidityEventEngine
from engine.models.liquidity import (
    LiquidityPool,
    LiquidityStatus,
    LiquidityType,
)
from engine.models.liquidity_event import LiquidityEventType
from engine.models.strength import StrengthCategory
from engine.models.ohlcv import OHLCVCandle


def make_pool(
    *,
    liquidity_type,
    price=100.0,
    created_at=None,
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
    timestamp,
    open,
    high,
    low,
    close,
):

    return OHLCVCandle(
        timestamp=timestamp,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )


def test_empty_input_returns_empty_list():

    engine = LiquidityEventEngine()

    assert engine.analyze([], []) == []


def test_buy_side_breakout():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=99,
            high=102,
            low=98,
            close=101,
        ),

        make_candle(
            timestamp=datetime(2025,1,3),
            open=101,
            high=104,
            low=100,
            close=103,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected
    assert result.event_type == LiquidityEventType.BREAKOUT


def test_buy_side_sweep():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=99,
            high=102,
            low=98,
            close=101,
        ),

        make_candle(
            timestamp=datetime(2025,1,3),
            open=101,
            high=102,
            low=97,
            close=99,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.detected
    assert result.event_type == LiquidityEventType.SWEEP


def test_sell_side_breakout():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=101,
            high=102,
            low=98,
            close=99,
        ),

        make_candle(
            timestamp=datetime(2025,1,3),
            open=99,
            high=100,
            low=95,
            close=96,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.BREAKOUT


def test_sell_side_sweep():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=101,
            high=102,
            low=98,
            close=99,
        ),

        make_candle(
            timestamp=datetime(2025,1,3),
            open=99,
            high=103,
            low=97,
            close=101,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.SWEEP


def test_no_liquidity_event():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=99,
            high=100,
            low=98,
            close=99,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert not result.detected
    assert result.event_type == LiquidityEventType.NONE


def test_equal_high_is_not_breach():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
        price=100,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=99,
            high=100,
            low=98,
            close=99,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.NONE


def test_equal_low_is_not_breach():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
        price=100,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=101,
            high=102,
            low=100,
            close=101,
        ),
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.event_type == LiquidityEventType.NONE


def test_confidence_values():

    engine = LiquidityEventEngine()

    breakout = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    sweep = make_pool(
        liquidity_type=LiquidityType.SELL_SIDE,
    )

    breakout_result = engine.analyze(
        [breakout],
        [
            make_candle(
                timestamp=datetime(2025,1,2),
                open=99,
                high=102,
                low=98,
                close=101,
            )
        ],
    )[0]

    assert breakout_result.confidence in (0.0, 85.0, 90.0)


def test_timestamp_matches_breach_candle():

    engine = LiquidityEventEngine()

    breach = datetime(2025,1,2)

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [

        make_candle(
            timestamp=breach,
            open=99,
            high=102,
            low=98,
            close=101,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert result.event_timestamp == breach


def test_reasons_are_populated():

    engine = LiquidityEventEngine()

    pool = make_pool(
        liquidity_type=LiquidityType.BUY_SIDE,
    )

    candles = [

        make_candle(
            timestamp=datetime(2025,1,2),
            open=99,
            high=102,
            low=98,
            close=101,
        )
    ]

    result = engine.analyze([pool], candles)[0]

    assert len(result.reasons) > 0