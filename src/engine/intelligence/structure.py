"""
Market structure analysis engine.
"""

from __future__ import annotations

from engine.models.market_structure import (
    StructurePoint,
    StructureType,
)
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingType,
)


class MarketStructureEngine:
    """
    Classifies swing points into market structure.

    High swings become:
        - FIRST_HIGH
        - HIGHER_HIGH
        - LOWER_HIGH

    Low swings become:
        - FIRST_LOW
        - HIGHER_LOW
        - LOWER_LOW
    """

    def analyze(
        self,
        swings: list[SwingPoint],
    ) -> list[StructurePoint]:
        """
        Analyze swing points and classify market structure.
        """
        confirmed_swings = [
            swing
            for swing in swings
            if swing.status == SwingStatus.CONFIRMED
        ]

        structures: list[StructurePoint] = []

        last_high: SwingPoint | None = None
        last_low: SwingPoint | None = None


        for swing in confirmed_swings:

            # -------------------------
            # HIGHS
            # -------------------------
            if swing.swing_type == SwingType.HIGH:

                if last_high is None:
                    structure = StructureType.FIRST_HIGH

                elif swing.price > last_high.price:
                    structure = StructureType.HIGHER_HIGH

                else:
                    structure = StructureType.LOWER_HIGH

                last_high = swing

            # -------------------------
            # LOWS
            # -------------------------
            else:

                if last_low is None:
                    structure = StructureType.FIRST_LOW

                elif swing.price > last_low.price:
                    structure = StructureType.HIGHER_LOW

                else:
                    structure = StructureType.LOWER_LOW

                last_low = swing

            structures.append(
                StructurePoint(
                    swing=swing,
                    structure=structure,
                )
            )

        return structures