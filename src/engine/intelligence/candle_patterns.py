"""
Candle / price-action pattern detection engine (Sprint 11O).

``CandlePatternEngine`` inspects OHLCV candles and produces
structured ``CandlePattern`` evidence. It is deterministic and
historical-safe: the pattern attributed to candle ``T`` depends
ONLY on candles ``[T-1, T]`` (two-candle patterns) or ``T``
(single-candle patterns). No pattern inspects future candles.

The engine makes NO claim that a detected pattern is profitable
or predictive. A pattern is a description of candle shape.

Pattern definitions (explicit and testable):

DOJI (single candle, direction NEUTRAL)
    ``body_to_range_ratio <= doji_max_body_ratio`` when the
    range is positive, OR a zero-range candle
    (``open == high == low == close``). The canonical "indecision"
    shape: a negligible body relative to the range.

HAMMER (single candle, direction BULLISH)
    A small-bodied candle whose lower wick dominates:
        body > 0
        AND lower_wick >= hammer_min_lower_wick_to_body * body
        AND upper_wick <= hammer_max_upper_wick_to_body * body
    The canonical bullish-reversal hammer shape (long lower
    wick, small upper wick). No prior-trend context is used.

SHOOTING_STAR (single candle, direction BEARISH)
    Mirror of the hammer:
        body > 0
        AND upper_wick >= shooting_star_min_upper_wick_to_body * body
        AND lower_wick <= shooting_star_max_lower_wick_to_body * body

BULLISH_ENGULFING (two candles, direction BULLISH)
    A bearish candle (prior) followed by a bullish candle
    (current) whose body engulfs the prior body:
        prior.direction == BEARISH
        AND current.direction == BULLISH
        AND current_open <= prior_close
        AND current_close >= prior_open
        AND (current body > prior body)  [strict, default]
    The engulfment is measured against the prior body, not the
    prior full range.

BEARISH_ENGULFING (two candles, direction BEARISH)
    Mirror of bullish engulfing:
        prior.direction == BULLISH
        AND current.direction == BEARISH
        AND current_open >= prior_close
        AND current_close <= prior_open
        AND (current body > prior body)  [strict, default]

INSIDE_BAR (two candles, direction NEUTRAL)
    The current candle's full range is contained within the
    prior candle's range:
        current.high <= prior.high
        AND current.low >= prior.low
        AND current range is strictly less than prior range
    No directional bias is attributed.

Deterministic shape-quality score:
    Each pattern carries a ``score`` in ``[0.0, 1.0]`` derived
    ONLY from the explicit measurements above:

    DOJI
        ``1.0 - body_to_range_ratio`` clamped to ``[0, 1]``;
        zero-range candle -> ``1.0``.
    HAMMER
        ``min(lower_wick / (min_ratio * body), 1.0)``: how far
        the lower-wick-to-body ratio exceeds the minimum.
    SHOOTING_STAR
        ``min(upper_wick / (min_ratio * body), 1.0)``.
    BULLISH/BEARISH_ENGULFING
        ``min(current_body / prior_body - 1.0, 1.0)``: how much
        the current body exceeds the prior body, capped at 1.0.
    INSIDE_BAR
        ``1.0 - current_range / prior_range`` when the prior
        range is positive: how much smaller the inside range is.

These formulas are descriptive; they are NOT probabilities.
"""

from __future__ import annotations

from engine.config.candle_pattern_config import CandlePatternConfig
from engine.models.candle_pattern import (
    CandleDirection,
    CandleMeasurements,
    CandlePattern,
    CandlePatternType,
)
from engine.models.ohlcv import OHLCVCandle


