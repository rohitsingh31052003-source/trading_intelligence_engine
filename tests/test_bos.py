from datetime import UTC, datetime

from engine.intelligence.bos import BOSEngine

from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)

from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)

from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)


def make_structure(
    price: float,
    swing_type: SwingType,
    structure: StructureType,
) -> StructurePoint:

    swing = SwingPoint(
        timestamp=datetime.now(),
        index=0,
        confirmation_index=0,
        price=price,
        swing_type=swing_type,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        strength=SwingStrength.NORMAL,
    )

    return StructurePoint(
        swing=swing,
        structure=structure,
    )


def test_no_structures():

    engine = BOSEngine()

    analysis = StructureAnalysis(
        previous_bias=StructureBias.UNKNOWN,
        current_bias=StructureBias.UNKNOWN,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=None,
        bullish_sequence=0,
        bearish_sequence=0,
        structure_intact=False,
    )

    result = engine.analyze(analysis)

    assert result.detected is False
    assert result.bos_type.name == "NONE"


def test_bullish_bias_breaks_down():

    latest = make_structure(
        100,
        SwingType.LOW,
        StructureType.LOWER_LOW,
    )

    analysis = StructureAnalysis(
        previous_bias=StructureBias.BULLISH,
        current_bias=StructureBias.NEUTRAL,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=latest,
        bullish_sequence=5,
        bearish_sequence=0,
        structure_intact=False,
    )

    result = BOSEngine().analyze(analysis)

    assert result.detected is True
    assert result.bos_type.name == "BEARISH"


def test_bearish_bias_breaks_up():

    latest = make_structure(
        120,
        SwingType.HIGH,
        StructureType.HIGHER_HIGH,
    )

    analysis = StructureAnalysis(
        previous_bias=StructureBias.BEARISH,
        current_bias=StructureBias.BEARISH,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=latest,
        bullish_sequence=0,
        bearish_sequence=6,
        structure_intact=False,
    )

    result = BOSEngine().analyze(analysis)

    assert result.detected is True
    assert result.bos_type.name == "BULLISH"


def test_healthy_bullish_trend():

    latest = make_structure(
        120,
        SwingType.HIGH,
        StructureType.HIGHER_HIGH,
    )

    analysis = StructureAnalysis(
        previous_bias=StructureBias.BULLISH,
        current_bias=StructureBias.NEUTRAL,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=latest,
        bullish_sequence=4,
        bearish_sequence=0,
        structure_intact=True,
    )

    result = BOSEngine().analyze(analysis)

    assert result.detected is False
    assert result.bos_type.name == "NONE"


def test_healthy_bearish_trend():

    latest = make_structure(
        80,
        SwingType.LOW,
        StructureType.LOWER_LOW,
    )

    analysis = StructureAnalysis(
        previous_bias=StructureBias.BEARISH,
        current_bias=StructureBias.NEUTRAL,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=latest,
        bullish_sequence=0,
        bearish_sequence=4,
        structure_intact=True,
    )

    result = BOSEngine().analyze(analysis)

    assert result.detected is False
    assert result.bos_type.name == "NONE"
