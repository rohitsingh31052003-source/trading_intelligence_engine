from dataclasses import replace
from datetime import datetime

from engine.intelligence.support_resistance import StructuralLevelsEngine
from engine.models.structural_level_evidence import StructuralLevelEvidence
from engine.models.support_resistance import (
    LevelStatus,
    LevelType,
    StructuralLevel,
)
from engine.models.swing import SwingStrength


def make_level(
    *,
    originating_strength: SwingStrength = SwingStrength.NORMAL,
    touches: int = 1,
    successful_defenses: int = 0,
    failed_tests: int = 0,
    age_in_bars: int = 0,
) -> StructuralLevel:
    """
    Create a structural level for testing.
    """

    return StructuralLevel(
        price=100.0,
        created_at=datetime(2025, 1, 1),
        level_type=LevelType.SUPPORT,
        status=LevelStatus.ACTIVE,
        touches=touches,
        originating_strength=originating_strength,
        successful_defenses=successful_defenses,
        failed_tests=failed_tests,
        last_touch=datetime(2025, 1, 1),
        broken_at=None,
        age_in_bars=age_in_bars,
        evidence=StructuralLevelEvidence(),
    )


def test_origin_score():

    engine = StructuralLevelsEngine()

    weak = make_level(
        originating_strength=SwingStrength.WEAK,
    )

    major = make_level(
        originating_strength=SwingStrength.MAJOR,
    )

    weak_score = engine._calculate_evidence(
        weak
    ).origin_score

    major_score = engine._calculate_evidence(
        major
    ).origin_score

    assert major_score > weak_score


def test_defense_score():

    engine = StructuralLevelsEngine()

    level = replace(
        make_level(),
        successful_defenses=3,
    )

    evidence = engine._calculate_evidence(level)

    assert evidence.defense_score == 15.0


def test_freshness_score():

    engine = StructuralLevelsEngine()

    fresh = make_level(
        age_in_bars=0,
    )

    stale = make_level(
        age_in_bars=15,
    )

    fresh_score = engine._calculate_evidence(
        fresh
    ).freshness_score

    stale_score = engine._calculate_evidence(
        stale
    ).freshness_score

    assert fresh_score > stale_score


def test_penalty_score():

    engine = StructuralLevelsEngine()

    level = replace(
        make_level(),
        failed_tests=3,
    )

    evidence = engine._calculate_evidence(level)

    assert evidence.penalty_score == 15.0


def test_strength_equals_evidence_total():

    engine = StructuralLevelsEngine()

    level = make_level(
        originating_strength=SwingStrength.MAJOR,
        successful_defenses=2,
        touches=3,
    )

    evidence = engine._calculate_evidence(level)

    strength = engine._calculate_strength(level)

    assert strength == evidence.total


def test_strength_is_capped():

    engine = StructuralLevelsEngine()

    level = make_level(
        originating_strength=SwingStrength.MAJOR,
        successful_defenses=20,
        touches=20,
    )

    strength = engine._calculate_strength(level)

    assert strength <= 100.0


def test_strength_never_negative():

    engine = StructuralLevelsEngine()

    level = make_level(
        originating_strength=SwingStrength.WEAK,
        failed_tests=100,
        age_in_bars=100,
    )

    strength = engine._calculate_strength(level)

    assert strength >= 0.0