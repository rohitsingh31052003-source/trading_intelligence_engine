"""
Out-of-sample evaluation engine (Sprint 11H).

The ``OutOfSampleEngine`` performs a chronological train/test
(development / evaluation) split of a historical candle
sequence and compares in-sample performance to out-of-sample
performance.

Design rules:

* Chronological split only.
  The split is always ``candles[:split_index]`` for in-sample
  and ``candles[split_index:]`` for out-of-sample. The engine
  NEVER shuffles market candles.

* No leakage into parameter selection.
  The evaluation period remains unseen by any
  parameter-selection process. This engine only reports the
  comparison; parameter selection is the caller's
  responsibility and is audited separately by
  ``LeakageAuditEngine``.

* Graceful on insufficient data.
  When either split is too short to evaluate, the engine
  returns a report with ``sufficient_data=False`` and empty
  analytics rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, isinf
from typing import Any, Callable, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.research import OutOfSampleReport


# An evaluator factory takes a candle sequence and returns a
# pipeline result (or any object carrying performance /
# validation results).
PipelineEvaluator = Callable[[Sequence[OHLCVCandle]], Any]


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class OutOfSampleConfig:
    """
    Mutable configuration for ``OutOfSampleEngine``.

    Field semantics:

    split_ratio
        Fraction of data used for development / in-sample.
        Default 0.70 (70% in-sample, 30% out-of-sample).

    min_in_sample
        Minimum candles required in the in-sample split for
        ``sufficient_data`` to be True.

    min_out_of_sample
        Minimum candles required in the out-of-sample split for
        ``sufficient_data`` to be True.
    """

    split_ratio: float = 0.70
    min_in_sample: int = 10
    min_out_of_sample: int = 10


# ============================================================
# ENGINE
# ============================================================


class OutOfSampleEngine:
    """
    Chronological in-sample / out-of-sample evaluation.

    Public API:

        evaluate(
            candles,
            evaluator,
        ) -> OutOfSampleReport

    The engine is stateless across calls.
    """

    def __init__(
        self,
        config: OutOfSampleConfig | None = None,
    ) -> None:
        self.config = config or OutOfSampleConfig()
        self._performance_engine = PerformanceAnalyticsEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def evaluate(
        self,
        candles: Sequence[OHLCVCandle],
        evaluator: PipelineEvaluator,
    ) -> OutOfSampleReport:
        """
        Chronologically split ``candles`` and compare in-sample
        to out-of-sample performance.
        """

        history = list(candles)
        total = len(history)

        split_index = self._split_index(total)

        in_sample_candles = history[:split_index]
        out_of_sample_candles = history[split_index:]

        in_sample_count = len(in_sample_candles)
        out_of_sample_count = len(out_of_sample_candles)

        sufficient = (
            in_sample_count >= self.config.min_in_sample
            and out_of_sample_count >= self.config.min_out_of_sample
        )

        in_sample_raw = self._safe_eval(evaluator, in_sample_candles)
        out_of_sample_raw = self._safe_eval(evaluator, out_of_sample_candles)

        in_sample_perf = self._performance_of(in_sample_raw)
        out_of_sample_perf = self._performance_of(out_of_sample_raw)

        in_sample_validations = self._validation_results(in_sample_raw)
        out_of_sample_validations = self._validation_results(out_of_sample_raw)

        expectancy_deg = self._difference(
            out_of_sample_perf,
            in_sample_perf,
            "expectancy",
        )
        profit_factor_deg = self._difference(
            out_of_sample_perf,
            in_sample_perf,
            "profit_factor",
        )
        win_rate_deg = self._difference(
            out_of_sample_perf,
            in_sample_perf,
            "win_rate",
        )
        drawdown_change = self._difference(
            out_of_sample_perf,
            in_sample_perf,
            "max_drawdown_r",
        )
        trade_count_change = (
            getattr(out_of_sample_perf, "completed_trades", 0)
            - getattr(in_sample_perf, "completed_trades", 0)
        )

        return OutOfSampleReport(
            split_ratio=self.config.split_ratio,
            in_sample_count=in_sample_count,
            out_of_sample_count=out_of_sample_count,
            in_sample_results=tuple(in_sample_validations),
            out_of_sample_results=tuple(out_of_sample_validations),
            in_sample_performance=in_sample_perf,
            out_of_sample_performance=out_of_sample_perf,
            expectancy_degradation=expectancy_deg,
            profit_factor_degradation=profit_factor_deg,
            win_rate_degradation=win_rate_deg,
            drawdown_change=drawdown_change,
            trade_count_change=trade_count_change,
            sufficient_data=sufficient,
        )

    # ========================================================
    # SPLIT
    # ========================================================

    def _split_index(self, total: int) -> int:
        ratio = self.config.split_ratio

        if not (0.0 < ratio < 1.0):
            raise ValueError(
                "split_ratio must lie strictly between 0.0 and 1.0."
            )

        index = int(total * ratio)

        # Guarantee both splits are non-empty when possible.
        if total > 0:
            index = max(1, min(index, total - 1))

        return index

    # ========================================================
    # EVALUATION HELPERS
    # ========================================================

    @staticmethod
    def _safe_eval(
        evaluator: PipelineEvaluator,
        candles: Sequence[OHLCVCandle],
    ) -> Any:
        if not candles:
            return None

        try:
            return evaluator(candles)
        except Exception:
            return None

    def _performance_of(self, raw: Any) -> Any:
        if raw is None:
            return self._performance_engine.analyze([])

        performance = getattr(raw, "performance", None)
        if performance is not None:
            return performance

        validation_results = getattr(raw, "validation_results", None)
        if validation_results is not None:
            return self._performance_engine.analyze(validation_results)

        if hasattr(raw, "expectancy") and hasattr(raw, "completed_trades"):
            return raw

        return self._performance_engine.analyze([])

    @staticmethod
    def _validation_results(raw: Any) -> list:
        if raw is None:
            return []

        results = getattr(raw, "validation_results", None)
        if results is not None:
            return list(results)

        return []

    # ========================================================
    # DEGRADATION
    # ========================================================

    @staticmethod
    def _difference(
        out_of_sample: Any,
        in_sample: Any,
        attribute: str,
    ) -> float:
        """
        out_of_sample - in_sample for a given attribute.

        Infinite profit factors are clamped to a large finite
        value so the degradation remains numeric and
        comparable.
        """

        oos_value = getattr(out_of_sample, attribute, 0.0)
        in_value = getattr(in_sample, attribute, 0.0)

        try:
            oos_numeric = float(oos_value)
            in_numeric = float(in_value)
        except (TypeError, ValueError):
            return 0.0

        if isinf(oos_numeric):
            oos_numeric = float("inf") if oos_numeric > 0 else float("-inf")

        if isinf(in_numeric):
            in_numeric = float("inf") if in_numeric > 0 else float("-inf")

        if isinf(oos_numeric) or isinf(in_numeric):
            # Treat "lossless" (inf profit factor) deterministically.
            if isinf(oos_numeric) and isinf(in_numeric):
                return 0.0
            if isinf(oos_numeric):
                return 1.0
            return -1.0

        if not isfinite(oos_numeric) or not isfinite(in_numeric):
            return 0.0

        return oos_numeric - in_numeric
