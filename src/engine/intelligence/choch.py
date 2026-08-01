"""
Change of Character Engine.
"""

from engine.models.bos import BOSResult, BOSType
from engine.models.choch import CHOCHResult, CHOCHType
from engine.models.market_structure import StructurePoint, StructureType
from engine.models.structure_analysis import StructureAnalysis, StructureBias


class CHOCHEngine:
    """
    Detects confirmed Change of Character events.
    """

    def analyze(
        self,
        structures: list[StructurePoint],
        analysis: StructureAnalysis,
        bos: BOSResult,
    ) -> CHOCHResult:

        if not structures:
            return CHOCHResult(
                detected=False,
                choch_type=CHOCHType.NONE,
                previous_bias=StructureBias.UNKNOWN,
                new_bias=StructureBias.UNKNOWN,
                confidence=0.0,
                reasons=["No market structure available."],
            )

        latest = structures[-1]

        # Bullish → Bearish transition
        if (
            bos.detected
            and bos.bos_type == BOSType.BEARISH
            and latest.structure == StructureType.LOWER_HIGH
        ):
            return CHOCHResult(
                detected=True,
                choch_type=CHOCHType.BEARISH,
                previous_bias=analysis.previous_bias,
                new_bias=StructureBias.BEARISH,
                confidence=80.0,
                reasons=[
                    "Bearish Break of Structure confirmed.",
                    "Latest structure formed a LOWER_HIGH.",
                    "Market has transitioned into a bearish character.",
        ],
    )

        # Bearish → Bullish transition
        if (
            bos.detected
            and bos.bos_type == BOSType.BULLISH
            and latest.structure == StructureType.HIGHER_LOW
        ):
            return CHOCHResult(
                detected=True,
                choch_type=CHOCHType.BULLISH,
                previous_bias=analysis.previous_bias,
                new_bias=StructureBias.BULLISH,
                confidence=80.0,
                reasons=[
                    "Bullish Break of Structure confirmed.",
                    "Latest structure formed a HIGHER_LOW.",
                    "Market has transitioned into a bullish character.",
        ],
    )

        return CHOCHResult(
            detected=False,
            choch_type=CHOCHType.NONE,
            previous_bias=analysis.previous_bias,
            new_bias=analysis.current_bias,
            confidence=0.0,
            reasons=["No Change of Character detected."],
)