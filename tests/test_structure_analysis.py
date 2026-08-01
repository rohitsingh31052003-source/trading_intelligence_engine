from datetime import datetime

import pytest

from engine.intelligence.structure_analysis import StructureAnalysisEngine
from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)
from engine.models.structure_analysis import (
    StructureBias,
)
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)


def make_structure(
    structure: StructureType,
    index: int,
) -> StructurePoint:
    """
    Create a StructurePoint for testing.
    """

    swing = SwingPoint(
        timestamp=datetime(2025, 1, 1),
        index=index,
        price=100 + index,
        swing_type=SwingType.HIGH,
        confirmation_index=index + 2,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        strength=SwingStrength.NORMAL,
    )

    return StructurePoint(
        swing=swing,
        structure=structure,
    )


def test_bullish_sequence_detected():
    structures = [
        make_structure(StructureType.HIGHER_HIGH, 0),
        make_structure(StructureType.HIGHER_LOW, 1),
        make_structure(StructureType.HIGHER_HIGH, 2),
    ]

    analysis = StructureAnalysisEngine().analyze(structures)

    assert analysis.bias == StructureBias.BULLISH
    assert analysis.structure_intact is True


def test_bearish_sequence_detected():
    structures = [
        make_structure(StructureType.LOWER_LOW, 0),
        make_structure(StructureType.LOWER_HIGH, 1),
        make_structure(StructureType.LOWER_LOW, 2),
    ]

    analysis = StructureAnalysisEngine().analyze(structures)

    assert analysis.bias == StructureBias.BEARISH
    assert analysis.structure_intact is True


def test_mixed_structure_returns_neutral():
    structures = [
        make_structure(StructureType.HIGHER_HIGH, 0),
        make_structure(StructureType.LOWER_LOW, 1),
        make_structure(StructureType.HIGHER_HIGH, 2),
    ]

    analysis = StructureAnalysisEngine().analyze(structures)

    assert analysis.bias == StructureBias.NEUTRAL
    assert analysis.structure_intact is False


def test_empty_input_returns_unknown():
    engine = StructureAnalysisEngine()

    analysis = engine.analyze([])

    assert analysis.bias == StructureBias.UNKNOWN
    assert analysis.latest is None
    assert analysis.bullish_sequence == 0
    assert analysis.bearish_sequence == 0
    assert analysis.structure_intact is False


def test_sequence_counters():
    structures = [
        make_structure(StructureType.HIGHER_HIGH, 0),
        make_structure(StructureType.HIGHER_LOW, 1),
        make_structure(StructureType.HIGHER_HIGH, 2),
        make_structure(StructureType.LOWER_LOW, 3),
        make_structure(StructureType.LOWER_HIGH, 4),
    ]

    analysis = StructureAnalysisEngine().analyze(structures)

    assert analysis.bullish_sequence == 0
    assert analysis.bearish_sequence == 2


def test_structure_intact_false():
    structures = [
        make_structure(StructureType.HIGHER_HIGH, 0),
        make_structure(StructureType.LOWER_LOW, 1),
        make_structure(StructureType.LOWER_HIGH, 2),
    ]

    analysis = StructureAnalysisEngine().analyze(structures)

    assert analysis.structure_intact is False