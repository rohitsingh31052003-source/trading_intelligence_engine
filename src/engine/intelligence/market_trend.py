"""
Descriptive market trend / context engine (Sprint 11P).

``MarketTrendEngine`` produces a *descriptive* trend state derived
from market structure and (optionally) the range state. It is
deliberately distinct from the signal-pipeline ``TrendEngine``
(``engine.intelligence.trend``), which is driven by Break of
Structure / Change of Character and is consumed by the confluence /
decision / signal logic. This engine makes NO change to that pipeline
trend engine; it adds an independent, descriptive context view.

Classification rules (documented and deterministic):

* When a consolidation range is active (``range.state == IN_RANGE``)
  the trend state is ``RANGE`` regardless of the underlying bias,
  because sideways behaviour dominates directional structure.
* Otherwise the trend state follows the structure bias when the
  directional structure is intact:
    - BULLISH + structure_intact -> BULLISH
    - BEARISH + structure_intact -> BEARISH
* A NEUTRAL bias with confirmed structure yields ``NEUTRAL``.
* Any other condition (including no confirmed structures) yields
  ``UNKNOWN``.

No trade signal is produced. A "BULLISH" trend state describes the
observed structure; it does not recommend a long position.
"""

from __future__ import annotations

from engine.models.market_context import (
    MarketTrend,
    MarketTrendState,
    RangeContext,
    RangeState,
)
from engine.models.structure_analysis import (
    StructureAnalysis,
    StructureBias,
)


class MarketTrendEngine:
    """
    Derive a descriptive trend / context state from market structure.
    """

    def analyze(
        self,
        analysis: StructureAnalysis,
        range_context: RangeContext,
    ) -> MarketTrend:
        """
        Classify the descriptive trend state at the evaluation point.
        """

        if range_context.state == RangeState.IN_RANGE:
            return MarketTrend(
                state=MarketTrendState.RANGE,
                bias=analysis.current_bias,
                structure_intact=analysis.structure_intact,
                reasons=[
                    "Active consolidation range detected; sideways "
                    "behaviour dominates directional structure.",
                ],
            )

        if range_context.state == RangeState.UNKNOWN and not analysis.latest:
            return MarketTrend(
                state=MarketTrendState.UNKNOWN,
                bias=StructureBias.UNKNOWN,
                structure_intact=False,
                reasons=[
                    "No confirmed market structure available for "
                    "trend classification.",
                ],
            )

        bias = analysis.current_bias

        if bias == StructureBias.BULLISH and analysis.structure_intact:
            return MarketTrend(
                state=MarketTrendState.BULLISH,
                bias=bias,
                structure_intact=True,
                reasons=[
                    "Bullish market structure remains intact "
                    "(higher highs and higher lows).",
                ],
            )

        if bias == StructureBias.BEARISH and analysis.structure_intact:
            return MarketTrend(
                state=MarketTrendState.BEARISH,
                bias=bias,
                structure_intact=True,
                reasons=[
                    "Bearish market structure remains intact "
                    "(lower highs and lower lows).",
                ],
            )

        if bias == StructureBias.NEUTRAL and analysis.latest is not None:
            return MarketTrend(
                state=MarketTrendState.NEUTRAL,
                bias=bias,
                structure_intact=analysis.structure_intact,
                reasons=[
                    "No dominant directional structure; bias is "
                    "neutral.",
                ],
            )

        return MarketTrend(
            state=MarketTrendState.UNKNOWN,
            bias=bias,
            structure_intact=analysis.structure_intact,
            reasons=[
                "Insufficient directional structure for trend "
                "classification.",
            ],
        )
