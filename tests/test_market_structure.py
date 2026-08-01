from datetime import datetime

from engine.intelligence.structure import MarketStructureEngine
from engine.models.market_structure import StructureType
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)


def make_swing(
    price: float,
    swing_type: SwingType,
    index: int,
) -> SwingPoint:
    return SwingPoint(
        timestamp=datetime(2025, 1, 1),
        index=index,
        price=price,
        swing_type=swing_type,
        confirmation_index=index + 2,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        strength=SwingStrength.NORMAL,
    )


def test_first_high():
    swings = [
        make_swing(100, SwingType.HIGH, 0),
    ]

    engine = MarketStructureEngine()

    result = engine.analyze(swings)

    assert len(result) == 1
    assert result[0].structure == StructureType.FIRST_HIGH


def test_higher_high():
    swings = [
        make_swing(100, SwingType.HIGH, 0),
        make_swing(110, SwingType.HIGH, 1),
    ]

    engine = MarketStructureEngine()

    result = engine.analyze(swings)

    assert result[0].structure == StructureType.FIRST_HIGH
    assert result[1].structure == StructureType.HIGHER_HIGH


def test_lower_high():
    swings = [
        make_swing(120, SwingType.HIGH, 0),
        make_swing(110, SwingType.HIGH, 1),
    ]

    engine = MarketStructureEngine()

    result = engine.analyze(swings)

    assert result[0].structure == StructureType.FIRST_HIGH
    assert result[1].structure == StructureType.LOWER_HIGH


def test_higher_low_and_lower_low():
    swings = [
        make_swing(80, SwingType.LOW, 0),
        make_swing(90, SwingType.LOW, 1),
        make_swing(70, SwingType.LOW, 2),
    ]

    engine = MarketStructureEngine()

    result = engine.analyze(swings)

    assert result[0].structure == StructureType.FIRST_LOW
    assert result[1].structure == StructureType.HIGHER_LOW
    assert result[2].structure == StructureType.LOWER_LOW