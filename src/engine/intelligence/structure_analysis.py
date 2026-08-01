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
                bias=StructureBias.UNKNOWN,
                latest=None,
                bullish_sequence=0,
                bearish_sequence=0,
                structure_intact=False,
                protected_structure=None,
    )

        bullish_sequence = 0
        bearish_sequence = 0

        protected_structure = None

        for point in structures:
            structure = point.structure

            if structure == StructureType.HIGHER_LOW:
                protected_structure = point

            elif structure == StructureType.LOWER_HIGH:
                protected_structure = point

            if structure in (
                StructureType.HIGHER_HIGH,
                StructureType.HIGHER_LOW,
            ):
                bullish_sequence += 1
                bearish_sequence = 0

            elif structure in (
                StructureType.LOWER_HIGH,
                StructureType.LOWER_LOW,
            ):
                bearish_sequence += 1
                bullish_sequence = 0

            else:
                bullish_sequence = 0
                bearish_sequence = 0

        latest = structures[-1]

        bias = StructureBias.NEUTRAL
        structure_intact = False

        if len(structures) >= 3:

            last_three = [
                s.structure
                for s in structures[-3:]
            ]

            if last_three == [
                StructureType.HIGHER_HIGH,
                StructureType.HIGHER_LOW,
                StructureType.HIGHER_HIGH,
            ]:
                bias = StructureBias.BULLISH
                structure_intact = True

            elif last_three == [
                StructureType.LOWER_LOW,
                StructureType.LOWER_HIGH,
                StructureType.LOWER_LOW,
            ]:
                bias = StructureBias.BEARISH
                structure_intact = True

            else:
                bias = StructureBias.NEUTRAL
                structure_intact = False

        return StructureAnalysis(
            bias=bias,
            latest=latest,
            bullish_sequence=bullish_sequence,
            bearish_sequence=bearish_sequence,
            structure_intact=structure_intact,
            protected_structure=protected_structure,
        )