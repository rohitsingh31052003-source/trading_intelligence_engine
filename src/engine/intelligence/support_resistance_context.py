"""
Support / resistance context engine (Sprint 11P).

``SupportResistanceContextEngine`` builds the *context* of the
current price relative to the structural support and resistance levels
derived from confirmed swings. It is deterministic and
historical-safe: it operates only on the structure points it is given,
which are themselves derived from confirmed swings whose confirmation
was available at the evaluation point.

This is deliberately NOT a second structural-level lifecycle engine.
The existing ``StructuralLevelsEngine`` (Sprint layer) already detects
rich support/resistance levels with lifecycle metadata. This engine
adds the *context* a trade-setup layer will eventually need: nearest
support, nearest resistance, signed relative distances and a
descriptive price location.

No trade signal is produced. A "near support" classification is a
description of price position, not a buy recommendation.
"""

from __future__ import annotations

from engine.config.market_context_config import (
    SupportResistanceContextConfig,
)
from engine.models.market_context import (
    PriceLocation,
    SupportResistanceContext,
)
from engine.models.market_structure import StructurePoint
from engine.models.ohlcv import OHLCVCandle
from engine.models.swing import SwingType


class SupportResistanceContextEngine:
    """
    Build support / resistance context relative to the current price.

    Levels are taken from the most recent confirmed swing highs
    (resistance) and swing lows (support), capped at ``max_levels``.

    The nearest support is the highest confirmed swing low below (or
    equal to) the current price; the nearest resistance is the lowest
    confirmed swing high above (or equal to) the current price. When
    no qualifying level exists on a side, that side is ``None``.
    """

    def __init__(
        self,
        config: SupportResistanceContextConfig | None = None,
    ) -> None:
        self.config = config or SupportResistanceContextConfig()

    def analyze(
        self,
        structures: list[StructurePoint],
        candle: OHLCVCandle,
    ) -> SupportResistanceContext:
        """
        Build the support / resistance context at the evaluation point.
        """

        price = candle.close

        resistance_levels = [
            s.swing.price
            for s in structures
            if s.swing.swing_type == SwingType.HIGH
        ][-self.config.max_levels :]

        support_levels = [
            s.swing.price
            for s in structures
            if s.swing.swing_type == SwingType.LOW
        ][-self.config.max_levels :]

        # Nearest support: the swing low closest to the current price
        # by absolute distance. Nearest resistance: the swing high
        # closest to the current price. This lets the location classify
        # BELOW_SUPPORT / ABOVE_RESISTANCE even when the price has moved
        # beyond every known level on a side.
        support = self._nearest_by_distance(support_levels, price)
        resistance = self._nearest_by_distance(resistance_levels, price)

        distance_to_support = (
            (support - price) / price if support is not None else None
        )
        distance_to_resistance = (
            (resistance - price) / price
            if resistance is not None
            else None
        )

        location = self._location(
            price,
            support,
            resistance,
        )

        return SupportResistanceContext(
            support=support,
            resistance=resistance,
            distance_to_support=distance_to_support,
            distance_to_resistance=distance_to_resistance,
            location=location,
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _nearest_by_distance(
        levels: list[float],
        price: float,
    ) -> float | None:
        """
        Level closest to ``price`` by absolute distance. Returns
        ``None`` when no level exists. Ties are broken toward the
        smaller level (deterministic).
        """

        if not levels:
            return None
        return min(levels, key=lambda lvl: (abs(lvl - price), lvl))

    def _location(
        self,
        price: float,
        support: float | None,
        resistance: float | None,
    ) -> PriceLocation:
        """
        Descriptive position of the current price relative to the
        nearest support and resistance.
        """

        if support is None and resistance is None:
            return PriceLocation.UNKNOWN

        prox = self.config.proximity_threshold

        if support is not None and resistance is not None:
            if price < support:
                return PriceLocation.BELOW_SUPPORT
            if price > resistance:
                return PriceLocation.ABOVE_RESISTANCE

            near_support = (
                abs(price - support) / support <= prox
                if support > 0
                else False
            )
            near_resistance = (
                abs(resistance - price) / resistance <= prox
                if resistance > 0
                else False
            )

            if near_support:
                return PriceLocation.NEAR_SUPPORT
            if near_resistance:
                return PriceLocation.NEAR_RESISTANCE
            return PriceLocation.INSIDE_RANGE

        if support is not None:
            if price < support:
                return PriceLocation.BELOW_SUPPORT
            if support > 0 and abs(price - support) / support <= prox:
                return PriceLocation.NEAR_SUPPORT
            return PriceLocation.INSIDE_RANGE

        # resistance is not None, support is None
        if price > resistance:
            return PriceLocation.ABOVE_RESISTANCE
        if resistance > 0 and abs(resistance - price) / resistance <= prox:
            return PriceLocation.NEAR_RESISTANCE
        return PriceLocation.INSIDE_RANGE
