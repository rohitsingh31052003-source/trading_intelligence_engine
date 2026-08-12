"""
Configuration for the candle / price-action pattern engine
(Sprint 11O).

All thresholds live here; no magic numbers are embedded in the
detection logic. The defaults are deliberately simple and
deterministic. They are NOT calibrated to any market; they
express canonical candlestick-shape definitions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class CandlePatternConfig:
    """
    Configuration for ``CandlePatternEngine``.

    Threshold semantics (all documented in the engine):

    doji_max_body_ratio
        A candle is a DOJI when its body is at most this fraction
        of its high-low range. Default ``0.1`` (body <= 10% of
        range). A zero-range candle is also classified as DOJI.

    hammer_min_lower_wick_to_body
        Minimum ratio of the lower wick to the body for a HAMMER.
        Default ``2.0`` (lower wick at least twice the body).

    hammer_max_upper_wick_to_body
        Maximum ratio of the upper wick to the body for a HAMMER.
        Default ``1.0`` (upper wick no larger than the body).

    shooting_star_min_upper_wick_to_body
        Minimum ratio of the upper wick to the body for a
        SHOOTING_STAR. Default ``2.0``.

    shooting_star_max_lower_wick_to_body
        Maximum ratio of the lower wick to the body for a
        SHOOTING_STAR. Default ``1.0``.

    engulfing_strict
        When True (default) an engulfing pattern requires the
        current body to be STRICTLY larger than the prior body
        AND to fully engulf it. When False, equal-size bodies
        are accepted. Strict is the canonical definition.
    """

    doji_max_body_ratio: float = 0.1

    hammer_min_lower_wick_to_body: float = 2.0
    hammer_max_upper_wick_to_body: float = 1.0

    shooting_star_min_upper_wick_to_body: float = 2.0
    shooting_star_max_lower_wick_to_body: float = 1.0

    engulfing_strict: bool = True
