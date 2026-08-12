"""
Domain models for market context & price structure intelligence
(Sprint 11P).

These models describe the *market context* knowable at an
evaluation point ``T``. They make NO claim about profitability or
directional prediction. A market context is a description of price
structure, not a trade.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Values are derived ONLY from candles up to and including ``T`` and
  from confirmed swings whose confirmation_index <= T. No future
  candle is ever read (the ``MarketContextEngine`` enforces this by
  construction).
* Optional fields use ``None`` (or explicit ``UNKNOWN`` members) so
  that "unobserved" is never silently reported as "zero / false".
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from engine.models.market_structure import StructurePoint
from engine.models.structure_analysis import StructureBias


class MarketTrendState(Enum):
    """
    Descriptive trend / context state derived from market structure.

    The classification prefers actual directional structure over
    simplistic "price went up" rules. RANGE is reported when a
    consolidation condition is detected; otherwise the structure bias
    is reported. UNKNOWN means insufficient confirmed structure.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class RangeState(Enum):
    """
    Descriptive consolidation / range classification.
    """

    IN_RANGE = "IN_RANGE"
    NOT_IN_RANGE = "NOT_IN_RANGE"
    UNKNOWN = "UNKNOWN"


class PriceLocation(Enum):
    """
    Current price's position relative to the nearest support and
    resistance context.
    """

    NEAR_SUPPORT = "NEAR_SUPPORT"
    NEAR_RESISTANCE = "NEAR_RESISTANCE"
    INSIDE_RANGE = "INSIDE_RANGE"
    ABOVE_RESISTANCE = "ABOVE_RESISTANCE"
    BELOW_SUPPORT = "BELOW_SUPPORT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SupportResistanceContext:
    """
    Support / resistance context relative to the current price.

    All levels are derived from confirmed swings whose confirmation
    was available at the evaluation point. ``distance_*`` is expressed
    as a fraction of the current price (signed so support distances
    are <= 0 and resistance distances are >= 0 when the price sits
    between them).

    Attributes:

    support / resistance
        Nearest confirmed support / resistance price to the current
        price, or ``None`` when no relevant level is known.

    distance_to_support / distance_to_resistance
        ``(level - price) / price`` relative distance. ``None`` when
        the corresponding level is absent.

    location
        Descriptive position of the current price relative to the
        nearest support/resistance context.
    """

    support: float | None
    resistance: float | None
    distance_to_support: float | None
    distance_to_resistance: float | None
    location: PriceLocation


@dataclass(frozen=True, slots=True)
class RangeContext:
    """
    Descriptive consolidation / range context.

    Attributes:

    state
        ``IN_RANGE`` when a consolidation is detected, ``NOT_IN_RANGE``
        when directional structure dominates, ``UNKNOWN`` when
        insufficient data is available.

    high / low
        Approximate range boundaries when ``state == IN_RANGE``;
        ``None`` otherwise.

    width
        ``high - low`` when in range; ``None`` otherwise.

    position
        Current price position within the range as a fraction in
        ``[0.0, 1.0]`` (``0`` at the low, ``1`` at the high).
        ``None`` when not in range or when the range has zero width.

    reason
        Human-readable description of how the classification was
        reached.
    """

    state: RangeState
    high: float | None
    low: float | None
    width: float | None
    position: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class MarketTrend:
    """
    Descriptive trend / context state.

    Attributes:

    state
        The descriptive trend state.

    bias
        Underlying structure bias (BULLISH / BEARISH / NEUTRAL /
        UNKNOWN) from which the trend was derived.

    structure_intact
        Whether the directional structure was judged intact at the
        evaluation point.

    reasons
        Human-readable reasons explaining the classification.
    """

    state: MarketTrendState
    bias: StructureBias
    structure_intact: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MarketContext:
    """
    Structured market context at an evaluation point ``T``.

    Bundles the descriptive intelligence produced by Sprint 11P.
    Every field is derived ONLY from candles up to and including
    ``T`` and from confirmed swings whose confirmation was available
    at ``T``. The context is DESCRIPTIVE: it does not constitute a
    trade signal and is NOT consumed by the existing confluence /
    decision / signal engines.

    Attributes:

    index
        Chronological index of the evaluation point.

    trend
        Descriptive trend / context state.

    range
        Descriptive consolidation / range context.

    support_resistance
        Support / resistance context relative to the current price.

    recent_structure
        Ordered (oldest -> newest) structure types of the most recent
        confirmed swings. Useful for inspecting *why* the trend was
        classified (e.g. ``[HH, HL, HH]``).

    confirmed_swings
        Number of confirmed swings available at the evaluation point.
    """

    index: int
    trend: MarketTrend
    range: RangeContext
    support_resistance: SupportResistanceContext
    recent_structure: tuple[StructurePoint, ...] = field(
        default_factory=tuple,
    )
    confirmed_swings: int = 0
