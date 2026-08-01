"""
Analyzes market structure to determine overall trend bias.
"""

from __future__ import annotations

from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)
from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)


class StructureAnalysisEngine:
    """
    Determines overall market structure bias from
    classified structure points.
    """

    def analyze(
        self,
        structures: list[StructurePoint],
    ) -> StructureAnalysis:
        """
        Analyze market structure and return the current bias.
        """

        if not structures:
            return StructureAnalysis(
                previous_bias=StructureBias.UNKNOWN,
                current_bias=StructureBias.UNKNOWN,
                previous_protected_structure=None,
                current_protected_structure=None,
                latest=None,
                bullish_sequence=0,
                bearish_sequence=0,
                structure_intact=False,
                reasons=[
                    "No confirmed market structure available."
                ],
            )

        bullish_sequence = 0
        bearish_sequence = 0

        previous_bias = StructureBias.UNKNOWN
        current_bias = StructureBias.NEUTRAL

        previous_protected_structure = None
        current_protected_structure = None

        reasons: list[str] = []

        # -----------------------------
        # Count trend sequences
        # -----------------------------
        for point in structures:

            structure = point.structure

            if structure in (
                StructureType.HIGHER_HIGH,
                StructureType.HIGHER_LOW,
            ):
                bullish_sequence += 1
                bearish_sequence = 0

                current_protected_structure = point

            elif structure in (
                StructureType.LOWER_HIGH,
                StructureType.LOWER_LOW,
            ):
                bearish_sequence += 1
                bullish_sequence = 0

                current_protected_structure = point

            else:
                bullish_sequence = 0
                bearish_sequence = 0

        latest = structures[-1]

        previous_structure = (
            structures[-2]
            if len(structures) >= 2
            else None
)

        structure_intact = False

        # -----------------------------
        # Determine current bias
        # -----------------------------
        if len(structures) >= 3:

            last_three = [
                s.structure
                for s in structures[-3:]
            ]

            # Bullish structure
            if last_three == [
                StructureType.HIGHER_HIGH,
                StructureType.HIGHER_LOW,
                StructureType.HIGHER_HIGH,
            ]:

                current_bias = StructureBias.BULLISH
                previous_bias = StructureBias.BULLISH

                structure_intact = True

                reasons = [
                    "Bullish market structure remains intact."
                ]

            # Bearish structure
            elif last_three == [
                StructureType.LOWER_LOW,
                StructureType.LOWER_HIGH,
                StructureType.LOWER_LOW,
            ]:

                current_bias = StructureBias.BEARISH
                previous_bias = StructureBias.BEARISH

                structure_intact = True

                reasons = [
                    "Bearish market structure remains intact."
                ]

            # Bullish -> Neutral
            elif (
                latest.structure == StructureType.LOWER_LOW
                and previous_structure is not None
                and previous_structure.structure
                in (
                    StructureType.HIGHER_HIGH,
                    StructureType.HIGHER_LOW,
    )
            ):

                previous_bias = StructureBias.BULLISH
                current_bias = StructureBias.NEUTRAL

                previous_protected_structure = (
                current_protected_structure
                )

                structure_intact = False

                reasons = [
                    "Bullish structure was invalidated.",
                    "Bias transitioned to NEUTRAL.",
                ]

            # Bearish -> Neutral
            elif (
                latest.structure == StructureType.HIGHER_HIGH
                and previous_structure is not None
                and previous_structure.structure
                in (
                    StructureType.LOWER_LOW,
                    StructureType.LOWER_HIGH,
    )
            ):

                previous_bias = StructureBias.BEARISH
                current_bias = StructureBias.NEUTRAL

                previous_protected_structure = (
                current_protected_structure
                )

                structure_intact = False

                reasons = [
                    "Bearish structure was invalidated.",
                    "Bias transitioned to NEUTRAL.",
                ]

            else:

                previous_bias = current_bias
                current_bias = StructureBias.NEUTRAL

                structure_intact = False

                reasons = [
                    "No confirmed bullish or bearish structure."
                ]

        return StructureAnalysis(
            previous_bias=previous_bias,
            current_bias=current_bias,
            previous_protected_structure=previous_protected_structure,
            current_protected_structure=current_protected_structure,
            latest=latest,
            bullish_sequence=bullish_sequence,
            bearish_sequence=bearish_sequence,
            structure_intact=structure_intact,
            reasons=reasons,
        )