"""
Suite selection engine (Sprint 11N).

The :class:`SuiteSelectionEngine` selects among PERSISTED experiment
suites (Sprint 11M). It implements NO trading, validation, pipeline,
research, experiment, registry, query, suite or comparison logic. Every
value is read from the persisted ``SuiteResult`` objects loaded through
an existing :class:`SuiteRegistry` (Sprint 11M). The trading pipeline,
the experiment runner and the suite runner are NEVER rerun to make a
selection.

Public API:

    select(criteria, label="", metadata=None) -> SelectionResult

Design rules:

* Persisted-only. The engine loads suites via
  ``suite_registry.list()`` + ``suite_registry.load_suite()``
  (persisted data only).

* Suite-level evidence gating. A suite is eligible only when it has at
  least one SUFFICIENT member. A suite is NEVER selected merely because
  it contains a high-performing insufficient member. A suite with no
  SUFFICIENT member is NOT_ELIGIBLE.

* Representative metrics. A suite's selection metrics are the metrics of
  its best SUFFICIENT member (ranked by the same deterministic ranking
  used for experiments). This means suite selection effectively ranks
  suites by their best eligible member, which naturally respects the
  member evidence gating rules.

* Missing evidence is NEVER treated as positive evidence.

* Determinism. Identical persisted suites always produce the same
  selected suite. No random selection.

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
from engine.models.suite import SuiteResult
from engine.selection.core import _rank_key, build_selection_result
from engine.selection.engine import SelectionEngine
from engine.selection.identity import SelectionIdentity
from engine.suite.registry import SuiteRegistry


# ============================================================
# SUITE SELECTION ENGINE
# ============================================================


class SuiteSelectionEngine:
    """
    Select among persisted experiment suites.

    The engine is constructed with a :class:`SuiteRegistry` (Sprint
    11M). All data is loaded from persisted suite manifests + persisted
    member experiments; the trading pipeline, the experiment runner and
    the suite runner are never rerun to make a selection.

    The engine is stateless across calls: identical inputs always
    produce identical outputs.

    Parameters:

    suite_registry
        The :class:`SuiteRegistry` to load persisted suites from.
    """

    def __init__(self, suite_registry: SuiteRegistry) -> None:
        self._suite_registry = suite_registry

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def suite_registry(self) -> SuiteRegistry:
        """The underlying Sprint 11M suite registry."""

        return self._suite_registry

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
        Select among all persisted suites matching the criteria.

        Loads every persisted suite from the registry (persisted data
        only; no pipeline / runner rerun), projects each to a
        :class:`SelectionCandidate` (using its best SUFFICIENT member's
        metrics), and delegates to the shared selection core for
        evidence gating, criteria evaluation and deterministic ranking.

        A suite with no SUFFICIENT member is NOT_ELIGIBLE and can never
        be SELECTED. When no eligible SUFFICIENT candidate exists,
        ``selected`` is ``None`` -- a winner is never manufactured.
        """

        identity = SelectionIdentity(
            selection_type=SelectionType.SUITE,
            criteria=criteria,
            label=label,
            metadata=metadata if metadata is not None else {},
        )

        ids = self._suite_registry.list()
        suites = [self._suite_registry.load_suite(sid) for sid in ids]

        candidates = [self._to_candidate(suite) for suite in suites]
        candidates.sort(key=lambda c: c.entity_id)

        return build_selection_result(identity, candidates)

    # ========================================================
    # CANDIDATE PROJECTION
    # ========================================================

    def _to_candidate(self, suite: SuiteResult) -> SelectionCandidate:
        """
        Project a persisted ``SuiteResult`` into a selection candidate.

        The suite's evidence status is derived from its members
        (SUFFICIENT if any member is SUFFICIENT; PARTIAL if none
        sufficient but some partial; INSUFFICIENT otherwise). The
        suite is eligible only when it has at least one SUFFICIENT
        member -- a suite is never selected merely because it contains
        a high-performing insufficient member.

        The suite's representative metrics are the metrics of its best
        SUFFICIENT member (ranked by the same deterministic ranking used
        for experiments). Nothing is recomputed; the member summaries
        are read as-is.
        """

        members = list(suite.members)

        sufficient_members = [
            m for m in members
            if m.summary.evidence_status == ExperimentEvidenceStatus.SUFFICIENT
        ]

        evidence_status = self._suite_evidence_status(members)
        eligible = bool(sufficient_members)

        reproducible = bool(suite.reproducibility.reproducible)

        if not sufficient_members:
            return SelectionCandidate(
                entity_id=suite.suite_id,
                label=suite.label,
                selection_type=SelectionType.SUITE,
                evidence_status=evidence_status,
                expectancy=0.0,
                total_r=0.0,
                max_drawdown_r=0.0,
                completed_trades=0,
                robust=None,
                oos_expectancy=None,
                oos_trades=0,
                reproducible=reproducible,
                eligible=False,
            )

        best = self._best_sufficient_member(sufficient_members)
        summary = best.summary

        return SelectionCandidate(
            entity_id=suite.suite_id,
            label=suite.label,
            selection_type=SelectionType.SUITE,
            evidence_status=evidence_status,
            expectancy=summary.expectancy,
            total_r=summary.total_r,
            max_drawdown_r=summary.max_drawdown_r,
            completed_trades=summary.completed_trades,
            robust=summary.robust,
            oos_expectancy=summary.oos_expectancy,
            oos_trades=summary.oos_trades,
            reproducible=reproducible,
            eligible=True,
        )

    # ========================================================
    # SUITE EVIDENCE STATUS
    # ========================================================

    @staticmethod
    def _suite_evidence_status(
        members: list[ExperimentResult],
    ) -> ExperimentEvidenceStatus:
        """
        Derive a suite's evidence status from its members.

        SUFFICIENT if any member is SUFFICIENT; PARTIAL if none
        sufficient but at least one partial; INSUFFICIENT otherwise
        (all insufficient or empty).
        """

        statuses = [m.summary.evidence_status for m in members]

        if any(s == ExperimentEvidenceStatus.SUFFICIENT for s in statuses):
            return ExperimentEvidenceStatus.SUFFICIENT

        if any(s == ExperimentEvidenceStatus.PARTIAL for s in statuses):
            return ExperimentEvidenceStatus.PARTIAL

        return ExperimentEvidenceStatus.INSUFFICIENT

    # ========================================================
    # BEST SUFFICIENT MEMBER
    # ========================================================

    @staticmethod
    def _best_sufficient_member(
        sufficient_members: list[ExperimentResult],
    ) -> ExperimentResult:
        """
        Return the best SUFFICIENT member by the deterministic ranking.

        Each member is projected to an experiment selection candidate
        (eligible, since it is SUFFICIENT) and ranked by the shared
        ranking key. The first ranked member is the suite's
        representative. Ties are broken by ascending experiment id.
        """

        candidates = [
            SelectionEngine._to_candidate(m) for m in sufficient_members
        ]
        ranked = sorted(candidates, key=_rank_key)
        best_id = ranked[0].entity_id

        for member in sufficient_members:
            if member.experiment_id == best_id:
                return member

        # Defensive fallback (should never happen): first member.
        return sufficient_members[0]


__all__ = ["SuiteSelectionEngine"]
