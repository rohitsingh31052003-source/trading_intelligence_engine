from datetime import datetime

from engine.intelligence.structure_analysis import (
    StructureAnalysisEngine,
)
from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingType,
)
from engine.models.swing import SwingEvidence
from engine.models.structure_analysis import StructureBias


def make_structure(
    price: float,
    swing_type: SwingType,
    structure: StructureType,
) -> StructurePoint:

    swing = SwingPoint(
        timestamp=datetime(2025, 1, 1),
        index=0,
        confirmation_index=0,
        price=price,
        swing_type=swing_type,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        evidence=SwingEvidence(),
    )

    return StructurePoint(
        swing=swing,
        structure=structure,
    )


def test_bullish_sequence_detected():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(95, SwingType.LOW, StructureType.HIGHER_LOW),
        make_structure(110, SwingType.HIGH, StructureType.HIGHER_HIGH),
    ]

    analysis = engine.analyze(structures)

    assert analysis.previous_bias == StructureBias.BULLISH
    assert analysis.current_bias == StructureBias.BULLISH
    assert analysis.structure_intact is True


def test_bearish_sequence_detected():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.LOW, StructureType.LOWER_LOW),
        make_structure(105, SwingType.HIGH, StructureType.LOWER_HIGH),
        make_structure(90, SwingType.LOW, StructureType.LOWER_LOW),
    ]

    analysis = engine.analyze(structures)

    assert analysis.previous_bias == StructureBias.BEARISH
    assert analysis.current_bias == StructureBias.BEARISH
    assert analysis.structure_intact is True


def test_mixed_structure_returns_neutral():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(90, SwingType.LOW, StructureType.LOWER_LOW),
        make_structure(95, SwingType.HIGH, StructureType.LOWER_HIGH),
    ]

    analysis = engine.analyze(structures)

    assert analysis.current_bias == StructureBias.NEUTRAL
    assert analysis.structure_intact is False


def test_empty_input_returns_unknown():

    engine = StructureAnalysisEngine()

    analysis = engine.analyze([])

    assert analysis.previous_bias == StructureBias.UNKNOWN
    assert analysis.current_bias == StructureBias.UNKNOWN
    assert analysis.latest is None


def test_sequence_counters():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(95, SwingType.LOW, StructureType.HIGHER_LOW),
        make_structure(110, SwingType.HIGH, StructureType.HIGHER_HIGH),
    ]

    analysis = engine.analyze(structures)

    assert analysis.bullish_sequence == 3
    assert analysis.bearish_sequence == 0


def test_structure_intact_false():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(90, SwingType.LOW, StructureType.LOWER_LOW),
    ]

    analysis = engine.analyze(structures)

    assert analysis.structure_intact is False


def test_bullish_break_transitions_to_neutral():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(95, SwingType.LOW, StructureType.HIGHER_LOW),
        make_structure(110, SwingType.HIGH, StructureType.HIGHER_HIGH),
        make_structure(80, SwingType.LOW, StructureType.LOWER_LOW),
    ]

    analysis = engine.analyze(structures)

    assert analysis.previous_bias == StructureBias.BULLISH
    assert analysis.current_bias == StructureBias.NEUTRAL


def test_bearish_break_transitions_to_neutral():

    engine = StructureAnalysisEngine()

    structures = [
        make_structure(100, SwingType.LOW, StructureType.LOWER_LOW),
        make_structure(105, SwingType.HIGH, StructureType.LOWER_HIGH),
        make_structure(90, SwingType.LOW, StructureType.LOWER_LOW),
        make_structure(120, SwingType.HIGH, StructureType.HIGHER_HIGH),
    ]

    analysis = engine.analyze(structures)

    assert analysis.previous_bias == StructureBias.BEARISH
    assert analysis.current_bias == StructureBias.NEUTRAL
