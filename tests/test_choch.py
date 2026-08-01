from datetime import datetime

from engine.intelligence.choch import CHOCHEngine
from engine.models.bos import BOSResult, BOSType
from engine.models.choch import CHOCHType
from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)
from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)
from engine.models.swing import (
    SwingEvidence,
    SwingPoint,
    SwingStatus,
    SwingType,
)


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


def make_analysis(
    previous_bias: StructureBias,
    current_bias: StructureBias,
) -> StructureAnalysis:

    return StructureAnalysis(
        previous_bias=previous_bias,
        current_bias=current_bias,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=None,
        bullish_sequence=0,
        bearish_sequence=0,
        structure_intact=False,
        reasons=[],
    )


def test_empty_structure_list():

    engine = CHOCHEngine()

    analysis = make_analysis(
        StructureBias.UNKNOWN,
        StructureBias.UNKNOWN,
    )

    bos = BOSResult(
        detected=False,
        bos_type=BOSType.NONE,
        protected_swing=None,
        trigger_swing=None,
    )

    result = engine.analyze([], analysis, bos)

    assert result.detected is False
    assert result.choch_type == CHOCHType.NONE


def test_bearish_choch():

    engine = CHOCHEngine()

    latest = make_structure(
        100,
        SwingType.HIGH,
        StructureType.LOWER_HIGH,
    )

    analysis = make_analysis(
        StructureBias.BULLISH,
        StructureBias.NEUTRAL,
    )

    bos = BOSResult(
        detected=True,
        bos_type=BOSType.BEARISH,
        protected_swing=None,
        trigger_swing=latest.swing,
        confidence=70,
    )

    result = engine.analyze(
        [latest],
        analysis,
        bos,
    )

    assert result.detected is True
    assert result.choch_type == CHOCHType.BEARISH
    assert result.new_bias == StructureBias.BEARISH


def test_bullish_choch():

    engine = CHOCHEngine()

    latest = make_structure(
        120,
        SwingType.LOW,
        StructureType.HIGHER_LOW,
    )

    analysis = make_analysis(
        StructureBias.BEARISH,
        StructureBias.NEUTRAL,
    )

    bos = BOSResult(
        detected=True,
        bos_type=BOSType.BULLISH,
        protected_swing=None,
        trigger_swing=latest.swing,
        confidence=70,
    )

    result = engine.analyze(
        [latest],
        analysis,
        bos,
    )

    assert result.detected is True
    assert result.choch_type == CHOCHType.BULLISH
    assert result.new_bias == StructureBias.BULLISH


def test_bos_without_confirmation():

    engine = CHOCHEngine()

    latest = make_structure(
        120,
        SwingType.HIGH,
        StructureType.HIGHER_HIGH,
    )

    analysis = make_analysis(
        StructureBias.BULLISH,
        StructureBias.NEUTRAL,
    )

    bos = BOSResult(
        detected=True,
        bos_type=BOSType.BEARISH,
        protected_swing=None,
        trigger_swing=latest.swing,
        confidence=70,
    )

    result = engine.analyze(
        [latest],
        analysis,
        bos,
    )

    assert result.detected is False
    assert result.choch_type == CHOCHType.NONE


def test_no_bos():

    engine = CHOCHEngine()

    latest = make_structure(
        120,
        SwingType.LOW,
        StructureType.HIGHER_LOW,
    )

    analysis = make_analysis(
        StructureBias.BEARISH,
        StructureBias.NEUTRAL,
    )

    bos = BOSResult(
        detected=False,
        bos_type=BOSType.NONE,
        protected_swing=None,
        trigger_swing=None,
    )

    result = engine.analyze(
        [latest],
        analysis,
        bos,
    )

    assert result.detected is False
    assert result.choch_type == CHOCHType.NONE