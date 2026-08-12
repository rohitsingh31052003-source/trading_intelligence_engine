"""
Consolidation / range detection engine (Sprint 11P).

``RangeDetectionEngine`` inspects confirmed market structure (HH/HL/
LH/LL) and classifies whether the market is currently *consolidating*
(behaving sideways) or *directional*. It is deterministic and
historical-safe: it operates only on the structure points it is given,
which are themselves derived from confirmed swings whose confirmation
was available at the evaluation point.

The goal is NOT a perfect institutional range detector. It is a clean,
descriptive first version that distinguishes sideways behaviour from
directional structure and provides approximate range boundaries plus
the current price position within the range.

No trade signal is produced. A range is a description of price
behaviour.
"""

from __future__ import annotations

from engine.config.market_context_config import RangeDetectionConfig
from engine.models.market_context import RangeContext, RangeState
from engine.models.market_structure import StructurePoint, StructureType
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingType


class RangeDetectionEngine:
    """
    Detect basic consolidation / range conditions.

    The classification rule is documented and deterministic:

    Flat swing grouping (checked first)
        The recent confirmed swing highs (resp. lows) are grouped. A
        group is "flat" when every member's price is within
        ``range_tolerance`` of the group average. When at least
        ``min_flat_count`` flat highs AND flat lows exist, the market
        is classified ``IN_RANGE`` with boundaries equal to the flat
        high average (resistance) and flat low average (support).
        Flat-ness is checked before directional dominance because a
        flat consolidation IS a range by definition, even when the
        (degenerate) structure labels fall back to LH/LL for equal
        swing prices.

    Directional dominance suppression
        Otherwise, if the most recent confirmed structures show strong
        directional dominance (more than
        ``max_recent_structure_strength`` consecutive HH+HL for a
        bull leg, or LH+LL for a bear leg), the engine reports
        ``NOT_IN_RANGE`` because directional structure dominates.

    Otherwise
        ``NOT_IN_RANGE`` when directional structure exists but no
        flat consolidation; ``UNKNOWN`` when insufficient confirmed
        swings are available.
    """

    def __init__(
        self,
        config: RangeDetectionConfig | None = None,
    ) -> None:
        self.config = config or RangeDetectionConfig()

    def detect(
        self,
        structures: list[StructurePoint],
        candle: OHLCVCandle,
    ) -> RangeContext:
        """
        Classify the range state at the evaluation point.

        ``structures`` are the confirmed structure points available at
        the evaluation point; ``candle`` is the candle at the
        evaluation point (used only for its close price and timestamp).
        """

        if len(structures) < self.config.min_swings:
            return RangeContext(
                state=RangeState.UNKNOWN,
                high=None,
                low=None,
                width=None,
                position=None,
                reason="Insufficient confirmed swings for range "
                "classification.",
            )

        highs = [
            s.swing.price
            for s in structures
            if s.swing.swing_type == SwingType.HIGH
        ]
        lows = [
            s.swing.price
            for s in structures
            if s.swing.swing_type == SwingType.LOW
        ]

        flat_highs = self._flat_group(highs)
        flat_lows = self._flat_group(lows)

        if flat_highs is not None and flat_lows is not None:
            resistance = sum(flat_highs) / len(flat_highs)
            support = sum(flat_lows) / len(flat_lows)
            width = resistance - support

            if width <= 0:
                return RangeContext(
                    state=RangeState.NOT_IN_RANGE,
                    high=None,
                    low=None,
                    width=None,
                    position=None,
                    reason="Range boundaries collapsed; no active "
                    "range.",
                )

            position = (candle.close - support) / width
            position = max(0.0, min(1.0, position))

            return RangeContext(
                state=RangeState.IN_RANGE,
                high=resistance,
                low=support,
                width=width,
                position=position,
                reason=(
                    f"Flat consolidation detected: {len(flat_highs)} "
                    f"resistance highs and {len(flat_lows)} support "
                    f"lows within {self.config.range_tolerance:.0%} "
                    f"tolerance."
                ),
            )

        directional = self._recent_directional_strength(structures)
        if directional:
            return RangeContext(
                state=RangeState.NOT_IN_RANGE,
                high=None,
                low=None,
                width=None,
                position=None,
                reason=(
                    "Recent confirmed structures show directional "
                    "dominance; no active range."
                ),
            )

        return RangeContext(
            state=RangeState.NOT_IN_RANGE,
            high=None,
            low=None,
            width=None,
            position=None,
            reason=(
                "No flat consolidation and no strong directional "
                "dominance among recent swings."
            ),
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _recent_directional_strength(
        self,
        structures: list[StructurePoint],
    ) -> bool:
        """
        True when the recent confirmed structures show strong
        directional dominance (more than the configured threshold of
        consecutive HH+HL or LH+LL).
        """

        limit = self.config.max_recent_structure_strength
        recent = structures[-(limit + 1) :] if len(structures) else []

        bull = (
            StructureType.HIGHER_HIGH,
            StructureType.HIGHER_LOW,
        )
        bear = (
            StructureType.LOWER_HIGH,
            StructureType.LOWER_LOW,
        )

        if len(recent) < 2:
            return False

        # Count consecutive same-direction structures from the end.
        last_is_bull = recent[-1].structure in bull
        last_is_bear = recent[-1].structure in bear

        if not (last_is_bull or last_is_bear):
            return False

        consecutive = 1
        for point in reversed(recent[:-1]):
            if last_is_bull and point.structure in bull:
                consecutive += 1
            elif last_is_bear and point.structure in bear:
                consecutive += 1
            else:
                break

        return consecutive > limit

    def _flat_group(
        self,
        prices: list[float],
    ) -> list[float] | None:
        """
        Return the most recent flat group of ``min_flat_count`` prices
        within ``range_tolerance`` of the group average, or ``None``.
        """

        if len(prices) < self.config.min_flat_count:
            return None

        # Inspect the most recent prices first so the active range
        # reflects the latest consolidation.
        window = prices[-self.config.min_flat_count :]
        avg = sum(window) / len(window)

        for price in window:
            if avg <= 0:
                return None
            if abs(price - avg) / avg > self.config.range_tolerance:
                return None

        return window
