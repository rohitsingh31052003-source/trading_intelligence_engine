"""
Performance segmentation engine (Sprint 11H).

The ``PerformanceSegmentationEngine`` groups completed
``ValidationResult`` objects (paired with their generating
``SignalResult``) along a chosen dimension and reports the
same core performance metrics used by
``PerformanceAnalyticsEngine`` for each segment.

Supported dimensions:

* DIRECTION       (LONG / SHORT / NONE)
* SETUP_QUALITY   (WEAK / MODERATE / STRONG / EXCELLENT / INVALID)
* CONFIDENCE      (LOW / MEDIUM / HIGH / VERY_HIGH)
* RISK_REWARD     (LOW_RR / MEDIUM_RR / HIGH_RR)
* REGIME          (market regime at the evaluation point)

Design rules:

* No duplication of analytics logic.
  Per-segment metrics are produced by delegating each segment's
  validation results to the existing
  ``PerformanceAnalyticsEngine``.

* Deterministic.
  Bucket assignment is purely a function of the signal's
  numeric fields and the (mutable, configurable) thresholds.

* No hard-coded profitability claims.
  Buckets describe characteristics only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite, isinf
from typing import Any, Iterable, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.research import (
    ConfidenceBucket,
    MarketRegime,
    RiskRewardBucket,
    SegmentStatistics,
    SegmentedPerformance,
    SegmentationDimension,
)
from engine.models.signal import SignalDirection, SignalResult
from engine.models.validation import ValidationResult
from engine.research.regime import MarketRegimeEngine


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class SegmentationConfig:
    """
    Mutable configuration for ``PerformanceSegmentationEngine``.

    Confidence and risk/reward bucket thresholds are
    configurable so research tooling can tune granularity.
    """

    # Confidence buckets (inclusive lower bound).
    confidence_low: float = 0.30
    confidence_medium: float = 0.50
    confidence_high: float = 0.70
    # >= confidence_very_high -> VERY_HIGH
    confidence_very_high: float = 0.85

    # Risk/reward buckets (inclusive lower bound).
    rr_low: float = 1.0
    rr_medium: float = 1.5
    rr_high: float = 2.5

    # Regime engine (used only for the REGIME dimension).
    regime_engine: MarketRegimeEngine = field(
        default_factory=MarketRegimeEngine,
    )


# ============================================================
# ENGINE
# ============================================================


class PerformanceSegmentationEngine:
    """
    Segment validation results along a chosen dimension.

    Public API:

        segment(
            pairs,
            dimension,
            candles=None,
        ) -> SegmentedPerformance

    where ``pairs`` is an iterable of ``(SignalResult,
    ValidationResult)`` tuples. ``candles`` is the full
    chronological candle sequence and is required for the
    REGIME dimension (the regime engine needs the walk-forward
    slice at each evaluation index).
    """

    def __init__(
        self,
        config: SegmentationConfig | None = None,
    ) -> None:
        self.config = config or SegmentationConfig()
        self._performance_engine = PerformanceAnalyticsEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def segment(
        self,
        pairs: Iterable[tuple[SignalResult, ValidationResult]],
        dimension: SegmentationDimension,
        candles: Sequence[OHLCVCandle] | None = None,
        evaluation_indices: Sequence[int] | None = None,
    ) -> SegmentedPerformance:
        """
        Build a ``SegmentedPerformance`` for the given dimension.

        Parameters:

        pairs
            ``(SignalResult, ValidationResult)`` tuples in
            chronological order.

        dimension
            The segmentation dimension.

        candles
            Full chronological candle sequence. Required for the
            REGIME dimension so the regime engine can classify
            the visible slice at each evaluation index.

        evaluation_indices
            The historical index T at which each pair's signal
            was generated, in the same order as ``pairs``.
            Required for the REGIME dimension.
        """

        pair_list = list(pairs)

        if dimension == SegmentationDimension.REGIME:
            labels = self._regime_labels(
                pair_list,
                candles,
                evaluation_indices,
            )
        else:
            labels = [
                self._label_for(pair_signal, dimension)
                for pair_signal, _ in pair_list
            ]

        groups: dict[str, list[ValidationResult]] = {}

        for (_signal, validation), label in zip(pair_list, labels):
            groups.setdefault(label, []).append(validation)

        segments = tuple(
            self._segment_statistics(dimension, label, results)
            for label, results in sorted(groups.items())
        )

        return SegmentedPerformance(
            dimension=dimension,
            segments=segments,
        )

    # ========================================================
    # LABEL ASSIGNMENT
    # ========================================================

    def _label_for(
        self,
        signal: SignalResult,
        dimension: SegmentationDimension,
    ) -> str:
        if dimension == SegmentationDimension.DIRECTION:
            return self._direction_label(signal)
        if dimension == SegmentationDimension.SETUP_QUALITY:
            return signal.quality.name
        if dimension == SegmentationDimension.CONFIDENCE:
            return self._confidence_bucket(signal).name
        if dimension == SegmentationDimension.RISK_REWARD:
            return self._risk_reward_bucket(signal).name

        return "UNKNOWN"

    @staticmethod
    def _direction_label(signal: SignalResult) -> str:
        direction = signal.direction
        if direction == SignalDirection.LONG:
            return "LONG"
        if direction == SignalDirection.SHORT:
            return "SHORT"
        return "NONE"

    def _confidence_bucket(
        self,
        signal: SignalResult,
    ) -> ConfidenceBucket:
        confidence = self._finite_or_zero(signal.confidence)
        cfg = self.config

        if confidence >= cfg.confidence_very_high:
            return ConfidenceBucket.VERY_HIGH
        if confidence >= cfg.confidence_high:
            return ConfidenceBucket.HIGH
        if confidence >= cfg.confidence_medium:
            return ConfidenceBucket.MEDIUM
        if confidence >= cfg.confidence_low:
            return ConfidenceBucket.LOW
        return ConfidenceBucket.LOW

    def _risk_reward_bucket(
        self,
        signal: SignalResult,
    ) -> RiskRewardBucket:
        rr = self._finite_or_zero(signal.risk_reward_ratio)
        cfg = self.config

        if rr >= cfg.rr_high:
            return RiskRewardBucket.HIGH_RR
        if rr >= cfg.rr_medium:
            return RiskRewardBucket.MEDIUM_RR
        return RiskRewardBucket.LOW_RR

    # ========================================================
    # REGIME LABELS
    # ========================================================

    def _regime_labels(
        self,
        pairs: list[tuple[SignalResult, ValidationResult]],
        candles: Sequence[OHLCVCandle] | None,
        evaluation_indices: Sequence[int] | None,
    ) -> list[str]:
        """
        Assign a regime label to each pair.

        The regime is classified from the walk-forward slice
        ``candles[:T+1]`` at the evaluation index T, so no
        future candle is ever read.
        """

        if candles is None:
            return [MarketRegime.UNKNOWN.name] * len(pairs)

        candle_list = list(candles)
        indices = (
            list(evaluation_indices)
            if evaluation_indices is not None
            else [0] * len(pairs)
        )

        labels: list[str] = []

        for i, idx in enumerate(indices):
            if idx < 0 or idx >= len(candle_list):
                labels.append(MarketRegime.UNKNOWN.name)
                continue

            visible = candle_list[: idx + 1]
            regime = self.config.regime_engine.classify(visible)
            labels.append(regime.name)

            # Guard against index/pair length mismatch.
            if i >= len(pairs) - 1:
                break

        # Pad / trim to pair count.
        while len(labels) < len(pairs):
            labels.append(MarketRegime.UNKNOWN.name)

        return labels[: len(pairs)]

    # ========================================================
    # SEGMENT STATISTICS
    # ========================================================

    def _segment_statistics(
        self,
        dimension: SegmentationDimension,
        label: str,
        results: list[ValidationResult],
    ) -> SegmentStatistics:
        performance = self._performance_engine.analyze(results)

        return SegmentStatistics(
            dimension=dimension,
            segment_label=label,
            total_results=performance.total_results,
            completed_trades=performance.completed_trades,
            wins=performance.wins,
            losses=performance.losses,
            win_rate=performance.win_rate,
            total_r=performance.total_r,
            average_r=performance.average_r,
            expectancy=performance.expectancy,
            profit_factor=performance.profit_factor,
            max_drawdown=performance.max_drawdown_r,
        )

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _finite_or_zero(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0

        if isinf(numeric) or not isfinite(numeric):
            return 0.0

        return numeric
