"""
Evaluation reporting engine (Sprint 11G).

The ``EvaluationReportEngine`` consumes an existing
``PipelineResult`` (produced by the Sprint 11F
``HistoricalEvaluationPipeline``) and produces a structured,
immutable ``EvaluationReport``.

Design rules:

* No duplication of analytics logic.
  Trade statistics are delegated to the
  ``PerformanceAnalytics`` already embedded in the pipeline
  result. This engine only reads, counts and projects.

* No mutation of input.
  The pipeline result and its evaluation points are read-only.

* Deterministic.
  Identical inputs always produce identical reports. The
  engine carries no state across calls.

* Additive integration.
  No existing engine or model is modified. This module is a
  new layer above the pipeline.

The reporting layer preserves the pipeline / signal / trade
separation required by Sprint 11G so downstream comparison and
research tooling can address each view independently.
"""

from __future__ import annotations

from math import isfinite, isinf
from typing import Any, Iterable, Mapping

from engine.models.evaluation import (
    EvaluationReport,
    PipelineStatistics,
    SignalStatistics,
    TradeStatistics,
)
from engine.models.pipeline import (
    PipelineEvaluationPoint,
    PipelineResult,
)
from engine.models.signal import SignalState
from engine.models.validation import ValidationStatus


# Terminal validation statuses: the validation reached a final
# outcome. OPEN is excluded because the future window ran out
# of candles before the signal resolved.
_TERMINAL_STATUSES = frozenset(
    {
        ValidationStatus.WIN,
        ValidationStatus.LOSS,
        ValidationStatus.EXPIRED,
        ValidationStatus.AMBIGUOUS,
        ValidationStatus.NOT_TRIGGERED,
    }
)


class EvaluationReportEngine:
    """
    Produce a structured ``EvaluationReport`` from a
    ``PipelineResult``.

    Public API:

        analyze(result, label, metadata) -> EvaluationReport

    The engine is stateless across calls.
    """

    # ========================================================
    # PUBLIC API
    # ========================================================

    def analyze(
        self,
        result: PipelineResult,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> EvaluationReport:
        """
        Build an immutable evaluation report from a pipeline
        result.

        Parameters:

        result
            The ``PipelineResult`` produced by the historical
            evaluation pipeline.

        label
            Optional identifying label for the run (strategy
            name, parameter set id, dataset name, ...).

        metadata
            Optional free-form string key/value context for
            future comparison / robustness layers.
        """

        pipeline_stats = self._pipeline_statistics(result)

        signal_stats = self._signal_statistics(result)

        trade_stats = TradeStatistics.from_performance(
            result.performance,
        )

        return EvaluationReport(
            label=label,
            pipeline=pipeline_stats,
            signals=signal_stats,
            trades=trade_stats,
            result=result,
            metadata=dict(metadata) if metadata is not None else {},
        )

    # ========================================================
    # PIPELINE-LEVEL
    # ========================================================

    def _pipeline_statistics(
        self,
        result: PipelineResult,
    ) -> PipelineStatistics:
        """
        Derive the walk-forward funnel from the pipeline result.

        Suppressed and terminal-validation counts are derived
        from the evaluation-point sequence, which is the source
        of truth for the one-active-signal policy.
        """

        points = result.evaluation_points_sequence

        suppressed = sum(1 for p in points if p.suppressed)

        validations_completed = sum(
            1
            for p in points
            if p.validation is not None
            and self._status_of(p.validation) in _TERMINAL_STATUSES
        )

        return PipelineStatistics(
            candles_processed=result.candles_processed,
            evaluation_points=result.evaluation_points,
            decisions_generated=result.decisions_generated,
            eligible_decisions=result.eligible_decisions,
            signals_generated=result.signals_generated,
            signals_suppressed=suppressed,
            signals_validated=result.signals_validated,
            validations_completed=validations_completed,
            completed_trades=result.completed_trades,
        )

    # ========================================================
    # SIGNAL-LEVEL
    # ========================================================

    def _signal_statistics(
        self,
        result: PipelineResult,
    ) -> SignalStatistics:
        """
        Derive signal-generation characteristics.

        Directional counts are taken from the generated signals
        tuple (``result.signals``), which contains only signals
        actually forwarded to validation. Suppressed signals
        live on the evaluation points and are counted there.
        """

        points = result.evaluation_points_sequence

        signals = result.signals

        long_signals = sum(
            1 for s in signals if s.state == SignalState.LONG
        )
        short_signals = sum(
            1 for s in signals if s.state == SignalState.SHORT
        )

        suppressed_signals = sum(1 for p in points if p.suppressed)

        no_signal_points = sum(
            1 for p in points if self._is_no_signal_point(p)
        )

        invalid_signals = sum(
            1
            for p in points
            if p.signal is not None
            and p.signal.state == SignalState.INVALID
        )

        confidences = [
            float(s.confidence)
            for s in signals
            if self._is_finite_number(s.confidence)
        ]

        risk_rewards = [
            float(s.risk_reward_ratio)
            for s in signals
            if self._is_finite_number(s.risk_reward_ratio)
        ]

        average_confidence = self._safe_mean(confidences)
        average_risk_reward = self._safe_mean(risk_rewards)

        return SignalStatistics(
            total_signals=len(signals),
            long_signals=long_signals,
            short_signals=short_signals,
            eligible_signals=result.eligible_decisions,
            suppressed_signals=suppressed_signals,
            no_signal_points=no_signal_points,
            invalid_signals=invalid_signals,
            average_confidence=average_confidence,
            average_risk_reward=average_risk_reward,
        )

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def _is_no_signal_point(
        point: PipelineEvaluationPoint,
    ) -> bool:
        """
        Whether an evaluation point produced no signal at all.

        A point whose signal is None, or whose signal state is
        NO_SIGNAL, contributes to the "no signal" count.
        """

        if point.signal is None:
            return True

        return point.signal.state == SignalState.NO_SIGNAL

    @staticmethod
    def _status_of(
        validation: Any,
    ) -> ValidationStatus:
        """
        Extract the validation status defensively.
        """

        status = getattr(validation, "status", None)

        if isinstance(status, ValidationStatus):
            return status

        return ValidationStatus.OPEN

    @staticmethod
    def _is_finite_number(
        value: Any,
    ) -> bool:
        """
        Whether a value is a finite number usable for averaging.
        """

        if value is None:
            return False

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False

        if isinf(numeric):
            return False

        return isfinite(numeric)

    @staticmethod
    def _safe_mean(
        values: Iterable[float],
    ) -> float:
        """
        Arithmetic mean that defaults to 0.0 on empty input.
        """

        values_list = list(values)

        if not values_list:
            return 0.0

        return sum(values_list) / len(values_list)
