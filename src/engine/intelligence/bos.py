"""
Break of Structure engine.
"""

from engine.models.bos import BOSResult, BOSType
from engine.models.market_structure import StructureType
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
                reasons=[
                    "No market structure available.",
                ],
            )

        latest = analysis.latest

        # ---------------------------------------
        # Previous Bullish Structure Broken
        # ---------------------------------------
        if (
            analysis.previous_bias == StructureBias.BULLISH
            and latest.structure == StructureType.LOWER_LOW
        ):
            return BOSResult(
                detected=True,
                bos_type=BOSType.BEARISH,
                protected_swing=(
                    analysis.previous_protected_structure.swing
                    if analysis.previous_protected_structure
                    else None
                ),
                trigger_swing=latest.swing,
                confidence=self._calculate_confidence(analysis),
                reasons=[
                    "Previous bullish structure was active.",
                    "Latest LOWER_LOW invalidated the protected Higher Low.",
                    "Market transitioned to a neutral state.",
                ],
            )

        # ---------------------------------------
        # Previous Bearish Structure Broken
        # ---------------------------------------
        if (
            analysis.previous_bias == StructureBias.BEARISH
            and latest.structure == StructureType.HIGHER_HIGH
        ):
            return BOSResult(
                detected=True,
                bos_type=BOSType.BULLISH,
                protected_swing=(
                    analysis.previous_protected_structure.swing
                    if analysis.previous_protected_structure
                    else None
                ),
                trigger_swing=latest.swing,
                confidence=self._calculate_confidence(analysis),
                reasons=[
                    "Previous bearish structure was active.",
                    "Latest HIGHER_HIGH invalidated the protected Lower High.",
                    "Market transitioned to a neutral state.",
                ],
            )

        # ---------------------------------------
        # No BOS
        # ---------------------------------------
        if analysis.current_bias == StructureBias.BULLISH:
            reasons = [
                "Current bullish structure remains intact.",
                "Latest confirmed swing did not invalidate the protected Higher Low.",
            ]

        elif analysis.current_bias == StructureBias.BEARISH:
            reasons = [
                "Current bearish structure remains intact.",
                "Latest confirmed swing did not invalidate the protected Lower High.",
            ]

        elif analysis.current_bias == StructureBias.NEUTRAL:
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
