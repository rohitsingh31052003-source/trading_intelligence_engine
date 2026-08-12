"""
Domain models for candle / price-action pattern intelligence
(Sprint 11O).

These models capture deterministic, single- and two-candle price
action as structured evidence. They make NO claim about the
profitability or predictive value of any pattern. A detected
pattern is a description of candle shape, nothing more.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model
  layer).
* Every numeric field is derived from explicit, documented
  candle measurements — never from a learned/ML score.
* ``index`` is the chronological position of the *triggering*
  candle (the current candle). Two-candle patterns also record
  ``prior_index`` (the immediately preceding candle) but the
  pattern is *attributed* to the current candle so the
  walk-forward invariant "pattern(T) depends only on
  candles[:T+1]" holds by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.ohlcv import OHLCVCandle


class CandlePatternType(Enum):
    """
    Catalog of supported candle patterns.

    Each value names a single, explicitly-defined pattern. No
    pattern implies a profitable trade.
    """

    DOJI = "DOJI"
    HAMMER = "HAMMER"
    SHOOTING_STAR = "SHOOTING_STAR"
    BULLISH_ENGULFING = "BULLISH_ENGULFING"
    BEARISH_ENGULFING = "BEARISH_ENGULFING"
    INSIDE_BAR = "INSIDE_BAR"


class CandleDirection(Enum):
    """
    Directional bias attributed to a pattern.

    NEUTRAL is used for patterns that carry no inherent
    directional information (e.g. the inside bar).
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class CandleMeasurements:
    """
    Deterministic measurements of a single OHLCV candle.

    All values are derived from the candle's open/high/low/close
    only. A zero-range candle (open == high == low == close) is
    handled safely: ``range`` and ``body`` are both ``0.0`` and
    the wicks are ``0.0``; ``body_to_range_ratio`` is ``0.0``
    (no body relative to no range).

    The ``direction`` of a zero-body candle is ``NEUTRAL``.
    """

    range: float
    body: float
    upper_wick: float
    lower_wick: float
    body_to_range_ratio: float
    direction: CandleDirection

    @classmethod
    def from_candle(
        cls,
        candle: OHLCVCandle,
    ) -> CandleMeasurements:
        """
        Compute measurements from a single candle.

        ``upper_wick`` is the distance from the high to the top
        of the body; ``lower_wick`` is the distance from the
        bottom of the body to the low.
        """

        candle_range = candle.high - candle.low
        body = candle.body_size

        body_high = max(candle.open, candle.close)
        body_low = min(candle.open, candle.close)

        upper_wick = candle.high - body_high
        lower_wick = body_low - candle.low

        if candle_range > 0:
            body_to_range_ratio = body / candle_range
        else:
            body_to_range_ratio = 0.0

        if candle.is_bullish:
            direction = CandleDirection.BULLISH
        elif candle.is_bearish:
            direction = CandleDirection.BEARISH
        else:
            direction = CandleDirection.NEUTRAL

        return cls(
            range=candle_range,
            body=body,
            upper_wick=upper_wick,
            lower_wick=lower_wick,
            body_to_range_ratio=body_to_range_ratio,
            direction=direction,
        )


@dataclass(frozen=True, slots=True)
class CandlePattern:
    """
    One detected candle pattern.

    Attributes:

    pattern_type
        The matched pattern from the catalog.

    index
        Chronological index of the triggering (current) candle.

    timestamp
        Timestamp of the triggering candle, when available.

    direction
        Directional bias attributed to the pattern.

    measurements
        Measurements of the triggering candle.

    score
        Deterministic shape-quality score in ``[0.0, 1.0]``.
        Derived ONLY from explicit candle measurements (see the
        engine for the per-pattern formula). It is NOT a
        confidence or probability and carries no predictive
        meaning.

    reason
        Human-readable description of the exact condition that
        matched, for auditability.

    prior_index / prior_measurements
        For two-candle patterns: the index and measurements of
        the immediately preceding candle. ``None`` for
        single-candle patterns.

    confirmed
        Always ``False`` for the base detector. Confirmation
        requires future candles, which the base detector NEVER
        inspects. The field exists so downstream layers have a
        stable place to express confirmation status later
        without reshaping the model.
    """

    pattern_type: CandlePatternType
    index: int
    timestamp: datetime | None
    direction: CandleDirection
    measurements: CandleMeasurements
    score: float
    reason: str
    prior_index: int | None = None
    prior_measurements: CandleMeasurements | None = None
    confirmed: bool = False
