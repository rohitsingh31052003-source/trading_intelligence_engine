"""
Multi-timeframe alignment engine (Sprint 11U).

``MTFAlignmentEngine`` produces a deterministic, descriptive alignment
between a higher-timeframe context and a lower-timeframe opportunity
direction. It is part of the multi-timeframe layer of the separated
concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)  <- this layer
    7. MARKET SCANNER               (Sprint 11U)  <- this layer

The engine is deterministic and pure. It reads ONLY the already-computed
higher-timeframe :class:`MarketContext` (Sprint 11P) and the
lower-timeframe opportunity direction. It inspects no candles directly
and therefore cannot introduce look-ahead bias.

DESIGN PRINCIPLE — do not fabricate higher-timeframe evidence:

The alignment is computed from the descriptive ``MarketTrendState`` of
the higher-timeframe context. RANGE / NEUTRAL higher context is
deliberately NON-directional: it is NEVER silently interpreted as
bullish or bearish. A RANGE higher context + a LONG lower opportunity is
NEUTRAL alignment (the higher context neither supports nor opposes the
setup). UNKNOWN / unavailable higher context yields UNKNOWN alignment —
missing evidence is never fabricated.

DESIGN PRINCIPLE — descriptive, not predictive:

An MTF alignment describes the relationship between the higher context
and the lower opportunity. It is NOT a probability of success, NOT a
profitability prediction, and NOT a trading recommendation. A CONFLICTING
alignment is reported honestly (never silently downgraded to NEUTRAL);
an ALIGNED alignment does NOT guarantee a winner.

This is intelligence, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g.
``from engine.intelligence.mtf_alignment import MTFAlignmentEngine``.
"""

from __future__ import annotations

from engine.models.market_context import MarketContext, MarketTrendState
from engine.models.market_scan import MTFAlignment


# Higher-timeframe context state -> directional disposition.
# RANGE / NEUTRAL / UNKNOWN are deliberately NON-directional so a neutral
# / ranging / unknown higher context is never silently interpreted as
# bullish or bearish.
_CONTEXT_DIRECTION = {
    MarketTrendState.BULLISH: "BULLISH",
    MarketTrendState.BEARISH: "BEARISH",
    MarketTrendState.RANGE: None,
    MarketTrendState.NEUTRAL: None,
    MarketTrendState.UNKNOWN: None,
}


class MTFAlignmentEngine:
    """
    Produce a deterministic, descriptive multi-timeframe alignment.

    Public API:

        align(higher_context, lower_direction) -> MTFAlignment

    The engine is stateless across calls: identical inputs always produce
    identical outputs.
    """

    def align(
        self,
        higher_context: MarketContext | None,
        lower_direction: str,
    ) -> MTFAlignment:
        """
        Classify the alignment between the higher-timeframe context and
        the lower-timeframe opportunity direction.

        Rules (deterministic, documented):

        * No higher context (``None``) -> ``UNKNOWN``. Missing
          higher-timeframe evidence is never fabricated.
        * Higher context trend is ``UNKNOWN`` (insufficient structure)
          -> ``UNKNOWN``.
        * Lower opportunity direction is non-directional
          (``""`` / ``"NONE"``) -> ``UNKNOWN``.
        * Higher context is ``RANGE`` / ``NEUTRAL`` (non-directional but
          observed) -> ``NEUTRAL``. The higher context neither supports
          nor opposes the setup; never silently bullish / bearish.
        * Higher context BULLISH + lower LONG -> ``ALIGNED``.
        * Higher context BULLISH + lower SHORT -> ``CONFLICTING``.
        * Higher context BEARISH + lower SHORT -> ``ALIGNED``.
        * Higher context BEARISH + lower LONG -> ``CONFLICTING``.
        """

        if higher_context is None:
            return MTFAlignment.UNKNOWN

        trend = higher_context.trend
        if trend is None:
            return MTFAlignment.UNKNOWN

        context_dir = _CONTEXT_DIRECTION.get(trend.state)
        if context_dir is None:
            # RANGE / NEUTRAL / UNKNOWN observed context. RANGE / NEUTRAL
            # are observed-but-non-directional -> NEUTRAL alignment;
            # UNKNOWN is insufficient -> UNKNOWN.
            if trend.state == MarketTrendState.UNKNOWN:
                return MTFAlignment.UNKNOWN
            return MTFAlignment.NEUTRAL

        direction = (lower_direction or "").strip().upper()
        if direction in ("", "NONE"):
            return MTFAlignment.UNKNOWN

        if direction == "LONG":
            lower_dir = "BULLISH"
        elif direction == "SHORT":
            lower_dir = "BEARISH"
        else:
            return MTFAlignment.UNKNOWN

        if context_dir == lower_dir:
            return MTFAlignment.ALIGNED
        return MTFAlignment.CONFLICTING


__all__ = ["MTFAlignmentEngine"]
