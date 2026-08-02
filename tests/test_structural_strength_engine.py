from datetime import datetime

from engine.intelligence.structural_strength import (
    StructuralStrengthEngine,
)
from engine.models.structural_level_evidence import (
    StructuralLevelEvidence,
)
from engine.models.support_resistance import (
    LevelStatus,
    LevelType,
    StructuralLevel,
)
from engine.models.swing import SwingStrength


def make_level(
    *,
    strength=SwingStrength.STRONG,
    touches=1,
    defenses=0,
    failed=0,
    age=0,
):
    return StructuralLevel(
        price=100.0,
        created_at=datetime(2025, 1, 1),
        level_type=LevelType.SUPPORT,
        status=LevelStatus.ACTIVE,
        touches=touches,
        originating_strength=strength,
        successful_defenses=defenses,
        failed_tests=failed,
        last_touch=datetime(2025, 1, 1),
        broken_at=None,
        age_in_bars=age,
        evidence=StructuralLevelEvidence(),
    )


def test_origin_score():

    engine = StructuralStrengthEngine()

    weak = make_level(strength=SwingStrength.WEAK)
    major = make_level(strength=SwingStrength.MAJOR)

    assert engine._origin_score(major) > engine._origin_score(weak)


def test_freshness_score():

    engine = StructuralStrengthEngine()

    recent = make_level(age=0)
    old = make_level(age=15)

    assert engine._freshness_score(recent) > engine._freshness_score(old)


def test_touch_score():

    engine = StructuralStrengthEngine()

    one = make_level(touches=1)
    four = make_level(touches=4)

    assert engine._touch_score(four) > engine._touch_score(one)


def test_defense_score():

    engine = StructuralStrengthEngine()

    none = make_level(defenses=0)
    many = make_level(defenses=5)

    assert engine._defense_score(many) > engine._defense_score(none)


def test_penalty_score():

    engine = StructuralStrengthEngine()

    clean = make_level(failed=0)
    weak = make_level(failed=4)

    assert engine._penalty_score(weak) > engine._penalty_score(clean)


def test_strength_category():

    engine = StructuralStrengthEngine()

    level = make_level(
        strength=SwingStrength.MAJOR,
        touches=4,
        defenses=4,
        failed=0,
        age=0,
    )

    result = engine.evaluate([level])[0]

    assert result.strength >= 70


def test_total_strength():

    engine = StructuralStrengthEngine()

    level = make_level()

    result = engine.evaluate([level])[0]

    assert result.strength == result.evidence.total


def test_strength_never_exceeds_100():

    engine = StructuralStrengthEngine()

    level = make_level(
        strength=SwingStrength.MAJOR,
        touches=20,
        defenses=20,
        failed=0,
        age=0,
    )

    result = engine.evaluate([level])[0]

    assert result.strength <= 100
