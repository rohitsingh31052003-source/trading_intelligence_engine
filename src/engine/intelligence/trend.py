"""
Trend Engine.
"""

from engine.intelligence import bos
from engine.models.trend import TrendResult, TrendState
from engine.intelligence.evidence.trend_evidence import TrendEvidence
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
                evidence=TrendEvidence(
                    structure_score=30,
                    sequence_score=20,
                    bos_score=15,
                    choch_score=25,
                    quality_score=10,
                ),
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
                evidence=TrendEvidence(
                    structure_score=30,
                    sequence_score=10,
                    bos_score=0,
                    choch_score=0,
                    quality_score=8,
                ),
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
                evidence=TrendEvidence(
                    structure_score=15,
                    sequence_score=10,
                    bos_score=15,
                    choch_score=0,
                    quality_score=5,
                ),
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
                evidence=TrendEvidence(
                    structure_score=30,
                    sequence_score=20,
                    bos_score=15,
                    choch_score=25,
                    quality_score=10,
                ),
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
                evidence=TrendEvidence(
                    structure_score=30,
                    sequence_score=10,
                    bos_score=0,
                    choch_score=0,
                    quality_score=8,
                ),
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
                evidence=TrendEvidence(
                    structure_score=10,
                    sequence_score=5,
                    bos_score=0,
                    choch_score=0,
                    quality_score=5,
                ),
                previous_bias=analysis.previous_bias,
                current_bias=analysis.current_bias,
                reasons=[
                    "No dominant directional structure.",
                ],
            )

        return TrendResult(
            state=TrendState.UNKNOWN,
            previous_bias=analysis.previous_bias,
            current_bias=analysis.current_bias,
            evidence=TrendEvidence(),
            reasons=[
                "Unable to determine market regime.",
            ],
        )
