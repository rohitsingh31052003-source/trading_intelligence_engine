"""
Trend Engine.
"""

from engine.intelligence import bos
from engine.models.trend import TrendResult, TrendState
from engine.models.structure_analysis import StructureAnalysis
from engine.models.bos import BOSResult
from engine.models.choch import CHOCHResult, CHOCHType


class TrendEngine:
    """
    Determines the overall market regime.
    """

    def analyze(
        self,
        analysis: StructureAnalysis,
        bos: BOSResult,
        choch: CHOCHResult,
    ) -> TrendResult:

        # Strong Bullish
        if choch.detected and choch.choch_type == CHOCHType.BULLISH:
            return TrendResult(
                state=TrendState.BULLISH,
                confidence=90.0,
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "Bullish Change of Character confirmed.",
                    "Bullish market regime established.",
        ],
    )

        # Strong Bearish
        if choch.detected and choch.choch_type == CHOCHType.BEARISH:
            return TrendResult(
                state=TrendState.BEARISH,
                confidence=90.0,
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "Bearish Change of Character confirmed.",
                    "Bearish market regime established.",
        ],
    )

        # Transition
        if bos.detected:
            return TrendResult(
                state=TrendState.TRANSITION,
                confidence=70.0,
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "Break of Structure detected.",
                    "Market is transitioning.",
        ],
    )

        # Bullish continuation
        if analysis.current_bias.name == "BULLISH":
            return TrendResult(
                state=TrendState.BULLISH,
                confidence=80.0,
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "Bullish market structure remains intact.",
        ],
    )

        # Bearish continuation
        if analysis.current_bias.name == "BEARISH":
            return TrendResult(
                state=TrendState.BEARISH,
                confidence=80.0,
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "Bearish market structure remains intact.",
        ],
    )

        # Neutral / Ranging
        if analysis.current_bias.name == "NEUTRAL":
            return TrendResult(
            state=TrendState.RANGING,
            confidence=60.0,
            previous_bias=analysis.previous_bias,
            current_bias=analysis.current_bias,
            reasons=[
                "No dominant directional structure.",
        ],
    )

        return TrendResult(
            state=TrendState.UNKNOWN,
            confidence=0.0,
            previous_bias=analysis.previous_bias,
            current_bias=analysis.current_bias,
            reasons=[
                "Unable to determine market regime.",
    ],
)