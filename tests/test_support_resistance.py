from datetime import datetime

from engine.intelligence.support_resistance import (
    StructuralLevelsEngine,
)
from engine.models.support_resistance import (
    LevelStatus,
    LevelType,
)
from engine.intelligence.structural_strength import (
    StructuralStrengthEngine,
)

from engine.models.swing import (
    SwingPoint,
    SwingStrength,
    SwingStatus,
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
        status=(SwingStatus.CONFIRMED if confirmed else SwingStatus.CANDIDATE),
        strength=SwingStrength.STRONG,
        evidence=SwingEvidence(),
    )


def test_confirmed_high_creates_resistance():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(
            1262,
            SwingType.HIGH,
        )
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 1
    assert levels[0].level_type == LevelType.RESISTANCE
    assert levels[0].price == 1262
    assert levels[0].status == LevelStatus.ACTIVE


def test_confirmed_low_creates_support():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(
            1215,
            SwingType.LOW,
        )
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 1
    assert levels[0].level_type == LevelType.SUPPORT
    assert levels[0].price == 1215
    assert levels[0].status == LevelStatus.ACTIVE


def test_unconfirmed_swings_are_ignored():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(
            1262,
            SwingType.HIGH,
            confirmed=False,
        ),
        make_swing(
            1215,
            SwingType.LOW,
            confirmed=False,
        ),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels == []


def test_empty_list_returns_empty_result():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    levels = level_engine.detect([])
    levels = strength_engine.evaluate(levels)

    assert levels == []


def test_initial_strength_is_30():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(
            1262,
            SwingType.HIGH,
        )
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels[0].strength == 40.0


def test_initial_touches_is_one():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(
            1215,
            SwingType.LOW,
        )
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels[0].touches == 1


def test_merge_close_supports():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100.00, SwingType.LOW),
        make_swing(100.15, SwingType.LOW),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 1
    assert levels[0].level_type == LevelType.SUPPORT


def test_merge_close_resistances():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(1325, SwingType.HIGH),
        make_swing(1327, SwingType.HIGH),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 1
    assert levels[0].level_type == LevelType.RESISTANCE


def test_do_not_merge_distant_levels():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100, SwingType.LOW),
        make_swing(108, SwingType.LOW),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 2


def test_support_and_resistance_never_merge():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100, SwingType.LOW),
        make_swing(100.05, SwingType.HIGH),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert len(levels) == 2


def test_touches_are_accumulated():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100.00, SwingType.LOW),
        make_swing(100.15, SwingType.LOW),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels[0].touches == 2


def test_average_price_after_merge():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100.00, SwingType.LOW),
        make_swing(100.20, SwingType.LOW),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels[0].price == 100.10


def test_strength_increases_after_merge():

    level_engine = StructuralLevelsEngine()
    strength_engine = StructuralStrengthEngine()

    swings = [
        make_swing(100.00, SwingType.LOW),
        make_swing(100.20, SwingType.LOW),
        make_swing(100.10, SwingType.LOW),
    ]

    levels = level_engine.detect(swings)
    levels = strength_engine.evaluate(levels)

    assert levels[0].touches == 3
    assert levels[0].strength == 47.0
