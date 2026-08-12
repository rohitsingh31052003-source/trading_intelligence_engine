"""
Experiment selection engine (Sprint 11N).

The :class:`SelectionEngine` selects among PERSISTED experiment results
(Sprint 11J/11K). It implements NO trading, validation, pipeline,
research, registry, query or comparison logic. Every value is read from
the persisted ``ExperimentResult`` objects loaded through an existing
:class:`ExperimentRegistry` (Sprint 11K). The trading pipeline and the
experiment runner are NEVER rerun to make a selection.

Public API:

    select(criteria, label="", metadata=None) -> SelectionResult

Design rules:

* Persisted-only. The engine loads experiments via
  ``registry.list()`` + ``registry.load_many()`` (persisted data only).
  It never invokes ``HistoricalEvaluationPipeline.evaluate`` or
  ``ExperimentRunner.run``.

* Evidence safety is structural. INSUFFICIENT experiments are
  NOT_ELIGIBLE and can never become CANDIDATE or SELECTED. PARTIAL
  experiments are NOT_ELIGIBLE and can never become SELECTED. Only
  SUFFICIENT experiments may become CANDIDATEs. A winner is never
  manufactured when no eligible SUFFICIENT candidate exists.

* Missing evidence is NEVER treated as positive evidence.

* Determinism. The selection id, ranking and tie-breaking are pure
  functions of the persisted data. Two identical persisted datasets
  always produce the same selected experiment. No random selection.

* No print() inside the engine.
"""

from __future__ import annotations

from typing import Mapping

from engine.models.experiment import ExperimentEvidenceStatus, ExperimentResult
from engine.models.selection import (
    SelectionCandidate,
    SelectionCriteria,
    SelectionResult,
    SelectionType,
)
from engine.registry.registry import ExperimentRegistry
from engine.selection.core import _is_eligible, build_selection_result
from engine.selection.identity import SelectionIdentity


# ============================================================
# EXPERIMENT SELECTION ENGINE
# ============================================================


class SelectionEngine:
    """
    Select among persisted experiment results.

    The engine is constructed with an :class:`ExperimentRegistry`
    (Sprint 11K). All data is loaded from persisted records; the trading
    pipeline and the experiment runner are never rerun to make a
    selection.

    The engine is stateless across calls: identical inputs always
    produce identical outputs.

    Parameters:

    registry
        The :class:`ExperimentRegistry` to load persisted experiments
        from.
    """

    def __init__(self, registry: ExperimentRegistry) -> None:
        self._registry = registry

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def registry(self) -> ExperimentRegistry:
        """The underlying Sprint 11K experiment registry."""

        return self._registry

    # ========================================================
    # PUBLIC API
    # ========================================================

    def select(
        self,
        criteria: SelectionCriteria,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> SelectionResult:
        """
        Select among all persisted experiments matching the criteria.

        Loads every persisted experiment from the registry (persisted
        data only; the trading pipeline is never rerun), projects each
        to a :class:`SelectionCandidate`, and delegates to the shared
        selection core for evidence gating, criteria evaluation and
        deterministic ranking.

        When no eligible SUFFICIENT candidate exists, ``selected`` is
        ``None`` -- a winner is never manufactured.
        """

        identity = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=criteria,
            label=label,
            metadata=metadata if metadata is not None else {},
        )

        ids = self._registry.list()
        results = self._registry.load_many(ids) if ids else []

        candidates = [
            self._to_candidate(result) for result in results
        ]
        candidates.sort(key=lambda c: c.entity_id)

        return build_selection_result(identity, candidates)

    # ========================================================
    # CANDIDATE PROJECTION
    # ========================================================

    @staticmethod
    def _to_candidate(result: ExperimentResult) -> SelectionCandidate:
        """
        Project a persisted ``ExperimentResult`` into a selection
        candidate.

        Reads authoritative values from the result summary and
        reproducibility metadata only; nothing is recomputed.
        Eligibility is the structural SUFFICIENT-evidence gate.
        """

        summary = result.summary
        repro = result.reproducibility

        evidence_status = summary.evidence_status
        eligible = _is_eligible(evidence_status)

        return SelectionCandidate(
            entity_id=result.experiment_id,
            label=result.label,
            selection_type=SelectionType.EXPERIMENT,
            evidence_status=evidence_status,
            expectancy=summary.expectancy,
            total_r=summary.total_r,
            max_drawdown_r=summary.max_drawdown_r,
            completed_trades=summary.completed_trades,
            robust=summary.robust,
            oos_expectancy=summary.oos_expectancy,
            oos_trades=summary.oos_trades,
            reproducible=bool(repro.reproducible) if repro is not None else False,
            eligible=eligible,
        )


__all__ = ["SelectionEngine"]
