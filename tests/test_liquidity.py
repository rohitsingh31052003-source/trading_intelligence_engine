from datetime import datetime

from engine.intelligence.liquidity import LiquidityEngine
from engine.models.liquidity import (
    LiquidityStatus,
    LiquidityType,
)
from engine.models.strength import StrengthCategory
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)
from engine.models.swing_evidence import SwingEvidence


def make_swing(
    price: float,
    swing_type: SwingType,
    confirmed: bool = True,
) -> SwingPoint:

    return SwingPoint(
        timestamp=datetime(2025, 1, 1),
        index=0,
        price=price,
        swing_type=swing_type,
        confirmation_index=5,
        confirmed=confirmed,
        status=(
            SwingStatus.CONFIRMED
            if confirmed
            else SwingStatus.CANDIDATE
        ),
        strength=SwingStrength.STRONG,
        evidence=SwingEvidence(),
    )


def test_confirmed_high_creates_buy_side_pool():

    engine = LiquidityEngine()

    swings = [
        make_swing(1325.00, SwingType.HIGH),
    ]

    pools = engine.detect(swings)

    assert len(pools) == 1
    assert pools[0].liquidity_type == LiquidityType.BUY_SIDE
    assert pools[0].status == LiquidityStatus.ACTIVE


def test_confirmed_low_creates_sell_side_pool():

    engine = LiquidityEngine()

    swings = [
        make_swing(1215.00, SwingType.LOW),
    ]

    pools = engine.detect(swings)

    assert len(pools) == 1
    assert pools[0].liquidity_type == LiquidityType.SELL_SIDE
    assert pools[0].status == LiquidityStatus.ACTIVE


def test_unconfirmed_swings_are_ignored():

    engine = LiquidityEngine()

    swings = [
        make_swing(
            1325,
            SwingType.HIGH,
            confirmed=False,
        ),
        make_swing(
            1215,
            SwingType.LOW,
            confirmed=False,
        ),
    ]

    pools = engine.detect(swings)

    assert pools == []


def test_nearby_highs_merge():

    engine = LiquidityEngine()

    swings = [
        make_swing(1325.00, SwingType.HIGH),
        make_swing(1325.30, SwingType.HIGH),
    ]

    pools = engine.detect(swings)

    assert len(pools) == 1
    assert pools[0].liquidity_type == LiquidityType.BUY_SIDE
    assert pools[0].swing_count == 2


def test_nearby_lows_merge():

    engine = LiquidityEngine()

    swings = [
        make_swing(1215.00, SwingType.LOW),
        make_swing(1215.20, SwingType.LOW),
    ]

    pools = engine.detect(swings)

    assert len(pools) == 1
    assert pools[0].liquidity_type == LiquidityType.SELL_SIDE
    assert pools[0].swing_count == 2


def test_different_liquidity_types_never_merge():

    engine = LiquidityEngine()

    swings = [
        make_swing(1325.00, SwingType.HIGH),
        make_swing(1325.05, SwingType.LOW),
    ]

    pools = engine.detect(swings)

    assert len(pools) == 2


def test_strength_increases_with_clustered_swings():

    engine = LiquidityEngine()

    swings = [
        make_swing(1325.00, SwingType.HIGH),
        make_swing(1325.20, SwingType.HIGH),
        make_swing(1325.15, SwingType.HIGH),
    ]

    pools = engine.detect(swings)

    assert pools[0].swing_count == 3
    assert pools[0].strength == 30.0


def test_strength_category():

    engine = LiquidityEngine()

    swings = [
        make_swing(1325.00, SwingType.HIGH),
        make_swing(1325.20, SwingType.HIGH),
        make_swing(1325.15, SwingType.HIGH),
    ]

    pools = engine.detect(swings)

    assert pools[0].category == StrengthCategory.WEAK


def test_empty_list_returns_empty_result():

    engine = LiquidityEngine()

    pools = engine.detect([])

    assert pools == []