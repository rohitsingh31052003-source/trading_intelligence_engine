from engine.intelligence.trend import TrendEngine
from engine.models.choch import CHOCHResult, CHOCHType
from engine.models.bos import BOSResult, BOSType
from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)
from engine.models.trend import TrendState


def make_analysis(
    previous: StructureBias,
    current: StructureBias,
    intact: bool,
) -> StructureAnalysis:

    return StructureAnalysis(
        previous_bias=previous,
        current_bias=current,
        previous_protected_structure=None,
        current_protected_structure=None,
        latest=None,
        bullish_sequence=0,
        bearish_sequence=0,
        structure_intact=intact,
        reasons=[],
    )


def make_bos(
    detected: bool,
    bos_type: BOSType,
) -> BOSResult:

    return BOSResult(
        detected=detected,
        bos_type=bos_type,
        protected_swing=None,
        trigger_swing=None,
        confidence=70.0,
        reasons=[],
    )


def make_choch(
    detected: bool,
    choch_type: CHOCHType,
    previous: StructureBias,
    new: StructureBias,
) -> CHOCHResult:

    return CHOCHResult(
        detected=detected,
        choch_type=choch_type,
        previous_bias=previous,
        new_bias=new,
        confidence=80.0,
        reasons=[],
    )


def test_choch_bullish():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.BEARISH,
        StructureBias.BULLISH,
        True,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        True,
        CHOCHType.BULLISH,
        StructureBias.BEARISH,
        StructureBias.BULLISH,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.BULLISH


def test_choch_bearish():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.BULLISH,
        StructureBias.BEARISH,
        True,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        True,
        CHOCHType.BEARISH,
        StructureBias.BULLISH,
        StructureBias.BEARISH,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.BEARISH


def test_bos_only_transition():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.BULLISH,
        StructureBias.NEUTRAL,
        False,
    )

    bos = make_bos(
        True,
        BOSType.BEARISH,
    )

    choch = make_choch(
        False,
        CHOCHType.NONE,
        StructureBias.BULLISH,
        StructureBias.NEUTRAL,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.TRANSITION


def test_bullish_structure():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.BULLISH,
        StructureBias.BULLISH,
        True,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        False,
        CHOCHType.NONE,
        StructureBias.BULLISH,
        StructureBias.BULLISH,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.BULLISH


def test_bearish_structure():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.BEARISH,
        StructureBias.BEARISH,
        True,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        False,
        CHOCHType.NONE,
        StructureBias.BEARISH,
        StructureBias.BEARISH,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.BEARISH


def test_neutral_structure():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.NEUTRAL,
        StructureBias.NEUTRAL,
        False,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        False,
        CHOCHType.NONE,
        StructureBias.NEUTRAL,
        StructureBias.NEUTRAL,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.RANGING


def test_unknown_structure():

    engine = TrendEngine()

    analysis = make_analysis(
        StructureBias.UNKNOWN,
        StructureBias.UNKNOWN,
        False,
    )

    bos = make_bos(False, BOSType.NONE)

    choch = make_choch(
        False,
        CHOCHType.NONE,
        StructureBias.UNKNOWN,
        StructureBias.UNKNOWN,
    )

    result = engine.analyze(
        analysis,
        bos,
        choch,
    )

    assert result.state == TrendState.UNKNOWN