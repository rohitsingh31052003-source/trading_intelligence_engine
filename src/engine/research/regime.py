"""
Market regime detection engine (Sprint 11H).

The ``MarketRegimeEngine`` classifies the market condition at a
single historical evaluation point using ONLY the candles
available up to and including that point. It never reads
future candles.

Design rules:

* No look-ahead.
  The engine receives a slice ``candles[:T+1]`` (the visible
  history at point T) and classifies the regime from that slice
  alone.

* Deterministic and intentionally simple.
  This is NOT a sophisticated statistical regime classifier.
  It uses directional movement and realized volatility against
  configurable thresholds. The objective is a reliable,
  transparent research segmentation layer that can later be
  improved, not an optimised predictor.

* Conservative.
  When there is insufficient information the engine returns
  ``UNKNOWN`` rather than guessing.

Classification logic (evaluated in order):

1. Insufficient history -> UNKNOWN.
2. Realized volatility above the high threshold -> HIGH_VOLATILITY.
3. Realized volatility below the low threshold -> LOW_VOLATILITY.
4. Directional movement dominant -> TRENDING.
5. Directional movement weak -> FLAT.
6. Otherwise -> UNKNOWN.

Volatility is measured as the average true range (high-low
range) of the recent window, normalised by the mean close, so
it is scale-independent. Directional movement is measured as
the net price change over the window relative to the total
absolute path length (a simplified directional efficiency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.models.ohlcv import OHLCVCandle
from engine.models.research import MarketRegime


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class RegimeConfig:
    """
    Mutable configuration for ``MarketRegimeEngine``.

    All thresholds are documented and have sensible defaults.
    The engine is conservative: it prefers ``UNKNOWN`` over a
    guess when evidence is weak.

    Field semantics:

    window
        Number of most-recent visible candles used to measure
        volatility and directional movement.

    min_history
        Minimum visible candles required before any non-UNKNOWN
        classification is returned.

    high_volatility_threshold
        Normalised average true range above which the regime is
        HIGH_VOLATILITY.

    low_volatility_threshold
        Normalised average true range below which the regime is
        LOW_VOLATILITY.

    trending_efficiency_threshold
        Minimum directional efficiency (net move / total path)
        for the regime to be TRENDING.

    flat_efficiency_threshold
        Maximum directional efficiency below which the regime
        is FLAT.
    """

    window: int = 20
    min_history: int = 10

    high_volatility_threshold: float = 0.02
    low_volatility_threshold: float = 0.005

    trending_efficiency_threshold: float = 0.6
    flat_efficiency_threshold: float = 0.2


# ============================================================
# ENGINE
# ============================================================


class MarketRegimeEngine:
    """
    Classify the market regime at a single evaluation point.

    Public API:

        classify(candles) -> MarketRegime

    The engine is stateless: identical inputs always produce
    identical outputs.
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def classify(
        self,
        candles: Sequence[OHLCVCandle],
    ) -> MarketRegime:
        """
        Classify the regime using only the supplied candles.

        The caller is responsible for passing the correct
        walk-forward slice ``candles[:T+1]``. The engine never
        reaches beyond the supplied sequence.
        """

        history = list(candles)
        total = len(history)

        if total < self.config.min_history:
            return MarketRegime.UNKNOWN

        window = history[-self.config.window :] if total >= self.config.window else history

        normalized_volatility = self._normalized_volatility(window)
        efficiency = self._directional_efficiency(window)

        # Volatility gates are evaluated first: an extremely
        # volatile or extremely quiet market is labelled by its
        # volatility character regardless of direction.
        if normalized_volatility >= self.config.high_volatility_threshold:
            return MarketRegime.HIGH_VOLATILITY

        if normalized_volatility <= self.config.low_volatility_threshold:
            return MarketRegime.LOW_VOLATILITY

        # Directional classification.
        if efficiency >= self.config.trending_efficiency_threshold:
            return MarketRegime.TRENDING

        if efficiency <= self.config.flat_efficiency_threshold:
            return MarketRegime.FLAT

        return MarketRegime.UNKNOWN

    # ========================================================
    # MEASUREMENTS
    # ========================================================

    @staticmethod
    def _normalized_volatility(
        window: list[OHLCVCandle],
    ) -> float:
        """
        Average true range (high-low range) divided by the mean
        close, so the measure is scale-independent.

        Returns 0.0 for an empty or single-candle window.
        """

        if len(window) < 2:
            return 0.0

        ranges = [c.high - c.low for c in window]
        mean_close = sum(c.close for c in window) / len(window)

        if mean_close <= 0.0:
            return 0.0

        avg_range = sum(ranges) / len(ranges)

        return avg_range / mean_close

    @staticmethod
    def _directional_efficiency(
        window: list[OHLCVCandle],
    ) -> float:
        """
        Simplified directional efficiency.

        efficiency = abs(net_move) / total_path

        where ``net_move`` is |close_last - close_first| and
        ``total_path`` is the sum of absolute close-to-close
        changes. A perfectly monotonic move scores 1.0; a flat
        oscillating market scores near 0.0.

        Returns 0.0 for an empty or single-candle window.
        """

        if len(window) < 2:
            return 0.0

        closes = [c.close for c in window]

        net_move = abs(closes[-1] - closes[0])
        path = sum(
            abs(closes[i] - closes[i - 1])
            for i in range(1, len(closes))
        )

        if path <= 0.0:
            return 0.0

        return net_move / path
