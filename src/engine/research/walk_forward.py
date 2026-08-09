"""
Walk-forward parameter selection engine (Sprint 11I).

The ``WalkForwardParameterEngine`` makes the development /
evaluation separation for parameter selection EXPLICIT and
auditable:

    historical data
          |
          +-- development window
          |       candidate evaluation (parameter sweep)
          |       parameter selection (descriptive, dev-only)
          |
          +-- evaluation window  (out-of-sample)
                  evaluation of the SELECTED configuration only

Design rules:

* Development / evaluation separation is structural.
  The engine physically splits the candle sequence into two
  non-overlapping windows. Candidate configurations are
  evaluated on the development window ONLY. The selected
  configuration is then evaluated on the evaluation window
  ONLY. The evaluation window is never supplied to candidate
  evaluation.

* No automatic overfitting claim.
  Selection is descriptive: the configuration with the highest
  DEVELOPMENT expectancy is selected. The engine never claims
  the selected configuration is predictive; it reports exactly
  what was selected and on what basis.

* Selection is isolated by construction.
  Because the engine performs the selection itself, it can
  PROVE isolation. ``selection_isolated_from_evaluation`` and
  ``selection_verified`` are therefore True by construction.

* Graceful on insufficient data.
  When the development window has too few candles / trades to
  select meaningfully, the engine still returns a report with
  ``sufficient_development_data=False`` rather than raising.

This engine does NOT duplicate trading logic: the caller
supplies the ``evaluator`` that runs the pipeline for a given
(candles, parameter_value) pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, isinf
from typing import Any, Callable, Sequence

from engine.intelligence.performance import (
    PerformanceAnalyticsEngine,
)
from engine.models.ohlcv import OHLCVCandle
from engine.models.research import (
    CandidateResult,
    SelectedConfiguration,
    WalkForwardSelectionReport,
)


# A walk-forward evaluator takes a candle sequence AND a
# parameter value and returns a pipeline result (or any object
# carrying performance / validation results). The engine calls
# it separately for the development window (candidate sweep) and
# the evaluation window (selected configuration only).
WalkForwardEvaluator = Callable[
    [Sequence[OHLCVCandle], Any], Any
]


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class WalkForwardConfig:
    """
    Mutable configuration for ``WalkForwardParameterEngine``.

    Field semantics:

    split_ratio
        Fraction of data used for the development window.
        Default 0.70 (70% development, 30% evaluation). The
        split is always chronological.

    min_development_candles
        Minimum candles in the development window for
        ``sufficient_development_window`` to be True.

    min_evaluation_candles
        Minimum candles in the evaluation window for
        ``sufficient_evaluation_window`` to be True.

    min_development_trades
        Minimum completed trades a candidate must have on the
        development window to be considered "sufficient" for
        selection. The descriptive best is still selected when
        no candidate meets this threshold, but
        ``sufficient_development_window`` becomes False.

    selection_metric
        Descriptive metric used for selection. Currently only
        ``"expectancy"`` is supported.
    """

    split_ratio: float = 0.70
    min_development_candles: int = 10
    min_evaluation_candles: int = 10
    min_development_trades: int = 1
    selection_metric: str = "expectancy"


# ============================================================
# ENGINE
# ============================================================


class WalkForwardParameterEngine:
    """
    Walk-forward parameter selection + out-of-sample evaluation.

    Public API:

        evaluate(
            candles,
            parameter_name,
            parameter_values,
            evaluator,
        ) -> WalkForwardSelectionReport

    The engine is stateless across calls.
    """

    def __init__(
        self,
        config: WalkForwardConfig | None = None,
    ) -> None:
        self.config = config or WalkForwardConfig()
        self._performance_engine = PerformanceAnalyticsEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def evaluate(
        self,
        candles: Sequence[OHLCVCandle],
        parameter_name: str,
        parameter_values: Sequence[Any],
        evaluator: WalkForwardEvaluator,
    ) -> WalkForwardSelectionReport:
        """
        Run the walk-forward parameter selection pipeline.

        Steps:

        1. Chronologically split ``candles`` into development and
           evaluation windows.
        2. Evaluate EVERY candidate on the development window
           only -> ``CandidateResult`` list.
        3. Select the descriptive best (highest development
           expectancy).
        4. Evaluate ONLY the selected configuration on the
           evaluation window.
        """

        history = list(candles)
        total = len(history)

        split_index = self._split_index(total)

        development_candles = history[:split_index]
        evaluation_candles = history[split_index:]

        dev_window = (0, split_index)
        eval_window = (split_index, total)

        dev_count = len(development_candles)
        eval_count = len(evaluation_candles)

        candidates = self._evaluate_candidates(
            development_candles,
            parameter_name,
            parameter_values,
            evaluator,
        )

        selected = self._select(candidates)

        out_of_sample_result = None
        out_of_sample_perf = self._performance_engine.analyze([])
        out_of_sample_completed = 0

        if selected is not None:
            out_of_sample_result = self._safe_eval(
                evaluator,
                evaluation_candles,
                selected.parameter_value,
            )
            out_of_sample_perf = self._performance_of(
                out_of_sample_result,
            )
            out_of_sample_completed = getattr(
                out_of_sample_perf, "completed_trades", 0
            )

        sufficient_dev = (
            dev_count >= self.config.min_development_candles
            and any(
                c.sufficient_development_trades for c in candidates
            )
        )
        sufficient_eval = (
            eval_count >= self.config.min_evaluation_candles
        )

        return WalkForwardSelectionReport(
            parameter_name=parameter_name,
            candidates=tuple(candidates),
            selected=selected,
            out_of_sample_result=out_of_sample_result,
            out_of_sample_performance=out_of_sample_perf,
            out_of_sample_completed_trades=out_of_sample_completed,
            development_window=dev_window,
            evaluation_window=eval_window,
            development_candle_count=dev_count,
            evaluation_candle_count=eval_count,
            selection_isolated_from_evaluation=True,
            selection_verified=True,
            sufficient_development_window=sufficient_dev,
            sufficient_evaluation_window=sufficient_eval,
        )

    # ========================================================
    # CANDIDATE EVALUATION (development window only)
    # ========================================================

    def _evaluate_candidates(
        self,
        development_candles: list[OHLCVCandle],
        parameter_name: str,
        parameter_values: Sequence[Any],
        evaluator: WalkForwardEvaluator,
    ) -> list[CandidateResult]:
        candidates: list[CandidateResult] = []

        for value in parameter_values:
            raw = self._safe_eval(
                evaluator,
                development_candles,
                value,
            )
            performance = self._performance_of(raw)

            completed = getattr(performance, "completed_trades", 0)
            expectancy = self._finite(
                getattr(performance, "expectancy", 0.0)
            )
            total_r = self._finite(
                getattr(performance, "total_r", 0.0)
            )

            candidates.append(
                CandidateResult(
                    parameter_value=value,
                    development_performance=performance,
                    development_completed_trades=completed,
                    development_expectancy=expectancy,
                    development_total_r=total_r,
                    sufficient_development_trades=(
                        completed >= self.config.min_development_trades
                    ),
                )
            )

        return candidates

    # ========================================================
    # SELECTION (development data only)
    # ========================================================

    def _select(
        self,
        candidates: list[CandidateResult],
    ) -> SelectedConfiguration | None:
        if not candidates:
            return None

        # Descriptive best: highest development expectancy.
        # Ties broken by original order (stable max).
        best_index = 0
        best = candidates[0]
        for i, candidate in enumerate(candidates[1:], start=1):
            if candidate.development_expectancy > best.development_expectancy:
                best = candidate
                best_index = i

        return SelectedConfiguration(
            parameter_value=best.parameter_value,
            selection_basis=(
                f"development_{self.config.selection_metric}"
            ),
            development_expectancy=best.development_expectancy,
            selected_from_development_data=True,
            selected_index=best_index,
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

        if total > 0:
            index = max(1, min(index, total - 1))

        return index

    # ========================================================
    # EVALUATION HELPERS
    # ========================================================

    @staticmethod
    def _safe_eval(
        evaluator: WalkForwardEvaluator,
        candles: Sequence[OHLCVCandle],
        value: Any,
    ) -> Any:
        if not candles:
            return None

        try:
            return evaluator(candles, value)
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
    def _finite(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0

        if isinf(numeric) or not isfinite(numeric):
            return 0.0

        return numeric
