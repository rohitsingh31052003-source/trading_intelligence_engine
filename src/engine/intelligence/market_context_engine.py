"""
Market context engine (Sprint 11P).

``MarketContextEngine`` orchestrates the new market-context
intelligence into a single, deterministic, historical-safe
``MarketContext`` snapshot at an evaluation point ``T``. It composes
the existing engines (swing detection, market structure, structure
analysis) with the new descriptive engines (range detection,
support/resistance context, descriptive trend) WITHOUT modifying any
existing engine or feeding the new evidence into the existing
confluence / decision / signal logic.

Dependency direction (preserved):

    models
       ↑
    intelligence engines (existing + new)
       ↑
    pipeline / orchestration

The new market-context intelligence is part of the intelligence layer,
not a new orchestration package. ``intelligence/__init__.py`` stays
intentionally empty; import via full paths, e.g.
``from engine.intelligence.market_context_engine import MarketContextEngine``.

Historical / look-ahead safety (STRUCTURAL):

``analyze_sequence(candles)`` walks the candle sequence forward. At
each evaluation point ``T`` it feeds ONLY ``candles[:T+1]`` to every
underlying engine. Because the existing ``SwingEngine`` confirms a
swing at index ``i`` only after ``lookback`` candles to the right of
``i`` are present, a swing whose confirmation index exceeds ``T`` is
NOT yet emitted and therefore cannot influence the context at ``T``.
The context at ``T`` is thus a function of ``candles[:T+1]`` only.

The ``analyze_at`` method produces the context for a single index
``T`` from a supplied prefix ``candles[:T+1]``; it is the building
block ``analyze_sequence`` reuses.
"""

from __future__ import annotations

from typing import Iterable

from engine.config.market_context_config import MarketContextConfig
from engine.config.swing_config import SwingConfig
from engine.intelligence.market_trend import MarketTrendEngine
from engine.intelligence.range_detection import RangeDetectionEngine
from engine.intelligence.structure import MarketStructureEngine
from engine.intelligence.structure_analysis import StructureAnalysisEngine
from engine.intelligence.support_resistance_context import (
    SupportResistanceContextEngine,
)
from engine.intelligence.swings import SwingEngine
from engine.models.market_context import MarketContext
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingStatus


class MarketContextEngine:
    """
    Orchestrate market-context intelligence into a single
    deterministic, historical-safe ``MarketContext`` snapshot.

    Public API:

        analyze_at(candles, index) -> MarketContext
            Context at ``index`` using only ``candles[:index+1]``.

        analyze_sequence(candles) -> tuple[MarketContext, ...]
            Walk-forward context for every evaluation point.
    """

    def __init__(
        self,
        config: MarketContextConfig | None = None,
        swing_config: SwingConfig | None = None,
    ) -> None:
        self.config = config or MarketContextConfig()
        self.swing_config = swing_config or SwingConfig()

        # Existing engines are constructed once and reused unchanged.
        self._swing_engine = SwingEngine(self.swing_config)
        self._structure_engine = MarketStructureEngine()
        self._structure_analysis_engine = StructureAnalysisEngine()

        # New Sprint 11P engines.
        self._range_engine = RangeDetectionEngine(self.config.range)
        self._sr_engine = SupportResistanceContextEngine(
            self.config.support_resistance,
        )
        self._trend_engine = MarketTrendEngine()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def analyze_at(
        self,
        candles: Iterable[OHLCVCandle],
        index: int,
    ) -> MarketContext:
        """
        Produce the market context at ``index`` using only
        ``candles[:index+1]``.
        """

        history = list(candles)
        if index < 0 or index >= len(history):
            raise IndexError(
                f"index {index} out of range for {len(history)} candles",
            )

        visible = history[: index + 1]
        candle = visible[-1]

        # Existing engines operate on the visible prefix only.
        swings = self._swing_engine.detect(visible)

        # Only confirmed swings feed structure analysis, matching the
        # existing ``MarketStructureEngine`` contract. ``SwingEngine``
        # emits a swing only when its right-side confirmation candles
        # are present, so no future-confirmed structure can leak into
        # the context at ``index``.
        confirmed = [s for s in swings if s.status == SwingStatus.CONFIRMED]

        structures = self._structure_engine.analyze(confirmed)
        analysis = self._structure_analysis_engine.analyze(structures)

        range_context = self._range_engine.detect(structures, candle)
        trend = self._trend_engine.analyze(analysis, range_context)
        sr_context = self._sr_engine.analyze(structures, candle)

        recent_structure = tuple(
            structures[-self.config.recent_structure_count :]
        )

        return MarketContext(
            index=index,
            trend=trend,
            range=range_context,
            support_resistance=sr_context,
            recent_structure=recent_structure,
            confirmed_swings=len(confirmed),
        )

    def analyze_sequence(
        self,
        candles: Iterable[OHLCVCandle],
    ) -> tuple[MarketContext, ...]:
        """
        Walk-forward market context for every candle index.

        The context at index ``T`` is computed using only
        ``candles[:T+1]`` (via ``analyze_at``).
        """

        history = list(candles)
        return tuple(
            self.analyze_at(history, t) for t in range(len(history))
        )
