"""
Break of Structure engine.
"""

from engine.models.bos import BOSResult, BOSType
from engine.models.market_structure import StructurePoint
from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)


class BOSEngine:
    """
    Detects confirmed Break of Structure events.
    """

    def analyze(
        self,
        analysis: StructureAnalysis,
    ) -> BOSResult:
        """
        Detect a Break of Structure.
        """

        if analysis.latest is None:
            return BOSResult(
                detected=False,
                bos_type=BOSType.NONE,
                protected_swing=None,
                trigger_swing=None,
                confidence=0.0,
                reasons=["No market structure available."],
            )

        latest = analysis.latest

        # ----------------------------
        # Bullish structure broken
        # ----------------------------
        if (
            analysis.bias == StructureBias.BULLISH
            and latest.structure.name == "LOWER_LOW"
        ):
            return BOSResult(
                detected=True,
                bos_type=BOSType.BEARISH,
                protected_swing=None,
                trigger_swing=latest.swing,
                confidence=self._calculate_confidence(analysis),
                reasons=[
                    "Bullish market structure was active.",
                    "Latest confirmed structure formed a LOWER_LOW.",
                    "Protected Higher Low has been invalidated.",
                ],
            )

        # ----------------------------
        # Bearish structure broken
        # ----------------------------
        if (
            analysis.bias == StructureBias.BEARISH
            and latest.structure.name == "HIGHER_HIGH"
        ):
            return BOSResult(
                detected=True,
                bos_type=BOSType.BULLISH,
                protected_swing=None,
                trigger_swing=latest.swing,
                confidence=self._calculate_confidence(analysis),
                reasons=[
                    "Bearish market structure was active.",
                    "Latest confirmed structure formed a HIGHER_HIGH.",
                    "Protected Lower High has been invalidated.",
                ],
            )

        # ----------------------------
        # No BOS
        # ----------------------------
        
        if analysis.bias == StructureBias.BULLISH:
            reasons = [
                "Current bullish structure remains intact.",
                "Latest confirmed swing did not invalidate the bullish structure.",
            ]

        elif analysis.bias == StructureBias.BEARISH:
            reasons = [
                "Current bearish structure remains intact.",
                "Latest confirmed swing did not invalidate the bearish structure.",
    ]

        elif analysis.bias == StructureBias.NEUTRAL:
            reasons = [
                "Market structure is neutral.",
                "No valid Break of Structure detected.",
    ]

        else:
            reasons = [
                "Market bias is unknown.",
                "Insufficient structure to evaluate BOS.",
    ]
        
        return BOSResult(
            detected=False,
            bos_type=BOSType.NONE,
            protected_swing=None,
            trigger_swing=None,
            confidence=0.0,
            reasons=reasons,
        )

    def _calculate_confidence(
        self,
        analysis: StructureAnalysis,
    ) -> float:
        """
        Calculate BOS confidence.
        """

        confidence = 60.0

        if analysis.bullish_sequence >= 3:
            confidence += 10

        if analysis.bearish_sequence >= 3:
            confidence += 10

        return min(confidence, 80.0)