class CandlePatternEngine:
    """
    Detect candle / price-action patterns from OHLCV candles.

    Public API:

        detect(candles) -> list[CandlePattern]

    The engine is stateless across calls: identical inputs
    always produce identical outputs.
    """

    def __init__(
        self,
        config: CandlePatternConfig | None = None,
    ) -> None:
        self.config = config or CandlePatternConfig()

        if self.config.doji_max_body_ratio < 0:
            raise ValueError(
                "doji_max_body_ratio must be non-negative.",
            )
        if self.config.hammer_min_lower_wick_to_body < 0:
            raise ValueError(
                "hammer_min_lower_wick_to_body must be "
                "non-negative.",
            )
        if self.config.shooting_star_min_upper_wick_to_body < 0:
            raise ValueError(
                "shooting_star_min_upper_wick_to_body must be "
                "non-negative.",
            )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def detect(
        self,
        candles: list[OHLCVCandle],
    ) -> list[CandlePattern]:
        """
        Detect all supported patterns in a chronological candle
        sequence.

        Patterns are attributed to the index of the triggering
        (current) candle. Single-candle patterns are evaluated
        for every candle; two-candle patterns require at least
        one preceding candle.

        Historical safety: a pattern at index ``T`` uses only
        candles ``[T-1, T]``. No future candle is read.
        """

        patterns: list[CandlePattern] = []

        for index in range(len(candles)):
            patterns.extend(
                self._detect_at(candles, index)
            )

        return patterns

    # ========================================================
    # PER-INDEX DETECTION
    # ========================================================

    def _detect_at(
        self,
        candles: list[OHLCVCandle],
        index: int,
    ) -> list[CandlePattern]:
        """
        Detect every pattern whose trigger candle is ``index``.

        Uses only ``candles[index]`` (single-candle patterns) and
        ``candles[index-1]`` (two-candle patterns, when index > 0).
        """

        found: list[CandlePattern] = []

        current = candles[index]
        measurements = CandleMeasurements.from_candle(current)
        timestamp = getattr(current, "timestamp", None)

        # ---- single-candle patterns ----
        doji = self._detect_doji(index, timestamp, measurements)
        if doji is not None:
            found.append(doji)

        hammer = self._detect_hammer(
            index, timestamp, measurements,
        )
        if hammer is not None:
            found.append(hammer)

        shooting_star = self._detect_shooting_star(
            index, timestamp, measurements,
        )
        if shooting_star is not None:
            found.append(shooting_star)

        # ---- two-candle patterns ----
        if index > 0:
            prior = candles[index - 1]
            prior_measurements = CandleMeasurements.from_candle(
                prior,
            )

            bullish_engulfing = self._detect_bullish_engulfing(
                index,
                timestamp,
                current,
                measurements,
                prior,
                prior_measurements,
            )
            if bullish_engulfing is not None:
                found.append(bullish_engulfing)

            bearish_engulfing = self._detect_bearish_engulfing(
                index,
                timestamp,
                current,
                measurements,
                prior,
                prior_measurements,
            )
            if bearish_engulfing is not None:
                found.append(bearish_engulfing)

            inside_bar = self._detect_inside_bar(
                index,
                timestamp,
                current,
                measurements,
                prior,
                prior_measurements,
            )
            if inside_bar is not None:
                found.append(inside_bar)

        return found

    # ========================================================
    # SINGLE-CANDLE PATTERNS
    # ========================================================

    def _detect_doji(
        self,
        index: int,
        timestamp,
        m: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        DOJI: negligible body relative to range, OR zero-range
        candle.
        """

        max_ratio = self.config.doji_max_body_ratio

        if m.range == 0:
            # Degenerate candle (no price movement). Treated as a
            # DOJI: the most extreme "indecision" shape.
            ratio = 0.0
            matched = True
            score = 1.0
            reason = (
                "DOJI: zero-range candle (open==high==low==close)."
            )
        else:
            ratio = m.body_to_range_ratio
            matched = ratio <= max_ratio
            score = max(0.0, min(1.0, 1.0 - ratio))
            reason = (
                f"DOJI: body/range={ratio:.4f} <= "
                f"{max_ratio:.4f}."
            )

        if not matched:
            return None

        return CandlePattern(
            pattern_type=CandlePatternType.DOJI,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.NEUTRAL,
            measurements=m,
            score=round(score, 4),
            reason=reason,
        )

    def _detect_hammer(
        self,
        index: int,
        timestamp,
        m: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        HAMMER: long lower wick, small upper wick, body > 0.
        """

        if m.body <= 0:
            return None

        min_lower = self.config.hammer_min_lower_wick_to_body
        max_upper = self.config.hammer_max_upper_wick_to_body

        lower_ok = m.lower_wick >= min_lower * m.body
        upper_ok = m.upper_wick <= max_upper * m.body

        if not (lower_ok and upper_ok):
            return None

        if min_lower * m.body > 0:
            score = max(
                0.0,
                min(1.0, m.lower_wick / (min_lower * m.body)),
            )
        else:
            score = 1.0

        return CandlePattern(
            pattern_type=CandlePatternType.HAMMER,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.BULLISH,
            measurements=m,
            score=round(score, 4),
            reason=(
                f"HAMMER: lower_wick={m.lower_wick:.4f} >= "
                f"{min_lower * m.body:.4f}, "
                f"upper_wick={m.upper_wick:.4f} <= "
                f"{max_upper * m.body:.4f}."
            ),
        )

    def _detect_shooting_star(
        self,
        index: int,
        timestamp,
        m: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        SHOOTING_STAR: long upper wick, small lower wick, body>0.
        """

        if m.body <= 0:
            return None

        min_upper = self.config.shooting_star_min_upper_wick_to_body
        max_lower = self.config.shooting_star_max_lower_wick_to_body

        upper_ok = m.upper_wick >= min_upper * m.body
        lower_ok = m.lower_wick <= max_lower * m.body

        if not (upper_ok and lower_ok):
            return None

        if min_upper * m.body > 0:
            score = max(
                0.0,
                min(1.0, m.upper_wick / (min_upper * m.body)),
            )
        else:
            score = 1.0

        return CandlePattern(
            pattern_type=CandlePatternType.SHOOTING_STAR,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.BEARISH,
            measurements=m,
            score=round(score, 4),
            reason=(
                f"SHOOTING_STAR: upper_wick={m.upper_wick:.4f} "
                f">= {min_upper * m.body:.4f}, "
                f"lower_wick={m.lower_wick:.4f} <= "
                f"{max_lower * m.body:.4f}."
            ),
        )

    # ========================================================
    # TWO-CANDLE PATTERNS
    # ========================================================

    def _detect_bullish_engulfing(
        self,
        index: int,
        timestamp,
        current: OHLCVCandle,
        m: CandleMeasurements,
        prior: OHLCVCandle,
        prior_measurements: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        BULLISH_ENGULFING: bearish prior, bullish current whose
        body engulfs the prior body.
        """

        if prior_measurements.direction != CandleDirection.BEARISH:
            return None
        if m.direction != CandleDirection.BULLISH:
            return None

        engulfs = (
            current.open <= prior.close
            and current.close >= prior.open
        )

        if not engulfs:
            return None

        if self.config.engulfing_strict and not (m.body > prior_measurements.body):
            return None

        if prior_measurements.body > 0:
            score = max(
                0.0,
                min(1.0, m.body / prior_measurements.body - 1.0),
            )
        else:
            score = 1.0

        return CandlePattern(
            pattern_type=CandlePatternType.BULLISH_ENGULFING,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.BULLISH,
            measurements=m,
            score=round(score, 4),
            reason=(
                "BULLISH_ENGULFING: prior bearish body engulfed "
                "by current bullish body."
            ),
            prior_index=index - 1,
            prior_measurements=prior_measurements,
        )

    def _detect_bearish_engulfing(
        self,
        index: int,
        timestamp,
        current: OHLCVCandle,
        m: CandleMeasurements,
        prior: OHLCVCandle,
        prior_measurements: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        BEARISH_ENGULFING: bullish prior, bearish current whose
        body engulfs the prior body.
        """

        if prior_measurements.direction != CandleDirection.BULLISH:
            return None
        if m.direction != CandleDirection.BEARISH:
            return None

        engulfs = (
            current.open >= prior.close
            and current.close <= prior.open
        )

        if not engulfs:
            return None

        if self.config.engulfing_strict and not (m.body > prior_measurements.body):
            return None

        if prior_measurements.body > 0:
            score = max(
                0.0,
                min(1.0, m.body / prior_measurements.body - 1.0),
            )
        else:
            score = 1.0

        return CandlePattern(
            pattern_type=CandlePatternType.BEARISH_ENGULFING,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.BEARISH,
            measurements=m,
            score=round(score, 4),
            reason=(
                "BEARISH_ENGULFING: prior bullish body engulfed "
                "by current bearish body."
            ),
            prior_index=index - 1,
            prior_measurements=prior_measurements,
        )

    def _detect_inside_bar(
        self,
        index: int,
        timestamp,
        current: OHLCVCandle,
        m: CandleMeasurements,
        prior: OHLCVCandle,
        prior_measurements: CandleMeasurements,
    ) -> CandlePattern | None:
        """
        INSIDE_BAR: current full range contained within prior
        full range, and strictly smaller.
        """

        if prior_measurements.range <= 0:
            return None

        contained = (
            current.high <= prior.high
            and current.low >= prior.low
        )

        if not contained:
            return None

        if m.range >= prior_measurements.range:
            return None

        score = max(
            0.0,
            min(1.0, 1.0 - m.range / prior_measurements.range),
        )

        return CandlePattern(
            pattern_type=CandlePatternType.INSIDE_BAR,
            index=index,
            timestamp=timestamp,
            direction=CandleDirection.NEUTRAL,
            measurements=m,
            score=round(score, 4),
            reason=(
                "INSIDE_BAR: current range within prior range "
                "(current.high<=prior.high and "
                "current.low>=prior.low)."
            ),
            prior_index=index - 1,
            prior_measurements=prior_measurements,
        )
